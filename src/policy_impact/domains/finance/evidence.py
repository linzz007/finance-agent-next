"""Turn-scoped evidence identities for finance research.

The model may summarize an article, but the runtime owns the identity of the
source fragment.  Evidence IDs are stable for the same source version and
line range, which lets claims and review output refer to a concrete fragment
instead of an unbounded artifact or a copied citation string.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
from typing import Any, Mapping


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


_MARKET_METRIC_FINGERPRINT_FIELDS = (
    "metric_id", "symbol", "start_date", "end_date", "start_close", "end_close",
    "change_pct", "unit", "price_unit", "latest_daily_change_pct", "status",
)


def _market_metric_digest(metric: Mapping[str, Any]) -> str:
    return _digest(json.dumps(
        {key: metric.get(key) for key in _MARKET_METRIC_FINGERPRINT_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ))


def _reference_evidence_id(item: "EvidenceFragment") -> str:
    aliases = (item.legacy_ref,) if item.legacy_ref else ()
    identity = json.dumps(
        {
            "source_type": item.source_type,
            "provenance": item.provenance,
            "source_ref": item.source_ref,
            "content_hash": item.content_hash,
            "published_at": item.published_at,
            "aliases": aliases,
            "metadata": dict(item.metadata or {}),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return "evidence_" + _digest(identity)


@dataclass(frozen=True)
class EvidenceFragment:
    evidence_id: str
    source_type: str
    provenance: str
    source_ref: str
    content_hash: str
    retrieved_at: str
    published_at: str = ""
    article_id: str = ""
    author: str = ""
    line_start: int | None = None
    line_end: int | None = None
    source_url: str = ""
    excerpt: str = ""
    legacy_ref: str = ""
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceLedger:
    """Collect one canonical evidence namespace for a finance turn.

    Older report fields use metric/document/fact IDs directly.  They are kept as
    aliases, but every alias resolves to one canonical ``evidence_*`` record.
    This lets old reports remain readable while new workflows and subagents share
    the same evidence contract.
    """

    def __init__(self) -> None:
        self._items: dict[str, EvidenceFragment] = {}
        self._aliases: dict[str, str] = {}

    def add_reference(
        self,
        *,
        source_type: str,
        provenance: str,
        source_ref: str,
        content_hash: str = "",
        retrieved_at: str = "",
        published_at: str = "",
        line_start: int | None = None,
        line_end: int | None = None,
        source_url: str = "",
        excerpt: str = "",
        aliases: tuple[str, ...] | list[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> EvidenceFragment:
        """Register any source kind and bind its old IDs as aliases."""
        alias_values = tuple(dict.fromkeys(str(value) for value in aliases if str(value)))
        identity = json.dumps(
            {
                "source_type": source_type,
                "provenance": provenance,
                "source_ref": source_ref,
                "content_hash": content_hash,
                "published_at": published_at,
                "aliases": alias_values,
                "metadata": dict(metadata or {}),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        evidence_id = "evidence_" + _digest(identity)
        item = EvidenceFragment(
            evidence_id=evidence_id,
            source_type=str(source_type),
            provenance=str(provenance),
            source_ref=str(source_ref),
            content_hash=str(content_hash),
            retrieved_at=str(retrieved_at),
            published_at=str(published_at),
            line_start=line_start,
            line_end=line_end,
            source_url=str(source_url),
            # Article reads are already line-bounded. Preserve the observed text so
            # downstream review can inspect every cited line, not just its opening.
            excerpt=(str(excerpt or "") if source_type in {"author_article", "public_web_page"}
                     else str(excerpt or "")[:1600]),
            legacy_ref=alias_values[0] if alias_values else "",
            metadata=dict(metadata or {}),
        )
        self._items[evidence_id] = item
        for alias in alias_values:
            self._aliases[alias] = evidence_id
        return item

    def add_article(self, reference: Mapping[str, Any], *, excerpt: str = "") -> EvidenceFragment:
        article_id = str(reference.get("article_id") or "")
        content_hash = str(reference.get("content_hash") or "")
        line_start = int(reference.get("line_start") or 1)
        line_end = int(reference.get("line_end") or line_start)
        source_ref = str(reference.get("path") or reference.get("source_url") or article_id)
        item = self.add_reference(
            source_type="author_article",
            provenance="author_view",
            source_ref=source_ref,
            content_hash=content_hash,
            retrieved_at=str(reference.get("retrieved_at") or ""),
            published_at=str(reference.get("published_at") or ""),
            source_url=str(reference.get("source_url") or ""),
            excerpt=excerpt,
            aliases=(article_id,),
            metadata={"article_id": article_id, "author": str(reference.get("author") or ""),
                      "line_start": line_start, "line_end": line_end,
                      "observed_excerpt_length": len(excerpt),
                      "observed_excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest()},
        )
        item = EvidenceFragment(
            **{**asdict(item), "article_id": article_id,
               "author": str(reference.get("author") or ""),
               "line_start": line_start, "line_end": line_end}
        )
        self._items[item.evidence_id] = item
        return item

    def add_public_source(self, reference: Mapping[str, Any], *, excerpt: str = "") -> EvidenceFragment:
        source_ref = str(reference.get("url") or "")
        content_hash = str(reference.get("sha256") or "")
        return self.add_reference(
            source_type="official_source",
            # Fetching a primary page proves retrieval and provenance only.  A
            # claim becomes verified only after a verifier binds it to a passage.
            provenance="retrieved_primary_source",
            source_ref=source_ref,
            content_hash=content_hash,
            retrieved_at=str(reference.get("retrieved_at") or ""),
            line_start=int(reference.get("line_start") or 1),
            line_end=int(reference.get("line_end") or 1),
            source_url=source_ref,
            excerpt=excerpt,
            aliases=(source_ref,),
            metadata={
                "anchor": str(reference.get("anchor") or ""),
                "content_type": str(reference.get("content_type") or ""),
            },
        )

    def add_market_metric(self, metric: Mapping[str, Any], *, snapshot_id: str = "") -> EvidenceFragment:
        legacy_ref = str(metric.get("metric_id") or metric.get("symbol") or "")
        symbol = str(metric.get("symbol") or "")
        unit = str(metric.get("unit") or "")
        start_date = str(metric.get("start_date") or "")
        end_date = str(metric.get("end_date") or "")
        audit_fields = (
            "name", "symbol", "start_date", "end_date", "latest_trade_date",
            "start_close", "end_close", "change_pct", "latest_daily_change_pct",
            "unit", "price_unit", "price_basis", "day_basis", "source_url",
        )
        audit_excerpt = json.dumps(
            {key: metric[key] for key in audit_fields if key in metric},
            ensure_ascii=False, sort_keys=True, default=str,
        )
        return self.add_reference(
            source_type="market_metric",
            provenance="calculated_observation",
            source_ref=f"{snapshot_id}:{symbol}",
            content_hash=_digest(json.dumps(dict(metric), ensure_ascii=False, sort_keys=True, default=str)),
            retrieved_at=str(metric.get("retrieved_at") or ""),
            published_at=end_date,
            source_url=str(metric.get("source_url") or ""),
            excerpt=audit_excerpt,
            aliases=(legacy_ref,),
            metadata={
                "metric_id": legacy_ref,
                "symbol": symbol,
                "snapshot_id": str(snapshot_id or ""),
                "unit": unit,
                "price_unit": str(metric.get("price_unit") or ""),
                "start_date": start_date,
                "end_date": end_date,
                "requested_start_date": str(metric.get("requested_start_date") or ""),
                "requested_end_date": str(metric.get("requested_end_date") or ""),
                "source_url": str(metric.get("source_url") or ""),
                "metric_digest": _market_metric_digest(metric),
            },
        )

    def validate_market_metric(self, evidence_id: str, metric: Mapping[str, Any]) -> tuple[bool, str]:
        """Check that a market citation still matches its canonical ledger item."""
        item = self.get(evidence_id)
        if item is None:
            return False, f"unknown market evidence_id: {evidence_id}"
        if item.source_type != "market_metric":
            return False, f"evidence_id is not a market metric: {evidence_id}"
        metadata = item.metadata or {}
        expected = {
            "snapshot_id": str(metric.get("snapshot_id") or ""),
            "symbol": str(metric.get("symbol") or ""),
            "unit": str(metric.get("unit") or ""),
            "start_date": str(metric.get("start_date") or ""),
            "end_date": str(metric.get("end_date") or ""),
        }
        for key, value in expected.items():
            if not value or str(metadata.get(key) or "") != value:
                return False, f"market evidence field mismatch: {key}"
        if str(metadata.get("metric_digest") or "") != _market_metric_digest(metric):
            return False, "market evidence metric value mismatch"
        expected_ref = f"{expected['snapshot_id']}:{expected['symbol']}"
        if item.source_ref != expected_ref:
            return False, "market evidence source_ref does not match snapshot and symbol"
        return True, ""

    def add_source_document(self, document: Mapping[str, Any]) -> EvidenceFragment:
        legacy_ref = str(document.get("document_id") or document.get("url") or "")
        return self.add_reference(
            source_type="source_document",
            provenance="retrieved_public_source",
            source_ref=str(document.get("url") or legacy_ref),
            content_hash=str(document.get("content_hash") or ""),
            retrieved_at=str(document.get("retrieved_at") or ""),
            published_at=str(document.get("published_at") or ""),
            source_url=str(document.get("url") or ""),
            excerpt=str(document.get("text") or ""),
            aliases=(legacy_ref,),
            metadata={"document_id": legacy_ref, "publisher": str(document.get("publisher") or ""),
                      "kind": str(document.get("kind") or "")},
        )

    def add_wiki_fact(self, fact: Mapping[str, Any]) -> EvidenceFragment:
        legacy_ref = str(fact.get("fact_id") or "")
        value = fact.get("value")
        return self.add_reference(
            source_type="wiki_fact",
            provenance="user_context",
            source_ref=str(fact.get("source_path") or legacy_ref),
            content_hash=_digest(json.dumps({"value": value, "source": fact.get("source")},
                                             ensure_ascii=False, sort_keys=True, default=str)),
            retrieved_at=str(fact.get("updated_at") or ""),
            source_url=str(fact.get("source") or ""),
            excerpt=str(value or ""),
            aliases=(legacy_ref,),
            metadata={"fact_id": legacy_ref, "status": fact.get("status"),
                      "confidence": fact.get("confidence")},
        )

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(self._items)

    def contains(self, evidence_id: str) -> bool:
        key = str(evidence_id or "")
        return key in self._items or key in self._aliases

    def get(self, evidence_id: str) -> EvidenceFragment | None:
        key = str(evidence_id or "")
        return self._items.get(key) or self._items.get(self._aliases.get(key, ""))

    def canonical_id(self, evidence_id: str) -> str:
        key = str(evidence_id or "")
        return key if key in self._items else self._aliases.get(key, key)

    def canonical_ids(self, refs: list[str] | tuple[str, ...]) -> list[str]:
        return [self.canonical_id(ref) for ref in refs]

    @property
    def aliases(self) -> dict[str, str]:
        return dict(self._aliases)

    def bundle(self) -> dict[str, Any]:
        items = [item.to_dict() for item in self._items.values()]
        return {
            "schema_version": "finance.evidence_bundle.v1",
            "evidence_ref_schema": "finance.evidence_ref.v1",
            "evidence_ids": list(self.evidence_ids),
            "aliases": self.aliases,
            "evidence": items,
            "count": len(items),
        }

    def merge_bundle(self, bundle: Mapping[str, Any]) -> tuple[str, ...]:
        """Merge child evidence into this turn's canonical namespace.

        Child agents use their own read-only gateway so permissions and audit
        remain isolated. Their evidence is nevertheless part of the same
        research turn after the child returns; copying the immutable fragment
        and aliases makes the parent trace replayable without re-reading files.
        """
        merged: list[str] = []
        if not isinstance(bundle, Mapping):
            return ()
        for raw in bundle.get("evidence") or []:
            if not isinstance(raw, Mapping):
                continue
            evidence_id = str(raw.get("evidence_id") or "").strip()
            source_ref = str(raw.get("source_ref") or "").strip()
            if not evidence_id or not source_ref:
                continue
            item = EvidenceFragment(
                evidence_id=evidence_id,
                source_type=str(raw.get("source_type") or ""),
                provenance=str(raw.get("provenance") or ""),
                source_ref=source_ref,
                content_hash=str(raw.get("content_hash") or ""),
                retrieved_at=str(raw.get("retrieved_at") or ""),
                published_at=str(raw.get("published_at") or ""),
                article_id=str(raw.get("article_id") or ""),
                author=str(raw.get("author") or ""),
                line_start=raw.get("line_start"),
                line_end=raw.get("line_end"),
                source_url=str(raw.get("source_url") or ""),
                excerpt=str(raw.get("excerpt") or ""),
                legacy_ref=str(raw.get("legacy_ref") or ""),
                metadata=dict(raw.get("metadata") or {}),
            )
            if evidence_id != _reference_evidence_id(item):
                raise ValueError("child evidence bundle contains an invalid evidence_id")
            if item.source_type == "market_metric":
                metadata = item.metadata or {}
                required = ("snapshot_id", "symbol", "unit", "start_date", "end_date", "metric_digest")
                if any(not str(metadata.get(key) or "") for key in required):
                    raise ValueError("child market evidence is missing canonical metric metadata")
            self._items.setdefault(evidence_id, item)
            merged.append(evidence_id)
        for alias, canonical in (bundle.get("aliases") or {}).items():
            alias_text = str(alias).strip()
            canonical_text = str(canonical).strip()
            if alias_text and canonical_text and canonical_text in self._items:
                self._aliases[alias_text] = canonical_text
        return tuple(dict.fromkeys(merged))

    def validate(self, refs: list[str] | tuple[str, ...]) -> None:
        unknown = [str(ref) for ref in refs if not self.contains(str(ref))]
        if unknown:
            raise ValueError("unknown evidence_id(s): " + ", ".join(unknown))


__all__ = ["EvidenceFragment", "EvidenceLedger"]
