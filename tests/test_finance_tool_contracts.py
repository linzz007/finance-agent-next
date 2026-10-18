"""Public JSON protocol and input contract regressions, with fixture data only."""

from copy import deepcopy
import json

import pytest

from policy_impact.domains.finance.tool_contracts import INPUTS, validate_tool_input
from policy_impact.harness.context_manifest import estimate_tokens
from policy_impact.harness.context_offload import RunContextStore
from policy_impact.runtime.plugin_config import read_plugin_toolkit


def test_every_published_tool_uses_its_executable_input_schema():
    tools = read_plugin_toolkit("finance_research.json")
    for tool in tools:
        schema = tool["parameters"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        expected = INPUTS[tool["name"]].model_json_schema()
        assert schema.get("required", []) == expected.get("required", [])
        assert schema["properties"].keys() == expected["properties"].keys()
    checkpoint = next(row for row in tools if row["name"] == "research.checkpoint")
    assert "Check" in checkpoint["parameters"]["$defs"]


@pytest.mark.parametrize("name,arguments,path", [
    ("article.read", {"article_id": "fixture", "limit": True}, "limit"),
    ("article.list", {"author": ["TT", "other"]}, "author"),
    ("article.read", {"article_id": "fixture", "path": "C:/outside"}, "path"),
    ("research.plan", {"operation": "create", "goals": "read,answer"}, "goals"),
    ("research.plan", {"operation": "create", "scope": {"authors": "TT"}}, "scope.authors"),
    ("research.checkpoint", {"checks": [{"claim": "x", "decision": "check", "outcome": "yes", "reason": "x"}]}, "checks.0.outcome"),
    ("research.coverage", {"aspects": [], "decision": "synthesize", "reason": "Evidence coverage is sufficient."}, "aspects"),
    ("context.open", {"context_id": "fixture", "offset": -1}, "offset"),
])
def test_bad_inputs_have_actionable_locations_without_echoing_input(name, arguments, path):
    with pytest.raises(ValueError, match="tool_contract_error") as failed:
        validate_tool_input(name, arguments)
    assert path in str(failed.value)
    assert "C:/outside" not in str(failed.value)


def test_valid_input_is_not_mutated_or_filled_with_defaults():
    arguments = {"operation": "create", "goals": ["Read sources", "Answer question"],
                 "scope": {"authors": ["TT"], "date_range": {"start": "2026-10-01"}}}
    original = deepcopy(arguments)
    validate_tool_input("research.plan", arguments)
    assert arguments == original


def test_large_observation_pages_reassemble_exact_original(tmp_path):
    store = RunContextStore(tmp_path / "run")
    original = {"tool_result": {"text": "important source text " * 2000, "evidence_id": "evidence_fixture"}}
    saved = store.save(original, "fixture.read")
    offset, pages = 0, []
    while offset is not None:
        view = store.view(saved["context_id"], offset=offset, max_tokens=1500)
        assert view["schema_version"] == "run.context_view.v1"
        assert view["checksum"] == saved["checksum"]
        assert estimate_tokens(view) <= 1500
        assert store.validate_view(view)["content"] == original
        pages.append(view["text"])
        offset = view["next_offset"]
    assert json.loads("".join(pages)) == original
    assert store.open(saved["context_id"])["content"] == original
    assert len(list(store.root.glob("context_*.json"))) == 1


def test_context_view_integrity_and_run_isolation(tmp_path):
    store = RunContextStore(tmp_path / "run")
    saved = store.save({"tool_result": {"text": "source" * 3000}}, "article.read")
    view = store.view(saved["context_id"], limit=300)
    tampered = {**view, "text": "invented source"}
    with pytest.raises(ValueError, match="differs"):
        store.validate_view(tampered)
    with pytest.raises(FileNotFoundError):
        RunContextStore(tmp_path / "other_run").view(saved["context_id"])
    with pytest.raises(ValueError, match="generated"):
        store.view("../credentials")


def test_small_unpaged_context_keeps_exact_v1_shape(tmp_path):
    store = RunContextStore(tmp_path / "run")
    saved = store.save({"tool_result": {"text": "short source"}}, "article.read")
    assert store.view(saved["context_id"]) == store.open(saved["context_id"])
    assert store.view(saved["context_id"], limit=3)["returned_chars"] == 3
