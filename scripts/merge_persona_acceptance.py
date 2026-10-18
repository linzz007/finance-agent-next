"""Assemble latest-per-case evidence without rewriting recorded test batches."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", nargs="+", required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    destination = ROOT / "data" / "acceptance" / f"persona_{args.label}"
    sources = [ROOT / "data" / "acceptance" / f"persona_{label}" for label in args.labels]
    if destination in sources:
        raise ValueError("Aggregate must not overwrite an execution batch")
    records = {}
    verdicts = {}
    runs = []
    companies = {}
    for source in sources:
        batch = read(source / "results.json")
        if batch is None:
            raise ValueError(f"Missing completed batch: {source}")
        for item in batch:
            item = {**item, "evidence_batch": source.name}
            directory = Path(item.get("evidence_dir") or source / "cases" / item["id"])
            item["evidence_dir"] = str(directory)
            trace = read(directory / "trace.json", {})
            model_steps = [step for step in trace.get("steps", []) if step.get("step_type") == "model_call"]
            provider = [step for step in model_steps if not step.get("metadata", {}).get("deterministic_action")
                        and step.get("metadata", {}).get("provider")]
            item["model_event_count"] = len(model_steps)
            item["provider_model_calls"] = len(provider)
            item["runtime_action_count"] = sum(bool(step.get("metadata", {}).get("deterministic_action")) for step in model_steps)
            records[item["id"]] = item
        runs.append({"batch": source.name, "case_count": len(batch), "summary": read(source / "summary.json", {})})
        for persona, company_id in read(source / "companies.json", {}).items():
            companies.setdefault(persona, company_id)
        for filename in ("semantic_company.json", "semantic_personal.json", "semantic_verdicts.json"):
            verdicts.update(read(source / filename, {}))
    suite = read(ROOT / "tests" / "fixtures" / "persona_acceptance.json")
    ordered = [records[case["id"]] for case in suite["cases"] if case["id"] in records]
    # A prior review is not evidence of semantic correctness after a new run.
    verdicts = {cid: verdict for cid, verdict in verdicts.items()
                if cid in records and verdict.get("evidence_batch") == records[cid]["evidence_batch"]}
    write(destination / "results.json", ordered)
    write(destination / "semantic_verdicts.json", verdicts)
    write(destination / "companies.json", companies)
    write(destination / "provenance.json", {"mode": "latest_per_case", "execution_batches": runs,
                                            "note": "This is not one complete run of the final revision."})
    write(destination / "summary.json", {
        "case_count": len(ordered), "structural_pass": sum(item["structural_pass"] for item in ordered),
        "semantic_verdicts": dict(Counter(item["verdict"] for item in verdicts.values())),
        "median_seconds": statistics.median(item["elapsed_seconds"] for item in ordered),
        "data_entry": "HTTP API only", "base_url": "http://127.0.0.1:8501", "mode": "latest_per_case",
    })
    print(json.dumps(read(destination / "summary.json"), ensure_ascii=False))


if __name__ == "__main__":
    main()
