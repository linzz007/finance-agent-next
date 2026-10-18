"""Run one live finance query and print its answer plus compact trace signals."""

from __future__ import annotations

import argparse
import json

from policy_impact.app.chat_workbench_service import ChatWorkbenchService
from policy_impact.harness.model_config import configured_model_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?")
    parser.add_argument("--title", default="Finance Agent acceptance")
    parser.add_argument("--company", default="finance_observer")
    parser.add_argument("--session")
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--evidence", action="store_true")
    parser.add_argument("--audit-session", action="store_true")
    parser.add_argument("--cross-only", action="store_true",
                        help="Replay only the cross-article pass for an already-audited session")
    args = parser.parse_args()

    service = ChatWorkbenchService()
    if args.session:
        session_id = args.session
        result = {}
    else:
        if not args.question:
            parser.error("question is required unless --session is given")
        session = service.create_session(
            args.company, args.title, "chat", configured_model_id(), "general_chat",
        )
        session_id = session["session_id"]
        print(json.dumps({"session_id": session_id, "question": args.question}, ensure_ascii=False), flush=True)
        result = service.send_message(args.company, session_id, args.question)
    trace = service.latest_trace(args.company, session_id)
    if args.session:
        messages = service.list_messages(args.company, session_id)
        result["answer"] = next((message.get("content") for message in reversed(messages)
                                 if message.get("role") == "assistant"), None)
    steps = trace.get("steps") or []
    if args.audit_session:
        from policy_impact.domains.finance.answer_audit import AnswerAudit
        from policy_impact.domains.finance.article_corpus import article_root
        from policy_impact.domains.finance.article_profile import load_holdings
        from policy_impact.domains.finance.evidence import EvidenceLedger
        from policy_impact.harness.model_gateway import AnthropicCompatibleModelAdapter

        candidate = next((
            (step.get("output_payload") or {}).get("payload", {}).get("output", {}).get("answer")
            for step in reversed(steps) if step.get("step_type") == "model_call"
            and (step.get("output_payload") or {}).get("payload", {}).get("type") == "final"
            and isinstance((step.get("output_payload") or {}).get("payload", {}).get("output"), dict)
            and (step.get("output_payload") or {}).get("payload", {}).get("output", {}).get("answer")
        ), None)
        bundle = next((
            (step.get("output_payload") or {}).get("evidence_bundle")
            for step in steps if step.get("step_type") == "evidence_audit"
        ), None)
        if not candidate or not bundle:
            raise RuntimeError("session has no final candidate or evidence bundle to audit")
        ledger = EvidenceLedger()
        ledger.merge_bundle(bundle)
        adapter = AnthropicCompatibleModelAdapter.from_env()
        adapter.max_tokens = max(adapter.max_tokens, 16384)
        question = next((item.get("content") or "" for item in service.list_messages(
            args.company, session_id,
        ) if item.get("role") == "user"), "")
        def complete(messages, schema_name):
            if args.cross_only and schema_name == "finance_answer_audit.v1":
                return {"checks": [], "coverage_complete": True}
            return adapter.complete(messages, schema_name=schema_name).payload["output"]

        audit = AnswerAudit(ledger, complete).call(candidate, question=question,
               personal_context={"current_holdings": load_holdings(article_root())})
        print(json.dumps({
            "candidate_length": len(candidate), "status": audit["status"],
            "reviewed_claim_count": audit["reviewed_claim_count"],
            "section_count": audit.get("section_count"),
            "coverage_complete": audit["coverage_complete"],
            "limitations": audit.get("limitations"),
            "issue_count": len(audit["issues"]),
            "issues": audit["issues"][:8],
            "cross_review": audit.get("cross_review"),
            "cross_only": args.cross_only,
        }, ensure_ascii=False), flush=True)
        return
    print(json.dumps({
        "run_id": result.get("run_id"),
        "answer": result.get("answer"),
        "trace_step_count": len(steps),
        "step_types": [step.get("step_type") for step in steps],
        "tool_names": [
            (step.get("input_payload") or {}).get("tool_name")
            for step in steps if step.get("step_type") == "tool_call"
        ],
    }, ensure_ascii=False), flush=True)
    if args.evidence:
        audit = next((step.get("output_payload") or {} for step in steps
                      if step.get("step_type") == "evidence_audit"), {})
        bundle = audit.get("evidence_bundle") or {}
        for row in bundle.get("evidence") or []:
            print(json.dumps({key: row.get(key) for key in (
                "evidence_id", "source_type", "source_url", "published_at", "excerpt",
            )}, ensure_ascii=False)[:1200], flush=True)
    for step in steps if args.trace else []:
        if step.get("step_type") not in {"model_call", "tool_result", "gate_check", "run_stopped"}:
            continue
        payload = step.get("output_payload") or {}
        event = {
            "step_type": step.get("step_type"),
            "title": step.get("title"),
            "status": step.get("status"),
        }
        if step.get("step_type") == "model_call":
            event["output_keys"] = list(payload)
            response = payload.get("response") or payload.get("model_output") or payload.get("output") or payload.get("payload") or {}
            event["action"] = str(response)[:500]
            event["turn_error"] = payload.get("turn_error")
            event["observation"] = str(payload.get("observation") or "")[:400]
        elif step.get("step_type") == "tool_result":
            observation = payload.get("observation") or {}
            event["result"] = {key: observation.get(key) for key in (
                "status", "error", "reason", "artifact_id", "card_id", "review_id",
                "comparison_id", "article_id", "count", "claim_count",
            ) if key in observation}
        else:
            event["output"] = payload
        print(json.dumps({
            **event,
        }, ensure_ascii=False, default=str)[:1200], flush=True)


if __name__ == "__main__":
    main()
