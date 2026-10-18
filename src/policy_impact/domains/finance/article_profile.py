"""File-authoritative, read-only research preferences and holdings."""

from __future__ import annotations

import json
from pathlib import Path
import re

from policy_impact.domains.finance.article_corpus import article_root


def _profile_path(root: Path | None, filename: str) -> Path:
    base = root or article_root()
    candidates = [base / filename, base.parent / filename]
    return next((path for path in candidates if path.is_file()), candidates[0])


def load_holdings(root: Path | None = None) -> dict:
    path = _profile_path(root, "current_holdings.md")
    text = path.read_text(encoding="utf-8-sig")
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.S)
    if len(blocks) != 1:
        raise ValueError("current_holdings.md must contain one JSON object")
    data = json.loads(blocks[0])
    if not isinstance(data, dict) or set(data) != {"as_of", "currency", "approximate", "positions"}:
        raise ValueError("invalid holdings schema")
    if not re.fullmatch(r"20\d\d-\d\d-\d\d", data["as_of"]):
        raise ValueError("invalid holdings date")
    if data["currency"] != "CNY" or data["approximate"] is not True:
        raise ValueError("holdings must be explicitly approximate CNY amounts")
    positions = data["positions"]
    if not isinstance(positions, list) or not 1 <= len(positions) <= 30:
        raise ValueError("invalid holdings list")
    for item in positions:
        if (not isinstance(item, dict) or set(item) != {"name", "amount"}
                or not isinstance(item["name"], str) or not item["name"].strip()
                or type(item["amount"]) not in (int, float) or item["amount"] < 0):
            raise ValueError("invalid position")
    return {**data, "approximate_total": sum(item["amount"] for item in positions),
            "source_path": str(path)}


def load_research_policy(root: Path | None = None) -> str:
    path = _profile_path(root, "research_policy.md")
    return path.read_text(encoding="utf-8-sig")[:8500]
