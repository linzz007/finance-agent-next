"""Start the isolated financial research application."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from run_policy_api import load_local_env

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def configure_environment(root: Path = ROOT, *, load_files: bool = True, model_profile: str | None = None) -> Path:
    root = root.resolve()
    if load_files:
        load_local_env(root)
    if model_profile is not None:
        os.environ["AGENT_WORKBENCH_MODEL_PROFILE"] = model_profile
    if os.getenv("AGENT_WORKBENCH_MODEL_PROFILE", "").strip():
        from policy_impact.harness.model_config import effective_model_config

        effective_model_config()
    configured = os.getenv("FINANCE_ARTICLE_ROOT", "").strip()
    article_path = Path(configured).expanduser() if configured else Path("data/finance/articles")
    if not article_path.is_absolute():
        article_path = root / article_path
    article_path = article_path.resolve()
    if not article_path.is_relative_to(root) or article_path == root:
        raise ValueError("FINANCE_ARTICLE_ROOT must be a directory inside finance-agent-next")
    if not article_path.is_dir():
        raise ValueError(f"Finance article directory does not exist: {article_path}")
    os.environ["FINANCE_ARTICLE_ROOT"] = str(article_path)
    return article_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run finance-agent-next locally")
    parser.add_argument("--port", type=int, help="Override the new application's port")
    parser.add_argument("--model-profile", help="Named model from config/model_profiles.json (gpt, gpt-responses or deepseek)")
    args = parser.parse_args()
    try:
        configure_environment(model_profile=args.model_profile)
        port = args.port if args.port is not None else int(os.getenv("POLICY_IMPACT_API_PORT", "8510"))
    except ValueError as exc:
        parser.error(str(exc))
    if not 1 <= port <= 65535:
        parser.error("port must be between 1 and 65535")
    import uvicorn

    uvicorn.run(
        "policy_impact.app.finance_research_app:app", host="127.0.0.1", port=port, reload=False
    )


if __name__ == "__main__":
    main()
