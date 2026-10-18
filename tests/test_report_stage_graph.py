import pytest

from policy_impact.harness.report_stage_graph import END, ReportStageError, ReportStageGraph, StageSpec


def graph():
    return ReportStageGraph("example", "run-1", (
        StageSpec("collect", "analyze", required_outputs=("items",),
                  check=lambda output: None if isinstance(output["items"], list) else "items must be a list"),
        StageSpec("analyze", "publish", next_on_ask="publish", required_outputs=("claims",)),
        StageSpec("publish", END, required_outputs=("path",)),
    ))


def test_state_graph_gate_controls_order_and_retains_detached_outputs():
    flow = graph()
    with pytest.raises(ReportStageError, match="cannot start analyze"):
        flow.start("analyze")
    flow.start("collect")
    items = [{"id": "source-1"}]
    first = flow.finish("collect", {"items": items}, values={"source_items": items})
    items[0]["id"] = "changed"
    assert first["to"] == "analyze"
    assert flow.state.require("collect", "items") == [{"id": "source-1"}]
    assert flow.state.require_value("source_items") == [{"id": "source-1"}]
    flow.state.require("collect", "items")[0]["id"] = "downstream edit"
    flow.state.require_value("source_items")[0]["id"] = "downstream edit"
    assert flow.state.require("collect", "items") == [{"id": "source-1"}]
    assert flow.state.require_value("source_items") == [{"id": "source-1"}]
    flow.start("analyze")
    flow.finish("analyze", {"claims": []}, {"decision": "ask", "reason": "no supported claim"})
    flow.start("publish")
    flow.finish("publish", {"path": "diagnostic.html"})
    assert flow.state.status == "partial"
    assert [item["to"] for item in flow.state.transitions] == ["analyze", "publish", END]
    assert flow.state.summary()["stage_output_keys"]["publish"] == ["path"]
    assert flow.state.summary()["state_value_keys"] == ["source_items"]


def test_missing_output_blocks_transition_before_next_node_can_start():
    flow = graph()
    flow.start("collect")
    with pytest.raises(ReportStageError, match="missing stage output: items"):
        flow.finish("collect", {})
    assert flow.state.status == "blocked"
    assert flow.state.transitions[-1]["decision"] == "deny"
    with pytest.raises(ReportStageError, match="cannot start analyze"):
        flow.start("analyze")


def test_explicit_failure_blocks_later_tool_nodes():
    flow = graph()
    flow.start("collect")
    flow.fail("collect", "source collection failed")
    assert flow.state.current_stage == "failed"
    with pytest.raises(ReportStageError):
        flow.start("analyze")


def test_terminal_skipped_path_does_not_claim_completion():
    flow = graph()
    flow.start("collect")
    flow.finish("collect", {"items": []})
    flow.start("analyze")
    flow.finish("analyze", {"claims": []}, {"decision": "ask", "reason": "no evidence"})
    flow.start("publish")
    flow.finish("publish", {}, {"decision": "deny", "reason": "not publishable"}, skipped=True)
    assert flow.state.status == "skipped"
    assert flow.state.current_stage == END
    assert "publish" not in flow.state.outputs
