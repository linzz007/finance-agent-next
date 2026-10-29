"""Offline integration coverage for approved032's durable finance action loop."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import fakeredis
import httpx
import pytest

from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.dispatch import DispatchRepository, FinanceHarnessV2Service, SchedulerConfig
from policy_impact.dispatch.contracts import (
    AgentSpawnInput,
    CallerContext,
    DispatchError,
    Spawn,
    Submission,
)
from policy_impact.dispatch.finance_agent import ROOT_TOOLS
from policy_impact.dispatch.finance_rules import load_rules
from policy_impact.dispatch.finance_tool_contracts import normalize_agent_spawn
from policy_impact.dispatch.model_execution import GatewayModelAdapter
from policy_impact.dispatch.transport import RedisStreams
from policy_impact.domains.finance.article_corpus import ArticleCorpus


class FixtureCorpus:
    def __init__(self):
        self.reads = []
        self.articles = {
            f"article_{name.lower()}": {
                "article_id": f"article_{name.lower()}",
                "author": name,
                "title": f"{name} view",
                "published_at": "2026-10-01",
                "source_url": f"https://example.invalid/{name.lower()}",
                "current_hash": f"hash-{name.lower()}",
                "content": f"# {name}\nSOURCE_BODY_{name.upper()}\nThe view remains conditional.",
            }
            for name in ("Alpha", "Beta")
        }

    def list_articles(self, *, limit=100, author="", **_filters):
        return [
            {key: value for key, value in article.items() if key != "content"}
            for article in self.articles.values()
            if not author or article["author"] == author
        ][:limit]

    def search(self, **_arguments):
        return self.list_articles()

    def get_article(self, article_id):
        self.reads.append(article_id)
        return self.articles.get(article_id)

    def claims(self, _article_id):
        return []


def _chat(payload, index):
    return {
        "id": f"provider-{index}",
        "model": "fixture-model",
        "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps(payload),
        }}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def _tool(name, **arguments):
    return {"type": "tool_call", "tool_name": name, "arguments": arguments}


def _spawn(key, role, refs, *, calls=12, wall_ms=300_000):
    arguments = {
        "task_name": key,
        "message": f"{key}: preserve the assigned source conditions",
        "profile": role,
        **({"profile_options": {"review_mode": "auto"}} if role == "analyst" else {}),
        "success_criteria": ["Use only granted sources and disclose unread material"],
        "input_refs": refs,
        "requested_budget": {"model_calls": calls, "wall_time_ms": wall_ms},
        "priority": -10,
    }
    return _tool("agent.spawn", **arguments)


def _material_payloads(state):
    materials = []
    for entry in state["materials"]:
        payload = entry.get("payload")
        material = json.loads(payload) if isinstance(payload, str) else entry
        if isinstance(payload, dict):
            material = payload
        if material.get("schema_version") == "finance.h2.material.v1":
            materials.append(material["payload"])
    return materials


def _business_observations(state, name):
    successful = [
        item["payload"] for item in _material_payloads(state)
        if item.get("schema_version") == "finance.h2.tool_result.v1"
        and item["payload"].get("tool") == name
    ]
    failed = [
        observation["payload"]["payload"]
        for observation in state["normalized_observations"]
        if isinstance(observation.get("payload"), dict)
        and observation["payload"].get("schema_version") == "finance.h2.tool_result.v1"
        and observation["payload"]["payload"].get("tool") == name
        and observation.get("status") == "failed"
    ]
    return [*successful, *failed]


def _scheduler_observations(state, name):
    return [item for item in state["action_observations"] if item.get("tool_name") == name]


def _child_results(state):
    return [
        item for item in _material_payloads(state)
        if item.get("schema_version") == "finance.h2.analyst_result.v1"
    ]


def _research_final(state):
    refs = [item["material_ref"] for item in state["article_states"] if item.get("material_ref")]
    content = "The observed source preserves a conditional view." if refs else "Metadata only; source unread."
    result = {
        "schema_version": "finance.research_result.v1", "content": content,
        "evidence_links": [{"text": content, "evidence_refs": refs}] if refs else [],
        "material_disposition": [{
            "material_ref": ref, "decision": "considered", "reason": "Explicitly assigned material",
        } for ref in state["material_refs"]],
        "gaps": [] if refs else [{"description": "No source body was read."}],
        "limitations": [],
    }
    if (state.get("role") == "analyst" and state.get("role_version") == "2.0.0"
            and state.get("output_contract") == {"name": "finance.analyst_result", "version": "2"}):
        result.update({
            "schema_version": "finance.analyst_result.v2",
            "review_targets": [],
            "review_decision": "skip",
            "review_decision_reason": "The assigned source contains a directly attributable view.",
            "review_dispositions": [],
        })
    return {"type": "final", "output": result}


def _root_final(state):
    refs = [item["material_ref"] for item in state["article_states"] if item.get("material_ref")]
    return {"type": "final", "output": {
        "answer": "The assigned work is complete; source claims remain conditional.",
        "evidence_refs": refs,
    }}


def _runtime(tmp_path: Path, decide, *, tools=None, transport_failures=(), config=None, corpus=None,
             public_sources=None, market_sources=None, personal_sources=None, research_clock=None,
             clock_ms=1_000_000):
    clock = SimpleNamespace(value=clock_ms)
    config = config or SchedulerConfig(
        budget_policy="hierarchical",
        stream_name="finance-agent-next:fixture:continuation", lease_ms=60_000,
        run_concurrency=1, worker_concurrency=1,
    )
    profile = {
        "model_id": "fixture-model", "base_url": "https://fixture.invalid/v1",
        "protocol": "openai-compatible", "auth_env": "H2_UNUSED_FIXTURE_KEY",
        "parameters": {"call_seconds": 30},
    }
    repository = DispatchRepository(
        tmp_path / "finance.sqlite", tmp_path / "records", config=config,
        profiles={"fixture": profile, "unused": {**profile, "model_id": "unused-model"}},
        roles={}, clock=lambda: clock.value,
    )
    repository.migrate()
    requests, prompts, mock_errors = [], [], []

    def handler(request):
        body = json.loads(request.content)
        user_messages = [json.loads(item["content"]) for item in body["messages"] if item["role"] == "user"]
        state = next(item for item in reversed(user_messages) if "query" in item)
        if user_messages[-1].get("input_policy") in {
            "finance.h2.isolated_correction.v1", "finance.h2.immutable_anchor_correction.v1",
        }:
            state = {**state, "fixture_repair_feedback": user_messages[-1]}
        requests.append(request)
        prompts.append(state)
        if len(requests) in transport_failures:
            raise httpx.ReadError("Synthetic correction transport failure", request=request)
        try:
            action = decide(state)
        except Exception as exc:
            mock_errors.append(exc)
            raise
        return httpx.Response(200, json=_chat(action, len(requests)))

    corpus = FixtureCorpus() if corpus is None else corpus
    transport = RedisStreams(
        fakeredis.FakeRedis(decode_responses=True), config.stream_name,
        group="finance-continuation-tests",
    )
    service = FinanceHarnessV2Service.for_finance_corpus(
        repository, transport, corpus=corpus, worker_id="only-worker",
        public_sources=public_sources, market_sources=market_sources,
        personal_sources=personal_sources,
        model_adapter=GatewayModelAdapter(
            credential_resolver=lambda _name: "fixture-key",
            http_transport=httpx.MockTransport(handler),
        ),
    )
    handle = service.create_run(
        run_id="continuation", session_ref="fixture-session", query="Explain Alpha conditionally.",
        selected_profile="fixture", allowed_profiles=["fixture", "unused"],
        tools=list(ROOT_TOOLS) if tools is None else tools,
        research_clock=research_clock,
    )
    return SimpleNamespace(
        repo=repository, service=service, handle=handle, corpus=corpus,
        transport=transport, clock=clock, requests=requests, prompts=prompts,
        mock_errors=mock_errors,
    )


@pytest.mark.parametrize("outcome", ["success", "transport", "contract"])
def test_root_correction_transport_retry_charges_each_physical_request_once(tmp_path, outcome):
    def decide(state):
        if "fixture_repair_feedback" not in state or outcome == "contract":
            return {"type": "not-an-action"}
        return {"type": "final", "output": {
            "answer": "No article was read; this is a transport-only fixture.", "evidence_refs": [],
        }}

    runtime = _runtime(tmp_path, decide, transport_failures=(2, 3) if outcome == "transport" else (2,))
    asyncio.run(_cycle(runtime))
    status = runtime.service.status(runtime.handle)["tasks"][0]
    assert status["status"] == ("succeeded" if outcome == "success" else "failed")
    expected_requests = 7 if outcome == "contract" else 3
    assert len(runtime.requests) == expected_requests
    correction = [json.loads(request.content) for request in runtime.requests[1:]]
    assert correction[0] == correction[1]
    assert all(body["model"] == "fixture-model" for body in correction)
    with runtime.repo.db.connection() as c:
        calls = c.execute("SELECT * FROM h2_model_calls ORDER BY rowid").fetchall()
        charges = c.execute("SELECT * FROM h2_budget_entries WHERE kind='charge'").fetchall()
        contexts = c.execute("SELECT * FROM h2_artifacts WHERE kind='context'").fetchall()
        assert c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='refund'").fetchone()[0] == 0
    assert len(calls) == len(charges) == expected_requests
    assert len({call["attempt_id"] for call in calls}) == 1
    assert len({call["budget_id"] for call in calls}) == 1
    assert len({call["model_snapshot_ref"] for call in calls}) == 1
    assert calls[0]["error_code"] == "contract_error" and calls[1]["error_code"] == "transport_error"
    assert calls[1]["status"] == "unknown"
    assert {charge["call_id"] for charge in charges} == {call["call_id"] for call in calls}
    assert all(charge["delta_calls"] == -1 for charge in charges)
    records = [json.loads(runtime.repo.artifacts.read(row)) for row in contexts]
    retry = next(item for item in records if item.get("schema_version") == "finance.h2.action_repair_transport.v1")
    assert retry["failed_call_id"] == calls[1]["call_id"] and retry["retry_number"] == 1
    assert retry["failed_output_ref"] is None
    if outcome == "success":
        linked = next(item for item in records if item.get("correction_call_id") == calls[2]["call_id"])
        assert linked["transport_retry_ref"]
    else:
        assert status["attempts"][-1]["error_code"] == (
            "transport_error" if outcome == "transport" else "contract_error"
        )
        assert len(status["attempts"]) == 1
        assert runtime.repo.reconcile()["changed_task_ids"] == []


def test_correction_transport_retry_cannot_spend_root_closing_reserve(tmp_path):
    runtime = _runtime(
        tmp_path, lambda _state: {"type": "not-an-action"}, transport_failures=(2,),
        config=SchedulerConfig(budget_policy="hierarchical", root_calls=3, root_reserve_calls=1),
    )
    asyncio.run(_cycle(runtime))
    status = runtime.service.status(runtime.handle)["tasks"][0]
    assert status["status"] == "failed"
    assert status["attempts"][-1]["error_code"] == "budget_denied"
    assert len(runtime.requests) == 2 and len(status["attempts"]) == 1
    assert status["budget"]["consumed"] == 2 and status["budget"]["local_available"] == 1
    with runtime.repo.db.connection() as c:
        assert c.execute("SELECT count(*) FROM h2_model_calls").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='charge'").fetchone()[0] == 2


async def _cycle(runtime):
    # Publish one priority-selected delivery so one worker can progress the parent first.
    runtime.service.publish_once(limit=1)
    assert await runtime.service.worker_once(block_ms=0) == 1
    if runtime.mock_errors:
        raise runtime.mock_errors[0]


async def _finish(runtime):
    for _ in range(60):
        await _cycle(runtime)
        task = runtime.service.status(runtime.handle)["tasks"][0]
        if task["status"] in {"succeeded", "partial", "failed", "cancelled", "timed_out"}:
            assert task["status"] == "succeeded", task
            return task
    pytest.fail("fixture action loop did not terminate within 60 deliveries")


def _artifact(runtime, ref):
    with runtime.repo.db.connection() as connection:
        row = connection.execute(
            "SELECT * FROM h2_artifacts WHERE artifact_ref=?", (ref,),
        ).fetchone()
    assert row is not None, ref
    return json.loads(runtime.repo.artifacts.read(row))


def _tasks(runtime):
    with runtime.repo.db.connection() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT t.*,h.role,h.depth,h.parent_thread_id,h.permission_snapshot_ref "
            "FROM h2_tasks t JOIN h2_threads h ON h.thread_id=t.thread_id ORDER BY h.depth,t.rowid",
        )]


def _events(runtime, name):
    with runtime.repo.db.connection() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM h2_events WHERE event_type=? ORDER BY event_id", (name,),
        )]


def _assert_ledger(runtime, count, *, corrections=0):
    assert len(runtime.requests) == count
    for request in runtime.requests:
        assert str(request.url) == "https://fixture.invalid/v1/chat/completions"
        assert json.loads(request.content)["model"] == "fixture-model"
    with runtime.repo.db.connection() as connection:
        calls = connection.execute("SELECT * FROM h2_model_calls ORDER BY rowid").fetchall()
        charges = connection.execute(
            "SELECT * FROM h2_budget_entries WHERE kind='charge'",
        ).fetchall()
        assert connection.execute(
            "SELECT count(*) FROM h2_budget_entries WHERE kind='refund'",
        ).fetchone()[0] == 0
        policies = connection.execute(
            "SELECT payload_json FROM h2_snapshots WHERE kind='model_policy'",
        ).fetchall()
    assert len(calls) == len(charges) == count
    per_attempt = Counter(row["attempt_id"] for row in calls)
    assert len(per_attempt) == count - corrections
    assert all(total <= min(6, 1 + corrections) for total in per_attempt.values())
    assert sum(total > 1 for total in per_attempt.values()) == bool(corrections)
    if corrections:
        assert sum(total == 1 + corrections for total in per_attempt.values()) == 1
    assert {row["status"] for row in calls} == {"succeeded"}
    assert {row["call_id"] for row in charges} == {row["call_id"] for row in calls}
    assert all(row["delta_calls"] == -1 for row in charges)
    assert all(json.loads(row["usage_json"])["total_tokens"] == 20 for row in calls)
    assert policies
    assert all(json.loads(row[0])["auto_fallback"] is False for row in policies)
    assert all(json.loads(row[0])["selected_profile"] == "fixture" for row in policies)


def _assert_compact_checkpoints(runtime):
    with runtime.repo.db.connection() as connection:
        checkpoints = connection.execute("SELECT state_ref FROM h2_checkpoints").fetchall()
    assert checkpoints
    for row in checkpoints:
        checkpoint = _artifact(runtime, row["state_ref"])
        state = _artifact(runtime, checkpoint["context_ref"])
        encoded = json.dumps(state)
        assert "SOURCE_BODY_" not in encoded
        assert "prepared_action_ref" not in state["execution"] or isinstance(
            state["execution"]["prepared_action_ref"], str,
        )
        assert "observations" not in state["execution"]
        assert len(encoded.encode()) < 128 * 1024


@pytest.mark.parametrize("old_limit", [5, 10])
def test_reindexed_source_read_progress_resumes_without_mixing_versions(tmp_path, monkeypatch, old_limit):
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path)
    company = tmp_path / "data" / "companies" / "finance_observer"
    article_root = company / "articles"
    source = article_root / "Alpha" / "articles" / "2026-10-01-view.md"
    source.parent.mkdir(parents=True)
    old_text = "# Alpha view\n2026-10-01\n" + "\n".join(f"Old condition {i}" for i in range(3, 11))
    source.write_text(old_text, encoding="utf-8")
    corpus = ArticleCorpus("finance_observer", article_root)
    assert corpus.sync()["updated"] == 1
    article = corpus.list_articles(author="Alpha")[0]
    article_id, old_hash = article["article_id"], article["current_hash"]
    new_hash = None

    def decide(state):
        reads = _business_observations(state, "article.read")
        if not reads:
            return _tool("article.read", article_id=article_id, start_line=1, limit=old_limit)
        progress = state["article_states"][0]
        if len(reads) == 1:
            assert progress["current_hash"] == old_hash
            return _tool("article.read", article_id=article_id, start_line=6, limit=5)
        assert progress["current_hash"] == new_hash
        if len(reads) == 2:
            assert progress["read_status"] == "partial"
            assert progress["read_ranges"] == [[6, 10]]
            assert f"article_source_version_changed:{article_id}" in state["state_update_warnings"]
            return _tool("article.read", article_id=article_id, start_line=1, limit=5)
        assert progress["read_status"] == "complete"
        assert progress["read_ranges"] == [[1, 10]]
        assert len(reads) == 3
        assert [read["article"]["current_hash"] for read in reads] == [old_hash, new_hash, new_hash]
        return _root_final(state)

    runtime = _runtime(tmp_path, decide, corpus=corpus)

    def checkpoint_state():
        with runtime.repo.db.connection() as connection:
            ref = connection.execute(
                "SELECT state_ref FROM h2_checkpoints WHERE task_id=? ORDER BY checkpoint_version DESC",
                (runtime.handle.task_id,),
            ).fetchone()[0]
        return _artifact(runtime, _artifact(runtime, ref)["context_ref"])

    def restart_service():
        runtime.service = FinanceHarnessV2Service.for_finance_corpus(
            runtime.repo, runtime.transport,
            corpus=ArticleCorpus("finance_observer", article_root),
            model_adapter=runtime.service.model_adapter, worker_id="resumed-worker",
        )

    asyncio.run(_cycle(runtime))
    first = checkpoint_state()
    assert first["article_states"][0]["read_status"] == ("partial" if old_limit == 5 else "complete")
    old_refs = first["article_states"][0]["material_refs"]

    new_text = old_text.replace("Old condition", "New condition")
    source.write_text(new_text, encoding="utf-8")
    assert corpus.sync()["updated"] == 1
    new_hash = corpus.get_article(article_id)["current_hash"]
    assert old_hash != new_hash
    restart_service()
    asyncio.run(_cycle(runtime))
    resumed = checkpoint_state()
    progress = resumed["article_states"][0]
    assert progress["current_hash"] == new_hash and progress["read_status"] == "partial"
    assert progress["read_ranges"] == [[6, 10]]
    assert set(old_refs) < set(progress["material_refs"])
    assert resumed["execution"]["metadata_status"] == "incomplete_metadata"
    assert first["article_states"][0]["current_hash"] == old_hash
    with corpus.store.connection() as connection:
        versions = connection.execute(
            "SELECT content_hash,content FROM research_article_versions WHERE article_id=?", (article_id,),
        ).fetchall()
    assert {row["content_hash"]: row["content"] for row in versions} == {old_hash: old_text, new_hash: new_text}

    restart_service()
    result = asyncio.run(_finish(runtime))
    assert result["status"] == "succeeded"
    _assert_ledger(runtime, 4)
    _assert_compact_checkpoints(runtime)


@pytest.mark.parametrize("first_valid_limit", [1, 100])
def test_first_valid_read_after_parameter_failure_resumes_without_version_warning(tmp_path, first_valid_limit):
    def decide(state):
        if not _business_observations(state, "article.list"):
            return _tool("article.list", author="Alpha", limit=100)
        reads = _business_observations(state, "article.read")
        if not reads:
            return _tool("article.read", article_id="article_alpha", limit=101)
        assert any(read.get("error", {}).get("code") == "invalid_arguments" for read in reads)
        progress = state["article_states"][0]
        assert "article_source_version_changed:article_alpha" not in state["state_update_warnings"]
        if progress["read_status"] == "failed":
            assert progress.get("current_hash") is None
            assert not progress.get("read_ranges")
            return _tool("article.read", article_id="article_alpha", start_line=1, limit=first_valid_limit)
        assert progress["current_hash"] == "hash-alpha"
        if progress["read_status"] == "partial":
            assert progress["read_ranges"] == [[1, 1]]
            return _tool("article.read", article_id="article_alpha", start_line=2, limit=100)
        assert progress["read_status"] == "complete"
        assert progress["read_ranges"] == [[1, 3]]
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    asyncio.run(_cycle(runtime))
    asyncio.run(_cycle(runtime))
    with runtime.repo.db.connection() as connection:
        state_ref = connection.execute(
            "SELECT state_ref FROM h2_checkpoints WHERE task_id=? ORDER BY checkpoint_version DESC LIMIT 1",
            (runtime.handle.task_id,),
        ).fetchone()[0]
    failed_state = _artifact(runtime, _artifact(runtime, state_ref)["context_ref"])
    assert failed_state["article_states"][0]["read_status"] == "failed"
    assert not runtime.corpus.reads
    runtime.service = FinanceHarnessV2Service.for_finance_corpus(
        runtime.repo, runtime.transport, corpus=runtime.corpus,
        model_adapter=runtime.service.model_adapter, worker_id="resumed-first-read-worker",
    )
    result = asyncio.run(_finish(runtime))
    assert result["status"] == "succeeded"
    assert failed_state["article_states"][0].get("current_hash") is None
    assert runtime.corpus.reads == ["article_alpha"] * (2 if first_valid_limit == 1 else 1)
    assert any(json.loads(event["payload_json"])["status"] == "failed"
               for event in _events(runtime, "tool.completed"))
    assert all(
        "article_source_version_changed:article_alpha" not in state["state_update_warnings"]
        for state in runtime.prompts
    )
    _assert_ledger(runtime, 5 if first_valid_limit == 1 else 4)
    _assert_compact_checkpoints(runtime)


def test_large_evidence_ledger_survives_durable_updates_and_service_restarts(tmp_path):
    expected_bindings = []
    describe_action = {"type": "tool_call", "tool_name": "tool.describe", "arguments": {"name": "article.read"}}

    def decide(state):
        if not state["article_states"]:
            return _tool("article.read", article_id="article_alpha", start_line=1, limit=100)
        source_ref = state["article_states"][0]["material_ref"]
        ledger = state["evidence_ledger"]
        claims = [item for item in ledger if item.get("claim_id")]
        if not claims:
            bindings = [{
                "claim_id": f"claim-{index}", "evidence_ref": source_ref, "relation": "supports",
                "source_kind": "article", "scope": "synthetic source", "certainty": "conditional",
            } for index in range(200)]
            expected_bindings.extend(bindings)
            return {**describe_action,
                    "state_update": {"evidence_additions": bindings}}
        assert claims == expected_bindings
        assert any(item.get("evidence_ref") in state["known_evidence_refs"] and not item.get("claim_id")
                   for item in ledger), "The original successful read binding was lost."
        if len(claims) == 200:
            opposing = {**claims[0], "relation": "contradicts", "scope": "synthetic contrary condition"}
            expected_bindings.append(opposing)
            return {**describe_action,
                    "state_update": {"evidence_additions": [opposing, opposing]}}
        assert len(claims) == 201
        if not state["coverage"]:
            return {**describe_action,
                    "state_update": {"coverage": {"gaps": ["No external verification in this fixture."]}}}
        assert claims[0]["relation"] == "supports" and claims[-1]["relation"] == "contradicts"
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    for _ in range(4):
        asyncio.run(_cycle(runtime))
        runtime.service = FinanceHarnessV2Service.for_finance_corpus(
            runtime.repo, runtime.transport, corpus=runtime.corpus,
            model_adapter=runtime.service.model_adapter, worker_id="resumed-ledger-worker",
        )
    root = asyncio.run(_finish(runtime))
    with runtime.repo.db.connection() as connection:
        result = connection.execute(
            "SELECT sections_json FROM h2_results WHERE task_id=? AND disposition='accepted'",
            (runtime.handle.task_id,),
        ).fetchone()
    saved = _artifact(runtime, json.loads(result["sections_json"])["finance_state"])
    claims = [item for item in saved["evidence_ledger"] if item.get("claim_id")]
    assert claims == expected_bindings and len(saved["evidence_ledger"]) == 202
    assert root["status"] == "succeeded" and runtime.corpus.reads == ["article_alpha"]
    source_ref = claims[0]["evidence_ref"]
    assert _artifact(runtime, source_ref)["payload"]["payload"]["content"].startswith("# Alpha")
    _assert_ledger(runtime, 5)
    _assert_compact_checkpoints(runtime)


def test_root_delivery_exposes_complete_model_envelope_through_result_get(tmp_path):
    metadata = {field: [f"{field}-{index}" for index in range(31)]
                for field in ("key_points", "gaps", "warnings")}

    def decide(state):
        if not state["article_states"]:
            return _tool("article.read", article_id="article_alpha", start_line=1, limit=100)
        final = _root_final(state)
        final["output"].update(metadata)
        return final

    runtime = _runtime(tmp_path, decide)

    async def finish_with_partial_delivery():
        for _ in range(60):
            await _cycle(runtime)
            task = runtime.service.status(runtime.handle)["tasks"][0]
            if task["status"] in {"succeeded", "partial", "failed", "cancelled", "timed_out"}:
                return task
        pytest.fail("fixture action loop did not terminate within 60 deliveries")

    root = asyncio.run(finish_with_partial_delivery())
    summary = runtime.repo.result_get(runtime.service.caller(runtime.handle), root["result_ref"], view="summary")
    assert summary["has_more"] is False
    envelope = json.loads(summary["content"])
    for field, entries in metadata.items():
        assert envelope[field] == entries
    full = runtime.repo.result_get(runtime.service.caller(runtime.handle), root["result_ref"], view="full")
    assert full["content"] == "The assigned work is complete; source claims remain conditional."
    assert envelope["evidence_refs"] == summary["evidence_refs"] and envelope["evidence_refs"]
    with runtime.repo.db.connection() as connection:
        row = connection.execute(
            "SELECT sections_json FROM h2_results WHERE task_id=? AND disposition='accepted'",
            (runtime.handle.task_id,),
        ).fetchone()
    saved = _artifact(runtime, json.loads(row["sections_json"])["finance_state"])
    candidate = _artifact(runtime, saved["candidate_answer"]["artifact_ref"])
    assert all(candidate["output"][field] == entries for field, entries in metadata.items())
    assert saved["article_states"][0]["use_status"] == "used"
    assert root["status"] == "partial"
    _assert_ledger(runtime, 2)
    _assert_compact_checkpoints(runtime)


def test_completed_child_followup_receives_new_task_and_reads_previous_result_explicitly(tmp_path):
    query = "Explain the strongest counterargument to the previous conclusion."
    criteria = ["Distinguish the new counterargument from the old summary."]
    seen_followups = []

    def decide(state):
        if state.get("role", "main") != "main":
            followup = state.get("follow_up_context")
            if followup:
                assert state["query"] == state["objective"] == state["task"] == query
                assert state["success_criteria"] == followup["success_criteria"] == criteria
                prior = followup["previous_result_ref"]
                seen_followups.append(followup)
                if not _scheduler_observations(state, "result.get"):
                    assert prior not in state["known_evidence_refs"]
                    return _tool("result.get", result_ref=prior, view="full")
            return _research_final(state)
        if not _business_observations(state, "article.list"):
            return _tool("article.list", author="Alpha", limit=10)
        if not _business_observations(state, "article.read"):
            return _tool("article.read", article_id="article_alpha", start_line=1, limit=100)
        if not state["scheduled_children"]:
            return _spawn("original-summary", "analyst", state["known_material_refs"])
        original = state["scheduled_children"][0]
        tasks = {task["task_id"]: task for task in _tasks(runtime)}
        if tasks[original["task_id"]]["status"] != "succeeded":
            return _tool("agent.wait", task_ids=[original["task_id"]])
        sends = _scheduler_observations(state, "agent.send")
        if not sends:
            return _tool(
                "agent.send", thread_id=original["thread_id"], idempotency_key=state["next_action_key"],
                content=query, follow_up={
                    "requested_budget": {"model_calls": 6, "wall_time_ms": 300_000},
                    "success_criteria": criteria,
                    "output_contract": {"name": "finance.research_result", "version": "1"},
                },
            )
        followup_task = sends[-1]["payload"]["data"]["task_id"]
        if tasks[followup_task]["status"] != "succeeded":
            return _tool("agent.wait", task_ids=[followup_task])
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    asyncio.run(_finish(runtime))
    tasks = _tasks(runtime)
    original = next(task for task in tasks if task["depth"] == 1 and task["task_type"] != "follow_up")
    followup = next(task for task in tasks if task["task_type"] == "follow_up")
    assert original["thread_id"] == followup["thread_id"]
    assert original["task_id"] != followup["task_id"]
    assert original["status"] == followup["status"] == "succeeded"
    assert seen_followups and all(item == seen_followups[0] for item in seen_followups)
    assert seen_followups[0]["previous_result_ref"] == original["result_ref"]
    reads = [json.loads(event["payload_json"]) for event in _events(runtime, "result.full_viewed")]
    assert len([read for read in reads if read["reader_task_id"] == followup["task_id"]
                and read["result_ref"] == original["result_ref"]]) == 1
    _assert_ledger(runtime, len(runtime.requests))
    _assert_compact_checkpoints(runtime)


def test_root_spawns_twice_and_reads_while_both_children_pending_on_one_worker(tmp_path):
    pending_snapshots = []

    def decide(state):
        if state.get("role", "main") != "main":
            assert state["role"] in {"verifier", "skeptic"}
            assert state["depth"] == 1
            assert state["success_criteria"] == ["Use only granted sources and disclose unread material"]
            assert state["output_contract"] == {"name": "finance.research_result", "version": "1"}
            return _research_final(state)
        if not _business_observations(state, "article.list"):
            return _tool("article.list", author="Alpha", limit=10)
        children = state["scheduled_children"]
        if len(children) < 2:
            return _spawn(
                f"independent-{len(children)}", ("verifier", "skeptic")[len(children)],
                state["known_material_refs"],
            )
        if not _business_observations(state, "article.read"):
            pending_snapshots.append(_tasks(runtime))
            assert not _events(runtime, "task.waiting")
            return _tool("article.read", article_id="article_alpha", start_line=1, limit=100)
        if len(_child_results(state)) < 2:
            pending_snapshots.append(_tasks(runtime))
            return _tool("agent.wait", task_ids=[child["task_id"] for child in children], mode="all")
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    root = asyncio.run(_finish(runtime))

    assert len(pending_snapshots) == 2
    for snapshot in pending_snapshots:
        children = [row for row in snapshot if row["depth"] == 1]
        assert len(children) == 2
        assert all(row["status"] in {"pending_dispatch", "queued"} for row in children)
    tasks = _tasks(runtime)
    assert len(tasks) == 3
    assert {row["role"] for row in tasks} == {"main", "verifier", "skeptic"}
    assert all(row["status"] == "succeeded" for row in tasks)
    assert [item.get("role", "main") for item in runtime.prompts[:5]] == ["main"] * 5
    assert len(root["attempts"]) == 6
    assert [attempt["status"] for attempt in root["attempts"]] == ["suspended"] * 5 + ["succeeded"]
    assert len(_events(runtime, "task.continued")) == 4
    assert runtime.corpus.reads == ["article_alpha"]
    with runtime.repo.db.connection() as connection:
        waits = connection.execute("SELECT * FROM h2_waits").fetchall()
        permissions = connection.execute(
            "SELECT payload_json FROM h2_snapshots WHERE kind='permission'",
        ).fetchall()
    assert len(waits) == 1 and waits[0]["owner_task_id"] == runtime.handle.task_id
    permission_tools = [set(json.loads(row[0])["tools"]) for row in permissions]
    assert set(ROOT_TOOLS) in permission_tools
    assert all(tools <= set(ROOT_TOOLS) for tools in permission_tools)
    _assert_ledger(runtime, 8)
    _assert_compact_checkpoints(runtime)


@pytest.mark.parametrize("calls,wall_ms,accepted", [
    (17, 780000, True), (9999, 780000, False), (17, 3600000, False),
])
def test_model_selected_spawn_budget_is_exact_or_explicitly_denied(tmp_path, calls, wall_ms, accepted):
    def decide(state):
        assert state["delegation_policy"]["preferred_tool"] == "agent.spawn"
        if state.get("role", "main") != "main":
            return _research_final(state)
        assert state["task_budget"]["budget"]["consumed"] == len(runtime.requests) - 1
        observed = _scheduler_observations(state, "agent.spawn")
        if not observed:
            return _spawn("explicit-budget", "analyst", [], calls=calls, wall_ms=wall_ms)
        if accepted:
            assert observed[0]["status"] == "succeeded"
            assert observed[0]["payload"]["data"]["allocated_budget"] == {
                "model_calls": calls, "wall_time_ms": wall_ms,
            }
        else:
            assert observed[0]["status"] == "failed"
            assert observed[0]["payload"]["error"]["code"] == "budget_denied"
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    asyncio.run(_finish(runtime))
    with runtime.repo.db.connection() as connection:
        budgets = connection.execute(
            "SELECT * FROM h2_budgets WHERE parent_budget_id IS NOT NULL",
        ).fetchall()
        children = connection.execute("SELECT * FROM h2_threads WHERE depth=1").fetchall()
    if accepted:
        assert len(budgets) == len(children) == 1
        assert budgets[0]["requested_calls"] == budgets[0]["allocated_calls"] == calls
        assert budgets[0]["deadline_at"] - budgets[0]["created_at"] == wall_ms
    else:
        assert not budgets and not children
    _assert_ledger(runtime, 2)


def test_research_children_continue_to_depth_two_without_article_tool_threads(tmp_path):
    depth_failure_seen, scope_failure_seen = [], []
    research_clock = {"today": "2026-10-19", "timezone": "Asia/Shanghai"}

    def decide(state):
        assert state["research_clock"] == research_clock
        role = state.get("role", "main")
        if role == "main":
            if not state["article_states"]:
                return _tool("article.list", author="Alpha", limit=10)
            if not state["scheduled_children"]:
                return _spawn("nested-analyst", "analyst", state["known_material_refs"], calls=30)
            if not _child_results(state):
                return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
            return _root_final(state)
        assert state["task"] == state["objective"]
        assert "evidence_links" in state["output_json_schema"]["required"]
        expected_article_tools = {"article.list", "article.search", "article.read", "article.claims"}
        assert set(state["allowed_article_tools"]) == expected_article_tools
        if role == "analyst":
            assert state["depth"] == 1
            if not state["article_states"]:
                assert len(state["material_refs"]) == 1, "normal Spawn lost its first metadata grant"
                assert "article_alpha" in json.dumps(state["materials"])
                assert "SOURCE_BODY_ALPHA" not in json.dumps(state["materials"])
                return _tool("article.list", limit=10)
            listed = _business_observations(state, "article.list")[0]["articles"]
            assert [row["article_id"] for row in listed] == ["article_alpha"]
            reads = _business_observations(state, "article.read")
            if not reads:
                return _tool("article.read", article_id="article_alpha", start_line=1, limit=100)
            if len(reads) == 1:
                assert reads[0]["content"].startswith("# Alpha\nSOURCE_BODY_ALPHA")
                return _tool("article.read", article_id="article_beta", start_line=1, limit=100)
            assert reads[-1]["ok"] is False
            assert reads[-1]["error"]["code"] == "forbidden"
            scope_failure_seen.append(reads[-1])
            if not state["scheduled_children"]:
                return _spawn(
                    "nested-verifier", "verifier", state["known_material_refs"], wall_ms=120_000,
                )
            if not _child_results(state):
                return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
            return _research_final(state)
        assert role == "verifier" and state["depth"] == 2
        if not state["article_states"]:
            return _tool("article.read", article_id="article_alpha", start_line=1, limit=100)
        failed = _scheduler_observations(state, "agent.spawn")
        if not failed:
            return _spawn("forbidden-depth-three", "skeptic", state["known_material_refs"], calls=1)
        assert failed[0]["status"] == "failed"
        assert failed[0]["payload"]["error"]["code"] == "depth_exceeded"
        depth_failure_seen.append(failed[0])
        return _research_final(state)

    runtime = _runtime(tmp_path, decide, research_clock=research_clock)
    asyncio.run(_finish(runtime))

    tasks = _tasks(runtime)
    assert [(row["depth"], row["role"]) for row in tasks] == [
        (0, "main"), (1, "analyst"), (2, "verifier"),
    ]
    assert all(row["status"] == "succeeded" for row in tasks)
    assert tasks[1]["parent_task_id"] == tasks[0]["task_id"]
    assert tasks[2]["parent_task_id"] == tasks[1]["task_id"]
    assert scope_failure_seen and len(depth_failure_seen) == 1
    assert runtime.corpus.reads == ["article_alpha", "article_alpha"]
    with runtime.repo.db.connection() as connection:
        children = connection.execute(
            "SELECT full_ref FROM h2_results WHERE task_id<>? AND disposition='accepted'",
            (runtime.handle.task_id,),
        ).fetchall()
        attempts = connection.execute("SELECT status,error_code FROM h2_attempts").fetchall()
        waits = connection.execute("SELECT owner_task_id FROM h2_waits").fetchall()
    assert len(children) == 2
    assert {row[0] for row in waits} == {tasks[0]["task_id"], tasks[1]["task_id"]}
    assert all(row["status"] in {"suspended", "succeeded"} and row["error_code"] is None for row in attempts)
    for row in children:
        result = _artifact(runtime, row["full_ref"])
        assert result["schema_version"] == "finance.h2.analyst_result.v1"
        assert result["result"]["schema_version"] == "finance.research_result.v1"
        assert result["result"]["evidence_links"]
    _assert_ledger(runtime, 13)
    _assert_compact_checkpoints(runtime)


@pytest.mark.parametrize("target,code", [
    ("empty", "invalid_arguments"), ("unknown", "not_found"), ("self", "invalid_arguments"),
])
def test_invalid_scheduler_wait_is_a_failed_observation_not_a_host_failure(tmp_path, target, code):
    def decide(state):
        failed = _scheduler_observations(state, "agent.wait")
        if not failed:
            ids = [] if target == "empty" else [
                runtime.handle.task_id if target == "self" else "unknown-task",
            ]
            return _tool("agent.wait", task_ids=ids)
        assert len(failed) == 1 and failed[0]["status"] == "failed"
        assert failed[0]["payload"]["error"]["code"] == code
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    root = asyncio.run(_finish(runtime))

    assert len(_tasks(runtime)) == 1
    assert [attempt["status"] for attempt in root["attempts"]] == ["suspended", "succeeded"]
    with runtime.repo.db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM h2_waits").fetchone()[0] == 0
    assert len(_events(runtime, "task.continued")) == 1
    _assert_ledger(runtime, 2)


@pytest.mark.parametrize("notify_first", [False, True])
def test_model_selected_result_pages_complete_read_audit_across_action_attempts(tmp_path, notify_first):
    source_body = "Independent public analysis. " * 1000
    result = {}

    def decide(state):
        pages = _scheduler_observations(state, "result.get")
        notifications = [item for item in state["normalized_observations"]
                         if item.get("result_ref") == result["result_ref"]]
        if notify_first and not notifications:
            return _tool("agent.wait", task_ids=[child["task_id"]])
        if not pages:
            if notify_first:
                assert len(notifications) == 1
                assert notifications[0]["full_read"] is False
                assert notifications[0]["summary"]["summary"] == "Independent source fixture."
                assert _events(runtime, "result.full_viewed") == []
                full_pages = [json.loads(row["payload_json"]) for row in _events(runtime, "result.page_viewed")]
                assert all(page["view"] != "full" for page in full_pages)
                assert state["materials"] == []
            return _tool("result.get", result_ref=result["result_ref"], view="full", limit=8192)
        if len(pages) == 1:
            assert pages[0]["payload"]["has_more"] is True
            return _tool("result.get", result_ref=result["result_ref"], view="full",
                         offset=pages[0]["payload"]["next_offset"], limit=64000)
        assert len(pages) == 2
        assert "".join(page["payload"]["content"] for page in pages) == source_body
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    internal_request = normalize_agent_spawn(
        _spawn("fixture-source", "analyst", [])["arguments"],
        dispatch_key="fixture-source",
        task_budget={"budget": {"policy": "requested"}},
        rules=load_rules(),
    )
    child = runtime.repo.spawn(
        runtime.service.caller(runtime.handle), Spawn.model_validate(internal_request, strict=True),
    )["data"]
    with runtime.repo.db.connection() as c:
        payload = json.loads(c.execute(
            "SELECT payload_json FROM h2_outbox WHERE task_id=?", (child["task_id"],)
        ).fetchone()[0])
    lease, ack_safe = runtime.repo.claim(payload, "fixture-source-writer")
    assert lease is not None and ack_safe is False
    runtime.repo.start(lease)
    result.update(runtime.repo.submit(lease, Submission(
        result_key="fixture-source", content=source_body, summary="Independent source fixture.",
    )))
    root = asyncio.run(_finish(runtime))
    pages = [json.loads(row["payload_json"]) for row in _events(runtime, "result.page_viewed")]
    pages = [page for page in pages if page["view"] == "full"]
    completed = [json.loads(row["payload_json"]) for row in _events(runtime, "result.full_viewed")]
    assert len(pages) == 2 and len(completed) == 1
    assert pages[0]["reader_attempt_id"] != pages[1]["reader_attempt_id"]
    assert completed[0]["reader_attempt_id"] == pages[1]["reader_attempt_id"]
    assert completed[0]["reader_task_id"] == runtime.handle.task_id
    assert completed[0]["reader_kind"] == "agent"
    assert [item["status"] for item in root["attempts"]] == ["suspended"] * (2 + int(notify_first)) + ["succeeded"]
    _assert_ledger(runtime, 3 + int(notify_first))


def test_two_call_child_exhaustion_is_reported_truthfully_to_parent(tmp_path):
    observed_failure = []

    def decide(state):
        if state.get("role", "main") != "main":
            if not _business_observations(state, "tool.describe"):
                return {"type": "tool_call", "tool_name": "tool.describe", "arguments": {"name": "article.read"}}
            return _tool("article.read", article_id="article_alpha", start_line=1, limit=100)
        if not state["article_states"]:
            return _tool("article.list", author="Alpha", limit=10)
        if not state["scheduled_children"]:
            return _spawn("two-call-child", "analyst", state["known_material_refs"], calls=2)
        failed = [item for item in state["observations"] if item.get("status") == "failed"]
        if not failed:
            return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
        observed_failure.extend(failed)
        assert "budget_denied" in json.dumps(failed)
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    asyncio.run(_finish(runtime))
    root, child = _tasks(runtime)
    assert root["status"] == "succeeded" and child["status"] == "failed"
    assert child["result_ref"] is None and observed_failure
    assert runtime.corpus.reads == ["article_alpha"]
    with runtime.repo.db.connection() as c:
        budget = c.execute("SELECT * FROM h2_budgets WHERE budget_id=?", (child["budget_ref"],)).fetchone()
        calls = c.execute("SELECT * FROM h2_model_calls WHERE task_id=?", (child["task_id"],)).fetchall()
        attempts = c.execute(
            "SELECT status,error_code FROM h2_attempts WHERE task_id=? ORDER BY attempt_no",
            (child["task_id"],),
        ).fetchall()
        balance = runtime.repo._balance(c, root["budget_ref"])
        refunds = c.execute("SELECT count(*) FROM h2_budget_entries WHERE kind='refund'").fetchone()[0]
    assert budget["requested_calls"] == budget["allocated_calls"] == len(calls) == 2
    assert [(a["status"], a["error_code"]) for a in attempts] == [
        ("suspended", None), ("suspended", None), ("failed", "budget_denied"),
    ]
    assert balance == 600 - len(runtime.requests) and balance >= runtime.repo.config.root_reserve_calls
    assert refunds == 0
    _assert_ledger(runtime, len(runtime.requests))


@pytest.mark.parametrize("mode", ["bare", "inner_anchor", "two_bad", "two_bad_spare", "exhausted"])
def test_child_repair_uses_same_attempt_model_and_exact_grant(tmp_path, mode):
    feedbacks, failures = [], []

    def decide(state):
        if state.get("role", "main") != "main":
            if not state["article_states"]:
                return _tool("article.read", article_id="article_alpha", start_line=1, limit=100)
            result = _research_final(state)
            feedback = state.get("fixture_repair_feedback")
            if feedback:
                feedbacks.append(feedback)
                assert feedback["final_output_schema_scope"] == "final.output"
                if mode == "inner_anchor":
                    assert feedback["input_policy"] == "finance.h2.immutable_anchor_correction.v1"
                    assert "anchors" in feedback["final_output_json_schema"]["required"]
                    schema = feedback["action_json_schema"]
                    assert set(schema["properties"]) == set(schema["required"]) == {"type", "output"}
                    assert schema["properties"]["type"]["const"] == "final"
                    assert schema["additionalProperties"] is False
                    assert feedback["action_example"] == {
                        "type": "final", "output": {"schema_version": "finance.h2.anchor_patch.v1", "anchors": []},
                    }
                    assert feedback["untrusted_draft"]["content"] == result["output"]["content"]
                    return {"type": "final", "output": {
                        "schema_version": "finance.h2.anchor_patch.v1",
                        "anchors": [{"link_index": 0, "text": result["output"]["content"]}],
                    }}
                assert "evidence_links" in feedback["final_output_json_schema"]["required"]
                assert "failed_public_response" not in feedback
                assert feedback["input_policy"] == "finance.h2.isolated_correction.v1"
                if mode != "bare":
                    assert feedback["validation_stage"] == "final.output"
                    assert "verbatim" in feedback["validation_errors"][0]["msg"]
                if mode not in {"two_bad", "two_bad_spare"}:
                    return result
            if mode == "bare":
                return result["output"]
            result["output"]["evidence_links"][0]["text"] = "Anchor missing from the public analysis."
            if mode in {"two_bad", "two_bad_spare"} and not feedback:
                # This is not anchor-only: keep testing the unchanged isolated recovery branch.
                result["output"]["supporting_artifact_refs"] = ["duplicate", "duplicate"]
            return result
        if not state["article_states"]:
            return _tool("article.list", author="Alpha", limit=10)
        if not state["scheduled_children"]:
            return _spawn("repair-child", "analyst", state["known_material_refs"],
                          calls=2 if mode == "exhausted" else 10 if mode == "two_bad_spare" else 7)
        failed = [item for item in state["observations"] if item.get("status") == "failed"]
        if failed:
            failures.extend(failed)
            expected = "budget_denied" if mode == "exhausted" else "contract_error"
            assert expected in json.dumps(failed)
            if mode == "two_bad_spare":
                assert failed[0]["attempts"][-1]["error_code"] == "contract_error"
                detail = failed[0]["failure_diagnostic"]
                assert detail["validation_stage"] == "final.output"
                assert "verbatim" in detail["validation_errors"][0]["msg"]
                assert "input" not in detail["validation_errors"][0]
                budget = failed[0]["budget"]
                assert budget["requested"] == budget["allocated"] == 10
                assert budget["consumed"] == 7
                assert budget["local_available"] == budget["remaining"] == 0
                assert state["task_budget"]["budget"]["local_available"] > 24
        elif not _child_results(state):
            return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    asyncio.run(_finish(runtime))
    root, child = _tasks(runtime)
    assert root["status"] == "succeeded"
    assert child["status"] == ("failed" if mode in {"two_bad", "two_bad_spare", "exhausted"} else "succeeded")
    assert bool(failures) == (mode in {"two_bad", "two_bad_spare", "exhausted"})
    expected_corrections = 0 if mode == "exhausted" else 5 if mode in {"two_bad", "two_bad_spare"} else 1
    assert len(feedbacks) == expected_corrections
    assert runtime.corpus.reads == ["article_alpha"]
    with runtime.repo.db.connection() as c:
        calls = c.execute("SELECT * FROM h2_model_calls WHERE task_id=? ORDER BY rowid",
                          (child["task_id"],)).fetchall()
        charges = c.execute("SELECT * FROM h2_budget_entries WHERE budget_id=? AND kind='charge'",
                            (child["budget_ref"],)).fetchall()
        outputs = c.execute("SELECT * FROM h2_artifacts WHERE kind='context'").fetchall()
        accepted = c.execute("SELECT count(*) FROM h2_results WHERE task_id=? AND disposition='accepted'",
                             (child["task_id"],)).fetchone()[0]
    assert len(calls) == len(charges) == 2 + expected_corrections
    assert calls[0]["attempt_id"] != calls[1]["attempt_id"]
    if len(calls) > 2:
        assert len({call["attempt_id"] for call in calls[1:]}) == 1
        assert len({call["model_snapshot_ref"] for call in calls[1:]}) == 1
    assert all(call["budget_id"] == child["budget_ref"] and call["status"] == "succeeded" for call in calls)
    assert calls[1]["error_code"] == ("contract_error" if mode == "bare" else None)
    assert accepted == (0 if failures else 1)
    repairs = [json.loads(runtime.repo.artifacts.read(row)) for row in outputs]
    initial_repair = next(item for item in repairs if item.get("original_call_id") == calls[1]["call_id"])
    assert initial_repair["validation_stage"] == ("action" if mode == "bare" else "final.output")
    assert initial_repair["limit"] == 5
    if not failures:
        assert any(item.get("correction_call_id") == calls[2]["call_id"] for item in repairs)
    assert {json.loads(request.content)["model"] for request in runtime.requests} == {"fixture-model"}
    _assert_ledger(runtime, len(runtime.requests), corrections=expected_corrections)


def test_committed_spawn_recovers_prepared_action_before_another_root_decision(tmp_path, monkeypatch):
    spawn_calls = []
    receipts = []

    def decide(state):
        if state.get("role", "main") != "main":
            return _research_final(state)
        if not state["scheduled_children"]:
            return _spawn("crash-window-child", "analyst", [])
        assert len(spawn_calls) == 2, "Root decided before the prepared action was replayed"
        observed = _scheduler_observations(state, "agent.spawn")
        assert len(observed) == 1 and observed[0]["payload"] == receipts[1]
        if not _child_results(state):
            return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    original_spawn = runtime.repo.spawn

    def crash_after_commit(caller, request):
        spawn_calls.append(request.model_dump(mode="json"))
        receipt = original_spawn(caller, request)
        receipts.append(receipt)
        if len(spawn_calls) == 1:
            raise sqlite3.OperationalError("fixture crash after spawn commit before receipt save")
        return receipt

    monkeypatch.setattr(runtime.repo, "spawn", crash_after_commit)
    with pytest.raises(sqlite3.OperationalError, match="after spawn commit"):
        asyncio.run(_cycle(runtime))

    assert len(_tasks(runtime)) == 2
    assert runtime.service.status(runtime.handle)["tasks"][0]["status"] == "running"
    assert runtime.transport.client.xpending(runtime.transport.stream, runtime.transport.group)["pending"] == 1
    _assert_ledger(runtime, 1)
    with runtime.repo.db.connection() as connection:
        checkpoint_ref = connection.execute(
            "SELECT state_ref FROM h2_checkpoints WHERE task_id=? ORDER BY checkpoint_version DESC",
            (runtime.handle.task_id,),
        ).fetchone()[0]
    checkpoint = _artifact(runtime, checkpoint_ref)
    prepared_state = _artifact(runtime, checkpoint["context_ref"])
    prepared = _artifact(runtime, prepared_state["execution"]["prepared_action_ref"])
    assert prepared["tool_name"] == "agent.spawn"
    recovered_action = AgentSpawnInput.model_validate(prepared["arguments"], strict=True)
    assert recovered_action.task_name == "crash-window-child"
    assert recovered_action.profile == "analyst"
    assert len(spawn_calls) == 1
    assert len(receipts) == 1 and receipts[0]["replayed"] is False
    assert not prepared_state["execution"].get("action_observation_refs")

    runtime.clock.value += runtime.repo.config.lease_ms + 1
    asyncio.run(runtime.service.recover_once(idle_ms=0))
    assert runtime.transport.client.xpending(runtime.transport.stream, runtime.transport.group)["pending"] == 0
    asyncio.run(_cycle(runtime))

    assert len(spawn_calls) == 2 and spawn_calls[0] == spawn_calls[1]
    assert receipts[0]["replayed"] is False and receipts[1]["replayed"] is True
    assert {key: value for key, value in receipts[0].items() if key != "replayed"} == {
        key: value for key, value in receipts[1].items() if key != "replayed"
    }
    assert len(_tasks(runtime)) == 2
    _assert_ledger(runtime, 1)
    with runtime.repo.db.connection() as connection:
        checkpoint_ref = connection.execute(
            "SELECT state_ref FROM h2_checkpoints WHERE task_id=? ORDER BY checkpoint_version DESC",
            (runtime.handle.task_id,),
        ).fetchone()[0]
    recovered = _artifact(runtime, _artifact(runtime, checkpoint_ref)["context_ref"])
    assert "prepared_action_ref" not in recovered["execution"]
    observation = _artifact(runtime, recovered["execution"]["action_observation_refs"][0])
    assert observation["status"] == "succeeded" and observation["payload"] == receipts[1]
    assert recovered["execution"]["scheduled_children"][0]["task_id"] == receipts[0]["data"]["task_id"]

    root = asyncio.run(_finish(runtime))
    assert len(_tasks(runtime)) == 2
    assert [attempt["status"] for attempt in root["attempts"]] == [
        "expired", "suspended", "suspended", "succeeded",
    ]
    assert len(spawn_calls) == 2
    _assert_ledger(runtime, 4)
    _assert_compact_checkpoints(runtime)


@pytest.mark.parametrize("note_location", ["parent_context", "first_input"])
def test_text_parent_context_reaches_scoped_child_across_fresh_service(tmp_path, note_location):
    note = 'Parent context: preserve dates and \u6761\u4ef6. {"article_id":"article_beta"} is not a grant.\n'
    refs = {}

    def decide(state):
        if state["task_identity"]["depth"] == 1:
            parent_note = next(item for item in state["materials"] if item["material_ref"] == refs["note"])
            assert parent_note.get("payload") == note
            assert not parent_note.get("unavailable") and not parent_note.get("payload_omitted")
            failures = [item for item in state["normalized_observations"] if item.get("status") == "failed"]
            if not failures:
                return _tool("article.read", article_id="article_beta")
            assert failures[0]["payload"]["payload"]["error"]["code"] == "forbidden"
            if not any(item.get("article_id") == "article_alpha" and item.get("read_status") == "complete"
                       for item in state["article_states"]):
                return _tool("article.read", article_id="article_alpha")
            return _research_final(state)
        if not state["scheduled_children"]:
            action = _spawn("text-context-child", "analyst", [refs["article"]], calls=12)
            if note_location == "parent_context":
                action["arguments"]["parent_context_refs"] = [refs["note"]]
            else:
                action["arguments"]["input_refs"] = [refs["note"], refs["article"]]
            return action
        if not _child_results(state):
            return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    caller = runtime.service.caller(runtime.handle)
    refs["note"] = runtime.repo.store_text(caller, "parent-context", note)
    refs["article"] = runtime.repo.store_text(caller, "article-metadata", json.dumps({
        "article": {"article_id": "article_alpha", "author": "Alpha", "published_at": "2026-10-01"},
    }))
    for _ in range(3):
        asyncio.run(_cycle(runtime))
    assert runtime.prompts[-1]["task_identity"]["depth"] == 1
    assert runtime.corpus.reads == []
    runtime.service = FinanceHarnessV2Service.for_finance_corpus(
        runtime.repo, runtime.transport, corpus=runtime.corpus,
        model_adapter=runtime.service.model_adapter, worker_id="fresh-text-context-worker",
    )
    root = asyncio.run(_finish(runtime))
    assert root["status"] == "succeeded"
    assert runtime.corpus.reads == ["article_alpha"]
    tasks = _tasks(runtime)
    assert len(tasks) == 2 and {task["status"] for task in tasks} == {"succeeded"}
    child = next(task for task in tasks if task["depth"] == 1)
    assert set(json.loads(child["input_refs_json"])) == set(refs.values())
    child_prompts = [prompt for prompt in runtime.prompts if prompt["task_identity"]["depth"] == 1]
    assert len(child_prompts) == 3
    assert len({prompt["task_identity"]["attempt_id"] for prompt in child_prompts}) == 3
    with runtime.repo.db.connection() as connection:
        artifact = connection.execute("SELECT * FROM h2_artifacts WHERE artifact_ref=?", (refs["note"],)).fetchone()
    assert runtime.repo.artifacts.read(artifact) == note.encode("utf-8")
    _assert_ledger(runtime, 6)
    _assert_compact_checkpoints(runtime)


@pytest.mark.parametrize("read_limit", [1, 100])
@pytest.mark.parametrize("long_result", [False, True])
@pytest.mark.parametrize("cite_child", [False, True])
def test_delegated_source_lineage_survives_return_without_parent_reread(tmp_path, read_limit, long_result, cite_child):
    def decide(state):
        if state["task_identity"]["depth"] == 1:
            if not state["article_states"]:
                return _tool("article.read", article_id="article_alpha", limit=read_limit)
            final = _research_final(state)
            if long_result:
                final["output"]["content"] += " Preserved analysis." * 1000
            return final
        if not state["materials"]:
            return _tool("article.list")
        if not state["scheduled_children"]:
            return _spawn("source-lineage-child", "analyst", state["known_material_refs"])
        results = [item for item in state["observations"] if item.get("result_ref")]
        if not results:
            return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
        assert bool(_child_results(state)) != long_result
        result_ref = results[0]["result_ref"]
        return {"type": "final", "output": {"answer": "The delegated view remains conditional.",
                                               "evidence_refs": [result_ref] if cite_child else []}}

    runtime = _runtime(tmp_path, decide)
    for _ in range(3):
        asyncio.run(_cycle(runtime))
    runtime.service = FinanceHarnessV2Service.for_finance_corpus(
        runtime.repo, runtime.transport, corpus=runtime.corpus,
        model_adapter=runtime.service.model_adapter, worker_id="resumed-lineage-worker",
    )
    root = asyncio.run(_finish(runtime))
    assert root["status"] == "succeeded" and runtime.corpus.reads == ["article_alpha"]
    with runtime.repo.db.connection() as connection:
        result = connection.execute(
            "SELECT sections_json FROM h2_results WHERE task_id=? AND disposition='accepted'",
            (runtime.handle.task_id,),
        ).fetchone()
    saved = _artifact(runtime, json.loads(result["sections_json"])["finance_state"])
    articles = {item["article_id"]: item for item in saved["article_states"]}
    alpha, beta = articles["article_alpha"], articles["article_beta"]
    assert alpha["read_status"] == ("partial" if read_limit == 1 else "complete")
    assert alpha["read_ranges"] == [[1, 1 if read_limit == 1 else 3]]
    assert alpha["author"] == "Alpha" and alpha["published_at"] == "2026-10-01"
    assert alpha["current_hash"] == "hash-alpha"
    assert alpha["use_status"] == ("used" if cite_child else "undecided")
    child = next(task for task in _tasks(runtime) if task["depth"] == 1)
    assert alpha["task_id"] == child["task_id"] != runtime.handle.task_id
    assert child["result_ref"] in alpha["result_refs"]
    with runtime.repo.db.connection() as connection:
        full_views = connection.execute(
            "SELECT count(*) FROM h2_events WHERE event_type='result.full_viewed' "
            "AND json_extract(payload_json,'$.reader_task_id')=? "
            "AND json_extract(payload_json,'$.result_ref')=?",
            (runtime.handle.task_id, child["result_ref"]),
        ).fetchone()[0]
    assert full_views == (0 if long_result else 1)
    assert beta["read_status"] == "unread" and beta["use_status"] == "undecided"
    assert saved["review"] is None and saved["coverage"] == {}
    _assert_ledger(runtime, 6)
    _assert_compact_checkpoints(runtime)


@pytest.mark.parametrize("binding", ["wrapper", "result"])
def test_depth_two_source_lineage_preserves_original_reader_without_ancestor_rereads(tmp_path, binding):
    private_refs = set()

    def decide(state):
        depth = state["task_identity"]["depth"]
        assert all(item["status"] == "succeeded" for item in _scheduler_observations(state, "agent.spawn"))
        if depth == 2:
            if not state["article_states"]:
                return _tool("article.read", article_id="article_alpha")
            private_refs.update(state["article_states"][0]["material_refs"])
            return _research_final(state)
        assert not private_refs.intersection(state["known_evidence_refs"])
        assert not private_refs.intersection(state["known_material_refs"])
        assert all(not private_refs.intersection(item.get("material_refs", [])) for item in state["article_states"])
        if depth == 0 and not state["materials"]:
            return _tool("article.list", author="Alpha")
        if not state["scheduled_children"]:
            child_role = "analyst" if depth == 0 else "verifier"
            return _spawn(f"lineage-depth-{depth + 1}", child_role, state["known_material_refs"],
                          calls=24 if depth == 0 else 8, wall_ms=300_000 if depth == 0 else 120_000)
        if not _child_results(state):
            return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
        if depth == 1 and binding == "result":
            if not _scheduler_observations(state, "result.get"):
                child_ref = next(item["result_ref"] for item in state["observations"] if item.get("result_ref"))
                return _tool("result.get", result_ref=child_ref, view="full", limit=16000)
            final = _research_final(state)
            final["output"]["content"] = "The delegated source preserves a conditional view."
            final["output"]["evidence_links"] = [{
                "text": final["output"]["content"], "evidence_refs": [
                    _scheduler_observations(state, "result.get")[0]["payload"]["result_ref"],
                ],
            }]
            final["output"]["gaps"] = []
            return final
        return _research_final(state) if depth == 1 else _root_final(state)

    runtime = _runtime(tmp_path, decide)
    root = asyncio.run(_finish(runtime))
    tasks = _tasks(runtime)
    assert root["status"] == "succeeded" and len(tasks) == 3
    assert runtime.corpus.reads == ["article_alpha"]
    for task in tasks:
        with runtime.repo.db.connection() as connection:
            result = connection.execute("SELECT sections_json FROM h2_results WHERE result_ref=?",
                                        (task["result_ref"],)).fetchone()
        saved = _artifact(runtime, json.loads(result["sections_json"])["finance_state"])
        source = saved["article_states"][0]
        assert source["article_id"] == "article_alpha" and source["read_status"] == "complete"
        assert source["task_id"] == tasks[-1]["task_id"] and source["use_status"] == "used"
        assert source["author"] == "Alpha" and source["current_hash"] == "hash-alpha"
        if task["depth"] < 2:
            assert not private_refs.intersection(source["material_refs"])
            assert not private_refs.intersection(saved["execution"]["evidence_refs"])
    parent = next(task for task in tasks if task["depth"] == 1)
    with runtime.repo.db.connection() as connection:
        attempt = connection.execute(
            "SELECT * FROM h2_attempts WHERE task_id=? ORDER BY rowid DESC LIMIT 1", (parent["task_id"],),
        ).fetchone()
    caller = CallerContext(runtime.handle.run_id, parent["thread_id"], parent["task_id"],
                           attempt["attempt_id"], attempt["lease_token"])
    assert private_refs
    for private_ref in private_refs:
        with pytest.raises(DispatchError) as denied:
            runtime.repo.read_input(caller, private_ref)
        assert denied.value.code == "not_found"
    _assert_ledger(runtime, 10 if binding == "result" else 9)
    _assert_compact_checkpoints(runtime)


@pytest.mark.parametrize("envelope_mode", ["subset", "foreign"])
def test_legacy_request_preserves_grants_and_scope_across_fresh_service(tmp_path, envelope_mode):
    note = "Parent context outside the compatibility request: retain the conditions.\n"
    refs = {}
    assigned_ids = ["article_alpha", "article_beta"] if envelope_mode == "subset" else ["article_alpha"]
    denied_id = "article_ungranted" if envelope_mode == "subset" else "article_beta"

    def decide(state):
        if state["task_identity"]["depth"] == 1:
            materials = {item["material_ref"]: item for item in state["materials"]}
            assert materials[refs["note"]]["payload"] == note
            assert refs["request"] not in materials and refs["request"] not in state["known_evidence_refs"]
            assert state["task"] == "Preserve the exact compatibility assignment."
            assert (refs["beta"] in materials) == (envelope_mode == "subset")
            failures = [item for item in state["normalized_observations"] if item.get("status") == "failed"]
            if not failures:
                return _tool("article.read", article_id=denied_id)
            assert failures[0]["payload"]["payload"]["error"]["code"] == "forbidden"
            read_ids = {item["article_id"] for item in state["article_states"]
                        if item.get("read_status") == "complete"}
            for article_id in assigned_ids:
                if article_id not in read_ids:
                    return _tool("article.read", article_id=article_id)
            return _research_final(state)
        if not state["scheduled_children"]:
            action = _spawn("legacy-envelope-child", "analyst", [refs["request"], refs["alpha"]], calls=12)
            action["arguments"]["parent_context_refs"] = [refs["note"]]
            if envelope_mode == "subset":
                action["arguments"]["parent_context_refs"].append(refs["beta"])
            return action
        if not _child_results(state):
            return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
        return _root_final(state)

    runtime = _runtime(tmp_path, decide)
    caller = runtime.service.caller(runtime.handle)
    refs["note"] = runtime.repo.store_text(caller, "parent-context", note)
    for name in ("alpha", "beta"):
        refs[name] = runtime.repo.store_text(caller, "article-metadata", json.dumps({
            "article": {"article_id": f"article_{name}", "author": name.title(),
                        "published_at": "2026-10-01"},
        }))
    nested_refs = [refs["alpha"]] if envelope_mode == "subset" else [refs["alpha"], refs["beta"]]
    refs["request"] = runtime.repo.store_text(caller, "legacy-request", json.dumps({
        "schema_version": "finance.h2.child_request.v1",
        "action": {"type": "delegate", "task": "Preserve the exact compatibility assignment."},
        "input_refs": nested_refs,
    }))
    for _ in range(3):
        asyncio.run(_cycle(runtime))
    assert runtime.prompts[-1]["task_identity"]["depth"] == 1
    assert runtime.corpus.reads == []
    runtime.service = FinanceHarnessV2Service.for_finance_corpus(
        runtime.repo, runtime.transport, corpus=runtime.corpus,
        model_adapter=runtime.service.model_adapter, worker_id="fresh-legacy-envelope-worker",
    )
    root = asyncio.run(_finish(runtime))
    assert root["status"] == "succeeded" and runtime.corpus.reads == assigned_ids
    tasks = _tasks(runtime)
    assert len(tasks) == 2 and {task["status"] for task in tasks} == {"succeeded"}
    child = next(task for task in tasks if task["depth"] == 1)
    grants = {refs["request"], refs["alpha"], refs["note"]}
    if envelope_mode == "subset":
        grants.add(refs["beta"])
    assert set(json.loads(child["input_refs_json"])) == grants
    _assert_ledger(runtime, 7 if envelope_mode == "subset" else 6)
    _assert_compact_checkpoints(runtime)


def test_root_and_child_identity_matches_ledger_across_fresh_service_continuation(tmp_path):
    from datetime import datetime, timezone

    research_clock = {"today": "2026-10-19", "timezone": "Asia/Shanghai"}

    def decide(state):
        assert state["research_clock"] == research_clock
        identity = state["task_identity"]
        assert identity["run_id"] == "continuation"
        if identity["depth"] == 1:
            if not _scheduler_observations(state, "agent.status"):
                return _tool("agent.status", task_ids=[identity["task_id"]])
            return _research_final(state)
        if not state["scheduled_children"]:
            return _spawn("identity-child", "analyst", [], calls=8)
        if not _child_results(state):
            return _tool("agent.wait", task_ids=[state["scheduled_children"][0]["task_id"]])
        return _root_final(state)

    runtime = _runtime(
        tmp_path, decide, research_clock=research_clock,
        clock_ms=int(datetime(2026, 10, 19, 15, 59, 59, tzinfo=timezone.utc).timestamp() * 1000),
    )
    for _ in range(3):
        asyncio.run(_cycle(runtime))
    saved_child_identity = runtime.prompts[-1]["task_identity"]
    assert saved_child_identity["depth"] == 1
    assert saved_child_identity["parent_task_id"] == runtime.handle.task_id
    assert saved_child_identity["parent_thread_id"] == runtime.handle.thread_id
    # Cross Shanghai midnight within the original lease and budget deadlines.
    runtime.clock.value += 2000
    runtime.service = FinanceHarnessV2Service.for_finance_corpus(
        runtime.repo, runtime.transport, corpus=runtime.corpus,
        model_adapter=runtime.service.model_adapter, worker_id="fresh-identity-worker",
    )
    root = asyncio.run(_finish(runtime))
    assert root["status"] == "succeeded"
    tasks = _tasks(runtime)
    assert len(tasks) == 2 and {task["status"] for task in tasks} == {"succeeded"}
    _assert_ledger(runtime, 5)
    with runtime.repo.db.connection() as connection:
        calls = connection.execute(
            "SELECT m.*,t.thread_id,t.parent_task_id,h.parent_thread_id,h.role,h.role_version,h.depth "
            "FROM h2_model_calls m JOIN h2_tasks t ON t.task_id=m.task_id "
            "JOIN h2_threads h ON h.thread_id=t.thread_id ORDER BY m.rowid",
        ).fetchall()
        result = connection.execute(
            "SELECT sections_json FROM h2_results WHERE result_ref=?", (root["result_ref"],),
        ).fetchone()
    keys = ("run_id", "thread_id", "task_id", "attempt_id", "parent_task_id", "parent_thread_id",
            "role", "role_version", "depth")
    assert len(calls) == len(runtime.prompts) == 5
    for call, prompt in zip(calls, runtime.prompts, strict=True):
        assert prompt["task_identity"] == {key: call[key] for key in keys}
        assert "lease_token" not in prompt["task_identity"]
    child_identities = [prompt["task_identity"] for prompt in runtime.prompts
                        if prompt["task_identity"]["depth"] == 1]
    assert len(child_identities) == 2
    assert child_identities[0]["thread_id"] == child_identities[1]["thread_id"]
    assert child_identities[0]["task_id"] == child_identities[1]["task_id"]
    assert child_identities[0]["attempt_id"] != child_identities[1]["attempt_id"]
    final_state = _artifact(runtime, json.loads(result["sections_json"])["finance_state"])
    assert final_state["execution"]["task_identity"] == runtime.prompts[-1]["task_identity"]
    assert final_state["execution"]["research_clock"] == research_clock
    _assert_compact_checkpoints(runtime)
