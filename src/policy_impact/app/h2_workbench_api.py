"""Single-user, loopback-only HTTP projection for the H2 workbench."""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from policy_impact.dispatch.contracts import DispatchConstraints, DispatchError
from policy_impact.dispatch.finance_review_status import (
    artifact_review_presentation,
    is_accepted_analyst_result,
)
from policy_impact.dispatch.http_api import _raise_http


class SessionInput(BaseModel):
    title: str = Field(default="新的研究", min_length=1, max_length=160)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4096)
    model_profile: str = Field(min_length=1, max_length=160)
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    dispatch_constraints: DispatchConstraints | None = None


def register_h2_workbench(app: FastAPI, static_dir):
    prefix = "/api/h2-workbench"

    def workbench():
        value = getattr(app.state, "h2_workbench", None)
        if value is None:
            raise HTTPException(503, "H2 workbench is not running")
        return value

    # Localhost binding is not authentication. Reject cross-origin browser
    # mutations so an unrelated website cannot initiate paid localhost runs.
    async def local_write(request: Request):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin mutation is not allowed")

    @app.get("/")
    @app.get("/workbench")
    def page():
        return FileResponse(
            static_dir / "h2-workbench/index.html", headers={"Cache-Control": "no-store"}
        )

    @app.get(prefix + "/config")
    def config():
        wb = workbench()
        c = wb.repository.config
        return {
            "runtime": "harness-v2",
            "transport_backend": wb.transport_backend,
            "default_profile": wb.default_profile,
            "models": wb.models,
            "driver_running": bool(wb.loops),
            "driver_error": wb.last_error,
            "budget": {
                "root": c.root_task_calls,
                "child": c.child_task_calls,
                "run": c.root_calls,
                "minutes": c.run_ms // 60000,
            },
        }

    @app.get(prefix + "/sessions")
    def sessions():
        return {"sessions": workbench().sessions()}

    def archives():
        value = workbench().archives
        if value is None:
            raise HTTPException(404, "Test records are not configured")
        return value

    @app.get(prefix + "/archives")
    def archive_catalog():
        value = workbench().archives
        return {"archives": value.catalog() if value is not None else []}

    @app.get(prefix + "/archives/{archive_id}")
    def archive_view(archive_id: str, after_event_id: str = Query("0", pattern=r"^[0-9]+$")):
        try:
            return archives().view(archive_id, after_event_id=after_event_id)
        except DispatchError as exc:
            _raise_http(exc)

    @app.get(prefix + "/archives/{archive_id}/artifacts/{artifact_ref}")
    def archive_artifact(
        archive_id: str, artifact_ref: str,
        offset: int = Query(0, ge=0), limit: int = Query(16000, ge=1, le=64000),
    ):
        try:
            return archives().artifact(archive_id, artifact_ref, offset=offset, limit=limit)
        except DispatchError as exc:
            _raise_http(exc)

    @app.post(prefix + "/sessions", status_code=201)
    async def create_session(payload: SessionInput, request: Request):
        await local_write(request)
        return workbench().create_session(payload.title)

    @app.get(prefix + "/sessions/{session_id}")
    def history(session_id: str):
        try:
            return {"runs": workbench().history(session_id)}
        except DispatchError as exc:
            _raise_http(exc)

    @app.post(prefix + "/sessions/{session_id}/messages", status_code=202)
    async def submit(session_id: str, payload: MessageInput, request: Request):
        await local_write(request)
        try:
            return workbench().submit(
                session_id, payload.query, payload.model_profile, payload.request_id,
                dispatch_constraints=(payload.dispatch_constraints.model_dump() if payload.dispatch_constraints else None),
            )
        except DispatchError as exc:
            _raise_http(exc)

    @app.get(prefix + "/runs/{run_id}")
    def view(run_id: str, session_id: str, after_event_id: str = Query("0", pattern=r"^[0-9]+$")):
        try:
            return workbench().view(run_id, session_id, after_event_id=after_event_id)
        except DispatchError as exc:
            _raise_http(exc)

    @app.post(prefix + "/runs/{run_id}/cancel")
    async def cancel(
        run_id: str,
        session_id: str,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=160),
    ):
        await local_write(request)
        try:
            return workbench().cancel(run_id, session_id, idempotency_key)
        except DispatchError as exc:
            _raise_http(exc)

    @app.get(prefix + "/runs/{run_id}/artifacts/{artifact_ref}")
    def artifact(
        run_id: str,
        artifact_ref: str,
        session_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(16000, ge=1, le=64000),
    ):
        wb = workbench()
        try:
            handle = wb.service.handle_for(run_id, session_ref=session_id)
            text = wb.repository.read_input(wb.service.caller(handle), artifact_ref).decode("utf-8")
            with wb.repository.db.connection() as connection:
                trusted = is_accepted_analyst_result(connection, run_id, artifact_ref)
            return {
                "content": text[offset : offset + limit],
                "has_more": offset + limit < len(text),
                "next_offset": min(offset + limit, len(text)),
                **artifact_review_presentation(text, trusted_result=trusted),
            }
        except DispatchError as exc:
            _raise_http(exc)
