import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from policy_impact.harness.model_gateway import ModelResponse, ScriptedModelAdapter
from policy_impact.skill_executors import recent_news_report as report


WIKI_FACT = "正在学习 Agent Trace 与 Eval，先阅读来源再设计只读实验。"


class OfflineAnalysisAdapter:
    def complete(self, messages, schema_name):
        events = json.loads(messages[1]["content"])["events"]
        rows = [{
            "event_id": event["event_id"],
            "event_quote": event["event_evidence"]["summary"],
            "wiki_facts": [{"fact_id": "personal.project", "quote": WIKI_FACT}],
            "event_summary_zh": "该来源描述了代理工具的技术变化，具体适用范围仍需核对。",
            "condition": "若当前学习项目使用了相同机制",
            "conditional_impact": "可能需要调整只读对照用例，尚不能确定实际收益。",
            "recommended_action": "先阅读所引来源，设计一条只读对照记录，不运行代码或迁移框架。",
        } for event in events]
        return ModelResponse("offline-coverage", {"output": {"analyses": rows}})


def news_item(index, topic, source_id="openai_codex_releases"):
    summaries = {
        "trace": "Added request tracing spans for agent tool execution with documented runtime logs.",
        "eval": "Added agent evaluation benchmarks with documented task scoring and reproducible test cases.",
    }
    urls = {
        "openai_codex_releases": "https://github.com/openai/codex/releases/tag/",
        "langgraph_releases": "https://github.com/langchain-ai/langgraph/releases/tag/",
        "google_news_agent_harness": "https://news.google.com/rss/articles/",
    }
    return {
        "title": f"{topic} release {index}", "summary": summaries[topic],
        "source": source_id, "source_id": source_id,
        "source_level": "L5" if source_id.startswith("google_news") else "L2",
        "source_type": "rss_atom", "url": urls[source_id] + str(index),
        "published_at": date.today().isoformat(), "retrieved_at": date.today().isoformat(),
        "data_mode": "live", "is_fixture": False,
    }


def offline_run(monkeypatch, tmp_path, items, *, count=3, include_web=False, focus=None):
    context = {
        "profile": {"profile_type": "personal", "company_name": "Coverage test"},
        "pinned_facts": [{
            "fact_id": "personal.project", "value": WIKI_FACT,
            "source_kind": "user_confirmed", "source_path": "wiki/background.md",
            "importance": 5, "confidence": 1.0,
        }],
    }
    source_ids = list(dict.fromkeys(item["source_id"] for item in items))
    manifest = {
        "mode": "source_snapshot", "collection_succeeded": True, "snapshot_path": "offline-only",
        "source_statuses": [{
            "source_id": source_id, "status": "ok",
            "item_count": sum(item["source_id"] == source_id for item in items),
        } for source_id in source_ids],
    }
    scores = {item["title"]: 100 - index for index, item in enumerate(items)}
    monkeypatch.setattr(report, "company_dir", lambda _: tmp_path)
    monkeypatch.setattr(report, "build_company_context_pack", lambda *args, **kwargs: deepcopy(context))
    monkeypatch.setattr(report, "_load_news_items", lambda **kwargs: (deepcopy(items), deepcopy(manifest)))
    monkeypatch.setattr(report, "_news_score", lambda event, hits: scores[event["title"]])
    stages = []
    result = report.run_recent_news_report(
        "personal", f"最近7天最多{count}条，" + ("官方和媒体都可以" if include_web else "只看官方来源"),
        refresh_live_sources=False, model_adapter=OfflineAnalysisAdapter(), on_stage=stages.append,
        task_profile={"focus_topics": ["trace", "eval"] if focus is None else focus,
                      "excluded_topics": ["memory"]},
    )
    artifact = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))
    assert context["pinned_facts"][0]["value"] == WIKI_FACT
    assert result["source_manifest"] == artifact["source_manifest"] == manifest
    assert artifact["stages"] == stages
    assert result["analysis_diagnostics"]["errors"] == []
    assert all(item["analysis_status"] == "done" for item in result["top_matches"])
    return result, artifact


def assert_final_coverage(result, artifact, *, candidate, selected):
    for payload in (result, artifact):
        assert payload["candidate_topic_coverage"] == candidate
        assert payload["topic_coverage"] == selected
    filtering = next(stage for stage in result["stages"]
                     if stage["stage"] == "filtering" and stage["status"] == "completed")
    final = next(stage for stage in result["stages"]
                 if stage["stage"] == "impact_analysis" and stage["status"] == "completed")
    assert filtering["candidate_topic_coverage"] == candidate
    assert final["topic_coverage"] == selected
    assert final["gate"]["reason"] == result["gate_reason"]
    assert selected == {
        topic: any(topic in item["matched_query_topics"] for item in artifact["analysis"])
        for topic in candidate
    }


@pytest.mark.parametrize("selection", ["count_limit", "source_diversity", "secondary_replacement"])
def test_unselected_eval_is_not_final_coverage(monkeypatch, tmp_path, selection):
    items = [news_item(1, "trace"), news_item(2, "trace")]
    if selection == "count_limit":
        items += [news_item(3, "trace", "langgraph_releases"), news_item(4, "eval")]
    elif selection == "source_diversity":
        items += [news_item(3, "eval"), news_item(4, "trace", "langgraph_releases")]
    else:
        items = [news_item(1, "trace"), news_item(2, "eval", "langgraph_releases"),
                 news_item(3, "trace", "google_news_agent_harness")]
    count = 2 if selection == "secondary_replacement" else 3
    result, artifact = offline_run(
        monkeypatch, tmp_path, items, count=count, include_web=selection == "secondary_replacement",
    )
    assert_final_coverage(result, artifact, candidate={"trace": True, "eval": True},
                          selected={"trace": True, "eval": False})
    assert result["selected_event_count"] == count
    assert result["publishable_event_count"] == len(items)
    assert all(item["matched_topics"] == ["trace"] for item in result["top_matches"])
    assert result["analysis_status"] == "partial"
    assert result["relevance_gate"] == "allow"
    assert "最终推荐未覆盖主题：Eval" in result["gate_reason"]
    assert "有可发布候选，但经排序与入选取舍后未进入最终推荐：Eval" in result["gate_reason"]
    assert "部分关注主题缺少可发布证据" not in result["incomplete_reasons"]
    for key in ("report_path", "html_report_path"):
        rendered = Path(result[key]).read_text(encoding="utf-8")
        assert result["gate_reason"] in rendered
        assert "主题覆盖均通过" not in rendered
        assert "不为补齐主题或数量强行加入项目" in rendered
        assert "来源成功" in rendered


@pytest.mark.parametrize("weak_eval", [False, True])
def test_missing_publishable_eval_is_disclosed_without_filling_slots(monkeypatch, tmp_path, weak_eval):
    items = [news_item(1, "trace")]
    if weak_eval:
        items.append({**news_item(2, "eval"), "summary": "No summary provided"})
    result, artifact = offline_run(monkeypatch, tmp_path, items)
    assert_final_coverage(result, artifact, candidate={"trace": True, "eval": False},
                          selected={"trace": True, "eval": False})
    assert result["selected_event_count"] == result["publishable_event_count"] == 1
    assert result["quality_diagnostics"]["missing_or_weak_summary_count"] == int(weak_eval)
    assert "候选来源中缺少可发布证据：Eval" in result["gate_reason"]
    assert "有可发布候选，但经排序" not in result["gate_reason"]
    assert result["analysis_status"] == "partial"


def test_all_selected_topics_can_still_complete(monkeypatch, tmp_path):
    result, artifact = offline_run(monkeypatch, tmp_path, [news_item(1, "trace"), news_item(2, "eval")], count=2)
    assert_final_coverage(result, artifact, candidate={"trace": True, "eval": True},
                          selected={"trace": True, "eval": True})
    assert result["status"] == result["analysis_status"] == "done"
    assert result["incomplete_reasons"] == []
    assert "最终推荐主题覆盖均通过" in result["gate_reason"]


def test_no_selected_evidence_does_not_cover_any_topic(monkeypatch, tmp_path):
    result, artifact = offline_run(monkeypatch, tmp_path, [])
    assert_final_coverage(result, artifact, candidate={"trace": False, "eval": False},
                          selected={"trace": False, "eval": False})
    assert result["selected_event_count"] == 0
    assert result["analysis_status"] == "partial"
    assert result["relevance_gate"] == "ask"
    assert result["analysis_diagnostics"]["model_calls"] == 0
    assert "最终推荐未覆盖主题：Trace 可观测性、Eval 评测" in result["gate_reason"]


def test_no_requested_topics_keeps_empty_coverage(monkeypatch, tmp_path):
    result, artifact = offline_run(monkeypatch, tmp_path, [news_item(1, "trace")], count=1, focus=[])
    assert_final_coverage(result, artifact, candidate={}, selected={})
    assert result["status"] == result["analysis_status"] == "done"
    assert "最终推荐未覆盖主题" not in result["gate_reason"]


def test_current_filter_orders_equal_topic_scores_by_publication_date():
    events = report._structure_events([
        {**news_item(1, "trace", "langgraph_releases"), "published_at": "2026-08-11T14:00:50+00:00"},
        {**news_item(2, "eval"), "published_at": "2026-09-08T17:59:50+00:00"},
    ])
    candidates, coverage = report._filter_events_for_profile("Trace Eval", events, {
        "focus_topics": ["trace", "eval"], "focus_origin": "task_profile", "excluded_topics": [],
    })
    assert len(candidates) == 2
    assert candidates[0]["query_match_score"] == candidates[1]["query_match_score"]
    assert [item["event_id"] for item in candidates] == ["event_002", "event_001"]
    assert candidates[0]["matched_query_topics"] == ["eval"]
    assert coverage == {"trace": True, "eval": True}


def test_release_p16_offline_replay_reports_unselected_eval(monkeypatch, tmp_path):
    case_dir = Path(__file__).parents[1] / "data/acceptance/persona_20260910_release/cases/P16"
    if not all((case_dir / name).exists() for name in ("run_artifact.json", "trace.json")):
        pytest.skip("Local P16 release evidence is not present")
    saved = json.loads((case_dir / "run_artifact.json").read_text(encoding="utf-8"))
    trace = json.loads((case_dir / "trace.json").read_text(encoding="utf-8"))
    payloads = [step["output_payload"]["payload"] for step in trace["steps"]
                if step.get("input_payload", {}).get("schema_name") == "profile_aware_news_report.v1"]
    recorded_candidates = next(
        stage["event_ids"] for stage in saved["stages"]
        if stage["stage"] == "filtering" and stage["status"] == "completed"
    )
    recorded_order = {event_id: index for index, event_id in enumerate(recorded_candidates)}
    assert len(recorded_order) == len(recorded_candidates)
    analyze = report._analyze_company_impact

    def analyze_recorded_order(company_id, context_pack, events, **kwargs):
        # Replay the captured PRE-selection boundary, not the expected final output.
        # Current date tie-breaking can otherwise send event_051 to an event_023 recording.
        assert len(events) == len(recorded_candidates)
        assert {event["event_id"] for event in events} == set(recorded_order)
        ordered = sorted(events, key=lambda event: recorded_order[event["event_id"]])
        return analyze(company_id, context_pack, ordered, **kwargs)

    class RecordedBatchAdapter(ScriptedModelAdapter):
        def __init__(self):
            super().__init__(deepcopy(payloads))
            self.requested_batches = []

        def complete(self, messages, schema_name):
            assert schema_name == "profile_aware_news_report.v1"
            assert len(self.requested_batches) < len(payloads)
            expected = payloads[len(self.requested_batches)]["output"]["analyses"]
            event_ids = [item["event_id"] for item in json.loads(messages[1]["content"])["events"]]
            assert event_ids == [item["event_id"] for item in expected]
            self.requested_batches.append(event_ids)
            return super().complete(messages, schema_name)

    class RecordedDate(date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(saved["query_constraints"]["date_to"])

    monkeypatch.setattr(report, "date", RecordedDate)
    monkeypatch.setattr(report, "company_dir", lambda _: tmp_path)
    monkeypatch.setattr(report, "build_company_context_pack", lambda *args, **kwargs: deepcopy(saved["wiki_snapshot"]))
    monkeypatch.setattr(report, "_load_news_items", lambda **kwargs: (
        deepcopy(saved["raw_items"]), deepcopy(saved["source_manifest"]),
    ))
    monkeypatch.setattr(report, "_analyze_company_impact", analyze_recorded_order)
    adapter = RecordedBatchAdapter()
    result = report.run_recent_news_report(
        "personal", saved["query"], refresh_live_sources=False,
        model_adapter=adapter,
    )
    artifact = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))
    assert_final_coverage(result, artifact, candidate={"trace": True, "eval": True},
                          selected={"trace": True, "eval": False})
    assert [item["event_id"] for item in artifact["analysis"]] == ["event_002", "event_004", "event_023"]
    assert result["selected_event_count"] == 3
    assert result["analysis_status"] == "partial"
    assert all(item["analysis_status"] == "done" for item in artifact["analysis"])
    assert result["analysis_diagnostics"]["errors"] == []
    assert result["analysis_diagnostics"]["model_calls"] == len(payloads) == 2
    assert adapter.requested_batches == [["event_002", "event_004"], ["event_023"]]
    assert {key: result["quality_diagnostics"][key] for key in saved["quality_diagnostics"]} == saved["quality_diagnostics"]
    assert result["quality_diagnostics"]["raw_count"] >= result["quality_diagnostics"]["cleaned_count"]
    assert result["source_manifest"] == saved["source_manifest"]
    assert "memory" in result["task_profile"]["excluded_topics"]
    for key in ("report_path", "html_report_path"):
        rendered = Path(result[key]).read_text(encoding="utf-8")
        assert "最终推荐未覆盖主题：Eval" in rendered
        assert "有可发布候选，但经排序与入选取舍后未进入最终推荐：Eval" in rendered
        assert "主题覆盖均通过" not in rendered
