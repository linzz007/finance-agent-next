"""Finite finance capabilities registered with the shared ToolGateway."""

from datetime import datetime, timezone


def collect(company_id: str, request: dict) -> dict:
    """Acquire or reuse the exact requested public data snapshot for this workspace."""
    from policy_impact.domains.finance.acquisition import collect_finance_snapshot
    from policy_impact.domains.finance.store import FinanceStore, exclusive_lock
    store = FinanceStore(company_id)
    existing = store.latest_snapshot()
    if (existing and not request.get("force_refresh") and existing.get("period") == request["period"]
            and request.get("session_mode", "explicit_period") == "explicit_period"
            and existing.get("session_mode", "explicit_period") == "explicit_period"
            and existing.get("document_period", existing["period"]) == request.get("document_period", request["period"])):
        symbols = {s["symbol"] for s in existing.get("series", [])}
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(existing["collected_at"].replace("Z", "+00:00"))).total_seconds()
        if symbols == set(request["symbols"]) and 0 <= age < 900:
            if request.get("quality_policy") == "strict":
                from policy_impact.domains.finance.metrics import compute_market_metrics
                from policy_impact.domains.finance.quality import require_market_coverage
                try:
                    checked = {**existing, "quality_policy": "strict"}
                    require_market_coverage(request, checked, compute_market_metrics(checked))
                except ValueError:
                    pass  # Incomplete snapshots are evidence, not a reusable successful acquisition.
                else:
                    return checked
            else:
                return existing
    with exclusive_lock(company_id, "collection") as acquired:
        if not acquired:
            raise ValueError("当前工作空间正在采集金融数据，请稍后重试报告")
        options = {key: request[key] for key in ("session_mode", "as_of", "document_period", "include_documents", "quality_policy") if key in request}
        result = collect_finance_snapshot(start=request["period"]["start"], end=request["period"]["end"],
                                          symbols=request["symbols"], **options)
        store.save_snapshot(result)
    return result


def metrics(snapshot: dict) -> list[dict]:
    """Compute changes from recorded market bars; the language model performs no arithmetic."""
    from policy_impact.domains.finance.metrics import compute_market_metrics
    return compute_market_metrics(snapshot)


def publish(company_id: str, report: dict) -> dict:
    """Publish validated report data to the owning workspace's immutable report artifacts."""
    from policy_impact.domains.finance.render import publish_report
    return publish_report(company_id, report)
