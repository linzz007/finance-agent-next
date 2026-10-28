"""Private background snapshots are not public facts or implicit read grants."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from tests.test_dispatch_finance_research import Capabilities


@pytest.fixture
def personal_files(tmp_path, monkeypatch):
    from policy_impact.company_wiki import loader

    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    wiki = tmp_path / "data/companies/finance_observer/wiki"
    wiki.mkdir(parents=True)
    wiki_text = (
        '---\ntitle: "Research interests"\nsource: user_confirmed\nlast_updated: 2024-06-01\n---\n'
        '## FACT: preference.focus\n- value: rates\n- importance: 5\n- confidence: 1.0\n'
        'Follow monetary policy and rates. This is a research interest, not a verified market fact.\n'
    )
    (wiki / "preferences.md").write_text(wiki_text, encoding="utf-8")
    articles = tmp_path / "profiles/articles"
    articles.mkdir(parents=True)
    policy = "Research only, not trading advice.\n" + "x" * 8600 + "\nPolicy tail."
    (articles / "research_policy.md").write_text(policy, encoding="utf-8", newline="\n")
    holdings = {"as_of": "2024-06-01", "currency": "CNY", "approximate": True,
                "positions": [{"name": "Synthetic fund", "amount": 1000}]}
    (articles / "current_holdings.md").write_text(
        "# Approximate holdings\n```json\n" + json.dumps(holdings) + "\n```\n", encoding="utf-8",
    )
    return articles, wiki, policy, holdings


def binding(files):
    from policy_impact.dispatch.finance_personal_sources import FinancePersonalSources

    return FinancePersonalSources("finance_observer", files[0])


def execute(adapter, caps, name, arguments=None, *, refs=(), root=True):
    return asyncio.run(adapter.execute(name, arguments or {}, caps, source_refs=refs, allow_capture=root))


def catalog(adapter, caps):
    return execute(adapter, caps, "personal.catalog")


@pytest.mark.parametrize("name,arguments", [
    ("context.open", {"context_id": "source.fixture"}),
    ("company_wiki_search", {"query": "rates"}),
])
def test_legacy_schemas_are_reused_without_default_binding(name, arguments):
    from policy_impact.dispatch.finance_agent import ROOT_TOOLS
    from policy_impact.dispatch.finance_tool_contracts import describe, validate
    from policy_impact.domains.finance.tool_contracts import INPUTS

    assert name not in ROOT_TOOLS
    assert describe(name)["parameters"] == INPUTS[name].model_json_schema()
    assert validate(name, arguments) == INPUTS[name].model_validate(arguments).model_dump(mode="json")


def test_catalog_is_metadata_only_and_frozen_pages_recover(personal_files):
    from policy_impact.dispatch.finance_personal_sources import personal_source_read

    caps = Capabilities()
    receipt = catalog(binding(personal_files), caps)
    assert receipt["ok"] and len(receipt["sources"]) == 3
    assert personal_source_read(receipt, "reader") is None
    assert "Synthetic fund" not in json.dumps(receipt)
    sources = {item["personal_kind"]: item for item in receipt["sources"]}
    ref = sources["research_policy"]["context_id"]
    (personal_files[0] / "research_policy.md").write_text("Changed after capture.", encoding="utf-8")
    restored = Capabilities(artifacts=deepcopy(caps.artifacts))
    first = execute(binding(personal_files), restored, "context.open", {"context_id": ref, "limit": 5000})
    second = execute(binding(personal_files), restored, "context.open", {"context_id": ref, "offset": first["next_offset"]})
    assert first["text"] + second["text"] == personal_files[2]
    assert first["has_more"] is True and second["has_more"] is False
    assert first["source_hash"] == hashlib.sha256(personal_files[2].encode()).hexdigest()
    assert personal_source_read(first, "reader")["full_source_read"] is False
    assert personal_source_read(second, "reader")["reader_task_id"] == "reader"


def test_holdings_validation_and_metadata_are_preserved(personal_files):
    caps = Capabilities()
    receipt = catalog(binding(personal_files), caps)
    item = next(item for item in receipt["sources"] if item["personal_kind"] == "holdings")
    opened = execute(binding(personal_files), caps, "context.open", {"context_id": item["context_id"]})
    assert opened["as_of"] == personal_files[3]["as_of"]
    assert opened["approximate"] is True
    assert opened["holdings"]["positions"] == personal_files[3]["positions"]
    assert opened["holdings"]["approximate_total"] == 1000
    assert "source_path" not in opened["holdings"]


def test_wiki_search_uses_existing_ranking_and_is_not_full_page_read(personal_files):
    from policy_impact.company_wiki.loader import load_company_knowledge
    from policy_impact.dispatch.finance_personal_sources import personal_source_read
    from policy_impact.mcp_server.tools_company import rank_wiki_facts

    (personal_files[1].parent / "company.yaml").write_text('{"company_id":"finance_observer"}', encoding="utf-8")
    caps = Capabilities()
    receipt = catalog(binding(personal_files), caps)
    refs = [item["context_id"] for item in receipt["sources"]]
    expected = rank_wiki_facts(load_company_knowledge("finance_observer")["facts"], "rates", top_k=5)
    result = execute(binding(personal_files), caps, "company_wiki_search", {"query": "rates"}, refs=refs)
    assert [{key: value for key, value in item.items() if key not in {"context_id", "source_hash"}}
            for item in result["results"]] == expected
    read = personal_source_read(result, "reader")
    assert read["source_kind"] == "personal_context" and read["full_source_read"] is False
    assert read["personal_kind"] == "wiki_search"
    empty = execute(binding(personal_files), caps, "company_wiki_search", {"query": "unmatchedZZZ"}, refs=refs)
    assert empty["results"] == [] and personal_source_read(empty, "reader") is None


def test_child_catalog_never_captures_ambient_sources(personal_files):
    caps = Capabilities()
    receipt = execute(binding(personal_files), caps, "personal.catalog", root=False)
    assert receipt["sources"] == [] and caps.artifacts == {}
    full = catalog(binding(personal_files), caps)
    ref = next(item["context_id"] for item in full["sources"] if item["personal_kind"] == "wiki")
    child = execute(binding(personal_files), caps, "personal.catalog", root=False, refs=[ref])
    assert [item["context_id"] for item in child["sources"]] == [ref]
    assert "holdings" not in json.dumps(child)


def test_malformed_holdings_are_unavailable_not_empty_positions(personal_files):
    (personal_files[0] / "current_holdings.md").write_text('```json\n{"positions":[]}\n```', encoding="utf-8")
    receipt = catalog(binding(personal_files), Capabilities())
    assert not any(item["personal_kind"] == "holdings" for item in receipt["sources"])
    assert any(item["source_path"] == "current_holdings.md" for item in receipt["unavailable"])


@pytest.mark.parametrize("change", ["hash", "text", "metadata"])
def test_corrupt_snapshot_is_rejected_without_rereading_files(personal_files, change):
    caps = Capabilities()
    receipt = catalog(binding(personal_files), caps)
    ref = receipt["sources"][0]["context_id"]
    record = caps.artifacts[ref]
    if change == "hash":
        record["snapshot_hash"] = "changed"
    elif change == "text":
        record["text"] = "changed"
    else:
        record["captured_at"] = "changed"
    with pytest.raises(ValueError, match="hash"):
        execute(binding(personal_files), caps, "context.open", {"context_id": ref})


@pytest.mark.parametrize("change", [{"text": "invented"}, {"as_of": "changed"}, {"ok": False},
                                    {"context_id": "different"}, {"content_hash": "broken"}])
def test_changed_read_cannot_be_used_as_personal_evidence(personal_files, change):
    from policy_impact.dispatch.finance_personal_sources import personal_source_read

    caps = Capabilities()
    ref = catalog(binding(personal_files), caps)["sources"][0]["context_id"]
    opened = execute(binding(personal_files), caps, "context.open", {"context_id": ref})
    assert personal_source_read(opened, "reader") is not None
    assert personal_source_read({**opened, **change}, "reader") is None


def test_duplicate_wiki_fact_ids_are_unavailable(personal_files):
    wiki = personal_files[1]
    (wiki / "duplicate.md").write_bytes((wiki / "preferences.md").read_bytes())
    receipt = catalog(binding(personal_files), Capabilities())
    assert not any(item["personal_kind"] == "wiki" for item in receipt["sources"])
    assert any(item["source_path"] == "wiki/" for item in receipt["unavailable"])


def test_missing_sources_and_exact_crlf_empty_source_pages(personal_files):
    from policy_impact.dispatch.finance_personal_sources import personal_source_read

    articles, wiki, *_ = personal_files
    (articles / "current_holdings.md").unlink()
    (articles / "research_policy.md").write_bytes(b"first\r\nsecond\r\n")
    caps = Capabilities()
    receipt = catalog(binding(personal_files), caps)
    source = next(item for item in receipt["sources"] if item["personal_kind"] == "research_policy")
    view = execute(binding(personal_files), caps, "context.open", {"context_id": source["context_id"]})
    assert view["text"] == "first\r\nsecond\r\n"
    assert personal_source_read(view, "reader")["full_source_read"] is True
    with pytest.raises(ValueError):
        execute(binding(personal_files), caps, "context.open", {"context_id": source["context_id"], "offset": 100})
    (articles / "research_policy.md").write_bytes(b"")
    receipt = catalog(binding(personal_files), caps)
    source = next(item for item in receipt["sources"] if item["personal_kind"] == "research_policy")
    view = execute(binding(personal_files), caps, "context.open", {"context_id": source["context_id"]})
    assert view["status"] == "empty" and personal_source_read(view, "reader") is None
    for path in wiki.glob("*.md"):
        path.unlink()
    receipt = catalog(binding(personal_files), caps)
    assert any(item["source_path"] == "wiki/" for item in receipt["unavailable"])


def test_linked_source_is_unavailable(personal_files, monkeypatch):
    from pathlib import Path

    original = Path.resolve
    target = personal_files[0] / "research_policy.md"

    def resolved(path, *args, **kwargs):
        if path == target:
            return target.parent / "outside-profile.md"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolved)
    receipt = catalog(binding(personal_files), Capabilities())
    assert not any(item["personal_kind"] == "research_policy" for item in receipt["sources"])


def test_capture_storage_failure_is_not_an_empty_profile(personal_files):
    class BrokenStorage(Capabilities):
        async def store_context(self, _payload):
            raise RuntimeError("synthetic disk failure")

    with pytest.raises(RuntimeError, match="disk failure"):
        catalog(binding(personal_files), BrokenStorage())


@pytest.mark.parametrize("bound,permitted", [(False, True), (True, False)])
def test_binding_and_task_permission_are_both_required(personal_files, bound, permitted):
    from policy_impact.dispatch.contracts import DispatchError
    from policy_impact.dispatch.finance_agent import FinanceH2Domain
    from policy_impact.dispatch.finance_root import FinanceRootState
    from policy_impact.runtime.actions import ToolCallAction
    from tests.test_dispatch_finance_research import _context

    state = FinanceRootState(query="Use authorized background only.", phase="research")
    context = replace(_context(state=state), brief={"allowed_tools": ["personal.catalog"] if permitted else []})
    caps = Capabilities()
    domain = FinanceH2Domain(None, personal_sources=binding(personal_files) if bound else None)
    action = ToolCallAction(type="tool_call", tool_name="personal.catalog", arguments={})
    if not permitted:
        with pytest.raises(DispatchError):
            asyncio.run(domain._business_action(context, state, action, caps))
    else:
        step = asyncio.run(domain._business_action(context, state, action, caps))
        assert caps.events[-1][2] == "failed" and not step.state.evidence_ledger
    assert not any(item.get("schema_version") == "finance.h2.personal_source.v1" for item in caps.artifacts.values())


def test_personal_reads_and_bad_quotes_preserve_separate_provenance(personal_files):
    from policy_impact.dispatch.finance_agent import FinanceH2Domain
    from policy_impact.dispatch.finance_research import FinanceH2ResearchDomain
    from policy_impact.dispatch.finance_root import FinanceRootState
    from policy_impact.dispatch.finance_tool_contracts import PERSONAL_TOOLS
    from policy_impact.runtime.actions import ToolCallAction
    from tests.test_dispatch_finance_research import _context, _result

    caps = Capabilities()
    state = FinanceRootState(query="Explain the research background.", phase="research")
    context = replace(_context(state=state), brief={"allowed_tools": sorted(PERSONAL_TOOLS)})
    domain = FinanceH2Domain(None, personal_sources=binding(personal_files))
    action = ToolCallAction(type="tool_call", tool_name="personal.catalog", arguments={})
    step = asyncio.run(domain._business_action(context, state, action, caps))
    assert not step.state.evidence_ledger and not step.state.article_states
    refs = step.state.execution["personal_context_refs"]
    action = ToolCallAction(type="tool_call", tool_name="context.open", arguments={"context_id": refs[0]})
    step = asyncio.run(domain._business_action(replace(context, state=step.state), step.state, action, caps))
    assert not step.state.article_states and not step.state.execution.get("public_sources")
    read = step.state.execution["personal_sources"][0]
    assert read["source_kind"] == "personal_context"
    ref = read["material_ref"]
    output = _result(ref)
    output["evidence_links"][0]["quotes"] = [{"evidence_ref": ref, "text": "Unobserved personal claim"}]
    final = asyncio.run(FinanceH2ResearchDomain(None)._final(context, step.state, output, caps))
    assert final.status == "partial" and final.submission.evidence_refs == []
    assert caps.artifacts[final.submission.full_ref]["result"]["content"] == output["content"]


def test_frozen_holdings_survive_fresh_service_and_catalog_replay(tmp_path, personal_files, monkeypatch):
    from policy_impact.dispatch import FinanceHarnessV2Service
    from policy_impact.dispatch.finance_agent import ROOT_TOOLS
    from policy_impact.dispatch.finance_tool_contracts import PERSONAL_TOOLS
    from tests.test_dispatch_finance_continuation import (
        _business_observations,
        _cycle,
        _finish,
        _runtime,
        _tool,
    )

    def decide(state):
        catalogs = _business_observations(state, "personal.catalog")
        if not catalogs:
            return _tool("personal.catalog")
        if not state.get("personal_sources"):
            source = next(item for item in catalogs[0]["sources"] if item["personal_kind"] == "holdings")
            return _tool("context.open", context_id=source["context_id"])
        return {"type": "final", "output": {"answer": "The supplied holdings are approximate dated background.",
                "evidence_refs": [state["personal_sources"][0]["material_ref"]]}}

    runtime = _runtime(tmp_path, decide, personal_sources=binding(personal_files), tools=[*ROOT_TOOLS, *PERSONAL_TOOLS])
    asyncio.run(_cycle(runtime))
    (personal_files[0] / "current_holdings.md").write_text("Invalid changed file.", encoding="utf-8")
    adapter = binding(personal_files)

    def fail_capture():
        raise AssertionError("a committed catalog must not recapture files")

    monkeypatch.setattr(adapter, "_capture", fail_capture)
    runtime.service = FinanceHarnessV2Service.for_finance_corpus(
        runtime.repo, runtime.transport, corpus=runtime.corpus, personal_sources=adapter,
        model_adapter=runtime.service.model_adapter, worker_id="personal-restored-worker",
    )
    root = asyncio.run(_finish(runtime))
    assert root["status"] == "succeeded" and not runtime.mock_errors and len(runtime.requests) == 3
    view = _business_observations(runtime.prompts[-1], "context.open")[0]
    assert view["holdings"]["positions"] == personal_files[3]["positions"]
    assert not runtime.prompts[-1]["article_states"] and not runtime.prompts[-1].get("public_sources")


@pytest.mark.parametrize("result_view", ["wrapper", "result"])
def test_explicit_private_grants_and_two_level_lineage(tmp_path, personal_files, result_view):
    from policy_impact.dispatch.contracts import CallerContext, DispatchError
    from policy_impact.dispatch.finance_agent import ROOT_TOOLS
    from policy_impact.dispatch.finance_tool_contracts import PERSONAL_TOOLS
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

    grants = {}
    content = "Monetary policy is a personal research interest, not a verified market fact."

    def decide(state):
        depth = state["task_identity"]["depth"]
        if depth == 0:
            catalogs = _business_observations(state, "personal.catalog")
            if not catalogs:
                return _tool("personal.catalog")
            grants.update({item["personal_kind"]: item["context_id"] for item in catalogs[0]["sources"]})
        else:
            assert grants["wiki"] in state["known_material_refs"]
            assert grants["holdings"] not in state["known_material_refs"]
            granted = next(item for item in state["materials"] if item.get("context_id") == grants["wiki"])
            assert "payload" not in granted and granted["expansion_tool"] == "context.open"
        if depth == 2:
            if not state.get("personal_sources"):
                return _tool("context.open", context_id=grants["wiki"])
            ref = state["personal_sources"][0]["material_ref"]
        else:
            if not state["scheduled_children"]:
                child_role = "analyst" if depth == 0 else "verifier"
                return _spawn(f"personal-depth-{depth + 1}", child_role, [grants["wiki"]],
                              calls=24 if depth == 0 else 8, wall_ms=300_000 if depth == 0 else 120_000)
            if not _child_results(state):
                return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
            returned = next(item for item in state["observations"] if item.get("result_ref"))
            ref = returned["material_ref"]
            if depth == 1 and result_view == "result":
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
                "review_decision_reason": "This fixture tests private-source handoff, not independent claim review.",
            } if depth == 1 else {}),
        }}

    runtime = _runtime(tmp_path, decide, personal_sources=binding(personal_files), tools=[*ROOT_TOOLS, *PERSONAL_TOOLS])
    root = asyncio.run(_finish(runtime))
    tasks = _tasks(runtime)
    assert root["status"] == "succeeded" and len(tasks) == 3 and not runtime.mock_errors
    for task in tasks:
        with runtime.repo.db.connection() as connection:
            result = connection.execute("SELECT sections_json FROM h2_results WHERE result_ref=?", (task["result_ref"],)).fetchone()
        saved = _artifact(runtime, json.loads(result["sections_json"])["finance_state"])
        assert saved["article_states"] == [] and not saved["execution"].get("public_sources")
        reads = saved["execution"]["personal_sources"]
        assert reads and all(item["reader_task_id"] == tasks[-1]["task_id"] for item in reads)
        assert all(item["context_id"] == grants["wiki"] for item in reads)
        if task["depth"] == 1:
            with runtime.repo.db.connection() as connection:
                attempt = connection.execute("SELECT * FROM h2_attempts WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
                                             (task["task_id"],)).fetchone()
            caller = CallerContext(runtime.handle.run_id, task["thread_id"], task["task_id"],
                                   attempt["attempt_id"], attempt["lease_token"])
            with pytest.raises(DispatchError):
                runtime.repo.read_input(caller, grants["holdings"])
    other = runtime.service.create_run(
        run_id="separate-personal-run", session_ref="fixture-session", query="Separate private scope.",
        selected_profile="fixture", allowed_profiles=["fixture"], tools=[*ROOT_TOOLS, *PERSONAL_TOOLS],
    )
    with pytest.raises(DispatchError):
        runtime.repo.read_input(runtime.service.caller(other), grants["wiki"])


def test_personal_preflight_is_explicit_not_a_semantic_pass():
    from scripts.harness_v2_live_acceptance import case_preflight

    assert case_preflight("Q23")["status"] == "blocked_missing_capability"
    assert case_preflight("Q23", personal_sources=True)["status"] == "executable"
    assert case_preflight("Q24", personal_sources=True)["status"] == "executable"
    assert case_preflight("Q25", personal_sources=True)["missing_capabilities"] == ["market_or_public_sources"]
    assert case_preflight("Q25", personal_sources=True, market_sources=True)["status"] == "executable"
    assert case_preflight("Q29", personal_sources=True)["status"] == "requires_isolated_environment"


def test_root_question_is_not_an_implicit_personal_source_grant(personal_files):
    from policy_impact.dispatch.finance_agent import FinanceH2Domain
    from policy_impact.dispatch.finance_root import FinanceRootState
    from policy_impact.runtime.actions import ToolCallAction
    from tests.test_dispatch_finance_research import _context

    caps = Capabilities()
    receipt = catalog(binding(personal_files), caps)
    caps.artifacts["root.question"] = deepcopy(caps.artifacts[receipt["sources"][0]["context_id"]])
    state = FinanceRootState(query="A question can contain JSON, not a private source grant.", phase="research")
    context = replace(_context(state=state), brief={"depth": 0, "allowed_tools": ["personal.catalog"],
                                                  "input_refs": ["root.question"]})
    step = asyncio.run(FinanceH2Domain(None, personal_sources=binding(personal_files))._business_action(
        context, state, ToolCallAction(type="tool_call", tool_name="personal.catalog", arguments={}), caps,
    ))
    assert "root.question" not in step.state.execution["personal_context_refs"]
    assert len(step.state.execution["personal_context_refs"]) == 3
