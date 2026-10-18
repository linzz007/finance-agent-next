"""Public query planning, bounded candidate cleaning, and source-independent identity."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import re
import unicodedata
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from policy_impact.source_connectors.http_client import public_url


CACHE_VERSION = 8


def bounded_int(value: Any, default: int, maximum: int, minimum: int = 1) -> int:
    if isinstance(value, bool):
        return default
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError, OverflowError):
        return default


def normalize_plan(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is not None and not isinstance(value, dict):
        raise ValueError("discovery_plan must be a dictionary or None")
    supplied = value or {}
    plan: dict[str, Any] = {}
    for key in ("search_terms", "focus_topics", "excluded_topics"):
        terms = supplied.get(key) or []
        if not isinstance(terms, list) or any(not isinstance(term, str) for term in terms):
            raise ValueError(f"{key} must be a list of strings")
        plan[key] = list(dict.fromkeys(" ".join(term.split())[:160] for term in terms if term.strip()))[:24]
    for key in ("date_from", "date_to"):
        raw = supplied.get(key)
        if raw in (None, ""):
            plan[key] = None
        else:
            if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                raise ValueError(f"{key} must be an ISO date (YYYY-MM-DD)")
            plan[key] = date.fromisoformat(raw).isoformat()
    if plan["date_from"] and plan["date_to"] and plan["date_from"] > plan["date_to"]:
        raise ValueError("date_from must not be after date_to")
    zone = supplied.get("query_timezone") or "Asia/Shanghai"
    try:
        if not isinstance(zone, str):
            raise ValueError("timezone must be a string")
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("query_timezone must be a valid IANA timezone") from exc
    plan["query_timezone"] = zone
    plan["per_source_limit"] = bounded_int(supplied.get("per_source_limit"), 40, 60)
    plan["max_candidates"] = bounded_int(supplied.get("max_candidates"), 500, 1000)
    # Informational only. No source groups, credentials, or Wiki content are accepted.
    plan["profile_domain"] = str(supplied.get("profile_domain") or "")[:160]
    return plan


def cache_identity(plan: dict[str, Any], config: dict[str, Any]) -> str:
    effective = {key: value for key, value in plan.items() if key != "profile_domain"}
    payload = {"version": CACHE_VERSION, "plan": effective, "config": config}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def query_terms(plan: dict[str, Any], source: dict[str, Any]) -> list[str]:
    supplied = plan["search_terms"] + plan["focus_topics"]
    terms = supplied or source.get("default_search_terms") or ["artificial intelligence"]
    return list(dict.fromkeys(str(term).replace("_", " ") for term in terms))[:32]


def quoted(term: str) -> str:
    # Treat planner topics as literal text, not provider query syntax.
    return '"' + re.sub(r'["\\\x00-\x1f]', " ", term).strip() + '"'


def _shift_day(value: str, offset: int) -> str:
    day = date.fromisoformat(value)
    return date.fromordinal(max(date.min.toordinal(), min(date.max.toordinal(), day.toordinal() + offset))).isoformat()


def publication_local_date(value: str, query_timezone: str) -> tuple[str, str, str]:
    """Return comparison day, local representation, and any timezone assumption."""
    text = str(value or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            day = date.fromisoformat(text).isoformat()
            return day, day, "date_only_no_timezone_conversion"
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        zone = ZoneInfo(query_timezone)
        assumption = "source_explicit_offset"
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
            assumption = "query_timezone_for_naive_datetime"
        local = parsed.astimezone(zone)
        return local.date().isoformat(), local.isoformat(), assumption
    except (ValueError, OverflowError):
        return "", "", "unknown"


def query_urls(source: dict[str, Any], plan: dict[str, Any], limit: int) -> list[str]:
    kind = source.get("type")
    base = urlparse(str(source["url"]))
    params = dict(parse_qsl(base.query, keep_blank_values=True))
    terms = query_terms(plan, source)
    if kind == "arxiv_search":
        terms = terms[:bounded_int(source.get("max_query_terms"), 8, 12)]
        query = "(" + " OR ".join("all:" + quoted(term) for term in terms) + ")"
        for term in plan["excluded_topics"]:
            query += " ANDNOT all:" + quoted(term.replace("_", " "))
        if plan["date_from"] or plan["date_to"]:
            lower = _shift_day(plan["date_from"] or "1991-01-01", -1).replace("-", "") + "0000"
            today = datetime.now(ZoneInfo(plan.get("query_timezone", "Asia/Shanghai"))).date().isoformat()
            upper = _shift_day(plan["date_to"] or today, 1).replace("-", "") + "2359"
            query += f" AND submittedDate:[{lower} TO {upper}]"
        params.update(search_query=query, start="0", max_results=str(limit),
                      sortBy="submittedDate", sortOrder="descending")
    elif kind == "google_news_search":
        query = "(" + " OR ".join(quoted(term) for term in terms) + ")"
        query += "".join(" -" + quoted(term.replace("_", " ")) for term in plan["excluded_topics"])
        if plan["date_from"]:
            # Exclusive provider dates plus a one-day timezone safety margin.
            query += " after:" + _shift_day(plan["date_from"], -2)
        if plan["date_to"]:
            query += " before:" + _shift_day(plan["date_to"], 2)
        params["q"] = query
    elif kind == "github_search":
        urls = []
        # Separate queries avoid GitHub's 256-character/5-boolean-operator limit.
        for term in terms[: bounded_int(source.get("max_queries"), 3, 4)]:
            query = quoted(term[:120]) + " in:name,description,readme is:public archived:false"
            if plan["date_from"]:
                query += " pushed:>=" + _shift_day(plan["date_from"], -1)
            params.update(q=query, per_page=str(limit), page="1", sort="updated", order="desc")
            urls.append(urlunparse(base._replace(query=urlencode(params))))
        return urls
    return [urlunparse(base._replace(query=urlencode(params)))]


def canonical_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.rstrip("/") or "/"
    if host in {"arxiv.org", "export.arxiv.org", "www.arxiv.org"}:
        host = "arxiv.org"
        path = re.sub(r"^/pdf/", "/abs/", path)
        path = re.sub(r"(?:v\d+)?(?:\.pdf)?$", "", path)
    params = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
              if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunparse(("https", host, path, "", urlencode(sorted(params)), ""))


def provenance(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item.get(key, "") for key in (
        "source_id", "source", "source_level", "source_type", "title", "summary", "url", "source_url",
        "published_at", "updated_at", "published_at_basis", "retrieved_at",
        "content_hash", "response_sha256", "evidence_kind", "discovery_query_url",
    )}


def _exact_content_identity(item: dict[str, Any]) -> str:
    title = " ".join(unicodedata.normalize("NFKC", str(item.get("title") or "")).split()).casefold()
    body = " ".join(unicodedata.normalize("NFKC", str(item.get("text") or item.get("summary") or "")).split())
    # Short feed stubs and title repetitions do not establish syndicated identity.
    if not title or len(body) < 80 or len(body) < len(title) + 40:
        return ""
    return hashlib.sha256((title + "\n" + body).encode("utf-8")).hexdigest()


def deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    by_content: dict[str, dict[str, Any]] = {}
    for original in items:
        item = dict(original)
        url = str(item.get("url") or item.get("source_url") or "")
        key = canonical_url(url) if url else str(item.get("content_hash") or "")
        if not key:
            continue
        content_key = _exact_content_identity(item)
        origins = list(item.get("provenance") or [provenance(item)])
        kept = seen.get(key) or (by_content.get(content_key) if content_key else None)
        if kept is not None:
            kept["provenance"].extend(origin for origin in origins if origin not in kept["provenance"])
            kept["source_ids"] = list(dict.fromkeys(
                str(origin.get("source_id") or "") for origin in kept["provenance"]))
            kept["topics"] = list(dict.fromkeys(kept.get("topics", []) + item.get("topics", [])))
            # Keep the primary record and its original evidence together; enrich separately.
            if len(str(item.get("text") or "")) > len(str(kept.get("text") or "")):
                kept["text"] = item["text"]
                kept["body_evidence"] = provenance(item)
            kept["original_urls"] = list(dict.fromkeys(
                str(origin.get("url") or origin.get("source_url") or "") for origin in kept["provenance"]))
            seen[key] = kept
            if content_key:
                by_content[content_key] = kept
            continue
        item["canonical_url"] = key
        item["provenance"] = origins
        item["source_ids"] = list(dict.fromkeys(str(origin.get("source_id") or "") for origin in origins))
        item["original_urls"] = list(dict.fromkeys(
            str(origin.get("url") or origin.get("source_url") or "") for origin in origins))
        seen[key] = item
        if content_key:
            by_content[content_key] = item
        output.append(item)
    return output


def matches_term(text: str, term: str) -> bool:
    term = term.casefold().replace("_", " ").strip()
    return bool(term and re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text.casefold()))


def title_quarantine_reason(title: str) -> str:
    text = " ".join(unicodedata.normalize("NFKC", title).casefold().split())
    # Explicit research about gambling harm or advertising is not an advertisement.
    subject = r"(?:博彩|赌博|赌场|垃圾广告|博彩广告|赌博广告|gambling|casino|spam|advertising)"
    research = r"(?:识别|检测|危害研究|成瘾研究|风险研究|监管研究|detecting|detection|harm|addiction|regulation)"
    if re.search(subject + r".{0,24}" + research + "|" + research + r".{0,24}" + subject, text):
        return ""
    compact = re.sub(r"\s+", "", text)
    branded_official = re.search(r"(?<![a-z])pg电子(?:游戏)?官网", compact)
    casino_service = re.search(r"在线真人赌博|真人博彩|线上赌场|体育下注|九五之尊足彩官网|博联官网开户", compact)
    promotion = re.search(r"娱乐平台|官网|开户|充值|返利|红利|下载|手机软件|正式上线|获行业大奖|安全更新|认证|合规升级", compact)
    if branded_official or (casino_service and promotion):
        return "obvious_casino_ad_title"
    return ""


def clean_candidates(items: list[dict[str, Any]], plan: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    counts = {key: 0 for key in ("raw_count", "invalid_count", "excluded_count", "outside_date_count",
                                "unknown_date_count", "future_date_count", "duplicate_count",
                                "unique_count", "truncated_count", "returned_count", "undated_lead_count",
                                "spam_title_quarantined_count")}
    counts["quarantine_reason_counts"] = {}
    counts["raw_count"] = len(items)
    valid = []
    query_timezone = plan.get("query_timezone", "Asia/Shanghai")
    today = datetime.now(ZoneInfo(query_timezone)).date().isoformat()
    for item in items:
        if not str(item.get("title") or "").strip() or not public_url(str(item.get("url") or "")):
            counts["invalid_count"] += 1
            continue
        reason = title_quarantine_reason(str(item["title"]))
        if reason:
            counts["spam_title_quarantined_count"] += 1
            counts["quarantine_reason_counts"][reason] = counts["quarantine_reason_counts"].get(reason, 0) + 1
            continue
        text = " ".join(str(item.get(key) or "") for key in ("title", "summary", "text"))
        if any(matches_term(text, term) for term in plan["excluded_topics"]):
            counts["excluded_count"] += 1
            continue
        published = str(item.get("published_at") or "")
        day, local_published, assumption = publication_local_date(published, query_timezone)
        if not day:
            counts["unknown_date_count"] += 1
            if item.get("collection_role") == "undated_source_lead":
                counts["undated_lead_count"] += 1
            elif plan["date_from"] or plan["date_to"]:
                continue
        elif day > today:
            counts["future_date_count"] += 1
            continue
        elif ((plan["date_from"] and day < plan["date_from"])
              or (plan["date_to"] and day > plan["date_to"])):
            counts["outside_date_count"] += 1
            continue
        valid.append({**item, "published_at_query_timezone": local_published,
                      "publication_query_timezone": query_timezone,
                      "published_at_timezone_assumption": assumption})
    unique = deduplicate(valid)
    counts["duplicate_count"] = len(valid) - len(unique)
    counts["unique_count"] = len(unique)
    counts["truncated_count"] = max(0, len(unique) - plan["max_candidates"])
    selected = unique[:plan["max_candidates"]]
    counts["returned_count"] = len(selected)
    return selected, counts
