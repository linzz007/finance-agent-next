"""Run only the finance acquisition worker; no model calls or external messages.

Example: py -3 scripts/run_finance_worker.py --company-id finance_observer --once
Without --once this stays in the foreground until stopped. Nothing is collected
while the process is stopped; restart coalesces missed intervals into one window.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from policy_impact.domains.finance.scheduler import DEFAULT_INTERVAL_MINUTES, run_worker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--interval-minutes", type=float, default=DEFAULT_INTERVAL_MINUTES)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_worker(args.company_id, args.interval_minutes, once=args.once)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=True))
        return 1
    print(json.dumps(result, ensure_ascii=True))
    status = result.get("collection_status", result["status"])
    return 0 if status in {"ok", "paused", "stopped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
