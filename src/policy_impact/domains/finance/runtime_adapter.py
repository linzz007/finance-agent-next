"""Bind domain workflow events and model calls to the existing AgentRun."""

from policy_impact.domains.finance.workflow import run_finance_report


def execute(runtime, run_id: str, message: str, model_id: str, *, publish_html: bool = True,
            authorized_reports: tuple[str, ...] = (), prepared_context: dict | None = None) -> dict:
    stage = "profile"

    def record(event):
        nonlocal stage
        stage = event["stage"]
        completed = event["status"] in {"completed", "failed"}
        runtime.store.add_agent_step(
            run_id, step_type="workflow_stage" if completed else "workflow_stage_start",
            title=event["title"], status="failed" if event["status"] == "failed" else "done", input_payload=event["input"],
            output_payload={**event["output"], **({"graph_transition": event["transition"]} if event.get("transition") else {})},
            tool_calls=event["tool_calls"], gate_result=event["gate"],
            metadata={"workflow": "financial_report", "stage": stage, "event_status": event["status"],
                      "loop_phase": "observe" if completed else "act", "recording_mode": "live_stage_callback",
                      "graph_transition": event.get("transition")},
        )

    class AuditedModel:
        def complete(self, messages, schema_name):
            return runtime._complete_model(
                run_id=run_id, title="复核金融分析与引用是否对应" if schema_name == "finance_verification.v1" else "基于真实金融快照生成中文分析", model_id=model_id,
                schema_name=schema_name, messages=messages,
                prompt_contract={"contract_id": schema_name, "evidence_scope": "current_finance_snapshot"},
            )

    try:
        allowed = runtime.registry.get_manifest("external_impact_report").allowed_tools if publish_html else ("finance.collect", "finance.metrics", "finance.research_audit")
        from policy_impact.domains.finance.chat_gateway import requests_saved_publication, forbids_acquisition
        if publish_html and requests_saved_publication(message):
            from policy_impact.domains.finance.workflow import publish_saved_analysis
            if not authorized_reports:
                raise ValueError("本会话未找到可沿用的已核验金融分析，请先分析行情或明确生成新的报告")
            result = publish_saved_analysis(runtime.company_id, authorized_reports[-1], parent_run_id=run_id,
                                            allowed_tools=allowed, on_stage=record)
        else:
            if forbids_acquisition(message):
                raise ValueError("用户要求不重新获取，但没有匹配范围的授权分析可直接发布；本轮未联网，请沿用原范围或明确允许获取新数据")
            result = run_finance_report(runtime.company_id, message, model_adapter=AuditedModel(),
                allowed_tools=allowed, on_stage=record, parent_run_id=run_id, publish_html=publish_html)
    except Exception as exc:
        runtime.store.add_agent_step(run_id, step_type="workflow_stage", title="金融报告阶段失败",
            status="failed", output_payload={"error_type": type(exc).__name__, "reason": str(exc)[:500]},
            gate_result={"decision": "deny", "reason": "阶段失败，未声称报告完成"},
            metadata={"workflow": "financial_report", "stage": stage, "loop_phase": "stop"})
        if isinstance(exc, ValueError) and publish_html:
            return {"answer": "本次金融报告没有生成：" + str(exc), "artifacts": {}, "citations": [],
                    "internal_route": "finance", "stop_reason": "quality_blocked",
                    "metadata": {"analysis_status": "blocked", "reason": str(exc)}}
        raise
    if publish_html and result.get("report"):
        review = runtime._run_finance_workflow_reviews(
            run_id=run_id,
            message=message,
            report=result["report"],
            model_id=model_id,
            prepared_context=prepared_context or {},
        )
        result.setdefault("metadata", {})["controlled_review"] = review
        result.setdefault("delegation_evidence", []).extend(review.get("delegation_evidence") or [])
        summary = "；".join(
            f"{item.get('role')}={item.get('status')}"
            for item in review.get("findings", [])
        )
        if summary:
            result["answer"] = str(result.get("answer") or "") + f"\n\n**受控复核**：{summary}。"
        if review.get("status") != "passed":
            result["stop_reason"] = "review_blocked"
            result["metadata"]["analysis_status"] = "review_blocked"
            result["answer"] += "报告已生成证据快照，但受控复核未完整通过，不能把其中的候选解释视为已确认结论。"
    runtime.store.add_agent_step(run_id, step_type="artifact_write", title="挂载金融报告与证据快照", status="done",
        output_payload={"artifacts": result["artifacts"], **result["metadata"],
                        "workflow_graph": result.get("workflow_graph")}, metadata={"loop_phase": "act"})
    if publish_html:
        result.pop("report", None)
    return result
