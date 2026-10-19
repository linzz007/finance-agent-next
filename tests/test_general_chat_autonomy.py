"""Offline regressions for model-owned chat decisions and delegation evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from policy_impact.company_wiki import loader as wiki_loader
from policy_impact.harness import agent_runtime as runtime_module
from policy_impact.harness import report_context
from policy_impact.harness.agent_runtime import AgentRuntime
from policy_impact.harness.chat_context import ChatContextConfig
from policy_impact.harness.context_manifest import estimate_tokens
from policy_impact.harness.model_config import DEFAULT_MODEL_ID
from policy_impact.harness.model_gateway import (
    AnthropicCompatibleModelAdapter,
    ModelInvocationError,
    ScriptedModelAdapter,
)
from policy_impact.harness.subagent_planner import QuerySubagentPlanner
from policy_impact.memory.store import PolicyMemoryStore


@pytest.fixture(autouse=True)
def isolated_company(tmp_path, monkeypatch):
    monkeypatch.setattr(wiki_loader, "project_root", lambda: tmp_path)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "offline-test-token")
    monkeypatch.setenv("ANTHROPIC_MODEL", DEFAULT_MODEL_ID)
    company_dir = tmp_path / "data" / "companies" / "company_test"
    (company_dir / "wiki").mkdir(parents=True)
    (company_dir / "company.yaml").write_text(
        json.dumps({"company_id": "company_test", "company_name": "Offline test"}),
        encoding="utf-8",
    )
    PolicyMemoryStore("company_test").seed_model_configs()

    def no_network(*args, **kwargs):
        pytest.fail("A test attempted an unmocked model transport call")

    monkeypatch.setattr(AnthropicCompatibleModelAdapter, "_request", no_network)


def _real_adapter(monkeypatch, payloads):
    adapter = AnthropicCompatibleModelAdapter(
        model_id=DEFAULT_MODEL_ID,
        base_url="https://offline.invalid",
        api_key="offline-test-token",
        max_attempts=1,
    )
    replies = iter(payloads)
    requests = []

    def request(system, messages):
        requests.append({"system": system, "messages": deepcopy(messages)})
        try:
            payload = next(replies)
        except StopIteration:
            pytest.fail("Unexpected extra model request")
        return {
            "id": f"offline_response_{len(requests)}",
            "model": DEFAULT_MODEL_ID,
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    monkeypatch.setattr(adapter, "_request", request)
    return adapter, requests


def _run_chat(runtime, message, *, mode="auto", active_skill_id=""):
    session_id = runtime.store.create_chat_session(
        title="Autonomous chat",
        mode=mode,
        model_id=DEFAULT_MODEL_ID,
        active_skill_id=active_skill_id,
    )
    result = runtime.run_turn(
        session_id=session_id,
        message=message,
        mode=mode,
        model_id=DEFAULT_MODEL_ID,
        active_skill_id=active_skill_id,
    )
    return result, runtime.store.list_agent_steps(result["run_id"])


def _main_calls(steps):
    return [
        step for step in steps
        if step["step_type"] == "model_call"
        and step["metadata"].get("embedded_loop") == "general_chat"
    ]


@pytest.mark.parametrize("mode", ["auto", "chat"])
@pytest.mark.parametrize("use_default_adapter", [False, True])
def test_real_adapter_makes_first_decision_despite_source_keywords(
    monkeypatch, mode, use_default_adapter,
):
    message = "Explain how sources support a claim using the supplied context."
    final = {"type": "final", "output": {"answer": "Evidence must support the claim."}}
    adapter, requests = _real_adapter(monkeypatch, [final])
    monkeypatch.setattr(
        AnthropicCompatibleModelAdapter, "from_env", classmethod(lambda cls: adapter),
    )
    runtime = AgentRuntime(
        company_id="company_test",
        model_adapter=None if use_default_adapter else adapter,
    )
    assert QuerySubagentPlanner().plan(
        message,
        skill_manifest=runtime.registry.get_manifest("general_chat"),
        artifact_ref="main_chat_context:test",
        prepared_context={},
    )

    def no_keyword_plan(*args, **kwargs):
        pytest.fail("Ordinary chat must not run the keyword subagent planner")

    monkeypatch.setattr(QuerySubagentPlanner, "plan", no_keyword_plan)
    result, steps = _run_chat(runtime, message, mode=mode)

    assert result["selected_skill_id"] == "general_chat"
    assert result["answer"] == final["output"]["answer"]
    assert len(requests) == 1
    calls = _main_calls(steps)
    assert len(calls) == 1
    assert calls[0]["output_payload"]["response_id"] == "offline_response_1"
    assert calls[0]["output_payload"]["payload"] == final
    assert calls[0]["metadata"]["adapter"] == "AnthropicCompatibleModelAdapter"
    assert not calls[0]["metadata"].get("deterministic_action")
    assert not any(step["step_type"].startswith("subagent_") for step in steps)

    system = json.loads(requests[0]["system"])
    visible = system["visible_context"]
    manifest = runtime.registry.get_manifest("general_chat")
    assert system["agent_role"] == "main_chat_agent"
    assert "recommended_delegation_plan" not in visible
    assert visible["delegation_policy"]["decision_owner"] == "main_agent_model"
    assert visible["delegation_policy"]["instructions"]
    assert visible["tool_policy_summary"]["runtime_owns_permissions"] is True
    assert visible["loop_budget"] == {
        "max_steps": manifest.max_steps,
        "max_model_calls": manifest.max_model_calls,
        "timeout_seconds": manifest.timeout_seconds,
        "reserve_final_model_call": True,
    }
    roster = visible["agent_roster"]
    assert {item["role"] for item in roster} == set(manifest.allowed_subagents)
    for item in roster:
        assert item["description"] and item["use_when"] and item["output_schema_name"]
        assert set(item["tools"]) <= set(manifest.allowed_tools)
        assert item["max_model_calls"] > 0
        assert item["can_publish"] is False
    assert set(visible) == set(system["visible_keys"])
    prompt = json.dumps(requests[0], ensure_ascii=False)
    assert "first_two_items_only" not in prompt
    assert "单次A/B试验不能消除采样噪声" in prompt
    assert "示例阈值不是已测量结果" in prompt
    assert "不按组件名直接排高低" in prompt
    assert "探索性检查" in prompt
    assert "比例指标必须给出明确分母" in prompt


@pytest.mark.parametrize("with_report", [False, True])
def test_report_followup_expands_only_local_artifact_budget(monkeypatch, with_report):
    evidence = [{
        "projection_scope": "selected_items_only_not_full_source_text",
        "report_evidence": [{
            "title": f"Saved item {index}", "summary": "已保存摘要" * 48,
            "condition": "若原型确认采用此机制",
            "conditional_impact": "可能需要验证失败轨迹与成本变化",
            "recommended_action": "先复现实验，再比较多次运行的分布",
        } for index in range(3)],
    }] if with_report else []
    if with_report:
        assert 600 < estimate_tokens(evidence) < 1800
    monkeypatch.setattr(report_context, "read_report_evidence", lambda *args: (evidence, {}))
    captured = []
    manifests = []
    original_compile = runtime_module.compile_chat_context
    original_manifest = runtime_module.build_context_manifest

    def compile_context(**kwargs):
        result = original_compile(**kwargs)
        captured.append((kwargs["config"], result))
        return result

    def manifest(**kwargs):
        result = original_manifest(**kwargs)
        if kwargs["stage_name"] == "chat_turn":
            manifests.append(result.metadata)
        return result

    monkeypatch.setattr(runtime_module, "compile_chat_context", compile_context)
    monkeypatch.setattr(runtime_module, "build_context_manifest", manifest)
    adapter, requests = _real_adapter(monkeypatch, [
        {"type": "final", "output": {"answer": "Use the saved evidence within its scope."}},
    ])
    runtime = AgentRuntime(company_id="company_test", model_adapter=adapter)
    _run_chat(runtime, "Compare the saved report items.")

    assert len(requests) == 1
    assert len(captured) == len(manifests) == 1
    config, compiled = captured[0]
    expected = asdict(ChatContextConfig())
    expected["artifact_token_budget"] = 1800 if with_report else 600
    assert asdict(config) == expected
    assert compiled["recent_artifacts"] == evidence
    assert compiled["context_policy"]["token_budget"] == config.manifest_budget()
    assert manifests[0]["token_budget"] == config.manifest_budget()
    assert manifests[0]["visible_content"]["recent_artifacts"] == evidence
    assert ChatContextConfig().artifact_token_budget == 600


@pytest.mark.parametrize("message,retain_wiki", [
    (
        "刚才报告中适合我两小时学习的一项，给出原文链接、发布日期和实际读取范围。"
        "它真的讲了从反馈改进 skill、memory 或 workflow 吗？算热门吗，热度指标和采样时间在哪里？没有就直接说。",
        True,
    ),
    (
        "我想用刚才报告的一项方法，让 Agent 根据失败反馈改进一个 skill、一条 memory 规则或一个 workflow 步骤。"
        "请比较哪个最值得先试，检查反馈噪声和过拟合风险，再设计今晚两小时内的小实验：固定输入、改动前后对照、"
        "验证指标和回滚条件。先不要运行，也不要把实验结果当成已发生。",
        True,
    ),
    ("请根据我的当前场景和约束，调整刚才报告里的验证计划。", True),
    ("只逐字复述刚才报告里的实验设计，不要新增建议。", False),
    ("原样重复刚才报告中‘请比较哪个最值得先试’这句话。", False),
    ("刚才报告里列了哪些实验？", False),
    ("刚才那三个临时重点是什么？", False),
], ids=["SD06", "SD10", "scenario_constraints", "verbatim_experiment",
        "verbatim_comparison", "report_recall", "ordinary_recall"])
def test_report_application_keeps_scoped_wiki_but_recall_does_not(monkeypatch, message, retain_wiki):
    facts = [{"fact_id": fact_id, "value": value, "source_kind": "user_confirmed"} for fact_id, value in [
        ("personal_identity", "我是个人 AI 工程师，正在学习 Agent 自我进化；不是金融信息企业，没有公司产品采用或营收目标。"),
        ("personal_goal", "理解 Agent 如何从任务反馈和失败轨迹改进 skill、memory 与 workflow，再通过 Eval、固定回归用例和可观测 Trace 验证改动。"),
        ("personal_stack", "学习项目使用 Python、SQLite 和 LLM API，目前只有单 Agent 循环、简单工具及少量手工记录。没有已完成自动技能更新、长期记忆训练、自我改进收益或性能基准的证据，也没有近期重写整个框架的计划。"),
        ("personal_constraints", "每天最多两小时，优先一个可在本地复现的小实验，写清反馈输入、候选改动、固定评测集、成功和失败标准及回滚条件。当前仅请求阅读与实验设计，不执行代码、不安装依赖、不自动修改 skill、memory、workflow 或 Wiki；不能声称实验已经成功。"),
        ("personal_watchlist", "最新不等于热门，引用量、点赞、星标和采样时间未知时不能虚构。"),
    ]]
    retrievals = []

    def retrieve(_gateway, role, tool, **kwargs):
        retrievals.append((role, tool, kwargs))
        if tool == "company_wiki_search":
            return deepcopy(facts)
        assert tool == "memory_search"
        return [{"id": "unrelated-memory", "content": "Cross-session memory must stay excluded."}]

    monkeypatch.setattr(runtime_module.ToolGateway, "call", retrieve)
    adapter, requests = _real_adapter(monkeypatch, [
        {"type": "final", "output": {"answer": "Use only confirmed context."}},
    ])
    runtime = AgentRuntime(company_id="company_test", model_adapter=adapter)
    _, steps = _run_chat(runtime, message)

    assert len(requests) == 1
    context = next(step["output_payload"]["context_manifest"]["visible_content"]
                   for step in steps if step["step_type"] == "context_build")
    assert context["relevant_memories"] == []
    expected = {item["fact_id"]: item["value"] for item in facts} if retain_wiki else {}
    assert {item["fact_id"]: item["value"] for item in context["company_facts"]} == expected
    visible = json.loads(requests[0]["system"])["visible_context"]
    assert {item["fact_id"]: item["value"] for item in visible["company_facts"]} == expected
    wiki_retrieval = next(call for call in retrievals if call[1] == "company_wiki_search")
    assert wiki_retrieval[2] == {"company_id": "company_test", "query": message, "top_k": 12}
    audit = next(step["output_payload"] for step in steps
                 if step["step_type"] == "tool_call" and "raw_fact_count" in step["output_payload"])
    assert audit["raw_fact_count"] == 5
    assert audit["fact_count"] == (5 if retain_wiki else 0)
    assert audit["history_only_resolution"] is not retain_wiki
    assert audit["wiki_context_retained_for_report_followup"] is retain_wiki


def test_implicit_report_followup_requires_same_session_report_reference():
    message = "请根据我的当前场景和约束，调整刚才那一项的验证计划。"
    assert not AgentRuntime._report_followup_needs_wiki(message, [])
    history = [{"role": "assistant", "metadata": {"artifacts": {"html_report": "report.html"}}}]
    assert AgentRuntime._report_followup_needs_wiki(message, history)


@pytest.mark.parametrize("message", [
    "Consider this proposal.",
    "Use the subagent skeptic and analyst to examine this proposal.",
    "Review the sources for this proposal.",
])
def test_real_model_selects_delegate_and_reassesses_structured_child_result(monkeypatch, message):
    child_output = {"claims": [{
        "claim_ref": "claim-1",
        "statement": "The proposal needs a measurable acceptance condition.",
        "evidence_refs": [],
        "confidence": 0.7,
    }]}
    final = {"type": "final", "output": {"answer": child_output["claims"][0]["statement"]}}
    adapter, requests = _real_adapter(monkeypatch, [
        {"type": "delegate", "role": "analyst", "task": "Identify an acceptance condition."},
        {"type": "final", "output": child_output},
        final,
    ])
    runtime = AgentRuntime(company_id="company_test", model_adapter=adapter)
    result, steps = _run_chat(runtime, message)

    assert result["answer"] == final["output"]["answer"]
    assert len(requests) == 3
    assert json.loads(requests[0]["system"])["agent_role"] == "main_chat_agent"
    calls = _main_calls(steps)
    assert [step["output_payload"]["response_id"] for step in calls] == [
        "offline_response_1", "offline_response_3",
    ]
    assert [step["output_payload"]["payload"]["type"] for step in calls] == ["delegate", "final"]
    assert calls[0]["output_payload"]["payload"]["role"] == "analyst"
    observation = next(
        item["content"]["subagent_result"]
        for item in calls[1]["input_payload"]["messages"]
        if item.get("role") == "tool" and item.get("tool_name") == "subagent.analyst"
    )
    assert observation["stop_reason"] == "success"
    assert observation["output"] == child_output
    assert observation["context_manifest_id"].startswith("ctx_subagent_")
    plans = [step for step in steps if step["step_type"] == "subagent_plan"]
    assert len(plans) == 1
    assert plans[0]["output_payload"]["planner"] == "main_agent_loop.delegate_action.v1"
    children = [step for step in steps if step["step_type"] == "subagent_delegate"]
    assert len(children) == 1
    assert children[0]["output_payload"]["output"] == child_output
    assert children[0]["output_payload"]["task_id"] == observation["task_id"]
    assert children[0]["gate_result"]["decision"] == "allow"
    run = runtime.store.get_agent_run(result["run_id"])
    assert run["metadata"]["execution_mode"] == "subagent_workflow"
    assert run["metadata"]["delegation_count"] == 1
    assert run["metadata"]["delegation_evidence"][0]["role"] == "analyst"

    child_system = json.loads(requests[1]["system"])
    child_brief = child_system["visible_context"]["task_brief"]
    assert child_brief["input_artifact_refs"] == [
        f"main_chat_context:{result['run_id'][:12]}",
    ]
    assert child_brief["task"] == "Identify an acceptance condition."
    assert any("main_chat_context_for_subagent.v1" in item["content"]
               for item in requests[1]["messages"])
    injected = [step for step in steps if step["metadata"].get("deterministic_action")]
    assert len(injected) == 1
    assert injected[0]["step_type"] == "runtime_action"
    assert injected[0]["metadata"]["decision_source"] == "runtime"
    assert injected[0]["metadata"]["loop_phase"] == "act"
    assert injected[0]["output_payload"]["payload"]["tool_name"] == "artifact_read"


@pytest.mark.parametrize(("action", "error_key", "category"), [
    ({"type": "delegate", "role": "publisher", "task": "Publish this."},
     "delegate_error", "tool_denied"),
    ({"type": "delegate", "role": "analyst", "task": "Read another artifact.",
      "input_artifact_refs": ["private:unrelated"]}, "delegate_error", "delegate_failed"),
])
def test_real_chat_retains_permission_and_artifact_boundaries(monkeypatch, action, error_key, category):
    final = {"type": "final", "output": {"answer": "The requested action was not completed."}}
    adapter, requests = _real_adapter(monkeypatch, [action, final])
    runtime = AgentRuntime(company_id="company_test", model_adapter=adapter)
    result, steps = _run_chat(runtime, "Review the sources.")

    calls = _main_calls(steps)
    assert len(requests) == 2
    assert result["answer"] == final["output"]["answer"]
    errors = [item["content"][error_key] for item in calls[-1]["input_payload"]["messages"]
              if error_key in item.get("content", {})]
    assert errors[-1]["category"] == category
    assert not any(step["step_type"] == "subagent_delegate" for step in steps)


def test_real_chat_cannot_run_report_workflow_as_a_tool(monkeypatch):
    adapter, requests = _real_adapter(monkeypatch, [
        {"type": "tool_call", "tool_name": "report_workflow.run", "arguments": {}},
    ])
    runtime = AgentRuntime(company_id="company_test", model_adapter=adapter)

    with pytest.raises(ModelInvocationError, match="tool_denied"):
        _run_chat(runtime, "Review the sources.")

    assert len(requests) == 1


def test_real_chat_stops_at_manifest_model_budget(monkeypatch):
    adapter, requests = _real_adapter(monkeypatch, [
        {"type": "delegate", "role": "analyst", "task": "Identify an acceptance condition."},
        {"type": "final", "output": {"claims": []}},
    ])
    runtime = AgentRuntime(company_id="company_test", model_adapter=adapter)
    get_manifest = runtime.registry.get_manifest
    manifest = get_manifest("general_chat").model_copy(update={"max_model_calls": 1})
    monkeypatch.setattr(runtime.registry, "get_manifest", lambda skill_id:
                        manifest if skill_id == "general_chat" else get_manifest(skill_id))

    with pytest.raises(ModelInvocationError, match="max_model_calls"):
        _run_chat(runtime, "Review the sources.")

    # The final model-call slot is reserved for a final response. A delegate
    # action in a one-call budget is rejected without starting a second call.
    assert len(requests) == 1
    assert json.loads(requests[0]["system"])["visible_context"]["loop_budget"]["max_model_calls"] == 1


def test_scripted_chat_still_controls_its_own_action_sequence():
    runtime = AgentRuntime(company_id="company_test", model_adapter=ScriptedModelAdapter([
        {"type": "delegate", "role": "skeptic", "task": "Identify an evidence gap."},
        {"type": "final", "output": {"counterexamples": [], "notes": ["No supplied evidence."]}},
        {"type": "final", "output": {"answer": "No supplied evidence."}},
    ]))
    result, steps = _run_chat(runtime, "Review the sources.")

    assert result["answer"] == "No supplied evidence."
    assert [step["output_payload"]["response_id"] for step in _main_calls(steps)] == [
        "scripted_1", "scripted_3",
    ]
    assert [step["output_payload"]["role"] for step in steps
            if step["step_type"] == "subagent_delegate"] == ["skeptic"]


def test_explicit_report_still_runs_workflow_before_real_adapter(monkeypatch):
    adapter, requests = _real_adapter(monkeypatch, [
        {"type": "final", "output": {"answer": "The workflow observation is available."}},
    ])
    runtime = AgentRuntime(company_id="company_test", model_adapter=adapter)
    workflow_messages = []

    def workflow(run_id, session_id, message, model_id, context_manifest, prepared_context):
        assert requests == []
        workflow_messages.append(message)
        return {"answer": "Offline workflow result.", "artifacts": {}, "citations": [],
                "internal_route": "news", "stop_reason": "success"}

    monkeypatch.setattr(runtime, "_run_external_impact_workflow", workflow)
    result, steps = _run_chat(runtime, "/report Review the sources.")

    assert result["selected_skill_id"] == "external_impact_report"
    assert workflow_messages == ["Review the sources."]
    assert len(requests) == 1
    assert json.loads(requests[0]["system"])["agent_role"] == "report_orchestrator"
    assert any("report_workflow.observation.v1" in item["content"]
               for item in requests[0]["messages"])
    assert any(step["metadata"].get("deterministic_action")
               and step["output_payload"].get("payload", {}).get("tool_name") == "report_workflow.run"
               for step in steps)
