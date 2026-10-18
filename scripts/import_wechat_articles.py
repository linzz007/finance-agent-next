"""Batch-import public WeChat exports or URLs into the finance article corpus."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from policy_impact.domains.finance.wechat_import import import_sources


def _load_local_env() -> None:
    """Use the same local article-root configuration as the API process."""
    for path in (ROOT / ".env", ROOT / ".env.local"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value:
                # .env.local has the same precedence as the API launcher.
                os.environ[key] = value


def main() -> None:
    _load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, default=[], help="Markdown/HTML file or directory; repeatable")
    parser.add_argument("--urls-file", type=Path, help="Text file containing one public WeChat URL per line")
    parser.add_argument("--root", type=Path, help="Article root; defaults to FINANCE_ARTICLE_ROOT")
    parser.add_argument("--author", default="", help="Author folder; recommended for exported files")
    parser.add_argument("--company-id", default="finance_observer")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-sync", action="store_true")
    parser.add_argument("--report", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    if not args.input and not args.urls_file:
        parser.error("provide --input and/or --urls-file")
    urls = []
    if args.urls_file:
        urls = [
            line.strip()
            for line in args.urls_file.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    result = import_sources(
        inputs=args.input,
        urls=urls,
        root=args.root,
        author=args.author,
        company_id=args.company_id,
        dry_run=args.dry_run,
        sync=not args.no_sync,
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
