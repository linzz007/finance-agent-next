"""Run the maintained finance/shared-runtime regression scope in temporary workspaces."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from policy_impact.company_wiki import loader
from policy_impact.harness.model_config import AUTH_ENV_VARS, PROFILE_ENV_VAR


class IsolatedWorkspaces:
    @pytest.fixture(autouse=True)
    def isolate_workspace(self, tmp_path_factory, monkeypatch):
        workspace = tmp_path_factory.mktemp("finance-baseline")
        monkeypatch.setattr(loader, "project_root", lambda: workspace)
        corpus = workspace / "articles"
        corpus.mkdir()
        monkeypatch.setenv("FINANCE_ARTICLE_ROOT", str(corpus))


def main() -> int:
    # This command does not load .env.local; individual tests may install fake credentials.
    for variable in (*AUTH_ENV_VARS, "DEEPSEEK_API_KEY", PROFILE_ENV_VAR):
        os.environ.pop(variable, None)
    patterns = (
        "test_finance*.py", "test_subagent*.py", "test_openai_streaming.py", "test_responses_transport.py",
        "test_agent_loop*.py", "test_turn_coordinator*.py",
        "test_context_manifest.py", "test_chat_context.py",
        "test_plugin_config_loading.py",
        "test_model_profiles.py", "test_model_gateway.py", "test_run_policy_api_env.py",
    )
    files = sorted({path for pattern in patterns for path in (ROOT / "tests").glob(pattern)})
    if not files:
        raise RuntimeError("No financial regression tests found")
    print(f"Financial/shared-runtime baseline: {len(files)} modules; temporary workspaces; no live credentials")
    return pytest.main(["-q", *map(str, files), *sys.argv[1:]], plugins=[IsolatedWorkspaces()])


if __name__ == "__main__":
    raise SystemExit(main())
