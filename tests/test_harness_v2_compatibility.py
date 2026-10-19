"""C16 offline semantic comparison between the legacy and H2 finance paths."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import fakeredis
import httpx

from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.dispatch import DispatchRepository, FinanceHarnessV2Service, SchedulerConfig
from policy_impact.dispatch.model_execution import GatewayModelAdapter
from policy_impact.dispatch.transport import RedisStreams
from policy_impact.domains.finance.answer_audit import AnswerAudit
from policy_impact.domains.finance.article_corpus import ArticleCorpus
from policy_impact.harness.agent_runtime import AgentRuntime
from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.harness.model_gateway import ScriptedModelAdapter
from policy_impact.memory.store import PolicyMemoryStore

QUERY = "Explain TT's gold viewpoint using the local article and preserve its conditions."
ANSWER = (
    "TT states that rates may affect gold; this is the author's conditional viewpoint, "
    "not an independently verified market fact. (TT/articles/gold.md:L3)"
)


def _chat(payload: dict, index: int) -> dict:
    return {
        "id": f"compat-provider-{index}",
        "model": "compat-fixture",
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(payload)},
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def _finance_user_payload(request: httpx.Request) -> dict:
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
    raise AssertionError("H2 finance root payload was not sent")


def _write_compatibility_corpus(tmp_path: Path, monkeypatch) -> tuple[ArticleCorpus, str]:
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path)
    company = tmp_path / "data" / "companies" / "finance_observer"
    (company / "wiki").mkdir(parents=True)
    (company / "company.yaml").write_text(
        json.dumps({"company_id": "finance_observer", "company_name": "Finance research"}),
        encoding="utf-8",
    )
    root = company / "articles"
    article_dir = root / "TT" / "articles"
    article_dir.mkdir(parents=True)
    (article_dir / "gold.md").write_text(
        "# Gold outlook\n2026-09-29\nThe author expects rates to affect gold.\n",
        encoding="utf-8",
    )
    (company / "research_policy.md").write_text(
        "Preserve source conditions and distinguish author views from facts.\n",
        encoding="utf-8",
    )
    (company / "current_holdings.md").write_text(
        "```json\n{\"as_of\":\"2026-09-29\",\"currency\":\"CNY\","
        "\"approximate\":true,\"positions\":[{\"name\":\"gold\",\"amount\":1}]}\n```",
        encoding="utf-8",
    )
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", str(root))
    PolicyMemoryStore("finance_observer").seed_model_configs()
    corpus = ArticleCorpus("finance_observer", root)
    corpus.sync()
    article_id = corpus.list_articles(author="TT")[0]["article_id"]
    return corpus, article_id


def _legacy_actions(article_id: str) -> list[dict]:
    return [
        {
            "type": "tool_call",
            "tool_name": "research.plan",
            "arguments": {
                "operation": "create",
                "goals": [
                    "Explain the author's viewpoint",
                    "Preserve the source conditions and identify evidence gaps",
                ],
            },
        },
        {
            "type": "tool_call",
            "tool_name": "research.plan",
            "arguments": {"operation": "update", "goal_id": 1, "status": "done"},
        },
        {
            "type": "tool_call",
            "tool_name": "research.plan",
            "arguments": {"operation": "update", "goal_id": 2, "status": "done"},
        },
        {
            "type": "tool_call",
            "tool_name": "article.read",
            "arguments": {"article_id": article_id},
        },
        {
            "type": "tool_call",
            "tool_name": "research.checkpoint",
            "arguments": {
                "checks": [{
                    "claim": "The answer attributes the conditional viewpoint to TT.",
                    "decision": "check",
                    "outcome": "inconclusive",
                    "method": "Source attribution only; no external market verification requested.",
                    "evidence_refs": [],
                    "reason": "The fixture tests source semantics, not external truth.",
                    "gap": "No external market evidence was supplied.",
                }],
            },
        },
        {
            "type": "final",
            "output": {
                "answer": ANSWER,
                "verification_status": "partial",
                "delivery_mode": "qualified",
                "answer_audit_coverage": "complete",
                "semantic_review": "complete",
            },
        },
    ]


def _stub_answer_audit(monkeypatch) -> None:
    monkeypatch.setattr(
        AnswerAudit,
        "select_review_targets",
        lambda self, answer, **kwargs: {
            "review_targets": [],
            "selection_reason": "C16 fixture isolates dispatch/source compatibility.",
        },
    )
    monkeypatch.setattr(
        AnswerAudit,
        "call",
        lambda self, answer, **kwargs: {
            "status": "accept",
            "issues": [],
            "checks": [],
            "reviewed_claim_count": 0,
            "coverage_complete": True,
            "review_scope": "c16_fixture",
        },
    )


def _run_legacy(article_id: str, monkeypatch) -> tuple[dict, list[dict]]:
    _stub_answer_audit(monkeypatch)
    runtime = AgentRuntime(
        company_id="finance_observer",
        model_adapter=ScriptedModelAdapter(_legacy_actions(article_id)),
    )
    session_id = runtime.store.create_chat_session(
        title="C16 compatibility",
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    result = runtime.run_turn(
        session_id=session_id,
        message=QUERY,
        mode="chat",
        model_id=DEFAULT_MODEL_ID,
        active_skill_id="general_chat",
    )
    return result, runtime.store.list_agent_steps(result["run_id"])


def _run_h2(tmp_path: Path, corpus: ArticleCorpus, article_id: str) -> tuple[dict, dict, dict]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request):
        calls.append(request)
        index = len(calls)
        if index == 1:
            payload = {"type": "tool_call", "tool_name": "article.list", "arguments": {"limit": 10}}
        elif index == 2:
            payload = {
                "type": "tool_call",
                "tool_name": "article.read",
                "arguments": {"article_id": article_id, "start_line": 1, "limit": 100},
            }
        else:
            state = _finance_user_payload(request)
            refs = [
                material["material_ref"]
                for material in state.get("materials", [])
                if '"tool":"article.read"' in material.get("payload", "")
            ]
            assert refs, "H2 final decision did not receive the read article"
            payload = {
                "type": "final",
                "output": {
                    "answer": ANSWER,
                    "summary": "Source-bound author viewpoint with preserved conditions.",
                    "evidence_refs": refs,
                    "verification_status": "partial",
                    "delivery_mode": "qualified",
                    "answer_audit_coverage": "complete",
                    "semantic_review": "complete",
                },
            }
        return httpx.Response(200, json=_chat(payload, index))

    config = SchedulerConfig(
        stream_name="finance-agent-next:c16:compatibility",
        publish_attempts=2,
        lease_ms=60000,
    )
    repository = DispatchRepository(
        tmp_path / "h2.sqlite",
        tmp_path / "runtime-records",
        config=config,
        profiles={
            "fixture": {
                "model_id": "compat-fixture",
                "base_url": "https://fixture.invalid/v1",
                "protocol": "openai-compatible",
                "auth_env": "C16_UNUSED_KEY",
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
        group="c16-compatibility-tests",
    )
    service = FinanceHarnessV2Service.for_finance_corpus(
        repository,
        transport,
        corpus=corpus,
        model_adapter=adapter,
        worker_id="c16-compatibility-worker",
    )
    handle = service.create_run(
        run_id="c16-compatibility",
        session_ref="c16-session",
        query=QUERY,
        selected_profile="fixture",
        allowed_profiles=["fixture"],
        tools=["agent.spawn", "agent.wait", "agent.status", "result.get"],
    )
    status = asyncio.run(service.run_until_terminal(handle, max_cycles=20))
    assert len(calls) == 3
    return status, {"repository": repository, "handle": handle}, {"calls": calls}


def test_c16_legacy_and_h2_preserve_source_semantics_without_claiming_h2_review(tmp_path, monkeypatch):
    corpus, article_id = _write_compatibility_corpus(tmp_path, monkeypatch)
    legacy_result, legacy_steps = _run_legacy(article_id, monkeypatch)
    h2_status, h2_context, _ = _run_h2(tmp_path, corpus, article_id)
    repository = h2_context["repository"]
    handle = h2_context["handle"]

    legacy_audit = next(step for step in legacy_steps if step["step_type"] == "evidence_audit")
    legacy_delivery = next(step for step in legacy_steps if step["step_type"] == "research_delivery")
    legacy_read = legacy_audit["output_payload"]["article_reads"]
    assert len(legacy_read) == 1
    assert legacy_read[0]["author"] == "TT"
    assert legacy_read[0]["published_at"] == "2026-09-29"
    assert legacy_read[0]["evidence_id"]
    assert legacy_read[0]["line_start"] == 1
    assert legacy_read[0]["line_end"] >= 3
    original_text = corpus.get_article(article_id)["content"]
    assert "rates to affect gold" in original_text
    assert legacy_result["answer"] == ANSWER
    assert legacy_result["delivery_status"] == "done"
    assert legacy_delivery["output_payload"]["answer_audit_coverage"] == "complete"

    assert h2_status["tasks"][0]["status"] == "succeeded"
    with repository.db.connection() as connection:
        result = connection.execute(
            "SELECT * FROM h2_results WHERE task_id=? AND disposition='accepted'",
            (handle.task_id,),
        ).fetchone()
        assert result is not None
        sections = json.loads(result["sections_json"])
        state_row = connection.execute(
            "SELECT * FROM h2_artifacts WHERE artifact_ref=?",
            (sections["finance_state"],),
        ).fetchone()
        assert state_row is not None
        state = json.loads(repository.artifacts.read(state_row))
        article_materials = []
        for article_state in state["article_states"]:
            material_row = connection.execute(
                "SELECT * FROM h2_artifacts WHERE artifact_ref=?",
                (article_state["material_ref"],),
            ).fetchone()
            assert material_row is not None
            material_candidate = json.loads(repository.artifacts.read(material_row))
            tool_payload = material_candidate.get("payload", {}).get("payload", {})
            if tool_payload.get("tool") == "article.read":
                article_materials.append((article_state, material_candidate))
        assert len(article_materials) == 1
        read_state, material = article_materials[0]
        assert all(
            json.loads(repository.artifacts.read(
                connection.execute(
                    "SELECT * FROM h2_artifacts WHERE artifact_ref=?",
                    (article_state["material_ref"],),
                ).fetchone()
            ))["payload"]["payload"].get("tool") != "article.list"
            for article_state in state["article_states"]
        )
        candidate_row = connection.execute(
            "SELECT * FROM h2_artifacts WHERE artifact_ref=?",
            (state["candidate_answer"]["artifact_ref"],),
        ).fetchone()
        assert candidate_row is not None
        candidate = json.loads(repository.artifacts.read(candidate_row))

    assert read_state["read_status"] == "complete"
    h2_payload = material["payload"]["payload"]
    assert h2_payload["article"]["author"] == "TT"
    assert h2_payload["article"]["published_at"] == "2026-09-29"
    assert "rates to affect gold" in h2_payload["content"]
    assert result["evidence_refs_json"]
    assert candidate["output"]["answer"] == ANSWER
    assert candidate["output"]["verification_status"] == "partial"
    assert candidate["output"]["answer_audit_coverage"] == "complete"

    # H2 currently records a candidate and delivery state, but does not yet
    # map model review metadata into FinanceRootState.review.  This is the
    # explicit 019 design gap; the test prevents silently treating it as a
    # completed audit while still proving source/answer semantics are intact.
    assert state["review"] is None
    assert state["delivery"]["status"] == "ready"

