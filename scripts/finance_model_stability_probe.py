"""Measure live model transport separately from finance business acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from run_policy_api import load_local_env

load_local_env()

from policy_impact.domains.finance.article_corpus import article_root
from policy_impact.harness.model_config import effective_model_config
from policy_impact.harness.model_gateway import ModelInvocationError
from policy_impact.harness.openai_model_gateway import OpenAICompatibleModelAdapter
from policy_impact.harness.responses_model_gateway import ResponsesModelAdapter

METADATA_FIELDS = (
    "model_id", "attempts", "max_attempts", "latency_ms", "total_latency_ms",
    "stop_reason", "usage", "temperature", "max_tokens", "timeout_seconds",
    "retry_errors", "last_error_kind", "contract_recovered", "transport",
    "stream", "stream_complete", "stream_event_count", "stream_content_chars",
    "time_to_first_token_ms", "stream_duration_ms", "stream_max_seconds",
    "transport_attempts",
    "api_style", "response_complete", "completion_state", "incomplete_reason", "response_id",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_record(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_business(output: Path) -> None:
    from policy_impact.memory.store import PolicyMemoryStore

    store = PolicyMemoryStore("finance_observer")
    results = []
    for path in sorted(output.glob("business_*/Q*.json")):
        if not re.fullmatch(r"Q\d{2}", path.stem):
            continue
        case = json.loads(path.read_text(encoding="utf-8"))
        if not case.get("run_id"):
            results.append({"path": str(path), "id": case["id"], "status": case["status"]})
            continue
        steps = store.list_agent_steps(case["run_id"])
        calls = []
        for step in steps:
            if step["step_type"] != "model_call":
                continue
            payload = step.get("output_payload") or {}
            metadata = {
                **(payload.get("metadata") or {}),
                **(payload.get("error_metadata") or {}),
                **(step.get("metadata") or {}),
            }
            action = payload.get("payload") or {}
            calls.append({
                "step_id": step.get("step_id"), "title": step["title"],
                "status": step["status"], "created_at": step["created_at"],
                "schema_name": (step.get("input_payload") or {}).get("schema_name")
                    or metadata.get("schema_name"),
                "action": {key: action.get(key) for key in ("type", "tool_name", "role")},
                "metadata": {key: metadata[key] for key in METADATA_FIELDS if key in metadata},
                "error": payload.get("error"),
            })
        write_record(path.with_name(path.stem + "_transport.json"), {"run_id": case["run_id"], "calls": calls})
        known_attempts = [c["metadata"]["attempts"] for c in calls
                          if isinstance(c["metadata"].get("attempts"), int)]
        attempts = [a for c in calls for a in c["metadata"].get("transport_attempts", [])]
        errors = Counter(e for c in calls for e in c["metadata"].get("retry_errors", []))
        results.append({
            "path": str(path), "id": case["id"], "run_id": case["run_id"],
            "session_id": case["session_id"], "duration_seconds": case["duration_seconds"],
            "answer_chars": len(case.get("answer") or ""),
            "research_delivery": (case.get("trace") or {}).get("research_delivery"),
            "model_nodes": len(calls), "failed_trace_nodes": sum(c["status"] == "failed" for c in calls),
            "known_attempts": sum(known_attempts), "nodes_with_unknown_attempts": len(calls) - len(known_attempts),
            "retried_nodes": sum(n > 1 for n in known_attempts),
            "recorded_transport_attempts": len(attempts),
            "incomplete_transport_attempts": sum(a.get("stream_complete") is False for a in attempts),
            "first_content_absent_attempts": sum(a.get("stream_complete") is False
                and a.get("time_to_first_token_ms") is None for a in attempts),
            "retry_errors": dict(errors),
            "schemas": dict(Counter(c["schema_name"] for c in calls)),
            "content_evidence_trace_acceptance": "manual_review_required",
        })
    write_record(output / "business_transport_summary.json", {"collected_at": utc_now(), "cases": results})
    print(json.dumps(results, ensure_ascii=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collect-business", action="store_true", help="Export diagnostics; does not score financial answers")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.collect_business:
        collect_business(args.output)
        return
    adapter_type = (ResponsesModelAdapter if effective_model_config()["protocol"] == "openai-responses"
                    else OpenAICompatibleModelAdapter)
    model = adapter_type.from_env()
    # Single attempts expose raw reliability; business tests keep configured retries.
    model.max_attempts = 1
    source = article_root() / "\u57f9\u98ce\u5ba2" / "articles" / "2026-10-01-302b85393c68199f.md"
    article = source.read_text(encoding="utf-8-sig")
    numbered = "\n".join(f"L{i}: {line}" for i, line in enumerate(article.splitlines(), 1))
    brief = (
        'Return exactly this JSON and nothing else: '
        '{"type":"final","output":{"answer":"stream transport ok"}}'
    )
    article_task = (
        "\u9605\u8bfb\u4ee5\u4e0b\u5355\u7bc7\u91d1\u878d\u6587\u7ae0\uff0c\u7528\u7ea6400-600\u4e2a\u6c49\u5b57\u5206\u6790\u4f5c\u8005\u5bf9\u9ec4\u91d1\u548c\u7f8e\u56fd\u5229\u7387\u7684\u601d\u8def\u3002"
        "\u533a\u5206\u6587\u7ae0\u63cf\u8ff0\u7684\u73b0\u8c61\u3001\u4f5c\u8005\u89e3\u91ca\u4e0e\u6761\u4ef6\u6027\u9884\u6d4b\uff0c\u4fdd\u7559\u65f6\u95f4\u548c\u6761\u4ef6\uff0c\u6807\u6ce8\u6b63\u6587\u884c\u53f7\u3002"
        "\u4e0d\u505a\u5916\u90e8\u67e5\u8bc1\uff0c\u4e0d\u628a\u4f5c\u8005\u7684\u89c2\u70b9\u8bf4\u6210\u5df2\u9a8c\u8bc1\u7684\u5e02\u573a\u4e8b\u5b9e\u3002"
        '\u4ec5\u8fd4\u56de\u4e00\u4e2a JSON\uff1a{"type":"final","output":{"answer":"\u4f60\u7684\u5206\u6790"}}\u3002\n\n'
        + numbered
    )
    schedule = [
        ("short_1", brief), ("article_1", article_task),
        ("short_2", brief), ("short_3", brief),
        ("article_2", article_task), ("short_4", brief),
    ]
    config = {
        "model_id": model.model_id, "endpoint": model.base_url,
        "stream": model.stream, "stream_max_seconds": model.stream_max_seconds,
        "timeout_seconds": model.timeout_seconds, "max_tokens": model.max_tokens,
        "temperature": model.temperature, "max_attempts": model.max_attempts,
    }
    manifest = {
        "started_at": utc_now(), "model": config,
        "purpose": "Raw single-attempt probes; not production business acceptance or quality scoring.",
        "article": {"path": str(source), "sha256": hashlib.sha256(article.encode()).hexdigest(),
                    "chars": len(article), "lines": len(article.splitlines())},
        "schedule": [name for name, _ in schedule],
    }
    write_record(args.output / "manifest.json", manifest)
    for name, prompt in schedule:
        destination = args.output / f"{name}.json"
        if destination.exists():
            print(json.dumps({"probe": name, "status": "already_recorded"}), flush=True)
            continue
        record = {"probe": name, "started_at": utc_now(), "model": config,
                  "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
        print(json.dumps({"probe": name, "status": "started"}), flush=True)
        started = time.monotonic()
        try:
            response = model.complete([{"role": "user", "content": prompt}], "general_chat_agent_loop.v1")
            record.update(status="response_received", payload=response.payload)
            metadata = response.metadata
        except ModelInvocationError as exc:
            record.update(status="model_error", error=str(exc), structured_output=exc.structured_output)
            metadata = exc.metadata
        record["metadata"] = {key: metadata[key] for key in METADATA_FIELDS if key in metadata}
        record["duration_seconds"] = round(time.monotonic() - started, 3)
        record["finished_at"] = utc_now()
        write_record(destination, record)
        print(json.dumps({"probe": name, "status": record["status"],
                          "duration_seconds": record["duration_seconds"],
                          "error_kind": metadata.get("last_error_kind")}), flush=True)


if __name__ == "__main__":
    main()
