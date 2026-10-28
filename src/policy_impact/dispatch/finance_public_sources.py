"""Opt-in reuse of safe legacy web tools with durable H2 source identities."""

from __future__ import annotations

from collections.abc import Mapping

from policy_impact.source_connectors.http_client import public_url

from .contracts import DispatchError
from .finance_market_sources import market_source_read
from .finance_tool_contracts import PUBLIC_TOOLS, validate


def public_source_read(value, reader_task_id):
    """Fetched pages/market reads are sources; leads and failures are not."""
    market_read = market_source_read(value, reader_task_id)
    if market_read is not None:
        return market_read
    if not isinstance(value, Mapping):
        return None
    if (value.get("tool") != "web.open" or value.get("ok") is not True
            or value.get("status") != "ok" or value.get("source_kind") != "public_web_page"
            or not isinstance(value.get("text"), str) or not value["text"].strip()
            or not isinstance(value.get("url"), str) or not public_url(value["url"])
            or not all(isinstance(value.get(key), str) and value[key]
                       for key in ("content_hash", "retrieved_at"))):
        return None
    if (not all(type(value.get(key)) is int for key in ("line_start", "line_end", "full_line_count"))
            or not 1 <= value["line_start"] <= value["line_end"] <= value["full_line_count"]
            or type(value.get("truncated")) is not bool):
        return None
    return {
        "source_kind": "public_web_page", "reader_task_id": reader_task_id,
        **{key: value[key] for key in (
            "url", "title", "author", "published_at", "retrieved_at", "content_hash",
            "line_start", "line_end", "full_line_count", "truncated", "next_start_line",
        ) if key in value},
    }


def remember_public_source(execution, read, *, material_ref="", result_ref=None):
    source = {**read, "material_ref": material_ref, "result_ref": result_ref}
    sources = list(execution.get("public_sources") or [])
    if source not in sources:
        sources.append(source)
    execution["public_sources"] = sources


class FinancePublicSources:
    """No cross-run mutable web cache or legacy evidence IDs escape this binding."""

    def execute(self, name, arguments):
        if name not in PUBLIC_TOOLS:
            raise DispatchError("forbidden", "unsupported public source tool")
        arguments = validate(name, arguments)
        from policy_impact.domains.finance.evidence import EvidenceLedger
        from policy_impact.domains.finance.web_research import WebResearch

        web = WebResearch(EvidenceLedger())
        try:
            result = web.search(**arguments) if name == "web.search" else web.open(**arguments)
        except DispatchError:
            raise
        except Exception:  # noqa: BLE001 - adapter errors are data, never raw provider text
            return {"tool": name, "ok": False, "status": "unavailable", "error": {
                "code": "public_source_unavailable",
                "message": "Public source acquisition failed; no page was read or externally verified.",
            }}
        payload = {key: value for key, value in result.items() if key != "evidence_id"}
        if name == "web.search":
            return {**payload, "tool": name, "ok": result.get("status") == "ok"}
        candidate = {**payload, "tool": name, "status": result.get("status", "ok"),
                     "ok": True, "source_kind": "public_web_page"}
        if public_source_read(candidate, None) is None:
            candidate["ok"] = False
        return candidate
