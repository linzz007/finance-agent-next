"""Run-local, evidence-bound working records for autonomous finance research."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from calendar import month_name, monthrange
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from policy_impact.company_wiki.loader import company_dir
from policy_impact.domains.finance.article_corpus import ArticleCorpus
from policy_impact.domains.finance.evidence import EvidenceLedger
from policy_impact.domains.finance.research_records import ResearchRecords, SCHEMA as V2_SCHEMA, ID_RE as V2_ID_RE


SCHEMA = "finance.research_artifact.v1"
KINDS = {"article_card", "comparison", "review", "research_note"}
CLAIM_KINDS = {
    "author_view", "objective_fact", "prediction", "causal_explanation", "model_inference",
}
RESEARCH_CONFIDENCE_LABELS = {"high", "medium", "low"}
RELATIONS = {"agreement", "disagreement", "change_over_time", "complementary"}
REVIEW_STATUSES = {
    "viewpoint_faithful", "supported", "refuted", "insufficient_evidence",
    "not_due", "reasoned",
}
PREFIXES = {
    "article_card": "card", "comparison": "comparison", "review": "review",
    "research_note": "note",
}
ID_RE = re.compile(r"(?:card|comparison|review|note)_[0-9a-f]{24}\Z")
RUN_RE = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")


def _text(value: Any, name: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"{name} must be text of at most {maximum} characters")
    return value.strip()


def _items(value: Any, name: str, maximum: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum or any(
        not isinstance(item, dict) for item in value
    ):
        raise ValueError(f"{name} must contain 1-{maximum} objects")
    return value


def _text_list(value: Any, name: str, maximum: int, item_limit: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{name} must contain at most {maximum} strings")
    return [_text(item, name, item_limit) for item in value]


def _identity(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:24]


def _horizon_end(value: str, published_at: str) -> date | None:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    month = re.fullmatch(r"(20\d{2})[-年](0?[1-9]|1[0-2])(?:月)?", text)
    if month:
        year, number = map(int, month.groups())
        return date(year, number, monthrange(year, number)[1])
    quarter = re.fullmatch(r"(20\d{2})[- ]?Q([1-4])", text, re.I)
    if quarter:
        year, number = map(int, quarter.groups())
        end_month = number * 3
        return date(year, end_month, monthrange(year, end_month)[1])
    chinese_quarter = re.fullmatch(r"(?:(20\d{2})年)?(?:第)?([一二三四])季度", text)
    if chinese_quarter:
        try:
            published = date.fromisoformat(published_at)
        except ValueError:
            return None
        year = int(chinese_quarter.group(1) or published.year)
        number = "一二三四".index(chinese_quarter.group(2)) + 1
        end_month = number * 3
        return date(year, end_month, monthrange(year, end_month)[1])
    month = re.fullmatch(r"(0?[1-9]|1[0-2])月", text)
    if month:
        try:
            published = date.fromisoformat(published_at)
        except ValueError:
            return None
        number = int(month.group(1))
        year = published.year + (number < published.month)
        return date(year, number, monthrange(year, number)[1])
    month_names = {name.casefold(): number for number, name in enumerate(month_name)
                   if name}
    english_month = re.fullmatch(r"([A-Za-z]+)(?:\s+(20\d{2}))?", text)
    if english_month and english_month.group(1).casefold() in month_names:
        number = month_names[english_month.group(1).casefold()]
        if english_month.group(2):
            year = int(english_month.group(2))
        else:
            try:
                published = date.fromisoformat(published_at)
            except ValueError:
                return None
            year = published.year + (number < published.month)
        return date(year, number, monthrange(year, number)[1])
    return None


def _quoted_horizon(quote: str, published_at: str) -> str:
    for pattern in (
        r"20\d{2}-\d{2}-\d{2}", r"20\d{2}[-年](?:0?[1-9]|1[0-2])月?",
        r"(?<!\d)(?:0?[1-9]|1[0-2])月", r"(?:20\d{2}年)?(?:第)?[一二三四]季度",
    ):
        match = re.search(pattern, quote)
        if match and (end := _horizon_end(match.group(), published_at)):
            return end.isoformat()
    return ""


def _source_available_by(ref: Any, as_of: date) -> bool:
    for value in (getattr(ref, "published_at", ""), getattr(ref, "retrieved_at", "")):
        try:
            observed = date.fromisoformat(str(value)[:10])
        except ValueError:
            continue
        return observed <= as_of
    return False


class ResearchArtifacts:
    """Persist structured working notes under one Runtime-generated finance run."""

    def __init__(
        self, company_id: str, run_id: str, session_id: str,
        corpus: ArticleCorpus, evidence: EvidenceLedger,
        read_refs: list[dict[str, Any]],
    ) -> None:
        self.corpus = corpus
        self.evidence = evidence
        self.read_refs = read_refs
        self.session_id = str(session_id or "")
        self.run_id = str(run_id or "")
        self.root: Path | None = None
        self.scope_root = company_dir(company_id)
        self.research = ResearchRecords(self)
        if self.session_id and RUN_RE.fullmatch(self.run_id):
            run_dir = company_dir(company_id) / "runs" / self.run_id
            self.root = run_dir / "research_artifacts"

    def call(
        self, operation: str, kind: str | None = None,
        payload: dict[str, Any] | None = None, artifact_id: str | None = None,
        schema_version: str = SCHEMA, research_task: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if self.root is None:
            raise PermissionError("research artifacts require a Runtime-bound session and run")
        if operation == "get" and isinstance(artifact_id, str) and V2_ID_RE.fullmatch(artifact_id):
            return self.research.get(artifact_id)
        if schema_version == V2_SCHEMA:
            return self.research.call(operation, kind=kind, payload=payload, artifact_id=artifact_id,
                                      research_task=research_task, cursor=cursor)
        if schema_version != SCHEMA or research_task is not None or cursor is not None:
            raise ValueError("unsupported research artifact version or version-specific fields")
        if operation == "save":
            if kind not in KINDS or not isinstance(payload, dict):
                raise ValueError("save requires a known kind and object payload")
            return self._save(kind, payload)
        if operation == "get":
            return self._get(_text(artifact_id, "artifact_id", 40))
        if operation == "list":
            return self._list(kind)
        raise ValueError("operation must be save, get or list")

    def _directory(self, *, create: bool = False) -> Path:
        assert self.root is not None
        run_dir = self.root.parent
        directory = self.root
        while True:
            if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()):
                raise PermissionError("research artifact directory cannot be a symlink or junction")
            if directory == self.scope_root:
                break
            if directory == directory.parent:
                raise PermissionError("research artifact directory escaped its workspace")
            directory = directory.parent
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def _file(self, artifact_id: str) -> Path:
        if not ID_RE.fullmatch(artifact_id):
            raise ValueError("artifact_id must be a generated research artifact ID")
        root = self._directory()
        path = root / f"{artifact_id}.json"
        if path.is_symlink():
            raise PermissionError("research artifact cannot be a symlink")
        return path

    def _save(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind == "article_card":
            body = self._card(payload)
            prefix = "card"
        elif kind == "comparison":
            body = self._comparison(payload)
            prefix = "comparison"
        elif kind == "review":
            body = self._review(payload)
            prefix = "review"
        else:
            body = self._note(payload)
            prefix = "note"
        artifact_id = f"{prefix}_{_identity(body)}"
        record = {
            "schema_version": SCHEMA, "artifact_id": artifact_id,
            "kind": kind, "session_id": self.session_id, "run_id": self.run_id,
            "payload": body,
        }
        root = self._directory(create=True)
        path = self._file(artifact_id)
        if path.exists():
            existing = self._get(artifact_id)
            if existing["payload"] != body:
                raise ValueError("research artifact ID collision")
        else:
            fd, temporary = tempfile.mkstemp(prefix="artifact.", suffix=".tmp", dir=root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(record, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return self._summary(record)

    def _card(self, raw: dict[str, Any]) -> dict[str, Any]:
        article_id = _text(raw.get("article_id"), "article_id", 80)
        article = self.corpus.get_article(article_id)
        if article is None:
            raise ValueError("article is not in the current corpus")
        depth = raw.get("depth")
        if depth not in {"skim", "deep"}:
            raise ValueError("depth must be skim or deep")
        lines = article["content"].splitlines()
        reads = [ref for ref in self.read_refs if ref.get("article_id") == article_id
                 and ref.get("content_hash") == article["current_hash"]]
        observed = {line for ref in reads for line in range(ref["line_start"], ref["line_end"] + 1)}
        if not observed:
            raise ValueError("article must be read in this turn before saving a card")
        if depth == "deep" and len(observed) != len(lines):
            raise ValueError("deep article card requires all source lines to be read")
        claims = []
        for item in _items(raw.get("claims"), "claims", 50):
            kind = item.get("kind")
            if kind not in CLAIM_KINDS:
                raise ValueError("claim kind is invalid")
            start, end = item.get("line_start"), item.get("line_end")
            if (type(start) is not int or type(end) is not int or
                    not 1 <= start <= end <= len(lines) or end - start > 30 or
                    not set(range(start, end + 1)).issubset(observed)):
                observed_ranges = [
                    f"{ref['line_start']}-{ref['line_end']}" for ref in reads
                ]
                raise ValueError(
                    "claim lines must be within observed source lines; "
                    f"claim_lines={start}-{end}, article_lines=1-{len(lines)}, "
                    f"observed_ranges={observed_ranges}. Read the missing lines or "
                    "correct the claim range before retrying."
                )
            quote = _text(item.get("quote"), "quote", 2000)
            if quote not in "\n".join(lines[start - 1:end]):
                source_excerpt = "\n".join(lines[start - 1:end])[:350]
                raise ValueError(
                    "claim quote must appear verbatim in the cited source lines; "
                    f"article_id={article_id}, claim_lines={start}-{end}, "
                    f"observed_text_prefix={source_excerpt!r}. Copy an exact contiguous "
                    "quote from those lines or correct the line range."
                )
            evidence_id = _text(item.get("evidence_id"), "evidence_id", 80)
            ref = self.evidence.get(evidence_id)
            if (ref is None or ref.evidence_id != evidence_id or ref.source_type != "author_article"
                    or ref.article_id != article_id or ref.content_hash != article["current_hash"]
                    or ref.line_start is None or ref.line_end is None
                    or not ref.line_start <= start <= end <= ref.line_end):
                matching_ids = [
                    candidate.evidence_id for read in reads
                    if read["line_start"] <= start <= end <= read["line_end"]
                    if (candidate := self.evidence.get(str(read.get("evidence_id") or "")))
                    is not None
                ]
                raise ValueError(
                    "claim evidence_id must bind the observed article version and lines; "
                    f"article_id={article_id}, claim_lines={start}-{end}, "
                    f"matching_evidence_ids={matching_ids}. Use the ID from the article.read "
                    "result covering those exact lines; a different read segment has a different ID."
                )
            conditions = _text(item.get("conditions", ""), "conditions", 500, required=False)
            horizon = _text(item.get("horizon", ""), "horizon", 200, required=False)
            confidence_label = item.get("claim_confidence_label")
            if confidence_label is None:
                confidence_label = item.get("research_confidence")
            if confidence_label is not None:
                confidence_label = _text(
                    confidence_label, "claim_confidence_label", 10
                )
                if confidence_label not in RESEARCH_CONFIDENCE_LABELS:
                    raise ValueError("claim_confidence_label must be high, medium or low")
            confidence_reason = _text(
                item.get("confidence_reason", ""), "confidence_reason", 500, required=False
            )
            if kind == "prediction":
                if not horizon:
                    horizon = _quoted_horizon(quote, article["published_at"])
                if not conditions and re.search(r"正常剧本下|如果|若是|若|只要", quote):
                    conditions = quote[:500]
            claim = {
                "kind": kind, "statement": _text(item.get("statement"), "statement", 1200),
                "quote": quote, "line_start": start, "line_end": end,
                "evidence_id": evidence_id,
                "conditions": conditions,
                "horizon": horizon,
            }
            if confidence_label is not None:
                claim["claim_confidence_label"] = confidence_label
                claim["confidence_reason"] = confidence_reason
            claim["claim_id"] = f"claim_{_identity({'article_id': article_id, **claim})}"
            claims.append(claim)
        if len({claim["claim_id"] for claim in claims}) != len(claims):
            raise ValueError("duplicate claims in article card")
        return {
            "article_id": article_id, "source_hash": article["current_hash"],
            "author": article["author"], "published_at": article["published_at"],
            "title": article["title"], "source_url": article["source_url"],
            "question": _text(raw.get("question"), "question", 500),
            "depth": depth, "line_count": len(lines), "observed_lines": len(observed),
            "claims": claims,
            "limitations": _text(raw.get("limitations", ""), "limitations", 1000, required=False),
        }

    def _comparison(self, raw: dict[str, Any]) -> dict[str, Any]:
        ids = raw.get("card_ids")
        if (not isinstance(ids, list) or not 2 <= len(ids) <= 20
                or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids)):
            raise ValueError("comparison requires 2-20 distinct card_ids")
        cards = [self._require_kind(item, "article_card") for item in ids]
        if len({card["payload"]["article_id"] for card in cards}) < 2:
            raise ValueError("comparison requires at least two distinct articles")
        claim_sources = {
            claim["claim_id"]: card["payload"]["article_id"]
            for card in cards for claim in card["payload"]["claims"]
        }
        relations = []
        for item in _items(raw.get("relations"), "relations", 30):
            claim_ids = item.get("claim_ids")
            if (item.get("type") not in RELATIONS or not isinstance(claim_ids, list)
                    or not 2 <= len(claim_ids) <= 12
                    or any(not isinstance(claim_id, str) for claim_id in claim_ids)
                    or any(claim_id not in claim_sources for claim_id in claim_ids)
                    or len({claim_sources[claim_id] for claim_id in claim_ids}) < 2):
                supplied = {str(claim_id): claim_sources.get(claim_id)
                            for claim_id in claim_ids or []} if isinstance(claim_ids, list) else {}
                raise ValueError(
                    "relation must bind claims from at least two selected articles; "
                    f"claim_article_map={supplied}"
                )
            relations.append({
                "type": item["type"], "claim_ids": list(dict.fromkeys(claim_ids)),
                "analysis": _text(item.get("analysis"), "analysis", 1500),
                "qualification": _text(item.get("qualification", ""), "qualification", 500, required=False),
            })
        relation_audit = []
        for item in (_items(raw["relation_audit"], "relation_audit", 30)
                     if raw.get("relation_audit") else []):
            index = item.get("relation_index")
            verdict = item.get("verdict")
            revised_type = item.get("revised_type", "")
            if (not isinstance(index, int) or isinstance(index, bool) or index < 0
                    or verdict not in {"retain", "revise", "reject"}
                    or revised_type not in {"", *RELATIONS}):
                raise ValueError("relation_audit has invalid index, verdict or revised_type")
            relation_audit.append({
                "relation_index": index, "verdict": verdict,
                "reason": _text(item.get("reason"), "reason", 1000),
                "revised_type": revised_type,
            })
        if len({item["relation_index"] for item in relation_audit}) != len(relation_audit):
            raise ValueError("relation_audit indices must be distinct")
        return {
            "question": _text(raw.get("question"), "question", 500),
            "card_ids": ids, "relations": relations,
            "limitations": _text(raw.get("limitations", ""), "limitations", 1000, required=False),
            "relation_audit": relation_audit,
        }

    def _review(self, raw: dict[str, Any]) -> dict[str, Any]:
        if "as_of" in raw:
            raise ValueError(
                "review payload.as_of is misplaced: put YYYY-MM-DD as_of inside each "
                "checks[] item, or use research.verify(card_ids, as_of)"
            )
        ids = raw.get("card_ids")
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 20
                or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids)):
            raise ValueError("review requires 1-20 distinct card_ids")
        cards = [self._require_kind(item, "article_card") for item in ids]
        claims = {claim["claim_id"]: claim for card in cards for claim in card["payload"]["claims"]}
        claim_publication_dates = {
            claim["claim_id"]: str(card["payload"].get("published_at") or "")
            for card in cards for claim in card["payload"]["claims"]
        }
        article_urls = {
            claim["claim_id"]: str(card["payload"].get("source_url") or "").rstrip("/").casefold()
            for card in cards for claim in card["payload"]["claims"]
        }
        checks = []
        for item in _items(raw.get("checks"), "checks", 30):
            if not isinstance(item.get("alternative_explanations", []), list):
                raise ValueError(
                    "review checks[].alternative_explanations must be a list of strings; "
                    "use research.verify for model-assisted typed checks"
                )
            if not isinstance(item.get("conflicts", []), list):
                raise ValueError(
                    "review checks[].conflicts must be a list of "
                    "{evidence_id, description} objects; use research.verify for typed checks"
                )
            claim_id = item.get("claim_id")
            status = item.get("status")
            if not isinstance(claim_id, str) or claim_id not in claims or status not in REVIEW_STATUSES:
                raise ValueError("review claim_id or status is invalid")
            claim = claims[claim_id]
            if item.get("claim_type") != claim["kind"]:
                raise ValueError("review claim_type does not match the saved article claim")
            refs = item.get("evidence_refs")
            if not isinstance(refs, list) or len(refs) > 10 or any(
                not isinstance(ref, str) or not ref.startswith("evidence_") for ref in refs
            ):
                raise ValueError("evidence_refs must be canonical EvidenceRef IDs")
            self.evidence.validate(refs)
            if claim["evidence_id"] not in refs:
                raise ValueError("review must retain the original article claim EvidenceRef")
            as_of = _text(item.get("as_of"), "as_of", 10)
            try:
                review_date = date.fromisoformat(as_of)
                if review_date.isoformat() != as_of:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("as_of must be a valid YYYY-MM-DD date") from exc
            horizon_end = _horizon_end(claim["horizon"], claim_publication_dates[claim_id])
            if status == "viewpoint_faithful" and claim["kind"] not in {
                "author_view", "prediction", "causal_explanation",
            }:
                raise ValueError("viewpoint_faithful is for author viewpoints, forecasts or attribution")
            if status in {"supported", "refuted"}:
                if claim["kind"] not in {"objective_fact", "prediction"}:
                    raise ValueError("decisive review is limited to checkable facts or due forecasts")
                if claim["kind"] == "prediction" and (
                    horizon_end is None or horizon_end > review_date
                ):
                    raise ValueError(
                        "a prediction cannot be supported or refuted before a parseable "
                        "forecast horizon has passed"
                    )
                external = [self.evidence.get(ref) for ref in refs]
                if not any(ref and ref.source_type in {
                    "official_source", "public_web_page", "market_metric", "market_bar_series",
                } and (
                    not article_urls[claim_id]
                    or str(ref.source_url or ref.source_ref).rstrip("/").casefold()
                    != article_urls[claim_id]
                ) and _source_available_by(ref, review_date) for ref in external):
                    raise ValueError(
                        "decisive review requires separately read public or market evidence "
                        "available by as_of"
                    )
            if status == "not_due" and (
                claim["kind"] != "prediction" or horizon_end is None
                or horizon_end <= review_date
            ):
                raise ValueError("not_due requires a parseable future forecast horizon")
            if status == "reasoned" and claim["kind"] not in {
                "causal_explanation", "model_inference",
            }:
                raise ValueError("reasoned is only for causal or model inference")
            conflicts = item.get("conflicts", [])
            if not isinstance(conflicts, list) or len(conflicts) > 8 or any(
                not isinstance(conflict, dict) for conflict in conflicts
            ):
                raise ValueError("conflicts must contain at most eight evidence-bound objects")
            normalized_conflicts = []
            for conflict in conflicts:
                evidence_id = _text(conflict.get("evidence_id"), "conflict.evidence_id", 80)
                if evidence_id not in refs:
                    raise ValueError("conflict evidence must also be in evidence_refs")
                normalized_conflicts.append({
                    "evidence_id": evidence_id,
                    "description": _text(conflict.get("description"), "conflict.description", 500),
                })
            checks.append({
                "claim_id": claim_id, "claim_type": claim["kind"], "status": status,
                "evidence_refs": list(dict.fromkeys(refs)),
                "conflicts": normalized_conflicts,
                "alternative_explanations": _text_list(
                    item.get("alternative_explanations", []), "alternative_explanations", 8, 400,
                ),
                "suggested_revision": _text(
                    item.get("suggested_revision"), "suggested_revision", 1200,
                ),
                "as_of": as_of,
                "reason": _text(item.get("reason"), "reason", 1200),
            })
        if len({item["claim_id"] for item in checks}) != len(checks):
            raise ValueError("review may assess each claim only once")
        return {
            "card_ids": ids, "checks": checks,
            "limitations": _text(raw.get("limitations", ""), "limitations", 1000, required=False),
            "review_scope": "main_agent_recorded",
        }

    def _note(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Validate a compact, run-local research state without judging it."""
        entries = []
        for raw_entry in _items(raw.get("entries"), "entries", 200):
            entry_type = _text(raw_entry.get("type"), "entries[].type", 80)
            statement = _text(raw_entry.get("statement"), "entries[].statement", 2500)
            raw_refs = raw_entry.get("evidence_refs")
            if (not isinstance(raw_refs, list) or not 1 <= len(raw_refs) <= 30
                    or any(not isinstance(item, str) for item in raw_refs)):
                raise ValueError(
                    "entries[].evidence_refs must contain 1-30 canonical EvidenceRef IDs"
                )
            evidence_refs = []
            for raw_ref in dict.fromkeys(raw_refs):
                evidence_id = _text(raw_ref, "entries[].evidence_refs[]", 100)
                ref = self.evidence.get(evidence_id)
                if ref is None or ref.evidence_id != evidence_id:
                    raise ValueError(
                        "research note evidence_refs must use canonical EvidenceRef IDs "
                        f"from the current run: {evidence_id}"
                    )
                if ref.source_type == "author_article":
                    self.corpus.sync()
                    article = self.corpus.get_article(ref.article_id)
                    if article is None or article["current_hash"] != ref.content_hash:
                        raise ValueError(
                            "research note is stale because an article EvidenceRef changed: "
                            f"{ref.article_id}"
                        )
                evidence_refs.append(evidence_id)
            article_id = raw_entry.get("article_id", "")
            if article_id:
                article_id = _text(article_id, "entries[].article_id", 80)
                article_refs = {
                    ref.article_id for ref in (self.evidence.get(item) for item in evidence_refs)
                    if ref is not None and ref.article_id
                }
                if article_refs != {article_id}:
                    raise ValueError(
                        "entries[].article_id must match all cited article EvidenceRefs"
                    )
            raw_claim_ids = raw_entry.get("claim_ids", [])
            if (not isinstance(raw_claim_ids, list) or len(raw_claim_ids) > 50
                    or any(not isinstance(item, str) for item in raw_claim_ids)):
                raise ValueError("entries[].claim_ids must contain at most 50 strings")
            entry = {
                "type": entry_type,
                "statement": statement,
                "evidence_refs": evidence_refs,
            }
            if article_id:
                entry["article_id"] = article_id
            if raw_claim_ids:
                claim_ids = list(dict.fromkeys(
                    _text(item, "entries[].claim_ids[]", 100) for item in raw_claim_ids
                ))
                known_claims = self._claim_evidence_refs()
                for claim_id in claim_ids:
                    if (claim_id not in known_claims
                            or known_claims[claim_id] not in evidence_refs):
                        raise ValueError(
                            "entries[].claim_ids must identify saved current-run claims "
                            "bound to an entry EvidenceRef"
                        )
                entry["claim_ids"] = claim_ids
            for name, maximum in (("conditions", 1000), ("time_scope", 200)):
                value = raw_entry.get(name, "")
                if value:
                    entry[name] = _text(value, f"entries[].{name}", maximum)
            entries.append(entry)
        return {
            "question": _text(raw.get("question"), "question", 500),
            "purpose": _text(raw.get("purpose"), "purpose", 500),
            "entries": entries,
            "open_questions": _text_list(raw.get("open_questions", []), "open_questions", 30, 1000),
            "limitations": _text(raw.get("limitations", ""), "limitations", 1500, required=False),
        }

    def _claim_evidence_refs(self) -> dict[str, str]:
        root = self._directory()
        if not root.exists():
            return {}
        claims = {}
        for path in root.glob("card_*.json"):
            if not ID_RE.fullmatch(path.stem):
                continue
            card = self._require_kind(path.stem, "article_card")
            for claim in card["payload"]["claims"]:
                claims[claim["claim_id"]] = claim["evidence_id"]
        return claims

    def _require_kind(
        self, artifact_id: Any, kind: str, ancestors: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        record = self._get(_text(artifact_id, "artifact_id", 40), ancestors)
        if record["kind"] != kind:
            raise ValueError(f"artifact must be a {kind}")
        return record

    def _get(self, artifact_id: str, ancestors: frozenset[str] = frozenset()) -> dict[str, Any]:
        if artifact_id in ancestors:
            raise ValueError("cyclic research artifact reference")
        path = self._file(artifact_id)
        if not path.is_file():
            raise FileNotFoundError(f"research artifact not found: {artifact_id}")
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record.get("schema_version") != SCHEMA or record.get("artifact_id") != artifact_id
                or record.get("session_id") != self.session_id or record.get("run_id") != self.run_id
                or record.get("kind") not in KINDS or not isinstance(record.get("payload"), dict)
                or artifact_id != f"{PREFIXES[record['kind']]}_{_identity(record['payload'])}"):
            raise ValueError("research artifact identity or schema is invalid")
        if record["kind"] == "article_card":
            self.corpus.sync()
            article = self.corpus.get_article(record["payload"]["article_id"])
            if article is None or article["current_hash"] != record["payload"]["source_hash"]:
                raise ValueError("article card is stale because the source article changed")
        elif record["kind"] == "research_note":
            # Re-run source-version checks when reloading a note.  A note is
            # working memory, not a way to keep using an edited article.
            self._note(record["payload"])
        else:
            for card_id in record["payload"]["card_ids"]:
                self._require_kind(card_id, "article_card", ancestors | {artifact_id})
        return record

    def _list(self, kind: str | None) -> dict[str, Any]:
        if kind is not None and kind not in KINDS:
            raise ValueError("unknown research artifact kind")
        root = self._directory()
        if not root.exists():
            return {"schema_version": SCHEMA, "artifacts": [], "count": 0}
        rows = []
        for path in sorted(path for path in root.glob("*.json") if ID_RE.fullmatch(path.stem))[:100]:
            try:
                record = self._get(path.stem)
            except (ValueError, FileNotFoundError, KeyError, TypeError,
                    AttributeError, PermissionError, OSError):
                continue
            if kind is None or record["kind"] == kind:
                rows.append(self._summary(record))
        return {"schema_version": SCHEMA, "artifacts": rows, "count": len(rows)}

    @staticmethod
    def _summary(record: dict[str, Any]) -> dict[str, Any]:
        payload = record["payload"]
        return {
            "schema_version": SCHEMA, "artifact_id": record["artifact_id"],
            "kind": record["kind"],
            "article_id": payload.get("article_id"),
            "card_ids": payload.get("card_ids", []),
            "claim_count": len(payload.get("claims", [])),
            "relation_count": len(payload.get("relations", [])),
            "check_count": len(payload.get("checks", [])),
            "entry_count": len(payload.get("entries", [])),
            "evidence_ref_count": len({
                evidence_id
                for entry in payload.get("entries", [])
                for evidence_id in entry.get("evidence_refs", [])
            }),
        }


__all__ = ["ResearchArtifacts"]
