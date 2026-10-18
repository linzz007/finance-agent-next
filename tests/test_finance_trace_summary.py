from policy_impact.app.chat_workbench_service import ChatWorkbenchService


def stage(name, output, status="done"):
    return {"step_type": "workflow_stage", "status": status,
            "metadata": {"workflow": "financial_report", "stage": name},
            "output_payload": output}


def test_finance_sources_show_recorded_partial_coverage_without_fake_news_counts():
    result = ChatWorkbenchService._summarize_sources([
        stage("acquisition", {"snapshot_id": "snap1", "collected_at": "2026-09-22T12:00:00Z",
            "source_statuses": [{"status": "ok"}, {"status": "partial"}, {"status": "error"}]}),
        stage("validation", {"series_count": 3, "document_count": 15}),
    ])
    assert result["recorded"] is True
    assert result["route"] == "finance"
    assert result["source_count"] == 3
    assert result["successful_source_count"] == 1
    assert result["partial_source_count"] == 1
    assert result["failed_source_count"] == 1
    assert result["snapshot_id"] == "snap1"
    assert result["series_count"] == 3
    assert result["document_count"] == 15
    assert result.get("snapshot_news_count") is None


def test_finance_failure_before_acquisition_does_not_invent_zero_sources():
    result = ChatWorkbenchService._summarize_sources([stage("profile", {"reason": "blocked"}, "failed")])
    assert result["route"] == "finance"
    assert result["recorded"] is False
    assert result["source_count"] is None
    assert result["document_count"] is None


def test_finance_explicit_empty_snapshot_is_distinct_from_missing():
    result = ChatWorkbenchService._summarize_sources([
        stage("acquisition", {"snapshot_id": "empty", "source_statuses": []}),
        stage("validation", {"series_count": 0, "document_count": 0}),
    ])
    assert result["recorded"] is True
    assert result["source_count"] == 0
    assert result["series_count"] == 0
    assert result["document_count"] == 0
