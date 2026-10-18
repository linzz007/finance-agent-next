"""Exercise the optional finance study, synthesis, and review tools with a live model."""

from __future__ import annotations

import argparse
from datetime import date
import json
from uuid import uuid4

from policy_impact.domains.finance.article_corpus import ArticleCorpus, article_root
from policy_impact.domains.finance.research_gateway import FinanceResearchGateway
from policy_impact.harness.model_gateway import AnthropicCompatibleModelAdapter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--author", default="TT")
    parser.add_argument("--dates", nargs=2, default=["2026-09-26", "2026-09-30"])
    parser.add_argument("--question", default="How did the author's gold selloff explanation change?")
    parser.add_argument("--company", default="finance_observer")
    args = parser.parse_args()

    adapter = AnthropicCompatibleModelAdapter.from_env()
    adapter.max_tokens = max(adapter.max_tokens, 8192)

    def complete(messages, schema_name):
        return adapter.complete(messages, schema_name=schema_name).payload["output"]

    corpus = ArticleCorpus(args.company, article_root())
    gateway = FinanceResearchGateway(
        args.company, set(), question=args.question, corpus=corpus,
        complete_study=complete, complete_synthesis=complete, complete_review=complete,
        session_id="acceptance_" + uuid4().hex, run_id=uuid4().hex,
    )
    listed = gateway.call(
        "chat_agent_loop", "article.list", author=args.author,
        published_after=min(args.dates), published_before=max(args.dates), limit=500,
    )["articles"]
    cards = []
    for publication_date in args.dates:
        matching = [row for row in listed if row["published_at"] == publication_date]
        if len(matching) != 1:
            raise ValueError(
                f"expected exactly one {args.author} article on {publication_date}; found {len(matching)}"
            )
        study = gateway.call(
            "chat_agent_loop", "article.study", article_id=matching[0]["article_id"],
            depth="deep", question=args.question,
        )
        if study["status"] != "studied":
            raise RuntimeError(f"no source-bound card for {publication_date}: {study}")
        cards.append(study)
        print(json.dumps({
            "date": publication_date, "status": study["status"],
            "card_id": study["card_id"], "claim_count": study["claim_count"],
            "limitations": study["limitations"],
        }, ensure_ascii=False), flush=True)

    card_ids = [row["card_id"] for row in cards]
    comparison = gateway.call(
        "chat_agent_loop", "research.synthesize", question=args.question,
        card_ids=card_ids,
    )
    print(json.dumps({"comparison": {
        key: comparison.get(key) for key in (
            "status", "comparison_id", "relation_count", "dropped_relation_count",
            "revised_relation_count", "rejected_relation_count", "selected_claim_count",
            "limitations", "relation_audit",
        )
    }, "relations": [
        {"type": row["type"], "claim_ids": row["claim_ids"],
         "analysis": row["analysis"]}
        for row in comparison.get("relations", [])
    ]}, ensure_ascii=False), flush=True)
    selected_claim_ids = list(dict.fromkeys(
        claim_id for relation in comparison.get("relations", [])
        for claim_id in relation["claim_ids"]
    ))[:30]
    if not selected_claim_ids:
        raise RuntimeError("comparison produced no claim IDs to verify")
    review = gateway.call(
        "chat_agent_loop", "research.verify", card_ids=card_ids,
        claim_ids=selected_claim_ids, as_of=date.today().isoformat(),
    )
    print(json.dumps({"review": {
        "review_id": review["review_id"], "check_count": review["check_count"],
        "statuses": [{"claim_id": row["claim_id"], "claim_type": row["claim_type"],
                      "status": row["status"]} for row in review["checks"]],
        "limitations": review["limitations"],
    }}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
