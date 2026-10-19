"""Financial product composition over the inherited shared Workbench endpoints."""

from __future__ import annotations

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


def create_app(*, harness_v2_service=None) -> FastAPI:
    app = FastAPI(title="finance-agent-next", version="0.1.0")
    selected = APIRouter()
    for route in shared.app.routes:
        if isinstance(route, APIRoute) and _research_route(route):
            selected.routes.append(route)
    app.include_router(selected, dependencies=[Depends(require_finance_scope)])
    app.mount("/static", StaticFiles(directory=str(shared.STATIC_DIR)), name="static")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "product": "finance-agent-next", "workspace": WORKSPACE_ID}

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

    @app.get("/companies/{company_id}/workbench/skills", dependencies=[Depends(require_finance_scope)])
    def skills(company_id: str) -> dict:
        return {"skills": [s for s in shared.workbench_service.list_skills() if s["id"] == "general_chat"]}

    if harness_v2_service is not None:
        from policy_impact.dispatch.http_api import register_harness_v2_routes

        register_harness_v2_routes(app, harness_v2_service)

    return app


app = create_app()
