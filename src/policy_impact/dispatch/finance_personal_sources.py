"""Opt-in immutable private background; no profile writes or public-fact claims."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .contracts import DispatchError
from .finance_tool_contracts import PERSONAL_TOOLS, validate

SOURCE_SCHEMA = "finance.h2.personal_source.v1"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _descriptor(record, ref):
    return {"context_id": ref, **{key: record[key] for key in (
        "personal_kind", "source_path", "source_hash", "file_hash", "captured_at", "as_of", "approximate",
    ) if key in record}, "total_chars": len(record["text"])}


def _observation(name, payload, text, *, nonempty=True):
    value = {**payload, "tool": name, "ok": True, "status": "ok" if nonempty else "empty",
             "source_kind": "personal_context", "text": text, "content_hash": _hash(text)}
    return {**value, "observation_hash": _hash(_json(value))}


def personal_source_read(value, reader_task_id):
    if (not isinstance(value, dict) or value.get("tool") not in {"context.open", "company_wiki_search"}
            or value.get("ok") is not True or value.get("status") != "ok"
            or value.get("source_kind") != "personal_context"
            or not isinstance(value.get("text"), str) or not value["text"].strip()
            or value.get("content_hash") != _hash(value["text"])):
        return None
    try:
        if value.get("observation_hash") != _hash(_json({key: item for key, item in value.items()
                                                        if key != "observation_hash"})):
            return None
    except (ValueError, TypeError):
        return None
    if value["tool"] == "company_wiki_search":
        if not value.get("results") or value["text"] != _json(value["results"]):
            return None
    elif (not all(type(value.get(key)) is int for key in ("offset", "returned_chars", "total_chars"))
          or not 0 <= value["offset"] < value["offset"] + value["returned_chars"] <= value["total_chars"]
          or value["returned_chars"] != len(value["text"])):
        return None
    return {"source_kind": "personal_context", "reader_task_id": reader_task_id,
            **{key: value[key] for key in (
                "tool", "personal_kind", "context_id", "source_path", "source_hash", "file_hash",
                "captured_at", "as_of", "approximate", "content_hash", "offset", "returned_chars",
                "total_chars", "next_offset", "has_more", "source_versions",
            ) if key in value},
            "full_source_read": value["tool"] == "context.open" and value["offset"] == 0 and not value["has_more"]}


def remember_personal_source(execution, read, *, material_ref="", result_ref=None):
    source = {**read, "material_ref": material_ref, "result_ref": result_ref}
    sources = list(execution.get("personal_sources") or [])
    if source not in sources:
        sources.append(source)
    execution["personal_sources"] = sources


class FinancePersonalSources:
    def __init__(self, company_id: str, article_root: Path):
        from policy_impact.company_wiki.loader import company_dir

        company_dir(company_id)  # Validate trusted identity without reading private content.
        self.company_id = company_id
        self.article_root = Path(article_root)

    def _capture(self):
        from policy_impact.company_wiki.loader import (
            _validate_unique_facts,
            company_dir,
            parse_wiki_page,
            resolve_wiki_page_path,
        )
        from policy_impact.domains.finance.article_profile import _profile_path, parse_holdings

        captured_at = datetime.now(timezone.utc).isoformat()
        records, unavailable = [], []

        def read(path, source_path, kind):
            try:
                if path.absolute() != path.resolve():
                    raise ValueError("linked sources are unavailable")
                raw = path.read_bytes()
                text = raw.decode("utf-8-sig")
                record = {"schema_version": SOURCE_SCHEMA, "personal_kind": kind,
                          "source_path": source_path, "text": text, "source_hash": _hash(text),
                          "file_hash": hashlib.sha256(raw).hexdigest(), "captured_at": captured_at}
                if kind == "wiki":
                    record["wiki_page"] = parse_wiki_page(text, source_path)
                if kind == "holdings":
                    holdings = parse_holdings(text, source_path=source_path)
                    holdings.pop("source_path")
                    record.update(holdings=holdings, as_of=holdings["as_of"], approximate=holdings["approximate"])
                _json(record)  # Reject non-finite or otherwise unserializable private data.
                return record
            except (OSError, UnicodeError, ValueError, TypeError):
                unavailable.append({"source_path": source_path, "code": "personal_source_unavailable"})
                return None

        try:
            wiki_root = company_dir(self.company_id) / "wiki"
            if wiki_root.resolve() != wiki_root:
                raise ValueError("linked Wiki workspace")
            for path in sorted(wiki_root.rglob("*.md")):
                source_path = path.relative_to(wiki_root.parent).as_posix()
                record = read(resolve_wiki_page_path(self.company_id, source_path), source_path, "wiki")
                if record:
                    records.append(record)
            _validate_unique_facts([record["wiki_page"] for record in records])
        except (OSError, ValueError):
            records = []
            unavailable.append({"source_path": "wiki/", "code": "personal_source_unavailable"})
        if not records and not any(item["source_path"] == "wiki/" for item in unavailable):
            unavailable.append({"source_path": "wiki/", "code": "personal_source_unavailable"})
        for filename, kind in (("research_policy.md", "research_policy"), ("current_holdings.md", "holdings")):
            record = read(_profile_path(self.article_root, filename), filename, kind)
            if record:
                records.append(record)
        return records, unavailable

    @staticmethod
    async def _load(ref, capabilities, *, required=False):
        raw = await capabilities.load_context(ref)
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            record = None
        if not isinstance(record, dict) or record.get("schema_version") != SOURCE_SCHEMA:
            if required:
                raise ValueError("context_id must identify an authorized personal-source snapshot")
            return None
        payload = {key: item for key, item in record.items() if key != "snapshot_hash"}
        if (record.get("snapshot_hash") != _hash(_json(payload))
                or not isinstance(record.get("text"), str) or record.get("source_hash") != _hash(record["text"])):
            raise ValueError("personal snapshot identity or content hash is invalid")
        return record

    async def execute(self, name, arguments, capabilities, *, source_refs=(), allow_capture=False):
        if name not in PERSONAL_TOOLS:
            raise DispatchError("forbidden", "unsupported personal-context tool")
        arguments = validate(name, arguments)
        if name == "context.open":
            ref = arguments["context_id"]
            record = await self._load(ref, capabilities, required=True)
            text, offset = record["text"], arguments["offset"]
            if offset > len(text) or (offset == len(text) and text):
                raise ValueError("context offset is outside the frozen source")
            end = min(len(text), offset + (arguments["limit"] or 12000))
            payload = {**_descriptor(record, ref), "offset": offset, "returned_chars": end - offset,
                       "next_offset": end if end < len(text) else None, "has_more": end < len(text)}
            if record["personal_kind"] == "holdings" and offset == 0 and end == len(text):
                payload["holdings"] = record["holdings"]
            return _observation(name, payload, text[offset:end], nonempty=bool(text[offset:end].strip()))

        sources = []
        for ref in dict.fromkeys(source_refs):
            record = await self._load(ref, capabilities)
            if record:
                sources.append((ref, record))
        unavailable = []
        if name == "personal.catalog" and allow_capture and not sources:
            records, unavailable = await asyncio.to_thread(self._capture)
            for record in records:
                record["snapshot_hash"] = _hash(_json(record))
                sources.append((await capabilities.store_context(record), record))
        if name == "personal.catalog":
            return {"tool": name, "ok": True, "sources": [_descriptor(record, ref) for ref, record in sources],
                    "unavailable": unavailable, "scope": "root_snapshot" if allow_capture else "explicit_inputs"}

        from policy_impact.mcp_server.tools_company import rank_wiki_facts

        wiki_sources = [(ref, record) for ref, record in sources if record["personal_kind"] == "wiki"]
        facts = [{**fact, "context_id": ref, "source_hash": record["source_hash"]}
                 for ref, record in wiki_sources for fact in record["wiki_page"]["facts"]]
        results = rank_wiki_facts(facts, arguments["query"], arguments["top_k"])
        matches = {item["context_id"] for item in results}
        payload = {"personal_kind": "wiki_search", "query": arguments["query"], "results": results,
                   "source_versions": [_descriptor(record, ref) for ref, record in wiki_sources if ref in matches]}
        return _observation(name, payload, _json(results), nonempty=bool(results))
