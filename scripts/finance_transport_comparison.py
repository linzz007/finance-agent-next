"""Explicit paid, paired text-transport probes; no financial quality scoring."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from pydantic import TypeAdapter

from policy_impact.harness.model_config import PROFILE_ENV_VAR, model_runtime_status
from policy_impact.harness.model_gateway import ModelInvocationError
from policy_impact.harness.openai_model_gateway import OpenAICompatibleModelAdapter
from policy_impact.harness.responses_model_gateway import ResponsesModelAdapter
from policy_impact.runtime.actions import AgentAction
from scripts.finance_research_live_acceptance import write_json

PROFILES = ("gpt", "gpt-responses")
KINDS = ("short", "long_input", "long_output", "json_action", "multiturn")
SYSTEM = "Synthetic transport test. Return one public JSON action, no markdown, XML, native tools or hidden reasoning."
EXPECTED_TOOL = {"type": "tool_call", "tool_name": "article.read", "arguments": {"article_id": "synthetic_alpha"}}


def now():
    return datetime.now(timezone.utc).isoformat()


def probe_messages(kind: str) -> list[dict]:
    short = 'Return exactly {"type":"final","output":{"answer":"alpha|beta|gamma"}}.'
    if kind == "long_input":
        filler = "Synthetic observation: a conditional scenario is not a verified market fact.\n" * 450
        prompt = short + "\nSTART=alpha\n" + filler + "MIDDLE=beta\n" + filler + "END=gamma"
    elif kind == "long_output":
        prompt = (
            'Return {"type":"final","output":{"answer":"your explanation"}}. '
            "Write a Chinese explanation of at least 1800 Chinese characters about evaluating a fictional "
            "author's conditional financial views. Cover time scope, assumptions, attribution, observations, "
            "alternative causes, changing predictions and uncertainty. Do not use real financial claims or browse."
        )
    elif kind in {"json_action", "multiturn"}:
        prompt = "Return exactly this public JSON action; do not execute it: " + json.dumps(EXPECTED_TOOL)
    else:
        prompt = short
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]


def expected(kind: str, payload: dict, turn: int) -> bool:
    if kind == "json_action" or (kind == "multiturn" and turn == 1):
        return payload == EXPECTED_TOOL
    answer = payload.get("output", {}).get("answer", "")
    if kind == "long_output":
        return payload.get("type") == "final" and isinstance(answer, str) and len(answer) >= 1800
    if kind == "multiturn":
        return answer == ("alpha|conditional" if turn == 2 else "alpha|conditional|beta|opposed")
    return payload.get("type") == "final" and answer == "alpha|beta|gamma"


def one_request(adapter, messages, kind, turn) -> dict:
    from policy_impact.harness.context_manifest import estimate_tokens

    started = time.monotonic()
    record = {"started_at": now(), "turn": turn, "status": "started",
              "input_sha256": hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
              "estimated_input_tokens": estimate_tokens(messages),
              "requested": {"model_id": adapter.model_id, "max_output_tokens": adapter.max_tokens,
                            "timeout_seconds": adapter.timeout_seconds, "response_max_seconds": adapter.stream_max_seconds}}
    metadata = {}
    try:
        system, wire = adapter._split_messages(messages)
        # Intentionally no complete(): each row is one physical request, no hidden repair/retry.
        data = adapter._request(system, wire)
        metadata = data.get("transport_metadata") or {}
        record["transport_completed"] = metadata.get("response_complete") is True
        record["stop_reason"] = data.get("stop_reason")
        record["usage"] = data.get("usage") or {}
        if data.get("stop_reason") == "max_tokens":
            record.update(status="truncated", contract_valid=False, expectation_met=False)
        else:
            public = adapter._response_text(data)
            record["public_text"] = public
            try:
                payload = json.loads(public)
                TypeAdapter(AgentAction).validate_python(payload)
                record.update(status="action_valid", payload=payload, contract_valid=True,
                              expectation_met=expected(kind, payload, turn))
            except ValueError:
                record.update(status="invalid_public_action", contract_valid=False, expectation_met=False)
    except ModelInvocationError as exc:
        metadata = exc.metadata.get("transport_metadata") or {}
        record.update(status="model_error", error=str(exc), transport_completed=metadata.get("response_complete") is True,
                      contract_valid=False, expectation_met=False,
                      permanent_error=not adapter._is_retryable_transport_error(str(exc))
                          and exc.metadata.get("failure_kind") != "empty_public_response")
    record.update(metadata=metadata, finished_at=now(), duration_seconds=round(time.monotonic() - started, 3))
    return record


def run_probe(adapter, kind, *, deadline, timeout_seconds, max_tokens, directory):
    messages = probe_messages(kind)
    turns = []
    maximum = 3 if kind == "multiturn" else 1
    directory.mkdir(parents=True)
    for turn in range(1, maximum + 1):
        remaining = deadline - time.monotonic()
        if remaining < 1:
            return {"status": "budget_stopped", "turns": turns}
        adapter.timeout_seconds = min(timeout_seconds, remaining)
        adapter.stream_max_seconds = min(180, remaining)
        adapter.max_tokens = max_tokens
        write_json(directory / f"turn-{turn}.input.json", messages)
        result = one_request(adapter, messages, kind, turn)
        turns.append(result)
        write_json(directory / f"turn-{turn}.json", result)
        print(json.dumps({"profile": adapter.profile_id, "kind": kind, "turn": turn,
                          "status": result["status"], "expectation_met": result["expectation_met"],
                          "seconds": result["duration_seconds"]}), flush=True)
        if not result["expectation_met"]:
            break
        if kind == "multiturn" and turn < maximum:
            messages.append({"role": "assistant", "content": json.dumps(result["payload"], ensure_ascii=False)})
            messages.append({"role": "user", "content": (
                'Synthetic observation: author=alpha, view=conditional. Return only '
                '{"type":"final","output":{"answer":"alpha|conditional"}}.' if turn == 1 else
                'Second synthetic observation: author=beta, view=opposed. Remember both observations. Return only '
                '{"type":"final","output":{"answer":"alpha|conditional|beta|opposed"}}.'
            )})
    return {"status": "passed" if len(turns) == maximum and all(t["expectation_met"] for t in turns) else "not_passed",
            "turns": turns}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicit opt-in to paid calls")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--kinds", nargs="+", choices=KINDS, default=list(KINDS))
    parser.add_argument("--max-seconds", type=int, default=1200)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--max-tokens", type=int, default=16384)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live required; this makes paid requests")
    if not 1 <= args.max_seconds <= 3600 or not 1 <= args.timeout_seconds <= 180 or not 256 <= args.max_tokens <= 32768:
        parser.error("Invalid bounded probe configuration")
    if len(set(args.kinds)) != len(args.kinds):
        parser.error("Duplicate kinds are not allowed")
    output = args.output.resolve()
    allowed = (ROOT / "data/acceptance").resolve()
    if output == allowed or not output.is_relative_to(allowed) or output.exists():
        parser.error("--output must be a new subdirectory of data/acceptance")
    from run_policy_api import load_local_env

    load_local_env(ROOT)
    configs, adapters = {}, {}
    for profile in PROFILES:
        os.environ[PROFILE_ENV_VAR] = profile
        status = model_runtime_status()
        if not status["available"]:
            parser.error("Selected profile has no configured credential")
        adapter_type = ResponsesModelAdapter if status["protocol"] == "openai-responses" else OpenAICompatibleModelAdapter
        adapters[profile] = adapter_type.from_env()
        configs[profile] = status
    if (configs["gpt"]["model_id"], configs["gpt"]["base_url"], configs["gpt"]["stream"]) != (
            configs["gpt-responses"]["model_id"], configs["gpt-responses"]["base_url"], configs["gpt-responses"]["stream"]):
        parser.error("Paired profiles must share model, endpoint and stream setting")
    output.mkdir(parents=True)
    manifest = {"started_at": now(), "profiles": configs, "repetitions": args.repetitions, "kinds": args.kinds,
                "single_physical_request_per_turn": True, "max_seconds": args.max_seconds,
                "timeout_seconds": args.timeout_seconds, "max_output_tokens": args.max_tokens,
                "automatic_financial_scoring": False, "synthetic_only": True,
                "request_upper_bound": args.repetitions * len(PROFILES) * sum(3 if k == "multiturn" else 1 for k in args.kinds),
                "source_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in (
                    Path(__file__), ROOT / "src/policy_impact/harness/openai_model_gateway.py",
                    ROOT / "src/policy_impact/harness/responses_model_gateway.py",
                    ROOT / "src/policy_impact/harness/model_gateway.py", ROOT / "src/policy_impact/harness/model_config.py",
                    ROOT / "src/policy_impact/harness/research_budget.py", ROOT / "config/model_profiles.json")}}
    for name in manifest["source_sha256"]:
        target = output / "provenance" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    write_json(output / "manifest.json", manifest)
    summary = {"status": "running", "probes": [], "stopped_profiles": {}, "financial_quality": "not_evaluated"}
    deadline = time.monotonic() + args.max_seconds
    consecutive_failures = Counter()
    for repetition in range(1, args.repetitions + 1):
        for kind in args.kinds:
            # Alternate which path goes first to reduce simple time/order bias.
            for profile in (PROFILES if repetition % 2 else tuple(reversed(PROFILES))):
                if profile in summary["stopped_profiles"]:
                    continue
                if time.monotonic() >= deadline or (output / "STOP").exists():
                    summary.update(status="stopped", finished_at=now())
                    write_json(output / "summary.json", summary)
                    return 2
                case = f"{profile}-{kind}-r{repetition}"
                result = run_probe(adapters[profile], kind, deadline=deadline, timeout_seconds=args.timeout_seconds,
                                   max_tokens=args.max_tokens, directory=output / case)
                row = {"id": case, "profile": profile, "kind": kind, "repetition": repetition, **result}
                summary["probes"].append(row)
                last = result["turns"][-1] if result["turns"] else {}
                transport_failed = last.get("status") == "model_error" and not last.get("transport_completed")
                consecutive_failures[profile] = consecutive_failures[profile] + 1 if transport_failed else 0
                if last.get("permanent_error") or consecutive_failures[profile] >= 2:
                    summary["stopped_profiles"][profile] = "permanent_error" if last.get("permanent_error") else "two_consecutive_transport_failures"
                write_json(output / "summary.json", summary)
    summary.update(status="completed" if not summary["stopped_profiles"] else "completed_with_blockers", finished_at=now())
    write_json(output / "summary.json", summary)
    return 0 if all(p["status"] == "passed" for p in summary["probes"]) and not summary["stopped_profiles"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
