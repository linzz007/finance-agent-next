"""Read-only source preflight; does not change the source registry or Wiki."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from policy_impact.source_connectors.http_client import domain_allowed, fetch_url
from policy_impact.source_connectors.ingestion import _decode_html, _html_to_text, _parse_feed


CANDIDATES = [
    ("ths_disclosure_background", "ths", "pdf", "https://static.cninfo.com.cn/finalpage/2026-03-10/1225002185.PDF"),
    ("ths_company_background", "ths", "html", "https://stock.10jqka.com.cn/20210331/c61833421.shtml"),
    ("ths_ifind_update_example", "ths", "html", "https://fthzcnc.10jqka.com.cn/thsft/iFindService/index/index/show?SEQ=62921&file=example.doc&type=noteall"),
    ("ths_wencai_home", "ths", "html", "https://www.iwencai.com/home/index"),
    ("choice_product_updates", "ths", "html", "https://choice.eastmoney.com/Product/ChoiceApp/"),
    ("csrc_news_list", "ths", "html", "https://www.csrc.gov.cn/csrc/c100028/common_list.shtml"),
    ("hermes_release_candidates", "agent_evolution", "feed", "https://github.com/NousResearch/hermes-agent/releases.atom"),
    ("evoagentx_release_candidates", "agent_evolution", "feed", "https://github.com/EvoAgentX/EvoAgentX/releases.atom"),
    ("arxiv_evolution_candidates", "agent_evolution", "feed", "https://export.arxiv.org/api/query?" + urlencode({
        "search_query": 'all:"self-evolving agents" OR all:"agent self-improvement" OR all:"agentic workflow optimization"',
        "start": 0, "max_results": 8, "sortBy": "submittedDate", "sortOrder": "descending",
    })),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", nargs="*")
    args = parser.parse_args()
    records = []
    for source_id, persona, kind, url in CANDIDATES:
        if args.sources and source_id not in args.sources:
            continue
        record = {"source_id": source_id, "persona": persona, "kind": kind, "url": url,
                  "checked_at": datetime.now(timezone.utc).isoformat(), "registered_in_application": False}
        try:
            host = urlparse(url).hostname
            response = fetch_url(url, allowed_domains=[host], timeout_seconds=12, retries=0, max_bytes=3_000_000)
            if not domain_allowed(response.url, [host]):
                raise ValueError("Unexpected redirect outside the candidate host")
            record.update(http_status=response.status, content_type=response.content_type,
                          bytes=len(response.body), sha256=response.sha256, resolved_url=response.url)
            if response.body.startswith(b"%PDF"):
                record.update(status="pdf_downloadable_not_integrated", actual_kind="pdf",
                              needs="PDF text extraction and dated disclosure-list adapter; this probe does not claim content extraction")
            elif kind == "feed":
                rows = _parse_feed(response.body, {"source_id": source_id, "name": source_id,
                                                  "level": "L3" if host.endswith("arxiv.org") else "L2",
                                                  "topics": [], "url": url}, response.retrieved_at)
                record.update(status="feed_parsed" if rows else "empty_feed", item_count=len(rows),
                              sample=[{key: row.get(key) for key in ("title", "url", "published_at")} for row in rows[:3]])
            elif kind == "pdf":
                record.update(status="unexpected_body", needs="Expected a PDF signature, received another content type")
            else:
                visible = _html_to_text(_decode_html(response.body, response.content_type))
                record.update(status="html_fetched_not_integrated", visible_text_chars=len(visible),
                              sample_text=visible[:160], needs="Verify article body, canonical URL, publication date, pagination and content terms")
        except Exception as exc:
            record.update(status="unavailable_this_probe", error=f"{type(exc).__name__}: {exc}")
        records.append(record)
        print(json.dumps({key: record.get(key) for key in ("source_id", "status", "http_status", "item_count", "visible_text_chars", "error")}, ensure_ascii=False), flush=True)
    out = ROOT / "data" / "acceptance" / ("source_preflight_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    out.mkdir(parents=True, exist_ok=False)
    (out / "results.json").write_text(json.dumps({"scope": "one request per candidate, no model calls, no config/Wiki changes",
                                               "records": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(out / "results.json"))


if __name__ == "__main__":
    main()
