"""Acceptance collection must isolate evidence and not manufacture a score."""

import json

import pytest

from scripts import finance_research_live_acceptance as live


def test_fixed_cases_reuse_original_query_text():
    queries = live.load_cases(live.ROOT / "docs/finance_agent_eval_v1_acceptance.md")
    assert list(queries) == ["Q04", "Q08", "Q14"]
    assert "2026-09-30" in queries["Q04"]
    assert "近几个月" in queries["Q08"]
    assert "TT 和培风客" in queries["Q14"]


@pytest.mark.parametrize("profile_in_articles", [False, True])
def test_snapshot_has_no_database_history_and_is_independent(tmp_path, profile_in_articles):
    company = tmp_path / "data/companies/finance_observer"
    (company / "wiki").mkdir(parents=True)
    (company / "company.yaml").write_text("{}", encoding="utf-8")
    (company / "wiki/index.md").write_text("# Fixture", encoding="utf-8")
    (company / "db").mkdir()
    (company / "db/private.sqlite").write_bytes(b"not a database")
    finance = tmp_path / "data/finance"
    articles = finance / "articles/Fixture/articles"
    articles.mkdir(parents=True)
    source = articles / "2026-09-30-fixture.md"
    source.write_text("# Fixture\nOriginal body", encoding="utf-8")
    profile_dir = finance / "articles" if profile_in_articles else finance
    (profile_dir / "research_policy.md").write_text("Fixture policy", encoding="utf-8")
    output = tmp_path / "acceptance"
    snapshot = live.snapshot_inputs(tmp_path, finance / "articles", output)
    assert snapshot["article_counts"] == {"Fixture": 1}
    assert not any("db/" in name for name in snapshot["files"])
    expected = "finance/articles/research_policy.md" if profile_in_articles else "finance/research_policy.md"
    assert expected in snapshot["files"]
    from policy_impact.domains.finance.article_profile import load_research_policy

    assert load_research_policy(output / "finance/articles") == "Fixture policy"
    source.write_text("Changed", encoding="utf-8")
    assert live.inventory(output) == snapshot["files"]
    with pytest.raises(FileExistsError):
        live.snapshot_inputs(tmp_path, finance / "articles", output)


def test_export_redacts_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fixture-private-token")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-deepseek-token")
    output = tmp_path / "record.json"
    live.write_json(output, {"error": "fixture-private-token fixture-deepseek-token sk-unknownTestToken12345"})
    assert "fixture-private-token" not in output.read_text(encoding="utf-8")
    assert "fixture-deepseek-token" not in output.read_text(encoding="utf-8")
    assert "sk-unknownTestToken12345" not in output.read_text(encoding="utf-8")
    assert "[REDACTED]" in json.loads(output.read_text(encoding="utf-8"))["error"]


def test_reuse_snapshot_preserves_inputs_and_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "ROOT", tmp_path)
    original = tmp_path / "data/acceptance/original"
    source = original / "snapshot/finance/articles/Fixture/articles/fixture.md"
    source.parent.mkdir(parents=True)
    source.write_text("Original fixture", encoding="utf-8")
    expected = {"files": live.inventory(original / "snapshot"), "article_counts": {"Fixture": 1}}
    live.write_json(original / "manifest.json", {"snapshot": expected, "research_day": "2026-10-17"})
    destination = tmp_path / "data/acceptance/retest/snapshot"
    snapshot, clock = live.reuse_snapshot(original, destination)
    assert snapshot == expected
    assert clock == "2026-10-17"
    assert live.inventory(destination) == expected["files"]
    with pytest.raises(FileExistsError):
        live.reuse_snapshot(original, destination)
    source.write_text("Changed after freezing", encoding="utf-8")
    assert live.inventory(destination) == expected["files"]
    with pytest.raises(ValueError, match="differs"):
        live.reuse_snapshot(original, tmp_path / "data/acceptance/another/snapshot")


def test_reuse_snapshot_rejects_outside_acceptance(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="inside data/acceptance"):
        live.reuse_snapshot(tmp_path / "outside", tmp_path / "destination")


@pytest.mark.parametrize("profile", [None, "deepseek", "gpt-responses"])
def test_single_case_revalidation_is_explicit_and_bounded(tmp_path, monkeypatch, profile):
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_MODEL", "fixture-default")
    monkeypatch.delenv("AGENT_WORKBENCH_MODEL_PROFILE", raising=False)
    configured = []

    def configure(**kwargs):
        configured.append(kwargs)
        return tmp_path / "corpus"

    monkeypatch.setattr("run_finance.configure_environment", configure)
    # Execution is mocked; generated runtime paths are covered separately.
    monkeypatch.setattr(live, "validate_windows_generated_paths", lambda *args: None)
    monkeypatch.setattr("policy_impact.harness.model_config.model_runtime_status", lambda: {
        "available": True, "protocol": "openai-responses" if profile == "gpt-responses" else "openai-compatible",
        "model_id": "fixture-override"})
    monkeypatch.setattr("policy_impact.domains.finance.article_profile.load_holdings", lambda path: {})
    monkeypatch.setattr("policy_impact.domains.finance.article_profile.load_research_policy", lambda path: "")
    monkeypatch.setattr(live, "load_cases", lambda path: {"Q04": "q4", "Q08": "q8", "Q14": "q14"})
    snapshot = {"files": {}, "article_counts": {"Fixture": 1}}
    monkeypatch.setattr(live, "reuse_snapshot", lambda source, target: (snapshot, "2026-10-17"))
    monkeypatch.setattr(live, "inventory", lambda path: {})
    monkeypatch.setattr(live, "code_inventory", dict)
    monkeypatch.setattr(live, "probe_service", lambda output, limits: True)
    calls = []

    def run_case(output, key, repetition, query, clock):
        calls.append((key, repetition, query, clock))
        return {"case": f"{key}-r{repetition}", "status": "returned", "answer": "Fixture answer"}

    monkeypatch.setattr(live, "run_case", run_case)
    output = tmp_path / "data/acceptance/revalidation"
    selection = ["--model-profile", profile] if profile else ["--model", "fixture-override"]
    monkeypatch.setattr("sys.argv", ["acceptance", "--live", "--output", str(output),
        "--repetitions", "1", "--cases", "Q08", *selection,
        "--snapshot-from", str(tmp_path / "data/acceptance/original")])
    assert live.main() == 0
    assert configured == [{"model_profile": profile}]
    assert calls == [("Q08", 1, "q8", "2026-10-17")]
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["repetitions"] == 1
    assert manifest["queries"] == {"Q08": "q8"}
    assert manifest["snapshot"] == snapshot
    assert manifest["automatic_semantic_scoring"] is False


def test_synthetic_probe_respects_configured_input_estimate():
    from policy_impact.harness.context_manifest import estimate_tokens

    for limit in (2000, 43904):
        messages = live.synthetic_messages(True, limit)
        assert 0.8 * limit <= estimate_tokens(messages) <= limit
        assert "START=alpha" in messages[0]["content"]
        assert "MIDDLE=beta" in messages[0]["content"]
        assert "END=gamma" in messages[0]["content"]


def test_probe_tests_pressure_boundary_not_full_context(tmp_path, monkeypatch):
    from policy_impact.harness.context_manifest import estimate_tokens
    from policy_impact.harness.model_gateway import ModelResponse
    from policy_impact.harness.research_budget import ResearchLimits

    requests = []

    class Model:
        max_tokens = 2048

        def complete(self, messages, schema_name):
            requests.append(estimate_tokens(messages))
            return ModelResponse(response_id="fixture", payload={"type": "final", "output": {
                "answer": "alpha|beta|gamma"}}, metadata={})

    monkeypatch.setattr("policy_impact.harness.model_config.effective_model_config", lambda: {
        "protocol": "openai-responses"})
    monkeypatch.setattr("policy_impact.harness.responses_model_gateway.ResponsesModelAdapter.from_env", Model)
    assert live.probe_service(tmp_path, ResearchLimits())
    assert requests[0] < 2000
    assert 630000 < requests[1] <= 700000
    record = json.loads((tmp_path / "probe_configured_input.json").read_text(encoding="utf-8"))
    assert record["probe_input_limit"] == 700000
    assert record["capacity_certified"] is False
    assert record["scope"] == "near_pressure_trigger"


def test_trace_collection_is_not_business_scoring():
    counts = live.trace_counts({"steps": [{"step_type": "model_call", "status": "done",
        "output_payload": {"payload": {"type": "delegate", "role": "analyst", "role_version": "2.0.0"}}}]})
    assert counts["model_call_nodes"] == 1
    assert counts["actions"][0]["role_version"] == "2.0.0"
    assert counts["business_acceptance"] == "manual_review_required"


def test_requires_explicit_paid_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["acceptance", "--output", str(tmp_path)])
    with pytest.raises(SystemExit, match="2"):
        live.main()


def test_model_selection_flags_are_mutually_exclusive(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["acceptance", "--live", "--output", str(tmp_path),
        "--model", "fixture", "--model-profile", "deepseek"])
    with pytest.raises(SystemExit, match="2"):
        live.main()


def test_bare_model_cannot_mix_with_environment_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_PROFILE", "deepseek")
    monkeypatch.setattr(live, "validate_windows_generated_paths", lambda *args: None)
    monkeypatch.setattr("run_finance.configure_environment", lambda **kwargs: tmp_path / "corpus")
    monkeypatch.setattr("sys.argv", ["acceptance", "--live", "--output",
        str(tmp_path / "data/acceptance/new"), "--model", "fixture"])
    with pytest.raises(SystemExit, match="2"):
        live.main()
    assert not (tmp_path / "data/acceptance/new").exists()


def test_windows_generated_path_boundary_is_checked():
    from pathlib import Path

    root = Path("C:/a")
    tail = (Path("cases/Q04-r1/workspace/data/companies/finance_observer/runs") / ("0" * 32)
            / "research_artifacts" / ("understanding_" + "0" * 24 + ".json"))
    remaining = 260 - len(str(root / "x" / tail).encode("utf-16-le")) // 2 + 1
    live.validate_windows_generated_paths(root / ("x" * (remaining - 1)), ["Q04"], 3)
    with pytest.raises(ValueError, match="shorter --output"):
        live.validate_windows_generated_paths(root / ("x" * remaining), ["Q04"], 1)


def test_windows_path_limit_counts_utf16_code_units():
    from pathlib import Path

    live.validate_windows_generated_paths(Path("C:/a") / ("x" * 70), ["Q14"], 1)
    with pytest.raises(ValueError, match="260-character"):
        live.validate_windows_generated_paths(Path("C:/a") / ("\U00010000" * 70), ["Q14"], 1)


def test_oversized_windows_output_fails_before_loading_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr("sys.platform", "win32")
    output = tmp_path / "data/acceptance" / ("x" * 200)

    def forbidden(**kwargs):
        raise AssertionError("Path preflight must run before environment/model setup")

    monkeypatch.setattr("run_finance.configure_environment", forbidden)
    monkeypatch.setattr("sys.argv", ["acceptance", "--live", "--output", str(output)])
    with pytest.raises(SystemExit, match="2"):
        live.main()
    assert not output.exists()


def test_known_fatal_service_error_stops_repetition():
    assert live.infrastructure_blocker({"error": "HTTP 401: rejected"}) == "http 401"
    assert live.infrastructure_blocker({"error": "HTTP 502: temporary"}) == ""


def test_nonempty_fallback_is_not_transport_success():
    failed = {"transport_attempts": [{"stream_complete": False, "time_to_first_token_ms": None}]}
    record = {"answer": "A truthful fallback, not a completed analysis",
              "trace_counts": {"step_types": {"research_failure_fallback": 1}},
              "model_failure_metadata": [failed]}
    assert live.infrastructure_blocker(record) == "transport_before_first_content"
    record["trace_counts"]["step_types"].clear()
    assert live.infrastructure_blocker(record) == ""
