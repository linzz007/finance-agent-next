"""Financial product composition over the inherited shared Workbench endpoints."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles

from policy_impact.app import api as shared

WORKSPACE_ID = "finance_observer"
LEGACY_COMMANDS = frozenset({"/report", "/impact", "/policy", "/news", "/research", "/wiki"})


async def require_finance_scope(request: Request) -> None:
    company_id = request.path_params.get("company_id")
    if company_id is not None and company_id != WORKSPACE_ID:
        raise HTTPException(status_code=404, detail="Workspace is outside this financial product")
    if request.method != "POST":
        return
    # Validate product entry controls, not the meaning of the user's research question.
    if "/sessions" in request.url.path:
        body = await request.json()
        if not isinstance(body, dict):
            return  # The endpoint schema reports the validation error.
        skill_id = body.get("active_skill_id")
        if skill_id not in (None, "", "general_chat"):
            raise HTTPException(status_code=400, detail="Only financial research chat is enabled")
        if body.get("mode") not in (None, "", "auto", "chat"):
            raise HTTPException(status_code=400, detail="Legacy workflow modes are not enabled")
        message = body.get("message")
        command = message.strip().split(maxsplit=1)[:1] if isinstance(message, str) else []
        if command and command[0].lower() in LEGACY_COMMANDS:
            raise HTTPException(status_code=400, detail="Legacy report commands are not enabled")


def _research_route(route: APIRoute) -> bool:
    path = route.path
    if path in {"/", "/workbench", "/companies/{company_id}"}:
        return True
    if not path.startswith("/companies/{company_id}/workbench/"):
        return False
    if path.endswith("/skills") or path.endswith("/models") and "POST" in route.methods:
        return False
    return "/finance/reports" not in path


def create_app(*, harness_v2_service=None, h2_workbench=None, default_h2=False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        if default_h2:
            wb = h2_workbench
            if wb is None:
                from policy_impact.app.h2_workbench import build_h2_workbench
                from policy_impact.domains.finance.article_corpus import article_root

                wb = await asyncio.to_thread(
                    build_h2_workbench,
                    article_root(),
                    project_root=Path(__file__).resolve().parents[3],
                )
            app.state.h2_workbench = wb
            await wb.start()
        try:
            yield
        finally:
            if default_h2:
                await app.state.h2_workbench.stop()

    app = FastAPI(
        title="finance-agent-next", version="2.0.0" if default_h2 else "0.1.0", lifespan=lifespan
    )
    selected = APIRouter()
    for route in shared.app.routes:
        if isinstance(route, APIRoute) and _research_route(route):
            if default_h2 and (route.path in {"/", "/workbench"} or "/sessions" in route.path):
                continue
            selected.routes.append(route)
    app.include_router(selected, dependencies=[Depends(require_finance_scope)])
    app.mount("/static", StaticFiles(directory=str(shared.STATIC_DIR)), name="static")

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "product": "finance-agent-next",
            "workspace": WORKSPACE_ID,
            "runtime": "harness-v2" if default_h2 else "legacy-v1",
        }

    @app.get("/companies")
    def companies() -> dict:
        overview = shared.service.company_overview(WORKSPACE_ID)
        profile = overview["profile"]
        summary = {
            "company_id": WORKSPACE_ID,
            "company_name": profile.get("company_name", "Financial Research"),
            "short_name": profile.get("short_name", "Financial Research"),
            "fact_count": overview.get("fact_count", 0),
        }
        return {"companies": [summary], "company_ids": [WORKSPACE_ID]}

    @app.get(
        "/companies/{company_id}/workbench/skills", dependencies=[Depends(require_finance_scope)]
    )
    def skills(company_id: str) -> dict:
        return {
            "skills": [
                s for s in shared.workbench_service.list_skills() if s["id"] == "general_chat"
            ]
        }

    if harness_v2_service is not None:
        from policy_impact.dispatch.http_api import register_harness_v2_routes

        register_harness_v2_routes(app, harness_v2_service)

    if default_h2:
        from policy_impact.app.h2_workbench_api import register_h2_workbench

        register_h2_workbench(app, shared.STATIC_DIR)

    return app


app = create_app(default_h2=True)
