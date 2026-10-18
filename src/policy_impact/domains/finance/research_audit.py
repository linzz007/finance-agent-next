"""Deterministic research boundaries, separate from execution success or causal proof."""

import re


def select_documents(request: dict, metrics: list[dict], documents: list[dict]) -> dict:
    """Conservative title triage, not proof of topical relevance or causality."""
    symbols = set(request.get("symbols", []))
    global_only = bool(symbols & {"ixic", "spx", "dji", "xauusd", "xagusd"}) and not any(s.startswith(("sh", "sz")) for s in symbols)
    banking_question = bool(re.search("银行|监管|贴现窗口|压力测试|bank|regulat", request.get("question", ""), re.I))
    chinese = any(d.get("source_id") == "stats_cn" for d in documents)
    eligible, excluded = [], []
    for doc in documents:
        reason = None
        if chinese and doc.get("source_id") == "stats_en":
            reason = "translated_republication_not_independent_evidence"
        elif global_only and not banking_question and not re.search(
            r"FOMC statement|economic projections|monetary policy|inflation|employment|economic outlook|"
            r"interest rate|gold|silver|precious metals|美元|黄金|白银|美国(?:通胀|就业|货币)", doc.get("title", ""), re.I):
            reason = "no_direct_macro_topic_in_title_keep_in_appendix"
        if reason:
            excluded.append({"document_id": doc["document_id"], "reason": reason})
        else:
            eligible.append(doc)
    eligible.sort(key=lambda d: (bool(global_only and d.get("source_id", "").startswith("fed")), d["published_at"]), reverse=True)
    excluded.extend({"document_id": d["document_id"], "reason": "evidence_count_limit"} for d in eligible[10:])
    return {"selected_ids": [d["document_id"] for d in eligible[:10]], "excluded": excluded,
            "notice": "标题初筛可能漏检，非语义相关性认证；原文仍全部保存在附录，可经report_read核查。"}


def audit(*, request: dict, metrics: list[dict], documents: list[dict]) -> dict:
    assets = []
    for metric in metrics:
        cutoff = metric["end_date"]
        changes = [row for row in metric.get("daily_changes", []) if row.get("change_pct") is not None]
        opposing = [row for row in changes if (row["change_pct"] > 0 and (metric.get("change_pct") or 0) < 0)
                    or (row["change_pct"] < 0 and (metric.get("change_pct") or 0) > 0)]
        timeline = []
        for doc in documents:
            published = doc["published_at"]
            relation = ("after_price_cutoff" if published > cutoff else
                        "same_day_order_unknown" if published == cutoff else
                        "before_observation_window" if published < request["period"]["start"] else
                        "within_observation_window")
            timeline.append({"document_id": doc["document_id"], "published_at": published,
                             "relation": relation, "url": doc["url"],
                             "first_public_at": None, "event_at": None, "timestamp_order": "unknown",
                             "allowed_usage": "retrospective_review" if published > cutoff else "historical_context",
                             "notice": "仅结构化日期标签比较；原文可能含时分/时区，但尚未抽取核验事件及首次公开时间，不能认证当时催化。期后文献可回顾既往事实。"})
        assets.append({"symbol": metric["symbol"], "name": metric["name"],
                       "metric_ref": metric["metric_id"], "price_start": metric["start_date"],
                       "price_cutoff": cutoff, "source_url": metric["source_url"],
                       "price_crosscheck": "not_independently_crosschecked",
                       "counter_direction_observations": sorted(opposing, key=lambda row: abs(row["change_pct"]), reverse=True)[:3],
                       "path_notice": "这是相对上一已知日末值的反向波动，不是事件冲击估计，不能单独证伪宏观机制。",
                       "timeline": timeline})
    question = request.get("question", "")
    descriptive = bool(re.search(r"只(?:列|看|给|展示).{0,6}(?:价格|行情|涨跌幅|指数|数据|表格)|不(?:要|用|需)(?:解释|分析|归因)", question))
    # Unknown intent remains conservative: an analysis must cover each asset.
    causal_requested = bool(question) and (not descriptive or bool(re.search("原因|为什么|为何|怎么会|怎么突然|导致|驱动|归因|影响", question)))
    return {"schema_version": "finance.research_audit.v1",
            "document_selection": select_documents(request, metrics, documents),
            "causal_requested": causal_requested,
            "assets": assets,
            "missing_driver_observations": [
                "美元汇率指数的同区间观测（广义美元指数不等于DXY）",
                "美国名义与实际国债收益率的同区间观测",
                "纳指成分股/行业贡献、财报及资金流证据",
                "白银工业供需、库存及持仓变化证据",
            ],
            "limitations": [
                "价格计算可复算，不代表已完成独立来源价格交叉核验。",
                "结构化字段仅有发布日期；原文可能包含时分，但系统尚未抽取核验，同日文章与跨时区收盘先后未知。",
                "发布日期不等于事件首次发生或市场首次知晓时间；区间内文章也不能单独解释整月变化。",
                "事后采集存在修订数据与事后信息风险，不是历史时点可交易回测。",
                "语义复核检查论述与资料一致性，不是独立金融事实核验或因果识别。",
            ]}


def validate_assessments(analysis: dict, audit_result: dict, evidence: dict) -> None:
    rows = analysis.get("asset_assessments", [])
    if (not audit_result["causal_requested"] and not rows) or not audit_result["assets"]:
        return
    expected = {a["symbol"]: a for a in audit_result["assets"]}
    if len(rows) != len(expected) or {row["symbol"] for row in rows} != set(expected):
        raise ValueError("原因问题必须逐资产填写asset_assessments，不能漏资产或重复资产")
    for row in rows:
        asset = expected[row["symbol"]]
        refs = row["evidence_refs"]
        if asset["metric_ref"] not in refs or not set(refs) <= set(evidence):
            raise ValueError("逐资产核查须引用本资产metric_ref，其他引用也须来自当前evidence")
        doc_refs = {ref for ref in refs if "text" in evidence[ref]}
        uses = row.get("document_uses", [])
        if len(uses) != len(doc_refs) or {u["document_id"] for u in uses} != doc_refs:
            raise ValueError("每条文章引用须在document_uses声明用途，不能把事后回顾当作当时催化")
        timeline = {d["document_id"]: d for d in asset["timeline"]}
        for usage in uses:
            record = timeline[usage["document_id"]]
            if usage["usage"] not in {"historical_context", "retrospective_review"}:
                raise ValueError("缺少事件时间与首次公开时间，不能认证当时催化")
            if record["relation"] == "after_price_cutoff" and usage["usage"] != "retrospective_review":
                raise ValueError("期后文章仅能事后回顾历史，不能声称当时可知；需另查更早首次公开证据")
        actual_dates = {d["date"] for d in asset["counter_direction_observations"]}
        claimed_dates = row.get("counter_observation_dates", [])
        if not set(claimed_dates) <= actual_dates or (actual_dates and not claimed_dates):
            raise ValueError("须从counter_direction_observations选择至少一条实际反向日期，不能空写或编造反证")
        if row["verdict"] == "hypothesis_only":
            test = row.get("historical_test") or {}
            if row.get("mechanism", "unidentified") == "unidentified" or test.get("mechanism") != row.get("mechanism"):
                raise ValueError("hypothesis_only须明确mechanism且historical_test.mechanism与之相同，不能换机制检验")
            if test.get("window") != {"start": asset["price_start"], "end": asset["price_cutoff"]}:
                raise ValueError("历史归因检验须使用本资产价格起止窗口；未来条件不能倒证历史原因")
            if not test.get("variables") or len(test.get("weakening_condition", "").strip()) < 8 or re.search(r"TBD|待定|暂无|没有反向", test.get("weakening_condition", ""), re.I):
                raise ValueError("历史检验必须指定变量与相反方向条件，不能占位")
        elif row.get("mechanism", "unidentified") != "unidentified":
            raise ValueError("原因未识别时mechanism须为unidentified，不强填机制")


def summarize(analysis: dict | None, audit_result: dict) -> dict:
    rows = (analysis or {}).get("asset_assessments", [])
    requested = audit_result["causal_requested"]
    return {"schema_version": "finance.research_quality.v1",
            "answer_status": "partial" if requested else "descriptive_only",
            "causal_identification": "not_established",
            "asset_coverage": [row["symbol"] for row in rows],
            "price_verification": "single_source_internal_consistency_only",
            "label": ("分析未完成" if analysis is None else "走势已核对，原因未证实" if requested else "行情观察，不作因果结论"),
            "reason": "现有日线与文章只能支持事实观察及候选解释；缺少驱动变量、事件窗口和独立价格交叉核验。"}
