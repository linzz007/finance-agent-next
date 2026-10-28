"""Durable H2 market data reuses legacy calculations and explicit read grants."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from policy_impact.dispatch.contracts import CallerContext, DispatchError
from policy_impact.dispatch.finance_agent import ROOT_TOOLS, FinanceH2Domain
from policy_impact.dispatch.finance_market_sources import FinanceMarketSources, market_source_read
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.dispatch.finance_root import FinanceRootState
from policy_impact.dispatch.finance_tool_contracts import MARKET_TOOLS, describe, validate
from policy_impact.domains.finance.evidence import EvidenceLedger
from policy_impact.domains.finance.market_research import MarketResearch
from policy_impact.domains.finance.schemas import FinanceSnapshot
from policy_impact.runtime.actions import ToolCallAction
from tests.test_dispatch_finance_research import Capabilities, _context, _result


def snapshot(*, bars=None):
    closes = [("2024-05-31", 100), ("2024-06-03", 110), ("2024-06-04", 121)]
    return FinanceSnapshot.model_validate({
        "snapshot_id": "synthetic-market-only", "collected_at": "2024-06-05T01:00:00Z",
        "period": {"start": "2024-06-03", "end": "2024-06-04"},
        "series": [{"symbol": "sh000001", "name": "Synthetic index", "provider": "Fixture",
                    "source_url": "https://example.com/market", "unit": "index_points",
                    "timezone": "Asia/Shanghai", "price_basis": "price_index",
                    "retrieved_at": "2024-06-05T01:00:00Z",
                    "bars": bars if bars is not None else [
                        {"date": date, "high": close, "low": close, "close": close} for date, close in closes
                    ]}],
    }).model_dump(mode="json")


@pytest.fixture
def acquired(monkeypatch):
    from policy_impact.domains.finance import acquisition

    calls = []
    data = snapshot()

    def collect(**arguments):
        calls.append(arguments)
        return deepcopy(data)

    monkeypatch.setattr(acquisition, "collect_finance_snapshot", collect)
    return data, calls


def execute(binding, caps, name, **arguments):
    return asyncio.run(binding.execute(name, arguments, caps))


@pytest.mark.parametrize("name,arguments", [
    ("market.catalog", {}), ("market.snapshot", {"symbols": ["sh000001"]}),
    ("market.metrics", {"snapshot_id": "authorized.snapshot"}),
    ("market.series", {"snapshot_id": "authorized.snapshot", "symbol": "sh000001"}),
])
def test_market_schemas_reuse_legacy_models_and_stay_offline_by_default(name, arguments):
    from policy_impact.domains.finance.tool_contracts import INPUTS

    assert describe(name)["parameters"] == INPUTS[name].model_json_schema()
    assert validate(name, arguments) == INPUTS[name].model_validate(arguments).model_dump(mode="json")
    assert name not in ROOT_TOOLS
    with pytest.raises(ValueError):
        validate(name, {**arguments, "provider_url": "http://127.0.0.1/"})


def test_snapshot_restore_uses_frozen_data_and_legacy_metrics(acquired):
    data, calls = acquired
    caps = Capabilities()
    receipt = execute(FinanceMarketSources(), caps, "market.snapshot", symbols=["sh000001"])
    ref = receipt["snapshot_id"]
    assert ref in caps.artifacts and ref != data["snapshot_id"]
    assert receipt["source_snapshot_id"] == data["snapshot_id"]
    assert market_source_read(receipt, "reader") is None
    restored = Capabilities(artifacts=deepcopy(caps.artifacts))
    payload = execute(FinanceMarketSources(), restored, "market.metrics", snapshot_id=ref)
    legacy = MarketResearch(EvidenceLedger())
    legacy.snapshots[ref] = data
    expected = legacy.metrics(ref)
    for metric in expected["metrics"]:
        metric.pop("evidence_id")
    assert payload["metrics"] == expected["metrics"]
    assert payload["metrics"][0]["change_pct"] == 21
    assert payload["metrics"][0]["start_date"] == "2024-05-31"
    assert payload["instruments"][0]["timezone"] == "Asia/Shanghai"
    assert payload["instruments"][0]["session_verified"] is False
    assert payload["instruments"][0]["unit"] == "index_points"
    assert "evidence_id" not in json.dumps(payload)
    assert len(calls) == 1 and restored.loads == [ref]
    assert FinanceH2ResearchDomain._source_text(payload) == payload["text"]
    assert FinanceH2ResearchDomain._source_reads(payload, "reader")[0]["source_kind"] == "public_market_data"


def test_series_pagination_and_partial_metrics_preserve_original_basis(acquired):
    data, calls = acquired
    data["series"][0]["retrieved_at"] = "2024-06-05T00:30:00Z"
    caps = Capabilities()
    binding = FinanceMarketSources()
    receipt = execute(binding, caps, "market.snapshot", symbols=["sh000001"])
    ref = receipt["snapshot_id"]
    first = execute(binding, caps, "market.series", snapshot_id=ref, symbol="sh000001", limit=2)
    second = execute(FinanceMarketSources(), caps, "market.series", snapshot_id=ref, symbol="sh000001", offset=2, limit=2)
    assert first["next_offset"] == 2 and second["next_offset"] is None
    assert first["bars"] + second["bars"] == data["series"][0]["bars"]
    assert len(calls) == 1 and market_source_read(first, "reader")
    read = market_source_read(first, "reader")
    assert first["retrieved_at"] == read["retrieved_at"] == "2024-06-05T00:30:00Z"
    assert first["collected_at"] == read["collected_at"] == data["collected_at"]
    assert json.loads(first["text"])["retrieved_at"] == first["retrieved_at"]
    metrics = execute(binding, caps, "market.metrics", snapshot_id=ref)
    assert metrics["retrieved_at"] == data["collected_at"]
    assert metrics["instruments"][0]["retrieved_at"] == first["retrieved_at"]
    assert market_source_read(metrics, "reader")


@pytest.mark.parametrize("changes", [{"ok": False}, {"status": "empty"}, {"text": "altered"},
                                     {"instruments": []}, {"instruments": [{"source_url": 123}]},
                                     {"retrieved_at": "changed"}, {"collected_at": "changed"},
                                     {"metrics": []}])
def test_invalid_market_observation_is_not_source(acquired, changes):
    caps = Capabilities()
    binding = FinanceMarketSources()
    receipt = execute(binding, caps, "market.snapshot", symbols=["sh000001"])
    payload = execute(binding, caps, "market.metrics", snapshot_id=receipt["snapshot_id"])
    assert market_source_read({**payload, **changes}, "reader") is None


@pytest.mark.parametrize("corrupt", ["hash", "schema", "data"])
def test_corrupt_snapshot_cannot_refetch_or_become_evidence(acquired, corrupt):
    _, calls = acquired
    caps = Capabilities()
    receipt = execute(FinanceMarketSources(), caps, "market.snapshot", symbols=["sh000001"])
    record = caps.artifacts[receipt["snapshot_id"]]
    if corrupt == "hash":
        record["content_hash"] = "broken"
    elif corrupt == "schema":
        record["schema_version"] = "untrusted"
    else:
        record["snapshot"]["series"][0]["bars"][0]["close"] = 9999
    with pytest.raises(ValueError):
        execute(FinanceMarketSources(), caps, "market.metrics", snapshot_id=receipt["snapshot_id"])
    assert len(calls) == 1


def test_unknown_snapshot_never_calls_acquisition(acquired):
    _, calls = acquired
    with pytest.raises(DispatchError):
        execute(FinanceMarketSources(), Capabilities(), "market.metrics", snapshot_id="not.granted")
    assert not calls


def test_model_driven_market_observation_becomes_external_source_not_article(acquired):
    caps = Capabilities()
    state = FinanceRootState(query="Observe market data.", phase="research")
    context = replace(_context(state=state), brief={"allowed_tools": list(MARKET_TOOLS)})
    domain = FinanceH2Domain(None, market_sources=FinanceMarketSources())
    action = ToolCallAction(type="tool_call", tool_name="market.snapshot", arguments={"symbols": ["sh000001"]})
    step = asyncio.run(domain._business_action(context, state, action, caps))
    receipts = [item["payload"] for item in caps.artifacts.values()
                if item.get("schema_version") == "finance.h2.tool_result.v1"]
    receipt = next(item for item in receipts if item["tool"] == "market.snapshot")
    assert not step.state.evidence_ledger
    action = ToolCallAction(type="tool_call", tool_name="market.metrics", arguments={"snapshot_id": receipt["snapshot_id"]})
    step = asyncio.run(FinanceH2Domain(None, market_sources=FinanceMarketSources())._business_action(
        replace(context, state=step.state), step.state, action, caps,
    ))
    assert not step.state.article_states and step.state.evidence_ledger
    assert step.state.execution["public_sources"][0]["source_kind"] == "public_market_data"
    ref = step.state.execution["material_refs"][-1]
    final = asyncio.run(FinanceH2ResearchDomain(None)._final(context, step.state, _result(ref), caps))
    assert final.status == "succeeded"
    lineage = caps.artifacts[final.submission.sections["finance_sources"]]
    assert lineage["bindings"][ref][0]["snapshot_id"] == receipt["snapshot_id"]


@pytest.mark.parametrize("bound,permitted", [(False, True), (True, False)])
def test_market_binding_and_permission_are_both_required(acquired, bound, permitted):
    _, calls = acquired
    caps = Capabilities()
    state = FinanceRootState(query="Read only authorized tools.", phase="research")
    context = replace(_context(state=state), brief={"allowed_tools": ["market.snapshot"] if permitted else []})
    domain = FinanceH2Domain(None, market_sources=FinanceMarketSources() if bound else None)
    action = ToolCallAction(type="tool_call", tool_name="market.snapshot", arguments={"symbols": ["sh000001"]})
    if not permitted:
        with pytest.raises(DispatchError):
            asyncio.run(domain._business_action(context, state, action, caps))
    else:
        step = asyncio.run(domain._business_action(context, state, action, caps))
        assert caps.events[-1][2] == "failed" and not step.state.evidence_ledger
    assert not calls


def test_partial_window_is_not_promoted_to_full_period_return(acquired):
    data, calls = acquired
    data["series"][0]["bars"] = data["series"][0]["bars"][1:]
    caps = Capabilities()
    receipt = execute(FinanceMarketSources(), caps, "market.snapshot", symbols=["sh000001"])
    result = execute(FinanceMarketSources(), caps, "market.metrics", snapshot_id=receipt["snapshot_id"])
    metric = result["metrics"][0]
    assert metric["status"] == "partial" and metric["change_pct"] == 10
    assert metric["baseline_available"] is False
    assert "NOT a full requested-period return" in metric["basis"]
    assert len(calls) == 1


def test_empty_acquisition_and_empty_series_never_become_price_evidence(acquired):
    data, _ = acquired
    caps = Capabilities()
    binding = FinanceMarketSources()
    receipt = execute(binding, caps, "market.snapshot", symbols=["sh000001"])
    empty = execute(binding, caps, "market.series", snapshot_id=receipt["snapshot_id"], symbol="sh000001", offset=100)
    assert empty["status"] == "empty" and market_source_read(empty, "reader") is None
    data["series"] = []
    receipt = execute(binding, caps, "market.snapshot", symbols=["sh000001"])
    assert receipt["missing_symbols"] == ["sh000001"]
    empty = execute(binding, caps, "market.metrics", snapshot_id=receipt["snapshot_id"])
    assert empty["status"] == "empty" and market_source_read(empty, "reader") is None


def test_acquisition_error_is_sanitized_and_storage_error_is_not_swallowed(monkeypatch):
    from policy_impact.domains.finance import acquisition

    def fail(**_arguments):
        raise TimeoutError("Authorization: Bearer private-fixture-only")

    monkeypatch.setattr(acquisition, "collect_finance_snapshot", fail)
    caps = Capabilities()
    result = execute(FinanceMarketSources(), caps, "market.snapshot", symbols=["sh000001"])
    assert result["ok"] is False and "private-fixture-only" not in json.dumps(result)
    assert not caps.artifacts
    monkeypatch.setattr(acquisition, "collect_finance_snapshot", lambda **_arguments: snapshot())

    async def unavailable(_content):
        raise OSError("synthetic storage failure")

    caps.store_context = unavailable
    with pytest.raises(OSError):
        execute(FinanceMarketSources(), caps, "market.snapshot", symbols=["sh000001"])


def test_market_preflight_removes_only_the_bound_capability():
    from scripts.harness_v2_live_acceptance import case_preflight

    assert case_preflight("Q03", market_sources=True)["status"] == "executable"
    assert case_preflight("Q21", market_sources=True)["missing_capabilities"] == ["independent_public_sources"]
    assert case_preflight("Q21", market_sources=True, public_sources=True)["status"] == "executable"
    assert case_preflight("Q23", market_sources=True)["missing_capabilities"] == ["wiki_context"]
    assert case_preflight("Q24", market_sources=True)["missing_capabilities"] == ["holdings_context"]
    assert case_preflight("Q29", market_sources=True)["status"] == "requires_isolated_environment"
    assert case_preflight("Q30", market_sources=True)["missing_capabilities"] == ["external_adapter_fault_injection"]


def test_committed_market_snapshot_survives_new_service_without_refetch(tmp_path, acquired):
    from policy_impact.dispatch import FinanceHarnessV2Service
    from tests.test_dispatch_finance_continuation import (
        _business_observations,
        _cycle,
        _finish,
        _runtime,
        _tool,
    )

    _, calls = acquired

    def decide(state):
        receipts = _business_observations(state, "market.snapshot")
        if not receipts:
            return _tool("market.snapshot", symbols=["sh000001"], start="2024-06-03", end="2024-06-04")
        if not state.get("public_sources"):
            return _tool("market.metrics", snapshot_id=receipts[0]["snapshot_id"])
        return {"type": "final", "output": {
            "answer": "The synthetic index rose 21 percent from the prior close, not a live quote.",
            "evidence_refs": [state["public_sources"][0]["material_ref"]],
        }}

    runtime = _runtime(tmp_path, decide, market_sources=FinanceMarketSources(), tools=[*ROOT_TOOLS, *MARKET_TOOLS])
    asyncio.run(_cycle(runtime))
    assert len(calls) == 1
    runtime.service = FinanceHarnessV2Service.for_finance_corpus(
        runtime.repo, runtime.transport, corpus=runtime.corpus, market_sources=FinanceMarketSources(),
        model_adapter=runtime.service.model_adapter, worker_id="market-restored-worker",
    )
    root = asyncio.run(_finish(runtime))
    assert root["status"] == "succeeded" and len(calls) == 1
    assert len(runtime.requests) == 3 and not runtime.mock_errors and not runtime.corpus.reads
    assert runtime.prompts[-1]["article_states"] == []


@pytest.mark.parametrize("binding", ["wrapper", "result"])
def test_market_source_returns_through_two_delegations_without_snapshot_grant(tmp_path, acquired, binding):
    from tests.test_dispatch_finance_continuation import (
        _artifact,
        _business_observations,
        _child_results,
        _finish,
        _runtime,
        _scheduler_observations,
        _spawn,
        _tasks,
        _tool,
    )

    _, calls = acquired
    private_refs = set()
    snapshot_refs = set()
    content = "The synthetic index rose 21 percent using a prior-session close, not a realtime quote."

    def decide(state):
        depth = state["task_identity"]["depth"]
        if depth == 2:
            receipts = _business_observations(state, "market.snapshot")
            if not receipts:
                return _tool("market.snapshot", symbols=["sh000001"], start="2024-06-03", end="2024-06-04")
            snapshot_refs.add(receipts[0]["snapshot_id"])
            if not state.get("public_sources"):
                return _tool("market.metrics", snapshot_id=receipts[0]["snapshot_id"])
            ref = state["public_sources"][0]["material_ref"]
            private_refs.add(ref)
        else:
            assert not private_refs.intersection(state["known_evidence_refs"])
            if not state["scheduled_children"]:
                child_role = "analyst" if depth == 0 else "verifier"
                return _spawn(f"market-depth-{depth + 1}", child_role, [], calls=24 if depth == 0 else 8,
                              wall_ms=300_000 if depth == 0 else 120_000)
            if not _child_results(state):
                return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
            returned = next(item for item in state["observations"] if item.get("result_ref"))
            ref = returned["material_ref"]
            if binding == "result" and depth == 1:
                if not _scheduler_observations(state, "result.get"):
                    return _tool("result.get", result_ref=returned["result_ref"], view="full")
                ref = returned["result_ref"]
        if depth == 0:
            return {"type": "final", "output": {"answer": content, "evidence_refs": [ref]}}
        return {"type": "final", "output": {
            "schema_version": "finance.research_result.v1", "content": content,
            "evidence_links": [{"text": content, "evidence_refs": [ref]}],
            "material_disposition": [], "gaps": [], "limitations": [],
            **({
                "schema_version": "finance.analyst_result.v2",
                "review_decision": "skip",
                "review_decision_reason": "This fixture tests source handoff, not independent claim review.",
            } if depth == 1 else {}),
        }}

    runtime = _runtime(tmp_path, decide, market_sources=FinanceMarketSources(), tools=[*ROOT_TOOLS, *MARKET_TOOLS])
    root = asyncio.run(_finish(runtime))
    tasks = _tasks(runtime)
    assert root["status"] == "succeeded" and len(tasks) == 3 and len(calls) == 1
    assert not runtime.mock_errors and not runtime.corpus.reads
    for task in tasks:
        with runtime.repo.db.connection() as connection:
            result = connection.execute("SELECT sections_json FROM h2_results WHERE result_ref=?", (task["result_ref"],)).fetchone()
        saved = _artifact(runtime, json.loads(result["sections_json"])["finance_state"])
        assert saved["article_states"] == []
        reads = saved["execution"]["public_sources"]
        assert reads and all(read["reader_task_id"] == tasks[-1]["task_id"] for read in reads)
        assert all(read["snapshot_id"] in snapshot_refs for read in reads)
        if task["depth"] == 1:
            with runtime.repo.db.connection() as connection:
                attempt = connection.execute(
                    "SELECT * FROM h2_attempts WHERE task_id=? ORDER BY rowid DESC LIMIT 1", (task["task_id"],),
                ).fetchone()
            caller = CallerContext(runtime.handle.run_id, task["thread_id"], task["task_id"],
                                   attempt["attempt_id"], attempt["lease_token"])
            for ref in snapshot_refs:
                with pytest.raises(DispatchError) as denied:
                    runtime.repo.read_input(caller, ref)
                assert denied.value.code == "not_found"


def test_parent_snapshot_requires_explicit_child_grant_and_stays_run_scoped(tmp_path, acquired):
    from tests.test_dispatch_finance_continuation import (
        _business_observations,
        _child_results,
        _finish,
        _runtime,
        _spawn,
        _tasks,
        _tool,
    )

    _, calls = acquired
    refs = set()
    content = "Synthetic data, not a realtime quote."

    def decide(state):
        if state["task_identity"]["depth"] == 1:
            assert refs.issubset(state["known_material_refs"])
            if not state.get("public_sources"):
                return _tool("market.metrics", snapshot_id=next(iter(refs)))
            return {"type": "final", "output": {
                "schema_version": "finance.analyst_result.v2", "content": content,
                "evidence_links": [{"text": content, "evidence_refs": [state["public_sources"][0]["material_ref"]]}],
                "material_disposition": [], "gaps": [], "limitations": [],
                "review_decision": "skip",
                "review_decision_reason": "This fixture tests explicit snapshot grants, not independent claim review.",
            }}
        receipts = _business_observations(state, "market.snapshot")
        if not receipts:
            return _tool("market.snapshot", symbols=["sh000001"])
        refs.add(receipts[0]["snapshot_id"])
        if not state["scheduled_children"]:
            return _spawn("market-granted", "analyst", sorted(refs))
        if not _child_results(state):
            return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
        returned = next(item for item in state["observations"] if item.get("result_ref"))
        return {"type": "final", "output": {"answer": content, "evidence_refs": [returned["material_ref"]]}}

    runtime = _runtime(tmp_path, decide, market_sources=FinanceMarketSources(), tools=[*ROOT_TOOLS, *MARKET_TOOLS])
    root = asyncio.run(_finish(runtime))
    assert root["status"] == "succeeded" and len(_tasks(runtime)) == 2
    assert len(calls) == 1 and not runtime.mock_errors
    handle = runtime.service.create_run(
        run_id="other-market-run", session_ref="fixture-session", query="Separate market scope.",
        selected_profile="fixture", allowed_profiles=["fixture"], tools=[*ROOT_TOOLS, *MARKET_TOOLS],
    )
    caller = runtime.service.caller(handle)

    async def load(ref):
        return runtime.repo.read_input(caller, ref)

    with pytest.raises(DispatchError) as denied:
        execute(FinanceMarketSources(), SimpleNamespace(load_context=load), "market.metrics", snapshot_id=next(iter(refs)))
    assert denied.value.code == "not_found" and len(calls) == 1


def test_market_quote_mismatch_preserves_complete_analysis(acquired):
    caps = Capabilities()
    binding = FinanceMarketSources()
    receipt = execute(binding, caps, "market.snapshot", symbols=["sh000001"])
    caps.artifacts["source.market"] = execute(binding, caps, "market.metrics", snapshot_id=receipt["snapshot_id"])
    state = FinanceRootState(query="Explain the market basis.", phase="research", execution={
        "material_refs": ["source.market"], "evidence_refs": ["source.market"],
    })
    output = _result("source.market")
    output["evidence_links"][0]["quotes"] = [{"evidence_ref": "source.market", "text": "Unsupported market quote"}]
    final = asyncio.run(FinanceH2ResearchDomain(None)._final(_context(state=state), state, output, caps))
    assert final.status == "partial" and final.submission.evidence_refs == []
    assert caps.artifacts[final.submission.full_ref]["result"]["content"] == output["content"]
    assert "research_quote_mismatch:source.market" in final.submission.warnings
