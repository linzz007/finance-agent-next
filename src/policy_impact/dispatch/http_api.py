"""Opt-in HTTP mapping for the durable Harness 2.0 dispatcher.

The router is injectable and does not create a repository, Redis client or
worker. All state transitions continue through ``FinanceHarnessV2Service``
and its SQLite-backed repository.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .contracts import Cancel, DispatchError, ModelPolicySet, Resume, Send, Spawn, Wait
from .execution import ResultGet, Status


class CreateRunRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    session_ref: str = Field(min_length=1, max_length=160)
    query: str = Field(min_length=1, max_length=4096)
    selected_profile: str = Field(min_length=1, max_length=160)
    allowed_profiles: list[str] = Field(min_length=1, max_length=16)
    tools: list[str] = Field(min_length=1, max_length=64)


def _raise_http(error: DispatchError) -> None:
    if error.code in {"not_found", "forbidden"}:
        raise HTTPException(status_code=404, detail="Harness run is not available") from error
    if error.code in {"invalid_arguments", "checkpoint_invalid", "contract_error"}:
        raise HTTPException(status_code=422, detail=error.code) from error
    if error.code in {
        "idempotency_conflict", "state_conflict", "invalid_state", "stale_lease", "thread_busy",
        "budget_denied", "budget_reduced", "depth_exceeded", "deadline_exceeded", "cancelled",
    }:
        raise HTTPException(status_code=409, detail=error.code) from error
    if error.code in {
        "provider_error", "quota_error", "timeout", "outcome_unknown", "transport_error",
        "artifact_unavailable", "dispatch_failed", "backpressured",
    }:
        raise HTTPException(status_code=503, detail=error.code) from error
    raise HTTPException(status_code=500, detail="harness operation failed") from error


def register_harness_v2_routes(
    app: FastAPI,
    service,
    *,
    prefix: str = "/api/harness/v2",
) -> APIRouter:
    """Mount the H2 routes against an already configured service."""
    router = APIRouter(prefix=prefix)

    def handle(run_id: str, session_ref: str):
        try:
            return service.handle_for(run_id, session_ref=session_ref)
        except DispatchError as error:
            _raise_http(error)

    @router.post("/runs", status_code=202)
    def create_run(payload: CreateRunRequest):
        try:
            value = service.create_run(**payload.model_dump())
        except DispatchError as error:
            _raise_http(error)
        return {"run": value.__dict__, "status": "accepted"}

    @router.post("/runs/{run_id}/agents", status_code=202)
    def spawn(
        run_id: str,
        payload: Spawn,
        x_harness_session: Annotated[str, Header()] = "",
    ):
        current = handle(run_id, x_harness_session)
        try:
            return service.repository.spawn(service.caller(current), payload)
        except DispatchError as error:
            _raise_http(error)

    @router.post("/runs/{run_id}/wait")
    def wait(
        run_id: str,
        payload: Wait,
        x_harness_session: Annotated[str, Header()] = "",
    ):
        current = handle(run_id, x_harness_session)
        try:
            return service.repository.wait_snapshot(service.caller(current), payload)
        except DispatchError as error:
            _raise_http(error)

    @router.post("/runs/{run_id}/status")
    def status(
        run_id: str,
        payload: Status,
        x_harness_session: Annotated[str, Header()] = "",
    ):
        current = handle(run_id, x_harness_session)
        try:
            return service.repository.status(service.caller(current), **payload.model_dump())
        except DispatchError as error:
            _raise_http(error)

    @router.post("/runs/{run_id}/threads/{thread_id}/messages", status_code=202)
    def send(
        run_id: str,
        thread_id: str,
        payload: Send,
        x_harness_session: Annotated[str, Header()] = "",
    ):
        if payload.thread_id != thread_id:
            raise HTTPException(status_code=409, detail="thread_id does not match the path")
        current = handle(run_id, x_harness_session)
        try:
            return service.repository.send(service.caller(current), payload)
        except DispatchError as error:
            _raise_http(error)

    @router.post("/runs/{run_id}/tasks/{task_id}/cancel", status_code=202)
    def cancel(
        run_id: str,
        task_id: str,
        payload: Cancel,
        x_harness_session: Annotated[str, Header()] = "",
    ):
        if payload.task_id != task_id:
            raise HTTPException(status_code=409, detail="task_id does not match the path")
        current = handle(run_id, x_harness_session)
        try:
            return service.repository.cancel(service.caller(current), payload)
        except DispatchError as error:
            _raise_http(error)

    @router.post("/runs/{run_id}/tasks/{task_id}/resume", status_code=202)
    def resume(
        run_id: str,
        task_id: str,
        payload: Resume,
        x_harness_session: Annotated[str, Header()] = "",
    ):
        if payload.task_id != task_id:
            raise HTTPException(status_code=409, detail="task_id does not match the path")
        current = handle(run_id, x_harness_session)
        try:
            return service.repository.resume(service.caller(current), payload)
        except DispatchError as error:
            _raise_http(error)

    @router.get("/runs/{run_id}/results/{result_ref}")
    def result(
        run_id: str,
        result_ref: str,
        x_harness_session: Annotated[str, Header()] = "",
        view: str = Query(default="summary"),
        section: str | None = Query(default=None),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=16000, gt=0, le=64000),
    ):
        current = handle(run_id, x_harness_session)
        try:
            request = ResultGet(
                result_ref=result_ref,
                view=view,
                section=section,
                offset=offset,
                limit=limit,
            )
            return service.repository.result_get(service.caller(current), **request.model_dump())
        except DispatchError as error:
            _raise_http(error)

    @router.put("/runs/{run_id}/model-policy")
    def model_policy(
        run_id: str,
        payload: ModelPolicySet,
        x_harness_session: Annotated[str, Header()] = "",
    ):
        current = handle(run_id, x_harness_session)
        try:
            return service.repository.set_model_policy(service.caller(current), payload)
        except DispatchError as error:
            _raise_http(error)

    app.include_router(router)
    return router


__all__ = ["CreateRunRequest", "register_harness_v2_routes"]
