"""Opt-in public sources retain real reads, not search leads or private grants."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from policy_impact.dispatch.contracts import DispatchError
from policy_impact.dispatch.finance_agent import FinanceH2Domain
from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
from policy_impact.dispatch.finance_root import FinanceRootState
from policy_impact.dispatch.finance_tool_contracts import describe, validate
from policy_impact.runtime.actions import ToolCallAction
from tests.test_dispatch_finance_research import Capabilities, _context, _result


def _page(**changes):
    return {
        "tool": "web.open", "ok": True, "status": "ok", "source_kind": "public_web_page",
        "url": "https://example.com/data", "text": "1: The reported value remains conditional.",
        "title": "Public fixture", "author": "", "published_at": "",
        "retrieved_at": "2026-10-20T00:00:00+00:00", "content_hash": "response-hash",
        "line_start": 1, "line_end": 1, "full_line_count": 1, "truncated": False,
        "next_start_line": None, **changes,
    }


class Sources:
    def __init__(self, result=None):
        self.calls = []
        self.result = _page() if result is None else result

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


def _context_with_web(state):
    context = _context(state=state)
    return replace(context, brief={**context.brief, "allowed_tools": ["web.open", "web.search", "tool.describe"]})


@pytest.mark.parametrize("name,arguments", [
    ("web.search", {"query": "public data"}),
    ("web.open", {"url": "https://example.com/data"}),
])
def test_public_tool_schema_reuses_legacy_input_models(name, arguments):
    from policy_impact.domains.finance.tool_contracts import INPUTS

    assert describe(name)["parameters"] == INPUTS[name].model_json_schema()
    assert validate(name, arguments) == INPUTS[name].model_validate(arguments).model_dump(mode="json")
    with pytest.raises(ValueError):
        validate(name, {**arguments, "grant_all_sources": True})


def test_web_page_is_a_source_but_search_leads_are_not():
    assert FinanceH2ResearchDomain._source_text(_page()) == _page()["text"]
    leads = {"tool": "web.search", "ok": True, "status": "ok",
             "results": [{"snippet": "The reported value remains conditional."}]}
    assert FinanceH2ResearchDomain._source_text(leads) is None
    assert FinanceH2ResearchDomain._source_reads(leads, "reader") == []


def test_public_lineage_retains_reader_and_does_not_invent_publication_date():
    reads = FinanceH2ResearchDomain._source_reads(_page(), "reader.original")
    assert len(reads) == 1
    assert reads[0]["reader_task_id"] == "reader.original"
    assert reads[0]["source_kind"] == "public_web_page"
    assert reads[0]["published_at"] == "" and reads[0]["retrieved_at"] == _page()["retrieved_at"]
    assert "article" not in reads[0]


def test_public_read_is_durable_source_without_creating_article_state():
    sources = Sources()
    domain = FinanceH2Domain(None, public_sources=sources)
    caps = Capabilities()
    state = FinanceRootState(query="Check the public page.", phase="research")
    context = _context_with_web(state)
    action = ToolCallAction(type="tool_call", tool_name="web.open", arguments={"url": _page()["url"]})
    step = asyncio.run(domain._business_action(context, state, action, caps))
    assert sources.calls == [("web.open", {"url": _page()["url"], "start_line": 1})]
    assert not step.state.article_states
    assert step.state.evidence_ledger and step.state.execution["public_sources"]
    ref = step.state.execution["material_refs"][-1]
    output = _result(ref)
    output["evidence_links"][0]["quotes"] = [{"evidence_ref": ref, "text": "The reported value remains conditional."}]
    final = asyncio.run(FinanceH2ResearchDomain(None)._final(context, step.state, output, caps))
    assert final.status == "succeeded"
    lineage = caps.artifacts[final.submission.sections["finance_sources"]]
    assert lineage["bindings"][ref][0]["url"] == _page()["url"]
    assert sources.calls == [("web.open", {"url": _page()["url"], "start_line": 1})]


def test_missing_binding_returns_failed_observation_without_a_source():
    domain = FinanceH2Domain(None)
    caps = Capabilities()
    state = FinanceRootState(query="Check the public page.", phase="research")
    action = ToolCallAction(type="tool_call", tool_name="web.open", arguments={"url": _page()["url"]})
    step = asyncio.run(domain._business_action(_context_with_web(state), state, action, caps))
    assert not step.state.execution["material_refs"] and not step.state.evidence_ledger
    assert caps.events[-1][2] == "failed"
    observation = caps.artifacts[step.state.execution["observation_refs"][-1]]
    assert observation["payload"]["payload"]["ok"] is False


def test_parent_accepts_public_lineage_without_granting_the_private_page():
    caps = Capabilities(artifacts={"section.public": {
        "schema_version": "finance.h2.source_lineage.v1",
        "bindings": {"source.private": [{
            "source_kind": "public_web_page", "url": _page()["url"],
            "reader_task_id": "task.grandchild", "content_hash": "response-hash",
            "retrieved_at": _page()["retrieved_at"], "published_at": "",
        }]},
    }})
    context = replace(_context(), observations=[{"tasks": [{
        "task_id": "task.child", "status": "succeeded", "result": {
            "result_ref": "res_child", "full_content": json.dumps({"content": "Source analysis."}),
            "section_refs": {"finance_sources": "section.public"},
            "envelope": {"evidence_refs": [{"evidence_ref": "source.private"}]},
        },
    }]}])
    state, _ = asyncio.run(FinanceH2Domain(None)._consume_observations(context, caps))
    assert not state.article_states
    source = state.execution["public_sources"][0]
    assert source["reader_task_id"] == "task.grandchild"
    assert source["result_ref"] == "res_child"
    assert "source.private" not in state.execution["evidence_refs"]
    assert "source.private" not in caps.loads


@pytest.mark.parametrize("changes", [
    {"ok": False}, {"status": "unsupported_format"}, {"text": ""},
    {"url": "http://127.0.0.1/private"}, {"url": "file:///private"},
    {"line_start": True}, {"line_end": 0}, {"full_line_count": 0},
    {"retrieved_at": ""}, {"content_hash": ""}, {"truncated": "false"},
])
def test_unread_or_invalid_page_never_becomes_source(changes):
    value = _page(**changes)
    assert FinanceH2ResearchDomain._source_text(value) is None
    assert FinanceH2ResearchDomain._source_reads(value, "reader") == []


def test_absent_permission_rejects_before_network_call():
    sources = Sources()
    caps = Capabilities()
    state = FinanceRootState(query="Check only granted tools.", phase="research")
    context = replace(_context_with_web(state), brief={"allowed_tools": ["article.read"]})
    action = ToolCallAction(type="tool_call", tool_name="web.open", arguments={"url": _page()["url"]})
    with pytest.raises(DispatchError, match="forbidden"):
        asyncio.run(FinanceH2Domain(None, public_sources=sources)._business_action(context, state, action, caps))
    assert sources.calls == [] and caps.events == []


@pytest.mark.parametrize("result", [
    {"tool": "web.search", "ok": True, "status": "ok", "results": [{"snippet": "Lead only"}]},
    {"tool": "web.open", "ok": False, "status": "unsupported_format", "url": _page()["url"]},
    {"tool": "web.search", "ok": False, "status": "unavailable", "results": []},
])
def test_search_or_failure_cannot_pass_research_source_validation(result):
    sources = Sources(result)
    caps = Capabilities()
    state = FinanceRootState(query="Check the source.", phase="research")
    context = _context_with_web(state)
    name = result["tool"]
    action = ToolCallAction(type="tool_call", tool_name=name, arguments=(
        {"query": "public data"} if name == "web.search" else {"url": _page()["url"]}
    ))
    step = asyncio.run(FinanceH2Domain(None, public_sources=sources)._business_action(context, state, action, caps))
    assert not step.state.evidence_ledger and not step.state.execution.get("public_sources")
    assert not step.state.article_states
    if result["ok"]:
        ref = step.state.execution["material_refs"][-1]
        output = _result(ref)
        final = asyncio.run(FinanceH2ResearchDomain(None)._final(context, step.state, output, caps))
        assert final.status == "partial" and final.submission.evidence_refs == []
        assert caps.artifacts[final.submission.full_ref]["result"]["content"] == output["content"]
    else:
        assert not step.state.execution["material_refs"] and caps.events[-1][2] == "failed"


def test_public_quote_mismatch_preserves_analysis_without_evidence():
    caps = Capabilities(artifacts={"source.page": _page()})
    state = FinanceRootState(query="Check the quote.", phase="research", execution={
        "material_refs": ["source.page"], "evidence_refs": ["source.page"],
    })
    output = _result("source.page")
    output["evidence_links"][0]["quotes"] = [{"evidence_ref": "source.page", "text": "Not in the page"}]
    final = asyncio.run(FinanceH2ResearchDomain(None)._final(_context(state=state), state, output, caps))
    assert final.status == "partial" and final.submission.evidence_refs == []
    assert caps.artifacts[final.submission.full_ref]["result"]["content"] == output["content"]
    assert "research_quote_mismatch:source.page" in final.submission.warnings


def test_production_binding_isolates_legacy_ledgers_and_strips_legacy_ids(monkeypatch):
    from policy_impact.dispatch.finance_public_sources import FinancePublicSources
    from policy_impact.domains.finance.web_research import WebResearch

    ledgers = []

    def opened(self, **_arguments):
        ledgers.append(self.ledger)
        return {key: value for key, value in {**_page(), "evidence_id": "legacy-only"}.items()
                if key not in {"tool", "ok", "status", "source_kind"}}

    monkeypatch.setattr(WebResearch, "open", opened)
    binding = FinancePublicSources()
    first = binding.execute("web.open", {"url": _page()["url"]})
    second = binding.execute("web.open", {"url": _page()["url"], "start_line": 1})
    assert first == second == _page()
    assert ledgers[0] is not ledgers[1]


@pytest.mark.parametrize("url", ["file:///private", "http://127.0.0.1/", "https://192.168.1.1/"])
def test_production_binding_keeps_original_url_safety(monkeypatch, url):
    from policy_impact.dispatch.finance_public_sources import FinancePublicSources
    from policy_impact.domains.finance import web_research

    def no_fetch(*_args, **_kwargs):
        pytest.fail("An unsafe URL reached the HTTP layer")

    monkeypatch.setattr(web_research, "fetch_url", no_fetch)
    payload = FinancePublicSources().execute("web.open", {"url": url})
    assert payload["ok"] is False and payload["status"] == "unavailable"


def test_production_binding_does_not_copy_exception_secrets(monkeypatch):
    from policy_impact.dispatch.finance_public_sources import FinancePublicSources
    from policy_impact.domains.finance.web_research import WebResearch

    def failed(self, **_arguments):
        raise TimeoutError("Authorization: Bearer fixture-secret")

    monkeypatch.setattr(WebResearch, "open", failed)
    payload = FinancePublicSources().execute("web.open", {"url": _page()["url"]})
    assert payload["ok"] is False and "fixture-secret" not in json.dumps(payload)


def test_bound_preflight_unblocks_only_actual_public_capabilities():
    from scripts.harness_v2_live_acceptance import case_preflight, select_cases

    cases = select_cases(None, True)
    assert sum(case_preflight(case, public_sources=True)["status"] == "executable" for case in cases) == 24
    for case in ("Q03", "Q19", "Q20", "Q22"):
        assert case_preflight(case)["status"] == "blocked_missing_capability"
        assert case_preflight(case, public_sources=True)["status"] == "executable"
    assert case_preflight("Q21", public_sources=True)["missing_capabilities"] == ["market_history"]
    assert case_preflight("Q25", public_sources=True)["missing_capabilities"] == ["wiki_context"]
    assert case_preflight("Q30", public_sources=True)["missing_capabilities"] == ["external_adapter_fault_injection"]


def test_committed_page_survives_new_service_without_refetch(tmp_path):
    from policy_impact.dispatch import FinanceHarnessV2Service
    from policy_impact.dispatch.finance_agent import ROOT_TOOLS
    from tests.test_dispatch_finance_continuation import _cycle, _runtime, _tool

    def decide(state):
        if not state.get("public_sources"):
            return _tool("web.open", url=_page()["url"])
        assert state["public_sources"][0]["reader_task_id"] == state["task_identity"]["task_id"]
        return {"type": "final", "output": {
            "answer": "The reported value remains conditional.",
            "evidence_refs": [state["public_sources"][0]["material_ref"]],
        }}

    sources = Sources()
    runtime = _runtime(tmp_path, decide, public_sources=sources, tools=[*ROOT_TOOLS, "web.open"])
    asyncio.run(_cycle(runtime))
    assert len(sources.calls) == 1
    runtime.service = FinanceHarnessV2Service.for_finance_corpus(
        runtime.repo, runtime.transport, corpus=runtime.corpus, public_sources=sources,
        model_adapter=runtime.service.model_adapter, worker_id="restored-worker",
    )
    asyncio.run(_cycle(runtime))
    assert runtime.service.status(runtime.handle)["tasks"][0]["status"] == "succeeded"
    assert len(sources.calls) == 1 and len(runtime.requests) == 2 and not runtime.mock_errors
    assert runtime.prompts[-1]["article_states"] == []


@pytest.mark.parametrize("binding", ["wrapper", "result"])
def test_public_source_returns_through_two_delegations_with_original_reader(tmp_path, binding):
    from policy_impact.dispatch.finance_agent import ROOT_TOOLS
    from tests.test_dispatch_finance_continuation import (
        _artifact,
        _child_results,
        _finish,
        _runtime,
        _scheduler_observations,
        _spawn,
        _tasks,
        _tool,
    )

    private_refs = set()

    def decide(state):
        depth = state["task_identity"]["depth"]
        if depth == 2:
            if not state.get("public_sources"):
                return _tool("web.open", url=_page()["url"])
            ref = state["public_sources"][0]["material_ref"]
            private_refs.add(ref)
        else:
            assert not private_refs.intersection(state["known_evidence_refs"])
            if not state["scheduled_children"]:
                child_role = "analyst" if depth == 0 else "verifier"
                return _spawn(f"public-depth-{depth + 1}", child_role, [],
                              calls=24 if depth == 0 else 8,
                              wall_ms=300_000 if depth == 0 else 120_000)
            if not _child_results(state):
                return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
            returned = next(item for item in state["observations"] if item.get("result_ref"))
            ref = returned["material_ref"]
            if binding == "result" and depth == 1:
                if not _scheduler_observations(state, "result.get"):
                    return _tool("result.get", result_ref=returned["result_ref"], view="full")
                ref = returned["result_ref"]
        content = "The reported value remains conditional."
        if depth == 0:
            return {"type": "final", "output": {"answer": content, "evidence_refs": [ref]}}
        return {"type": "final", "output": {
            "schema_version": "finance.research_result.v1", "content": content,
            "evidence_links": [{"text": content, "evidence_refs": [ref], "quotes": [
                {"evidence_ref": ref, "text": content},
            ]}], "material_disposition": [], "gaps": [], "limitations": [],
            **({
                "schema_version": "finance.analyst_result.v2",
                "review_decision": "skip",
                "review_decision_reason": "This fixture tests public-source lineage, not independent claim review.",
            } if depth == 1 else {}),
        }}

    sources = Sources()
    runtime = _runtime(tmp_path, decide, public_sources=sources, tools=[*ROOT_TOOLS, "web.open"])
    root = asyncio.run(_finish(runtime))
    tasks = _tasks(runtime)
    assert root["status"] == "succeeded" and len(tasks) == 3
    assert len(sources.calls) == 1 and not runtime.corpus.reads and not runtime.mock_errors
    for task in tasks:
        with runtime.repo.db.connection() as connection:
            result = connection.execute("SELECT sections_json FROM h2_results WHERE result_ref=?",
                                        (task["result_ref"],)).fetchone()
        saved = _artifact(runtime, json.loads(result["sections_json"])["finance_state"])
        assert saved["article_states"] == []
        reads = saved["execution"]["public_sources"]
        assert reads and all(read["reader_task_id"] == tasks[-1]["task_id"] for read in reads)
        assert all(read["published_at"] == "" and read["retrieved_at"] == _page()["retrieved_at"] for read in reads)
        if task["depth"] < 2:
            assert not private_refs.intersection(saved["execution"]["evidence_refs"])


def test_pagination_preserves_observed_range_and_original_reader():
    page = _page(line_start=3, line_end=4, full_line_count=8, truncated=True, next_start_line=5)
    wrapper = {"schema_version": "finance.h2.material.v1", "source_task_id": "reader.original",
               "payload": {"schema_version": "finance.h2.tool_result.v1", "payload": page}}
    read = FinanceH2ResearchDomain._source_reads(wrapper, "reader.parent")[0]
    assert (read["line_start"], read["line_end"], read["full_line_count"], read["next_start_line"]) == (3, 4, 8, 5)
    assert read["truncated"] and read["reader_task_id"] == "reader.original"
