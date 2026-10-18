import json

from policy_impact.harness.chat_context import ChatContextConfig, compile_chat_context
from policy_impact.harness.context_manifest import estimate_tokens
from policy_impact.harness.report_context import read_report_evidence


def test_report_followup_reads_exact_date_and_only_scoped_artifact(tmp_path):
    root = tmp_path / "personal"
    path = root / "reports" / "artifact.json"
    path.parent.mkdir(parents=True)
    payload = {"company_id": "personal", "skill": "recent_news_report",
               "structured_events": [{"event_id": "event1", "title": "Release", "published_at": "2026-09-08T20:37:31+00:00",
                                      "url": "https://github.com/example/repo/releases/tag/v1", "summary": "Fix tools",
                                      "retrieved_at": "2026-09-09T15:44:07+00:00"}],
               "source_manifest": {"collected_at": "2026-09-09T15:44:29+00:00"},
               "analysis": [{"event_id": "event1", "analysis_status": "complete", "company_evidence": [{"fact_id": "personal_goal"}]}]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    history = [{"role": "assistant", "metadata": {"run_id": "report1", "artifacts": {"run_artifact": str(path)}}}]
    records, audit = read_report_evidence(root, history, "刚才报告的发布时间是什么")
    assert records[0]["report_evidence"][0]["published_at"] == "2026-09-08T20:37:31+00:00"
    assert records[0]["report_evidence"][0]["retrieved_at"] == "2026-09-09T15:44:07+00:00"
    assert records[0]["snapshot_collected_at"] == "2026-09-09T15:44:29+00:00"
    assert records[0]["popularity_sampled_at"] is None
    assert records[0]["total_report_items"] == 1
    assert audit["fresh_fetch"] is False
    compiled = compile_chat_context(message="报告", skill={}, history=history, conversation_summary={}, memories=[], facts=[],
                                    tool_policy={}, report_evidence=records)
    assert any("report_evidence" in item for item in compiled["recent_artifacts"])
    assert estimate_tokens(compiled["recent_artifacts"]) <= ChatContextConfig().artifact_token_budget
    assert read_report_evidence(tmp_path / "other", history, "刚才报告")[0] == []
    assert read_report_evidence(root, history, "你好")[0] == []
    assert read_report_evidence(root, [{**history[0], "role": "user"}], "报告")[0] == []


def test_mismatched_report_identity_is_rejected(tmp_path):
    root = tmp_path / "personal"
    path = root / "reports" / "spoofed.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"company_id": "other", "skill": "recent_news_report"}), encoding="utf-8")
    records, _ = read_report_evidence(root, [{"role": "assistant", "metadata": {"artifacts": {"run_artifact": str(path)}}}], "报告")
    assert records == []


def test_report_followup_retains_mechanism_and_third_item(tmp_path):
    root = tmp_path / "personal"
    path = root / "runs" / "report.json"
    path.parent.mkdir(parents=True)
    payload = {"company_id": "personal", "skill": "recent_news_report",
               "structured_events": [{"event_id": f"e{i}", "title": f"Paper {i}",
                                      "summary": "Introduction. The method updates skills from failed trajectories."}
                                     for i in range(1, 5)],
               "analysis": [{"event_id": f"e{i}", "analysis_status": "done",
                             "evidence_links": {"event_quote": "Introduction."},
                             "conditional_impact": "可能帮助设计技能更新对照。"} for i in range(1, 5)]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    records, _ = read_report_evidence(root, [{"role": "assistant", "metadata": {
        "artifacts": {"run_artifact": str(path)}}}], "刚才报告的第三篇是什么机制？")
    rows = records[0]["report_evidence"]
    assert len(rows) == 3 and rows[0]["event_id"] == "e3"
    assert "updates skills" in rows[0]["source_excerpt"]
    assert records[0]["total_report_items"] == 4
    assert "not_a_complete_ranking" in records[0]["selection_scope"]
