"""The H2 Query driver must exercise H2, preserve full output, and stay opt-in."""

import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

from scripts.harness_v2_live_acceptance import (
    acceptance_config,
    case_preflight,
    ensure_frozen,
    environment_blocker,
    materialize_query,
    run_h2_case,
    select_cases,
    source_inventory,
    validate_output,
)


class Corpus:
    def __init__(self):
        self.article = {
            "article_id": "fixture-alpha", "author": "Alpha", "title": "Conditional view",
            "published_at": "2026-10-01", "source_url": "https://example.invalid/source",
            "current_hash": "fixture-hash", "content": "# Alpha\nThe author states a conditional view.",
        }

    def list_articles(self, **_kwargs):
        return [{key: value for key, value in self.article.items() if key != "content"}]

    def get_article(self, article_id):
        return self.article if article_id == self.article["article_id"] else None


def profile():
    return {"fixture": {
        "model_id": "fixture-model", "base_url": "https://fixture.invalid/v1",
        "protocol": "openai-compatible", "auth_env": "H2_UNUSED_KEY",
        "parameters": {"call_seconds": 30},
    }}


def response(payload):
    return httpx.Response(200, json={
        "id": "fixture-response", "model": "fixture-model",
        "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps(payload),
        }}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    })


@pytest.mark.parametrize("max_model_calls", [None, 60, 120])
def test_live_driver_uses_durable_h2_and_retains_paginated_full_body(tmp_path, max_model_calls):
    from policy_impact.dispatch.model_execution import GatewayModelAdapter

    requests = []
    answer = "The author keeps the conclusion conditional.\n" * 1800
    research_clock = {"today": "2026-10-19", "timezone": "Asia/Shanghai"}

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        state = json.loads(body["messages"][-1]["content"])
        assert state["research_clock"] == research_clock
        assert state["task_budget"]["budget"]["allocated"] == 120
        assert state["task_budget"]["budget"]["run_budget"]["limit"] == (max_model_calls or 600)
        index = len(requests)
        if index == 1:
            return response({
                "type": "tool_call", "tool_name": "article.list", "arguments": {},
                "state_update": {"research_plan": {"revision": 1, "goals": ["read source"]}},
            })
        if index == 2:
            return response({
                "type": "tool_call", "tool_name": "article.read",
                "arguments": {"article_id": "fixture-alpha"},
                "state_update": {"coverage": {"gaps": ["source reading pending"]}},
            })
        body = json.loads(request.content)
        state = json.loads(body["messages"][-1]["content"])
        refs = [m["material_ref"] for m in state["materials"] if '"tool":"article.read"' in m["payload"]]
        assert refs
        return response({
            "type": "final", "output": {"answer": answer, "summary": "conditional", "evidence_refs": refs},
            "state_update": {"coverage": {"gaps": []}, "review": {"status": "not_independently_verified"}},
        })

    adapter = GatewayModelAdapter(
        credential_resolver=lambda _name: "fixture-key", http_transport=httpx.MockTransport(handler),
    )
    output = tmp_path / "Q04-r1"
    record = run_h2_case(
        output, case_id="Q04-r1", query="Explain the author viewpoint", corpus=Corpus(),
        profiles=profile(), selected_profile="fixture", backend="fake", model_adapter=adapter,
        max_model_calls=max_model_calls,
        research_clock=research_clock,
    )
    assert record["status"] == "succeeded"
    assert record["research_clock"] == research_clock
    assert record["research_clock_bound_to_run"] is True
    assert record["research_clock_propagated_to_model"] is True
    assert record["execution_path"] == "FinanceHarnessV2Service.for_finance_corpus"
    assert record["transport_backend"] == "fakeredis" and not record["real_redis_verified"]
    assert record["task_count"] == 1 and record["checkpoint_count"] == 4
    assert record["exports"]["model_call_count"] == 3
    assert record["scheduler_config"]["root_calls"] == (max_model_calls or 600)
    assert record["runtime_status"]["tasks"][0]["budget"]["remaining"] == 117
    assert record["runtime_status"]["tasks"][0]["budget"]["run_budget"]["remaining"] == (max_model_calls or 600) - 3
    assert record["semantic_acceptance"] == "manual_review_required"
    saved_answer = json.loads((output / "answer.json").read_text(encoding="utf-8"))["answer"]
    assert saved_answer == answer.strip() and len(saved_answer) > 64000
    state = json.loads((output / "finance-state.json").read_text(encoding="utf-8"))
    assert state["research_plan"]["revision"] == 1
    assert state["coverage"]["gaps"] == []
    assert state["review"]["status"] == "not_independently_verified"
    assert state["article_states"][0]["use_status"] == "used"
    assert Path(record["exports"]["files"]["model_calls"]).is_file()


def test_live_driver_preserves_actual_model_failure_without_inventing_delivery(tmp_path):
    from policy_impact.dispatch.model_execution import GatewayModelAdapter

    def handler(_request):
        return httpx.Response(402, json={"error": {"code": "insufficient_quota"}})

    record = run_h2_case(
        tmp_path / "Q04-r1", case_id="Q04-r1", query="Explain", corpus=Corpus(),
        profiles=profile(), selected_profile="fixture", backend="fake",
        model_adapter=GatewayModelAdapter(
            credential_resolver=lambda _name: "fixture-key", http_transport=httpx.MockTransport(handler),
        ),
    )
    assert record["status"] == "failed" and not record["delivered"]
    assert record["exports"]["model_call_count"] == 1
    assert record["allowed_profiles"] == ["fixture"] and record["auto_fallback"] is False
    assert not (tmp_path / "Q04-r1/answer.json").exists()


@pytest.mark.parametrize("corrected", [True, False, "transport_error", "transport_recovered"])
def test_root_format_correction_is_bounded_same_model_and_fully_charged(tmp_path, corrected):
    from policy_impact.dispatch.model_execution import GatewayModelAdapter

    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) >= 2:
            feedback = json.loads(body["messages"][-1]["content"])
            assert "failed_public_response" not in feedback
            assert feedback["input_policy"] == "finance.h2.isolated_correction.v1"
            assert feedback["syntax_error"]["offset"] == len('{"type":"tool_call"')
            assert feedback["action_json_schema"]["discriminator"]["propertyName"] == "type"
            if corrected == "transport_error" or corrected == "transport_recovered" and len(requests) == 2:
                raise httpx.ReadError("fixture connection lost", request=request)
            if corrected is True or corrected == "transport_recovered":
                return response({"type": "final", "output": {"answer": "No source read yet."}})
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": '{"type":"tool_call"'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        })

    output = tmp_path / "correction"
    record = run_h2_case(
        output, case_id="Q04-r1", query="Explain", corpus=Corpus(), profiles=profile(),
        selected_profile="fixture", backend="fake", model_adapter=GatewayModelAdapter(
            credential_resolver=lambda _name: "fixture-key", http_transport=httpx.MockTransport(handler),
        ),
        max_model_calls=60,
    )
    count = 3 if isinstance(corrected, str) else 6 if corrected is False else 2
    succeeded = corrected is True or corrected == "transport_recovered"
    assert len(requests) == count and {r["model"] for r in requests} == {"fixture-model"}
    assert record["exports"]["model_call_count"] == count and record["task_count"] == 1
    assert record["runtime_status"]["tasks"][0]["budget"]["consumed"] == count
    assert record["scheduler_config"]["root_calls"] == 60
    assert record["runtime_status"]["tasks"][0]["budget"]["remaining"] == 120 - count
    assert record["runtime_status"]["tasks"][0]["budget"]["run_budget"]["remaining"] == 60 - count
    assert record["status"] == ("succeeded" if succeeded else "failed")
    if count == 3:
        assert requests[1] == requests[2]
    ledger = [json.loads(line) for line in Path(record["exports"]["files"]["model_calls"]).read_text().splitlines()]
    assert len({r["attempt_id"] for r in ledger}) == 1
    if succeeded:
        state = json.loads((output / "finance-state.json").read_text())
        assert state["execution"]["protocol_repairs"]
    else:
        assert not record["delivered"]


def test_tool_schema_lookup_rejects_bad_read_and_returns_corrective_observation(tmp_path):
    from policy_impact.dispatch.model_execution import GatewayModelAdapter

    calls = []

    def handler(request):
        body = json.loads(request.content)
        state = json.loads(body["messages"][-1]["content"])
        calls.append(body)
        index = len(calls)
        if index == 1:
            assert all("parameters" not in item for item in state["tool_catalog"])
            return response({"type": "tool_call", "tool_name": "tool.describe", "arguments": {"name": "article.read"}})
        if index == 2:
            material = json.loads(state["materials"][-1]["payload"])
            description = material["payload"]["payload"]["description"]
            assert description["parameters"]["additionalProperties"] is False
            assert description["parameters"]["properties"]["limit"]["default"] == 100
            return response({"type": "tool_call", "tool_name": "article.list", "arguments": {}})
        if index == 3:
            return response({"type": "tool_call", "tool_name": "article.read", "arguments": {
                "article_id": "fixture-alpha", "end_line": 50,
            }})
        if index == 4:
            observed = state["normalized_observations"][-1]
            assert observed["status"] == "failed"
            error = observed["payload"]["payload"]["error"]
            assert error["code"] == "invalid_arguments"
            assert error["issues"] == [{"path": ["end_line"], "type": "extra_forbidden"}]
            assert state["article_states"][0]["read_status"] == "failed"
            assert state["article_states"][0]["use_status"] == "undecided"
            assert state["evidence_ledger"] == []
            assert observed["result_ref"] not in state["known_evidence_refs"]
            return response({"type": "tool_call", "tool_name": "article.read", "arguments": {
                "article_id": "fixture-alpha", "start_line": 1, "limit": 100,
            }})
        assert index == 5
        assert state["article_states"][0]["read_status"] == "complete"
        return response({"type": "final", "output": {"answer": "The author view is conditional."}})

    record = run_h2_case(
        tmp_path / "lookup", case_id="Q08-r1", query="Explain", corpus=Corpus(), profiles=profile(),
        selected_profile="fixture", backend="fake", model_adapter=GatewayModelAdapter(
            credential_resolver=lambda _name: "fixture-key", http_transport=httpx.MockTransport(handler),
        ),
    )
    assert record["status"] == "succeeded" and record["exports"]["model_call_count"] == 5
    assert record["metrics"]["tasks"]["failed"] == 0
    assert record["task_count"] == 1  # A rejected tool is an observation, not a failed child Task.


def test_h2_uses_the_approved_single_text_tool_parser_without_format_retry(tmp_path):
    from policy_impact.dispatch.model_execution import GatewayModelAdapter

    calls = []
    text_tool = (
        '<||DSML|| calls><||DSML|| invoke name="tool_call">'
        '<||DSML|| parameter name="tool_name" string="true">tool.describe</||DSML|| parameter>'
        '<||DSML|| parameter name="arguments" string="false">{"name":"article.read"}</||DSML|| parameter>'
        '</||DSML|| invoke></||DSML|| calls>'
    )

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"choices": [{
                "finish_reason": "stop", "message": {"content": text_tool},
            }]})
        return response({"type": "final", "output": {"answer": "Only tool description was read."}})

    record = run_h2_case(
        tmp_path / "text-tool", case_id="Q04-r1", query="Explain", corpus=Corpus(), profiles=profile(),
        selected_profile="fixture", backend="fake", model_adapter=GatewayModelAdapter(
            credential_resolver=lambda _name: "fixture-key", http_transport=httpx.MockTransport(handler),
        ),
    )
    assert record["status"] == "succeeded" and len(calls) == 2 and record["task_count"] == 1
    state = json.loads((tmp_path / "text-tool/finance-state.json").read_text())
    assert not state["execution"].get("protocol_repairs")


def test_h2_accepts_current_deepseek_dsml_tool_call_dialect_without_native_tools(tmp_path):
    from policy_impact.dispatch.model_execution import GatewayModelAdapter

    calls = []
    marker = chr(0xFF5C) * 2
    text_tool = (
        f"<{marker}DSML{marker} calls>\n"
        f"<{marker}DSML{marker} invoke name=\"tool.call\">\n"
        f"<{marker}DSML{marker} parameter name=\"name\" string=\"true\">tool.describe"
        f"</{marker}DSML{marker} parameter>\n"
        f"<{marker}DSML{marker} parameter name=\"arguments\" string=\"false\">"
        f"{{\"name\":\"article.read\"}}</{marker}DSML{marker} parameter>\n"
        f"</{marker}DSML{marker} invoke>\n"
        f"</{marker}DSML{marker} calls>"
    )

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"choices": [{
                "finish_reason": "stop", "message": {"content": text_tool},
            }]})
        return response({"type": "final", "output": {"answer": "Only tool description was read."}})

    record = run_h2_case(
        tmp_path / "current-dsml", case_id="Q04-r1", query="Explain", corpus=Corpus(), profiles=profile(),
        selected_profile="fixture", backend="fake", model_adapter=GatewayModelAdapter(
            credential_resolver=lambda _name: "fixture-key", http_transport=httpx.MockTransport(handler),
        ),
    )
    assert record["status"] == "succeeded" and len(calls) == 2 and record["task_count"] == 1
    state = json.loads((tmp_path / "current-dsml/finance-state.json").read_text())
    assert not state["execution"].get("protocol_repairs")


@pytest.mark.parametrize("name,arguments", [
    ("article.read", {"article_id": "fixture-alpha", "end_line": 5}),
    ("article.read", {"article_ids": ["fixture-alpha"]}),
    ("article.read", {"article_id": "fixture-alpha", "limit": True}),
    ("article.list", {"authors": ["Alpha"]}),
    ("article.claims", {"article_id": "fixture-alpha", "extra": 1}),
    ("article.search", {"query": "test", "limit": 0}),
])
def test_h2_rejects_invalid_tool_arguments_before_touching_corpus(name, arguments):
    from policy_impact.dispatch.finance_agent import FinanceH2ChildExecutor

    class NoAccess:
        def __getattr__(self, _name):
            raise AssertionError("corpus touched before validation")

    with pytest.raises(ValueError):
        FinanceH2ChildExecutor(NoAccess())._tool({"tool_name": name, "arguments": arguments})


def test_case_selection_is_explicit_and_does_not_relax_the_frozen_catalog():
    assert select_cases(None, False) == ["Q04", "Q08", "Q14"]
    assert len(select_cases(None, True)) == 30
    assert select_cases(["Q04"], False) == ["Q04"]
    for cases, all_cases in [(["Q04"], True), (["Q04", "Q04"], False), (["Q99"], False)]:
        with pytest.raises(ValueError):
            select_cases(cases, all_cases)


def test_frozen_sources_include_the_actual_h2_driver_and_its_checks():
    hashes = source_inventory()
    for name in ("AGENTS.md", "scripts/harness_v2_live_acceptance.py",
                 "tests/test_harness_v2_live_acceptance.py", "src/policy_impact/dispatch/finance_agent.py"):
        assert len(hashes[name]) == 64


def test_output_guard_keeps_runs_in_the_approved_records_tree(tmp_path):
    root = tmp_path / "runtime-records"
    root.mkdir()
    assert validate_output(root / "batch", root) == root / "batch"
    (root / "existing").mkdir()
    for output in (root, root / "existing", tmp_path / "elsewhere"):
        with pytest.raises(ValueError):
            validate_output(output, root)


@pytest.mark.parametrize("cap", [None, 25, 60, 120, 600])
def test_acceptance_cap_changes_only_the_case_tree_grant(cap):
    from policy_impact.dispatch import SchedulerConfig

    actual = asdict(acceptance_config("fixture", max_model_calls=cap))
    expected = asdict(SchedulerConfig(stream_name=actual["stream_name"]))
    expected["root_calls"] = 600 if cap is None else cap
    assert actual == expected
    assert SchedulerConfig().root_calls == 600


@pytest.mark.parametrize("cap", [0, -1, 24, 601, True, False, 120.0, "120"])
def test_invalid_case_cap_is_rejected_before_output_or_model_access(tmp_path, cap):
    class NoAccess:
        def __getattr__(self, _name):
            pytest.fail("invalid cap reached an execution dependency")

    output = tmp_path / "never-created"
    with pytest.raises(ValueError, match="above the Root closing reserve"):
        run_h2_case(
            output, case_id="fixture", query="fixture", corpus=NoAccess(), profiles=profile(),
            selected_profile="fixture", backend="fake", model_adapter=NoAccess(), max_model_calls=cap,
        )
    assert not output.exists()


def test_lower_cap_accounts_for_child_format_correction_in_the_same_tree(tmp_path):
    from policy_impact.dispatch.model_execution import GatewayModelAdapter

    counts = {"main": 0, "verifier": 0}

    def handler(request):
        body = json.loads(request.content)
        state = json.loads(body["messages"][-1]["content"])
        if state.get("input_policy") == "finance.h2.isolated_correction.v1":
            state = json.loads(body["messages"][-2]["content"])
        role = state.get("role", "main")
        counts[role] += 1
        assert state["task_budget"]["budget"]["allocated"] == (120 if role == "main" else 60)
        if role == "verifier":
            if counts[role] == 1:
                return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                    "content": '{"type":"final"',
                }}]})
            return response({"type": "final", "output": {
                "schema_version": "finance.research_result.v1",
                "content": "No source was supplied; this is a scheduling fixture only.",
                "evidence_links": [], "material_disposition": [],
                "gaps": [{"description": "No source was supplied."}],
                "limitations": ["No financial conclusion is supported."],
            }})
        if counts[role] == 1:
            return response({"type": "tool_call", "tool_name": "agent.spawn", "arguments": {
                "task_name": "bounded-child", "profile": "verifier",
                "message": "Report the missing-source gap", "success_criteria": ["Disclose gaps"],
                "input_refs": [],
            }})
        if counts[role] == 2:
            return response({"type": "tool_call", "tool_name": "agent.wait", "arguments": {
                "task_ids": [state["scheduled_children"][0]["task_id"]], "mode": "all",
            }})
        return response({"type": "final", "output": {
            "answer": "The child disclosed its missing-source limitation; no financial conclusion.",
        }})

    output = tmp_path / "bounded-child"
    record = run_h2_case(
        output, case_id="fixture", query="Scheduling fixture", corpus=Corpus(), profiles=profile(),
        selected_profile="fixture", backend="fake", max_model_calls=60,
        model_adapter=GatewayModelAdapter(
            credential_resolver=lambda _name: "fixture-key", http_transport=httpx.MockTransport(handler),
        ),
    )
    assert counts == {"main": 3, "verifier": 2}
    assert record["status"] == "succeeded" and record["task_count"] == 2
    assert record["exports"]["model_call_count"] == 5
    assert record["runtime_status"]["tasks"][0]["budget"]["allocated"] == 120
    assert record["runtime_status"]["tasks"][0]["budget"]["consumed"] == 3
    assert record["runtime_status"]["tasks"][0]["budget"]["run_budget"]["consumed"] == 5
    with sqlite3.connect(output / "h2.sqlite") as connection:
        assert connection.execute("SELECT sum(delta_calls) FROM h2_budget_entries").fetchone()[0] == 55
        assert sorted(row[0] for row in connection.execute(
            "SELECT count(*) FROM h2_model_calls GROUP BY task_id",
        )) == [2, 3]
        assert connection.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='grant'").fetchone()[0] == 1


def test_fixed_child_reads_six_articles_and_delivers_beyond_eight_calls(tmp_path):
    from policy_impact.dispatch.model_execution import GatewayModelAdapter

    class SixArticles:
        def __init__(self):
            self.articles = [{**Corpus().article, "article_id": f"article-{index}",
                              "content": "\n".join(f"Conditional argument {line}." for line in range(
                                  179 if index == 5 else 10))} for index in range(6)]
            self.reads = []

        def list_articles(self, **_kwargs):
            return [{key: value for key, value in article.items() if key != "content"}
                    for article in self.articles]

        def get_article(self, article_id):
            self.reads.append(article_id)
            return next((article for article in self.articles if article["article_id"] == article_id), None)

    corpus = SixArticles()
    counts = {"main": 0, "analyst": 0}

    def handler(request):
        state = json.loads(json.loads(request.content)["messages"][-1]["content"])
        role = state.get("role", "main")
        counts[role] += 1
        index = counts[role]
        budget = state["task_budget"]["budget"]
        assert budget["allocated"] == (120 if role == "main" else 60)
        assert budget["consumed"] == index - 1
        if role == "analyst":
            if index == 1:
                return response({"type": "tool_call", "tool_name": "tool.describe",
                                 "arguments": {"name": "article.read"}})
            if index <= 7:
                return response({"type": "tool_call", "tool_name": "article.read",
                                 "arguments": {"article_id": f"article-{index - 2}"}})
            if index == 8:
                assert state["article_states"][-1]["read_status"] == "partial"
                return response({"type": "tool_call", "tool_name": "article.read",
                                 "arguments": {"article_id": "article-5", "start_line": 101}})
            assert index == 9
            assert len(state["article_states"]) == 6
            assert all(article["read_status"] == "complete" for article in state["article_states"])
            refs = []
            for material in state["materials"]:
                payload = material.get("payload")
                while isinstance(payload, dict) and "tool" not in payload:
                    payload = payload.get("payload")
                if isinstance(payload, dict) and payload.get("tool") == "article.read":
                    refs.append(material["material_ref"])
            assert len(refs) == 7
            return response({"type": "final", "output": {
                "schema_version": "finance.analyst_result.v2",
                "content": "Six articles preserve conditional arguments; no external verification.",
                "evidence_links": [{"text": "Six articles preserve conditional arguments; no external verification.",
                                    "evidence_refs": refs}],
                "material_disposition": [], "gaps": [], "limitations": ["Source-only analysis."],
                "review_targets": [], "review_decision": "skip",
                "review_decision_reason": "This source-bound reading task has no independent review target.",
                "review_dispositions": [],
            }})
        if index == 1:
            return response({"type": "tool_call", "tool_name": "article.list", "arguments": {}})
        if index == 2:
            assert len(state["article_states"]) == 6
            return response({"type": "tool_call", "tool_name": "agent.spawn", "arguments": {
                "task_name": "Six-article source-bound analysis", "profile": "analyst",
                "message": "Read all six articles including the final page",
                "profile_options": {"review_mode": "auto"},
                "success_criteria": ["Source-bound result"],
                "input_refs": state["known_material_refs"],
            }})
        if index == 3:
            return response({"type": "tool_call", "tool_name": "agent.wait", "arguments": {
                "task_ids": [state["scheduled_children"][0]["task_id"]], "mode": "all",
            }})
        assert index == 4
        assert len(state["article_states"]) == 6
        assert all(article["read_status"] == "complete" for article in state["article_states"])
        assert budget["run_budget"]["consumed"] == 12
        returned = [item for item in state["normalized_observations"] if item.get("result_ref")]
        assert len(returned) == 1
        original_refs = {item["evidence_ref"] for item in returned[0]["summary"]["evidence_refs"]}
        assert len(original_refs) == 7 and not original_refs.intersection(state["known_evidence_refs"])
        assert returned[0]["result_ref"] in state["known_evidence_refs"]
        return response({"type": "final", "output": {
            "answer": "Six articles preserve conditional arguments; no external verification.",
            "evidence_refs": [returned[0]["result_ref"]],
        }})

    record = run_h2_case(
        tmp_path / "six-articles", case_id="fixture-six", query="Read all six articles", corpus=corpus,
        profiles=profile(), selected_profile="fixture", backend="fake", model_adapter=GatewayModelAdapter(
            credential_resolver=lambda _name: "fixture-key", http_transport=httpx.MockTransport(handler),
        ),
    )
    assert record["status"] == "succeeded" and record["delivered"] and record["task_count"] == 2
    assert counts == {"main": 4, "analyst": 9}
    assert len(corpus.reads) == 7 and corpus.reads.count("article-5") == 2
    assert record["exports"]["model_call_count"] == 13
    root_budget = record["runtime_status"]["tasks"][0]["budget"]
    assert root_budget["consumed"] == 4 and root_budget["run_budget"]["consumed"] == 13
    with sqlite3.connect(tmp_path / "six-articles/h2.sqlite") as connection:
        assert sorted(row[0] for row in connection.execute(
            "SELECT count(*) FROM h2_model_calls GROUP BY task_id")) == [4, 9]
        assert connection.execute("SELECT sum(delta_calls) FROM h2_budget_entries").fetchone()[0] == 587


def test_lower_cap_counts_same_model_transport_retry_without_new_grant(tmp_path):
    from policy_impact.dispatch.model_execution import GatewayModelAdapter

    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadError("fixture disconnected", request=request)
        return response({"type": "final", "output": {"answer": "Fixture only; no source read."}})

    output = tmp_path / "bounded-retry"
    record = run_h2_case(
        output, case_id="fixture", query="Fixture", corpus=Corpus(), profiles=profile(),
        selected_profile="fixture", backend="fake", max_model_calls=60,
        model_adapter=GatewayModelAdapter(
            credential_resolver=lambda _name: "fixture-key", http_transport=httpx.MockTransport(handler),
        ),
    )
    assert record["status"] == "succeeded" and len(requests) == 2
    assert {json.loads(request.content)["model"] for request in requests} == {"fixture-model"}
    assert record["runtime_status"]["tasks"][0]["budget"]["remaining"] == 118
    assert record["runtime_status"]["tasks"][0]["budget"]["run_budget"]["remaining"] == 58
    with sqlite3.connect(output / "h2.sqlite") as connection:
        assert connection.execute("SELECT count(DISTINCT attempt_id) FROM h2_model_calls").fetchone()[0] == 2
        assert connection.execute("SELECT sum(delta_calls) FROM h2_budget_entries").fetchone()[0] == 58
        assert connection.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='grant'").fetchone()[0] == 1


def test_lower_cap_stops_before_using_reserved_closing_calls(tmp_path):
    from policy_impact.dispatch.model_execution import GatewayModelAdapter

    requests = []

    def handler(request):
        requests.append(request)
        return response({"type": "tool_call", "tool_name": "tool.describe", "arguments": {
            "name": "article.read",
        }})

    record = run_h2_case(
        tmp_path / "bounded-stop", case_id="fixture", query="Fixture", corpus=Corpus(), profiles=profile(),
        selected_profile="fixture", backend="fake", max_model_calls=25,
        model_adapter=GatewayModelAdapter(
            credential_resolver=lambda _name: "fixture-key", http_transport=httpx.MockTransport(handler),
        ),
    )
    assert len(requests) == record["exports"]["model_call_count"] == 1
    root = record["runtime_status"]["tasks"][0]
    assert root["status"] == "failed" and root["attempts"][-1]["error_code"] == "budget_denied"
    assert root["budget"]["run_budget"]["remaining"] == 24
    assert not record["delivered"]


@pytest.mark.parametrize("cap", [24, 601])
def test_cli_rejects_invalid_cap_before_snapshot_or_provider_lookup(tmp_path, cap):
    result = subprocess.run(
        [sys.executable, "scripts/harness_v2_live_acceptance.py", "--live",
         "--max-model-calls", str(cap), "--output", str(tmp_path / "never"),
         "--snapshot-from", str(tmp_path / "absent"), "--model-profile", "fixture", "--backend", "fake"],
        check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 2 and "above the Root closing reserve" in result.stderr
    assert not (tmp_path / "never").exists()


def test_cli_refuses_model_calls_without_explicit_live_flag(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/harness_v2_live_acceptance.py", "--output", str(tmp_path / "never"),
         "--snapshot-from", str(tmp_path / "absent"), "--model-profile", "fixture", "--backend", "fake"],
        check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 2 and "--live is required" in result.stderr
    assert not (tmp_path / "never").exists()


def test_business_catalog_preflight_records_every_case_without_provider_access():
    results = {case: case_preflight(case) for case in select_cases(None, True)}
    assert len(results) == 30
    assert sum(row["status"] == "executable" for row in results.values()) == 20
    assert results["Q29"] == {"status": "requires_isolated_environment", "model_calls": 0}
    assert results["Q30"]["missing_capabilities"] == ["external_adapter_fault_injection"]
    assert sum(row["status"] == "blocked_missing_capability" for row in results.values()) == 9
    with pytest.raises(ValueError, match="unknown frozen case"):
        case_preflight("Q31")


def test_missing_author_materialization_does_not_rewrite_other_queries():
    original = '总结“某位文章库中不存在的作者”最近五篇黄金文章，并给出引用。'
    actual = materialize_query("Q26", original, authors={"acceptance_missing_author_Q26"})
    assert actual == '总结“acceptance_missing_author_Q26_absent”最近五篇黄金文章，并给出引用。'
    assert materialize_query("Q04", original, authors=set()) == original
    with pytest.raises(ValueError, match="placeholder"):
        materialize_query("Q26", "unapproved new query", authors=set())


@pytest.mark.parametrize("changed", ["sources", "snapshot", None])
def test_business_batch_refuses_changed_sources_or_inputs_before_next_case(tmp_path, monkeypatch, changed):
    from scripts import harness_v2_live_acceptance as driver

    manifest = {"source_hashes": {"code": "a"}, "snapshot": {"files": {"source": "b"}}}
    monkeypatch.setattr(driver, "source_inventory", lambda: {"code": "changed" if changed == "sources" else "a"})
    monkeypatch.setattr(driver, "inventory", lambda _root: {"source": "changed" if changed == "snapshot" else "b"})
    if changed:
        with pytest.raises(RuntimeError, match="no next case"):
            ensure_frozen(tmp_path, manifest)
    else:
        ensure_frozen(tmp_path, manifest)


def test_interrupted_h2_case_exports_evidence_without_inventing_runtime_terminal_state(tmp_path, monkeypatch):
    from scripts import harness_v2_live_acceptance as driver

    async def interrupt(*_args):
        raise KeyboardInterrupt

    monkeypatch.setattr(driver, "drive", interrupt)
    output = tmp_path / "interrupted"
    with pytest.raises(KeyboardInterrupt):
        run_h2_case(
            output, case_id="Q04-r1", query="Fixture", corpus=Corpus(), profiles=profile(),
            selected_profile="fixture", backend="fake", max_model_calls=120,
        )
    record = json.loads((output / "record.json").read_text(encoding="utf-8"))
    assert record["status"] == "host_interrupted"
    assert record["runtime_terminal_state_inferred"] is False
    assert record["runtime_status"]["tasks"][0]["status"] not in {"succeeded", "failed"}
    assert record["exports"]["model_call_count"] == 0
    assert record["research_clock_propagated_to_model"] is False
    assert not record["delivered"]


@pytest.mark.parametrize("code,expected", [("quota_error", True), ("provider_error", True),
                                          ("transport_error", False), ("contract_error", False)])
def test_business_batch_stops_paid_cases_after_a_provider_environment_failure(code, expected):
    assert environment_blocker({"runtime_status": {"tasks": [{"attempts": [
        {"error_code": code},
    ]}]}}) is expected


@pytest.mark.parametrize("provider_blocked", [False, True])
def test_complete_business_matrix_keeps_blocked_cases_and_original_queries(tmp_path, monkeypatch, provider_blocked):
    import run_finance
    from policy_impact.dispatch import model_execution
    from policy_impact.domains.finance import article_corpus
    from policy_impact.harness import model_config
    from scripts import harness_v2_live_acceptance as driver

    class FixtureCorpus:
        def __init__(self, *_args):
            pass

        def sync(self):
            pass

        def list_authors(self):
            return [{"author": "acceptance_missing_author_Q26"}]

    def snapshot(_source, destination):
        (destination / "company").mkdir(parents=True)
        return {"files": {}}, "2026-10-19"

    invoked = []

    def run_case(path, **kwargs):
        invoked.append(kwargs)
        record = {"case": kwargs["case_id"], "query": kwargs["query"],
                  "status": "failed" if provider_blocked else "succeeded",
                  "runtime_status": {"tasks": [{"attempts": [
                      {"error_code": "quota_error" if provider_blocked else None},
                  ]}]}}
        driver.write_json(path / "record.json", record)
        return record

    output = tmp_path / "batch"
    monkeypatch.setattr(driver, "validate_output", lambda path: path)
    monkeypatch.setattr(driver, "reuse_snapshot", snapshot)
    monkeypatch.setattr(driver, "source_inventory", dict)
    monkeypatch.setattr(driver, "inventory", lambda _root: {})
    monkeypatch.setattr(driver, "run_h2_case", run_case)
    monkeypatch.setattr(run_finance, "configure_environment", lambda **_kwargs: None)
    monkeypatch.setattr(model_execution, "profiles_from_registry", lambda _profiles: profile())
    monkeypatch.setattr(model_config, "model_runtime_status", lambda: {"model_id": "fixture"})
    monkeypatch.setattr(article_corpus, "ArticleCorpus", FixtureCorpus)
    monkeypatch.setattr(sys, "argv", ["runner", "--live", "--all-cases", "--backend", "fake",
                                     "--model-profile", "fixture", "--snapshot-from", str(tmp_path / "source"),
                                     "--max-model-calls", "120", "--output", str(output)])
    assert driver.main() == 1  # Complete execution matrix is not 30/30 business acceptance.
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert len(summary["cases"]) == 30
    assert manifest["maximum_batch_model_calls"] == 2400
    assert manifest["research_clock_binding"] == "immutable_run_brief"
    assert manifest["research_clock_propagation_status"] == "per_case_record_after_execution"
    assert all(case["research_clock"] == {"today": "2026-10-19", "timezone": "Asia/Shanghai"}
               for case in invoked)
    assert len(invoked) == (1 if provider_blocked else 20)
    if not provider_blocked:
        assert all(case["max_model_calls"] == 120 for case in invoked)
        query_record = json.loads((output / "queries/Q26-r1.json").read_text(encoding="utf-8"))
        assert "某位文章库中不存在的作者" in query_record["original_query"]
        assert "acceptance_missing_author_Q26_absent" in query_record["executed_query"]
    assert summary["status"] == "execution_complete"
    assert summary["sources_unchanged"] and summary["snapshot_unchanged"]
