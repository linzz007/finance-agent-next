from policy_impact.harness.agent_runtime import AgentRuntime
from policy_impact.company_wiki import loader
from policy_impact.harness.model_gateway import ScriptedModelAdapter
from policy_impact.memory.store import PolicyMemoryStore


def test_explicit_chat_preference_does_not_also_save_skill_result(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    root = tmp_path / "data" / "companies" / "memory_test"
    (root / "wiki").mkdir(parents=True)
    (root / "company.yaml").write_text('{"company_name":"Memory test"}', encoding="utf-8")
    store = PolicyMemoryStore("memory_test")
    store.seed_model_configs()
    sid = store.create_chat_session(title="memory", mode="auto", model_id="deepseek-v4-flash", active_skill_id="")
    runtime = AgentRuntime("memory_test", model_adapter=ScriptedModelAdapter([
        {"type": "final", "output": {"answer": "已收到。"}},
    ]))
    result = runtime.run_turn(sid, "请记住：先给结论，再给依据。", "auto", "deepseek-v4-flash")
    writes = [s for s in store.list_agent_steps(result["run_id"]) if s["step_type"] == "memory_write"]
    assert len(writes) == 1
    assert writes[0]["input_payload"]["memory_type"] == "explicit_user_memory"


def test_explaining_inline_slash_does_not_execute_report():
    message = "解释一下 /report 命令怎么用，先不要执行或生成报告。"
    skill_id, task = AgentRuntime._parse_slash_command(message)
    assert skill_id is None
    assert task == message


def test_explicit_inline_slash_still_executes():
    skill_id, task = AgentRuntime._parse_slash_command("请基于我的Wiki /report 生成最近7天的报告")
    assert skill_id == "external_impact_report"
    assert "/report" not in task


def test_quoted_command_is_not_activation():
    message = "文档中的`/report`是什么意思？"
    assert AgentRuntime._parse_slash_command(message) == (None, message)


def test_asking_about_updated_wiki_is_not_new_durable_feedback():
    message = "我刚在Wiki改了关注重点。请读取最新内容，说清这次重点是什么，哪些暂时不关注。"
    assert AgentRuntime._feedback_memory_content(message) == ""


def test_explicit_future_feedback_can_be_saved():
    message = "我不认可第二条建议，我们短期不做C端。请根据这个反馈调整后续关注重点。"
    assert "短期不做C端" in AgentRuntime._feedback_memory_content(message)


def test_literal_reply_suffix_can_be_removed_without_overriding_a_refusal():
    assert AgentRuntime._literal_reply_prefix("这次是临时要求。请只回复收到。", "收到。本次不会保存。") == "收到"
    assert AgentRuntime._literal_reply_prefix("请只回复成功", "无法执行该操作。") is None
    assert AgentRuntime._literal_reply_prefix("请解释报告", "收到。下面解释。") is None


def test_future_report_diagnostic_does_not_recommend_source_retry():
    runtime = AgentRuntime.__new__(AgentRuntime)
    answer = runtime._news_workflow_answer("未来报告", {
        "relevance_gate": "ask", "source_disclosure": "saved snapshot", "event_count": 10,
        "matched_event_count": 0, "gate_reason": "future_window: 资料尚未发布", "topic_coverage": {},
    }, {})
    assert "改用已经过去的日期窗口" in answer
    assert "请接入" not in answer


def test_unknown_slash_has_a_deterministic_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    root = tmp_path / "data" / "companies" / "commands_test"
    (root / "wiki").mkdir(parents=True)
    (root / "company.yaml").write_text('{"company_name":"Command test"}', encoding="utf-8")
    store = PolicyMemoryStore("commands_test")
    store.seed_model_configs()
    sid = store.create_chat_session(title="command", mode="auto", model_id="deepseek-v4-flash", active_skill_id="")
    runtime = AgentRuntime("commands_test", model_adapter=ScriptedModelAdapter([]))
    result = runtime.run_turn(sid, "/unknown_skill 生成报告", "auto", "deepseek-v4-flash")
    assert "未识别的技能命令" in result["answer"]
    assert not result["artifacts"]
    steps = store.list_agent_steps(result["run_id"])
    assert not any(step["step_type"] == "model_call" for step in steps)
    assert any(step.get("gate_result", {}).get("reason") == "unknown_slash_command" for step in steps)
