"""Read-only presentation of persisted review decisions, outside the reviewed body."""

from __future__ import annotations

import json
from collections.abc import Mapping

SCHEMA = "finance.h2.review_status.v1"
SNAPSHOT_NOTICE = (
    "分析正文与限制为保存时的快照，其中流程描述仅代表当时时点。当前核验状态以运行记录为准。"
)
SCOPE_NOTICE = "核验结论仅针对列出的范围，不代表其他外部事实已核实，也不代表主报告整体已通过核验。"
SCOPE_LABELS = {
    "source_fidelity": "原文忠实度", "reasoning_conditions": "推理与成立条件",
    "external_facts": "外部事实", "evidence_support": "证据支持",
    "temporal_conditions": "时间条件", "counterevidence": "反向证据",
}
LABELS = {
    "passed": "核验通过", "not_requested": "未请求核验", "skipped_with_reason": "已跳过核验",
    "round_limit_reached": "核验达轮数上限，未通过", "review_failed": "核验未完成",
    "issues_open": "核验问题待解决", "reviewed_with_resolutions": "已审阅，旧版未记录通过判定",
    "waiting": "等待核验", "dispatch_pending": "等待派发核验", "report_pending_read": "等待完整读取核验报告",
    "report_ready": "核验报告已返回，等待最终交付", "report_invalid": "核验报告校验失败",
}
RESULTS_SQL = (
    "SELECT t.task_id,t.objective,t.status AS task_status,r.result_ref,r.full_ref "
    "FROM h2_tasks t JOIN h2_results r ON r.result_ref=t.result_ref AND r.run_id=t.run_id "
    "JOIN h2_threads th ON th.thread_id=t.thread_id "
    "WHERE t.run_id=? AND th.role='analyst' AND r.disposition='accepted'"
)


def build_review_status(artifact):
    """Derive labels from Runtime's result wrapper; ignore any model-written labels."""
    if not isinstance(artifact, Mapping) or artifact.get("schema_version") != "finance.h2.analyst_result.v1":
        return None
    review = artifact.get("review")
    if not isinstance(review, Mapping):
        return None
    status = review.get("status")
    policy = review.get("policy") if isinstance(review.get("policy"), Mapping) else {}
    raw_scope = policy.get("scope")
    scope = [s for s in raw_scope if isinstance(s, str)] if isinstance(raw_scope, list) else []
    if status == "passed" and (
        review.get("verdict") != "passed" or review.get("blocking_finding_ids")
        or not review.get("draft_ref") or not review.get("report_ref")
        or type(review.get("draft_revision")) is not int or review["draft_revision"] < 1
    ):
        status = "invalid"
    label = LABELS.get(status, "核验状态不可确认")
    return {
        "schema_version": SCHEMA, "authority": "runtime", "status": status or "unknown",
        "label": label, "summary": label,
        "scope": scope, "scope_labels": [SCOPE_LABELS.get(s, s) for s in scope],
        "mode": policy.get("mode"), "rounds": review.get("rounds", 0),
        "max_rounds": policy.get("max_rounds"), "verdict": review.get("verdict"),
        "draft_ref": review.get("draft_ref"), "draft_revision": review.get("draft_revision"),
        "report_ref": review.get("report_ref"), "snapshot_notice": SNAPSHOT_NOTICE,
        "scope_notice": SCOPE_NOTICE,
    }


def collect_review_statuses(connection, run_id, read_artifact):
    """Project accepted task results through a caller-provided, hash-checked reader."""
    entries = []
    rows = connection.execute(RESULTS_SQL + " ORDER BY t.created_at,t.rowid", (run_id,))
    for row in rows:
        try:
            artifact = json.loads(read_artifact(row["full_ref"]))
        except (ValueError, UnicodeError):
            continue
        status = build_review_status(artifact)
        if status is not None:
            entries.append({**status, "task_id": row["task_id"], "task_status": row["task_status"],
                            "objective": row["objective"], "result_ref": row["result_ref"],
                            "result_artifact_ref": row["full_ref"]})
    return entries


def is_accepted_analyst_result(connection, run_id, artifact_ref):
    return connection.execute(
        RESULTS_SQL + " AND r.full_ref=? LIMIT 1", (run_id, artifact_ref),
    ).fetchone() is not None


def artifact_review_presentation(text, *, trusted_result=False):
    try:
        artifact = json.loads(text)
    except (ValueError, UnicodeError):
        return {}
    status = build_review_status(artifact) if trusted_result else None
    if status is not None:
        return {"review_status": status, "snapshot_notice": SNAPSHOT_NOTICE}
    if isinstance(artifact, Mapping) and artifact.get("schema_version") == "finance.h2.review_draft.v1":
        return {"snapshot_notice": SNAPSHOT_NOTICE, "draft_revision": artifact.get("draft_revision")}
    return {}
