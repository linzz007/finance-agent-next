"""Offline acceptance for the model-driven Harness 2.0 finance path."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import fakeredis
import httpx
import pytest

from policy_impact.dispatch import DispatchRepository, FinanceHarnessV2Service, SchedulerConfig
from policy_impact.dispatch.finance_agent import ROOT_TOOLS
from policy_impact.dispatch.model_execution import GatewayModelAdapter
from policy_impact.dispatch.transport import RedisStreams

TOOLS = list(ROOT_TOOLS)


class FixtureCorpus:
    def __init__(self) -> None:
        content = "# Alpha\n\n发布日期：2026-10-01\n\n" + "\n".join(
            f"Line {index}: the author keeps the view conditional." for index in range(1, 8)
        )
        self.article = {
            "article_id": "article_alpha",
            "author": "Alpha",
            "title": "Alpha view",
            "published_at": "2026-10-01",
            "source_url": "https://example.invalid/alpha",
            "current_hash": "hash-alpha",
            "content": content,
        }

    def list_articles(self, *, limit=100, **_filters):
        return [
            {
                key: self.article[key]
                for key in (
                    "article_id",
                    "author",
                    "title",
                    "published_at",
                    "source_url",
                    "current_hash",
                )
            }
        ][:limit]

    def search(self, **_arguments):
        return []

    def get_article(self, article_id):
        return self.article if article_id == self.article["article_id"] else None

    def claims(self, _article_id):
        return []


def _chat(payload, index):
    return {
        "id": f"provider-{index}",
        "model": "fixture-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(payload)},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def _json_user_payload(request: httpx.Request) -> dict:
    body = json.loads(request.content)
    for message in reversed(body["messages"]):
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "query" in value:
            return value
    raise AssertionError("finance root user payload was not sent")


@pytest.mark.parametrize(
    "state_mode", ["legacy", "complete", "bad-type", "noncompact", "forged-read", "direct-source-ref"]
)
def test_model_driven_finance_path_reads_then_delivers_with_durable_resume(
    tmp_path: Path, state_mode: str,
):
    class CorpusWithUnreadArticle(FixtureCorpus):
        def list_articles(self, *, limit=100, **filters):
            rows = super().list_articles(limit=limit, **filters)
            return [*rows, {**rows[0], "article_id": "article_unread"}][:limit]

    corpus = CorpusWithUnreadArticle()
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        index = len(calls)
        if index == 1:
            payload = {
                "type": "tool_call",
                "tool_name": "article.list",
                "arguments": {"limit": 10},
            }
            if state_mode != "legacy":
                payload["state_update"] = {
                    "research_plan": {"revision": 1, "goals": ["explain conditional reasoning"]},
                    "coverage": {"gaps": ["article not read"]},
                    "review": {"status": "pending", "reason": "await source"},
                    "delivery": {"proposed_status": "researching"},
                }
        elif index == 2:
            state = _json_user_payload(request)
            if state_mode != "legacy":
                assert state["research_plan"]["revision"] == 1
                assert state["coverage"]["gaps"] == ["article not read"]
                assert state["review"]["status"] == "pending"
                assert state["delivery"]["proposed_status"] == "researching"
            payload = {
                "type": "tool_call",
                "tool_name": "article.read",
                "arguments": {"article_id": corpus.article["article_id"], "start_line": 1, "limit": 100},
            }
            if state_mode != "legacy":
                payload["state_update"] = {
                    "research_plan": {"revision": 2, "goals": ["read and explain conditions"]},
                    "article_updates": [{
                        "article_id": corpus.article["article_id"],
                        "relevance": "primary", "requested_depth": "deep",
                        "reason": "contains the requested reasoning",
                    }],
                }
        else:
            state = _json_user_payload(request)
            refs = []
            for material in state.get("materials", []):
                if '"tool":"article.read"' in material.get("payload", ""):
                    if state_mode == "direct-source-ref":
                        refs.extend(item["evidence_ref"] for item in state["evidence_ledger"])
                    else:
                        refs.append(material["material_ref"])
            assert refs, "the final root decision did not receive the read result"
            if state_mode != "legacy":
                assert state["research_plan"]["revision"] == 2
                assert state["article_states"][0]["read_status"] == "complete"
                assert state["article_states"][0]["requested_depth"] == "deep"
                assert state["review"]["reason"] == "await source"
            payload = {
                "type": "final",
                "output": {
                    "answer": "The author presents a conditional view grounded in the read article.",
                    "summary": "The article was read before delivery.",
                    "evidence_refs": refs,
                },
            }
            if state_mode == "complete":
                payload["state_update"] = {
                    "evidence_additions": [{
                        "claim_id": "conditional-view", "evidence_ref": refs[0],
                        "relation": "supports", "source_kind": "article",
                        "scope": "the read article", "certainty": "conditional",
                    }],
                    "coverage": {"gaps": [], "evidence_refs": refs, "stop_rationale": "source read"},
                    "review": {"status": "complete", "evidence_refs": refs},
                    "delivery": {"proposed_status": "ready"},
                }
            elif state_mode == "bad-type":
                payload["state_update"] = "not an object"
            elif state_mode == "noncompact":
                payload["state_update"] = {"coverage": {"body": "not compact metadata"}}
            elif state_mode == "forged-read":
                payload["state_update"] = {"article_updates": [{
                    "article_id": corpus.article["article_id"], "read_status": "complete",
                }]}
        return httpx.Response(200, json=_chat(payload, index))

    config = SchedulerConfig(
        stream_name="finance-agent-next:fixture:model-driven", budget_policy="hierarchical",
        publish_attempts=2,
        lease_ms=60000,
    )
    repository = DispatchRepository(
        tmp_path / "finance.sqlite",
        tmp_path / "records",
        config=config,
        profiles={
            "fixture": {
                "model_id": "fixture-model",
                "base_url": "https://fixture.invalid/v1",
                "protocol": "openai-compatible",
                "auth_env": "H2_TEST_UNUSED_KEY",
                "parameters": {"call_seconds": 30},
            }
        },
        roles={},
    )
    repository.migrate()
    adapter = GatewayModelAdapter(
        credential_resolver=lambda _name: "fixture-key",
        http_transport=httpx.MockTransport(handler),
    )
    transport = RedisStreams(
        fakeredis.FakeRedis(decode_responses=True),
        config.stream_name,
        group="finance-agent-model-driven-tests",
    )
    service = FinanceHarnessV2Service.for_finance_corpus(
        repository,
        transport,
        corpus=corpus,
        model_adapter=adapter,
        worker_id="finance-model-driven-worker",
    )
    handle = service.create_run(
        run_id="finance-model-driven",
        session_ref="session-model-driven",
        query="Explain Alpha's article and preserve its conditions.",
        selected_profile="fixture",
        allowed_profiles=["fixture"],
        tools=TOOLS,
    )

    status = asyncio.run(service.run_until_terminal(handle, max_cycles=20))

    invalid_update = state_mode in {"bad-type", "noncompact", "forged-read"}
    final_status = "partial" if invalid_update else "succeeded"
    assert status["tasks"][0]["status"] == final_status
    assert len(calls) == 3
    assert len(status["tasks"][0]["attempts"]) == 3
    assert [attempt["status"] for attempt in status["tasks"][0]["attempts"]] == [
        "suspended",
        "suspended",
        "failed" if invalid_update else "succeeded",
    ]
    with repository.db.connection() as connection:
        model_calls = connection.execute(
            "SELECT COUNT(*) FROM h2_model_calls WHERE run_id=? AND status='succeeded'",
            (handle.run_id,),
        ).fetchone()[0]
        result = connection.execute(
            "SELECT sections_json FROM h2_results WHERE task_id=? AND disposition='accepted'",
            (handle.task_id,),
        ).fetchone()
        sections = json.loads(result[0])
        state_artifact = connection.execute(
            "SELECT * FROM h2_artifacts WHERE artifact_ref=?",
            (sections["finance_state"],),
        ).fetchone()
        evidence_refs = json.loads(
            connection.execute(
                "SELECT evidence_refs_json FROM h2_results WHERE task_id=? AND disposition='accepted'",
                (handle.task_id,),
            ).fetchone()[0]
        )
    assert model_calls == 3
    assert "finance_state" in sections
    saved_state = json.loads(repository.artifacts.read(state_artifact))
    assert saved_state["phase"] == "delivery"
    assert saved_state["article_states"][0]["read_status"] == "complete"
    assert saved_state["article_states"][1]["article_id"] == "article_unread"
    assert saved_state["article_states"][1]["read_status"] == "unread"
    assert saved_state["article_states"][1]["use_status"] == "undecided"
    assert evidence_refs and evidence_refs[0]["evidence_ref"]
    result_ref = status["tasks"][0]["result_ref"]
    full = repository.result_get(service.caller(handle), result_ref, view="full")
    assert full["content"] == "The author presents a conditional view grounded in the read article."
    if state_mode != "legacy":
        assert saved_state["research_plan"]["revision"] == 2
        assert saved_state["article_states"][0]["use_status"] == "used"
    if state_mode == "complete":
        assert saved_state["execution"]["metadata_status"] == "complete"
        assert saved_state["coverage"]["gaps"] == []
        assert saved_state["review"]["status"] == "complete"
        assert any(item.get("claim_id") == "conditional-view" for item in saved_state["evidence_ledger"])
    if invalid_update:
        assert saved_state["delivery"]["metadata_status"] == "incomplete_metadata"
        assert "state_update_invalid" in saved_state["delivery"]["warnings"]
        assert "state_update_missing" not in saved_state["execution"]["state_update_warnings"]
        assert saved_state["review"]["status"] == "pending"


def test_article_read_is_bounded_and_does_not_return_full_source():
    from policy_impact.dispatch.finance_agent import FinanceH2ChildExecutor

    corpus = FixtureCorpus()
    executor = FinanceH2ChildExecutor(corpus)
    payload = executor._tool(
        {
            "tool_name": "article.read",
            "arguments": {"article_id": "article_alpha", "start_line": 2, "limit": 2},
        }
    )
    assert payload["content"].splitlines() == ["", "发布日期：2026-10-01"]
    assert payload["start_line"] == 2
    assert payload["end_line"] == 3
    assert payload["has_more"] is True


@pytest.mark.parametrize("role,role_version", [
    ("analyst", "1.0.0"), ("analyst", "2.0.0"),
    ("verifier", "1.0.0"), ("skeptic", "1.0.0"),
])
def test_analyst_delegate_receives_authorized_material_and_returns_research_result(tmp_path: Path, role, role_version):
    corpus = FixtureCorpus()
    calls = []
    read_ref = None

    def handler(request: httpx.Request):
        nonlocal read_ref
        calls.append(request)
        index = len(calls)
        state = _json_user_payload(request) if index in {1, 2, 3, 5} else {}
        if index == 1:
            assert state["delegation_policy"]["preferred_tool"] == "agent.spawn"
            assert state["task_budget"]["budget"]["allocated"] == 600
            payload = {"type": "tool_call", "tool_name": "article.list", "arguments": {"limit": 10}}
        elif index == 2:
            payload = {
                "type": "tool_call",
                "tool_name": "article.read",
                "arguments": {"article_id": "article_alpha", "start_line": 1, "limit": 100},
            }
        elif index == 3:
            expected_budget = {"model_calls": 4, "wall_time_ms": 600000} if role == "analyst" else {
                "model_calls": 1, "wall_time_ms": 120000,
            }
            assert state["delegation_policy"]["legacy_delegate"]["requested_budget_by_role"][role] == expected_budget
            read_ref = next(
                material["material_ref"]
                for material in state["materials"]
                if '"tool":"article.read"' in material.get("payload", "")
            )
            payload = {
                "type": "delegate",
                "role": "analyst",
                "role_version": "2.0.0",
                "research_task": {
                    "schema_version": "finance.research_task.v1",
                    "original_query": "Explain Alpha's article.",
                    "objective": "Explain the author's conditional argument.",
                    "material_refs": [read_ref],
                    "requested_result_kind": "article_understanding",
                    "completion_expectation": "Preserve conditions and evidence gaps.",
                    "reading_request": {"material_ref": read_ref, "depth": "deep"},
                },
            }
            if role_version == "1.0.0":
                payload = {
                    "type": "delegate", "role": role,
                    "task": "Explain the author's conditional argument.",
                    "input_artifact_refs": [read_ref],
                }
        elif index == 4:
            child_input = json.loads(json.loads(request.content)["messages"][-1]["content"])
            assert child_input["role"] == role
            assert child_input["delegation_policy"]["preferred_tool"] == "agent.spawn"
            assert child_input["task_budget"]["budget"]["allocated"] == (4 if role == "analyst" else 1)
            assert child_input["task"]
            if role_version == "2.0.0":
                assert child_input["task"]["reading_request"]["depth"] == "deep"
            assert child_input["success_criteria"]
            assert "evidence_links" in child_input["output_json_schema"]["required"]
            result = {
                "schema_version": (
                    "finance.analyst_result.v2"
                    if role == "analyst" and role_version == "2.0.0"
                    else "finance.research_result.v1"
                ),
                "content": "The author offers a conditional view.",
                "evidence_links": [{"text": "The author offers a conditional view.", "evidence_refs": [read_ref]}],
                "material_disposition": [{
                    "material_ref": read_ref,
                    "decision": "considered",
                    "reason": "The article is the assigned source.",
                }],
                "gaps": [],
                "limitations": [],
            }
            if role == "analyst" and role_version == "2.0.0":
                result.update({
                    "review_targets": [],
                    "review_decision": "skip",
                    "review_decision_reason": "The assigned source contains one directly attributable view.",
                    "review_dispositions": [],
                })
            payload = {"type": "final", "output": result}
        else:
            analyst_refs = [
                material["material_ref"]
                for material in state["materials"]
                if '"schema_version":"finance.h2.analyst_result.v1"' in material.get("payload", "")
            ]
            assert analyst_refs, "the root did not receive the analyst result"
            payload = {
                "type": "final",
                "output": {
                    "answer": "The author offers a conditional view, supported by the article analysis.",
                    "evidence_refs": [read_ref],
                },
            }
        return httpx.Response(200, json=_chat(payload, index))

    config = SchedulerConfig(
        stream_name="finance-agent-next:fixture:analyst-delegate", budget_policy="hierarchical",
        publish_attempts=2,
        lease_ms=60000,
    )
    repository = DispatchRepository(
        tmp_path / "finance.sqlite",
        tmp_path / "records",
        config=config,
        profiles={
            "fixture": {
                "model_id": "fixture-model",
                "base_url": "https://fixture.invalid/v1",
                "protocol": "openai-compatible",
                "auth_env": "H2_TEST_UNUSED_KEY",
                "parameters": {"call_seconds": 30},
            }
        },
        roles={},
    )
    repository.migrate()
    adapter = GatewayModelAdapter(
        credential_resolver=lambda _name: "fixture-key",
        http_transport=httpx.MockTransport(handler),
    )
    transport = RedisStreams(
        fakeredis.FakeRedis(decode_responses=True),
        config.stream_name,
        group="finance-agent-analyst-tests",
    )
    service = FinanceHarnessV2Service.for_finance_corpus(
        repository,
        transport,
        corpus=corpus,
        model_adapter=adapter,
        worker_id="finance-analyst-worker",
    )
    handle = service.create_run(
        run_id="finance-analyst-delegate",
        session_ref="session-analyst",
        query="Explain Alpha's article and preserve its conditions.",
        selected_profile="fixture",
        allowed_profiles=["fixture"],
        tools=TOOLS,
    )

    status = asyncio.run(service.run_until_terminal(handle, max_cycles=30))

    assert status["tasks"][0]["status"] == "succeeded"
    assert len(calls) == 5
    with repository.db.connection() as connection:
        child = connection.execute(
            "SELECT t.status, h.role, b.requested_calls, b.allocated_calls, "
            "b.deadline_at-b.created_at AS wall_time_ms "
            "FROM h2_tasks t JOIN h2_threads h ON h.thread_id=t.thread_id "
            "JOIN h2_budgets b ON b.budget_id=t.budget_ref "
            "WHERE t.run_id=? AND h.role=?",
            (handle.run_id, role),
        ).fetchone()
    assert child["role"] == role
    assert child["status"] == "succeeded"
    assert child["requested_calls"] == child["allocated_calls"] == (4 if role == "analyst" else 1)
    assert child["wall_time_ms"] == (600000 if role == "analyst" else 120000)

