"""Build reviewable Chinese deliverables from recorded HTTP acceptance evidence."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "acceptance"


def read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def link(label, path):
    return f"[{label}](<{Path(path).resolve().as_posix()}>)"


def write(name, lines):
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    root = ROOT / "data" / "acceptance" / ("persona_" + args.label)
    suite = read(ROOT / "tests" / "fixtures" / "persona_acceptance.json")
    records = read(root / "results.json", [])
    verdicts = read(root / "semantic_verdicts.json", {})
    for filename in ("semantic_company.json", "semantic_personal.json"):
        verdicts.update(read(root / filename, {}))
    companies = read(root / "companies.json", {})
    provenance = read(root / "provenance.json", {})
    index = {r["id"]: r for r in records}
    lines = ["# 32 条中文用户测试集", "", "先录入 Wiki，再按照下列顺序测试。相同 persona/session 必须在同一会话；不同 session 新建对话。", "",
             "这是一组功能验收，不是准确率 benchmark；每条既看答案，也看副作用和 Trace。", ""]
    if provenance:
        lines += ["当前验收表采用逐例最新复测证据，不是最后一次代码修订后单次32条全跑。每条回答和Trace均标注原始批次、空间和会话。", ""]
    for key, persona in suite["personas"].items():
        lines += [f"## {persona['name']}", "", f"空间标识：`{companies.get(key, '')}`。", ""]
        lines += [f"- {fact['id']}：{fact['value']}" for fact in persona["facts"]]
    for case in suite["cases"]:
        lines += ["", f"## {case['id']} · {case['persona']} / {case['session']}", "", f"> {case['query']}", "",
                  f"验收：{case['expect']}"]
        if case.get("wiki_updates"):
            lines += ["", "前置操作：通过 Wiki 保存接口修改：", *[f"- `{k}`：{v}" for k, v in case["wiki_updates"].items()]]
    write("persona_query_set_cn.md", lines)

    answers = ["# 本轮真实问答", "", f"运行批次：`{args.label}`。下面直接摘录 API 返回，不是另写的理想答案。", ""]
    traces = ["# 本轮 Trace 验收", "", f"批次：`{args.label}`。记录的是执行输入、公开动作、输出及校验，不展示模型私有思维链。", "",
              "## 怎么读", "", "1. 前端切换对应工作空间，打开验收会话，展开回答上方的运行过程。",
              "2. 按 run_id 对照下面的步骤表，点击完整 Trace 可查看输入上下文、模型输出、工具权限和 Gate。",
              "3. Trace 回答为什么得到结果；HTML/Markdown 是用户交付物，RunArtifact 是该次报告的资料与分析快照。",
              "4. model_call 中 deterministic_action / adapter 标明运行时强制动作；只有带供应方 usage 的真实调用计入模型用量。",
              "5. SSE 的 progress 是执行中的实时进度，正文仍是 buffered_final，不宣称供应方 token 流式。",
              "6. 对话run_id与内部Workflow产物run_id可能不同；用该轮工具输出和Artifact路径关联。不要拿HTML文件名的短ID去当作对话run_id。", ""]
    results = ["# 双画像功能验收结果", "", f"批次：`{args.label}`；模型：`deepseek-v4-flash`。Wiki 和聊天全部通过公开 HTTP API 录入。", "",
               "## 口径", "", "结构通过不等于业务语义通过；部分完成、安全拒绝、无数据诊断不充作有用报告成功。没有等条件多次测量，不宣称性能提升比例。", ""]
    if provenance:
        description = "逐例最新证据合并：" + "、".join(f"{item['batch']}（{item['case_count']}条）" for item in provenance["execution_batches"])
        for document in (answers, traces, results):
            document += [description + "。不是同一版本的一次完整运行；原始批次全部保留。", ""]
    times = [r["elapsed_seconds"] for r in records if "elapsed_seconds" in r]
    results += [f"- 实际完成：{len(records)}/{len(suite['cases'])}；结构及硬约束通过：{sum(bool(r.get('structural_pass')) for r in records)}/{len(records)}。",
                f"- 端到端中位耗时：{statistics.median(times):.3f} 秒。" if times else "- 暂无耗时记录。",
                f"- 本表所选证据的 Trace 记录 input tokens：{sum(r.get('input_tokens', 0) for r in records)}；output tokens：{sum(r.get('output_tokens', 0) for r in records)}。不是本次所有修复复跑的总用量，也不等同账单；重试、缓存和金额以供应方记录为准。",
                f"- 独立语义评审：{dict(Counter(v.get('verdict', '未评审') for v in verdicts.values())) if verdicts else '待人工/子任务审核，不能据此宣布通过'}。", "",
                "| Case | 硬约束 | 语义结论 | 秒 | 真实回答与证据 |", "| --- | --- | --- | ---: | --- |"]
    for case in suite["cases"]:
        cid = case["id"]
        record = index.get(cid, {})
        cdir = Path(record.get("evidence_dir") or root / "cases" / cid)
        v = verdicts.get(cid, {})
        hard_status = "通过" if record.get("structural_pass") else ("未通过" if record else "未跑")
        results.append(f"| {cid} | {hard_status} | {v.get('verdict', '待评审')} | {record.get('elapsed_seconds', '-')} | {link('回答', cdir / 'answer.md')} / {link('Trace', cdir / 'trace.json')} |")
        if not (cdir / "answer.md").exists():
            continue
        answers += [f"## {cid}", "", f"用户：{case['query']}", "", f"Run：`{record.get('run_id')}`；会话：`{record.get('session_id')}`。",
                    f"空间：`{record.get('company_id')}`；原始批次：`{record.get('evidence_batch', args.label)}`。", "",
                    (cdir / "answer.md").read_text(encoding="utf-8"), "", f"验收结论：{v.get('verdict', '待独立评审')}。{v.get('reason', '')}", "",
                    link("完整 Trace", cdir / "trace.json"), ""]
        trace = read(cdir / "trace.json", {})
        model_events = [step for step in trace.get("steps", []) if step.get("step_type") == "model_call"]
        provider_calls = sum(bool((step.get("metadata") or {}).get("provider"))
                             and not (step.get("metadata") or {}).get("deterministic_action") for step in model_events)
        runtime_actions = sum(bool((step.get("metadata") or {}).get("deterministic_action")) for step in model_events)
        traces += [f"## {cid} · {case['query']}", "", f"Run：`{record.get('run_id')}`；原始批次：`{record.get('evidence_batch', args.label)}`。",
                   f"真实供应方调用：{provider_calls}；运行时确定性动作：{runtime_actions}。", "",
                   "| 步骤 | 类型 | 做了什么 | 记录状态 |", "| ---: | --- | --- | --- |"]
        for step in trace.get("steps", []):
            traces.append(f"| {step.get('step_index')} | {step.get('step_type')} | {str(step.get('title', '')).replace('|', '/')} | {step.get('status')} |")
        summary = trace.get("subagent_summary") or {}
        if summary.get("results"):
            traces += ["", "子任务结果：", *[f"- `{r.get('role')}`：`{r.get('stop_reason')}`；task_id=`{r.get('task_id')}`；Context=`{r.get('context_manifest_id')}`。" for r in summary["results"]]]
        ts = trace.get("trace_summary") or {}
        traces += ["", f"Memory 写入 {ts.get('memory_write_count', 0)} 次。", "",
                   f"{link('完整输入输出', cdir / 'trace.json')}；{link('前置 Wiki', cdir / 'wiki_before.json')}；{link('硬断言', cdir / 'checks.json')}。", ""]
        events = read(cdir / "sse.json", [])
        if events:
            first_progress = next((e["at_ms"] for e in events if e["event"] == "progress"), None)
            first_text = next((e["at_ms"] for e in events if e["event"] == "chunk"), None)
            traces += [f"SSE 首次进度 {first_progress} ms；首次正文 {first_text} ms。", ""]
        artifact = read(cdir / "run_artifact.json", {})
        if artifact.get("skill") == "recent_news_report":
            constraints = artifact.get("query_constraints") or {}
            quality = artifact.get("quality_diagnostics") or {}
            traces += ["### 报告证据链", "",
                       f"原始 {len(artifact.get('raw_items', []))} -> 清洗 {len(artifact.get('cleaned_items', []))} -> 结构化 {len(artifact.get('structured_events', []))}；选题命中 {quality.get('matched_count', '-')}，可发布候选 {quality.get('publishable_count', '-')}，最终分析 {len(artifact.get('analysis', []))}。",
                       f"实际日期窗口：{constraints.get('date_from')} 至 {constraints.get('date_to')}；分析状态：{artifact.get('analysis_status')}。",
                       f"门控：{artifact.get('gate_reason')}。", "",
                       "清洗前后数量相同不代表清洗无效，也不能宣称本条删掉了重复项；去重/非法字段的故障覆盖另见单元测试。", "",
                       "| 事件 | Wiki事实引用 | 中文摘要 |", "| --- | --- | --- |"]
            for row in artifact.get("analysis", []):
                links = row.get("evidence_links") or {}
                fact_ids = ", ".join(str(fact.get("fact_id")) for fact in links.get("wiki_facts", []))
                traces.append(f"| {row.get('event_id')} · {str(row.get('title', '')).replace('|', '/')} | {fact_ids} | {str(row.get('event_summary_zh', '')).replace('|', '/')} |")
            traces += ["", link("完整资料、引用与分析快照", cdir / "run_artifact.json"), ""]
    results += ["", "## 尚未完全通过的用例", ""]
    remaining = [(cid, verdict) for cid, verdict in verdicts.items() if verdict.get("verdict") != "通过"]
    if remaining:
        results += [f"- **{cid} {verdict.get('verdict')}**：{verdict.get('reason', '')}" for cid, verdict in remaining]
    else:
        results += ["本次已评审用例没有部分通过项；仍不代表未覆盖能力、长期稳定性或生产安全已经验证。"]
    results += ["", "## 耗时分类", "", "以下均为该用例一次执行，不是压测SLO，也不是多次采样p95。源缓存、模型重试和输入不同，不可直接据此声称优化比例。", "",
                "| 类型 | 数量 | 中位秒 | 最小-最大秒 |", "| --- | ---: | ---: | --- |"]
    groups = {}
    for case in suite["cases"]:
        record = index.get(case["id"])
        if not record:
            continue
        group = "报告/资料不足诊断" if case.get("skill") == "external_impact_report" else ("显式子任务" if case.get("roles") else "普通对话/追问/记忆")
        groups.setdefault(group, []).append(record["elapsed_seconds"])
    for group, values in groups.items():
        results.append(f"| {group} | {len(values)} | {statistics.median(values):.3f} | {min(values):.3f}-{max(values):.3f} |")
    results += ["", "## 本地复现", "", "```powershell", f"py -3 scripts/run_persona_acceptance.py --label {args.label}_reproduce", "```", "",
                "这会创建新的隔离测试空间，不覆盖本轮证据。日志仅保存业务请求，模型密钥不写入报告。", "",
                "相关文档：", f"- {link('验收设计方案', DOCS / 'persona_functional_test_plan_cn.md')}",
                f"- {link('32条测试集', DOCS / 'persona_query_set_cn.md')}", f"- {link('完整问答', DOCS / 'persona_answers_cn.md')}",
                f"- {link('逐轮Trace', DOCS / 'persona_trace_report_cn.md')}", f"- {link('修复与已知边界', DOCS / 'persona_changes_and_limits_cn.md')}"]
    write("persona_answers_cn.md", answers)
    write("persona_trace_report_cn.md", traces)
    write("persona_results_cn.md", results)
    guide = ["# 从这里开始验收", "", "页面：http://127.0.0.1:8501/", "",
             "这次交付的是32种真实中文用户任务、实际运行证据和修复，不是只列一份理想测试计划。结果中的部分通过仍需你评议，不当作满分。", "",
             "## 先看三个演示", "",
             "在左侧下方展开“工作空间”，选择下面的空间ID，再搜索对应会话名。不要选同名的baseline/round2旧空间。", ""]
    for cid, title in (("H03", "企业默认报告"), ("P01", "个人默认研判"), ("P10", "分析与质疑子任务")):
        record = index.get(cid)
        if not record:
            continue
        case = next(case for case in suite["cases"] if case["id"] == cid)
        guide += [f"### {cid} {title}", "", f"- 空间：`{record['company_id']}`。",
                  f"- 会话名：`验收 {case['persona']} {case['session']}`；run_id：`{record['run_id']}`。",
                  f"- 用户问题：{record['query']}", f"- 评审：{verdicts.get(cid, {}).get('verdict', '待评审')}；{verdicts.get(cid, {}).get('reason', '')}"]
        artifact = (record.get("artifacts") or {}).get("html_report")
        if artifact and artifact.get("server_path"):
            url = f"http://127.0.0.1:8501/companies/{record['company_id']}/workbench/artifact?path={quote(artifact['server_path'], safe='')}"
            guide.append(f"- [打开实际HTML报告]({url})")
        guide += [""]
    guide += ["## 再看不完美的真实案例", "",
              "- P04：论文专题有引文校验失败的情况。对照问答与Trace看partial如何披露，不把有HTML等同于内容全部达标。",
              "- H09：政策原文排除条件与企业是否符合分开；附件缺失时只能给有边界的报告。",
              "- P19：未来时间窗没有已发布资料，应改日期，不应建议换数据源来凑结果。", "",
              "## 你重点验收什么", "", "1. 企业和个人问同一句/report，建议是否真的因Wiki不同而不同。",
              "2. 报告里是否能区分外部事实、用户画像、推断和待确认条件，来源是否能定位。",
              "3. H04/P02/P05追问的是刚才报告，而不是另找一份结果；发布时间和抓取时间是否分开。",
              "4. P06-P10四角色是否有真实上下文和输出；不能只看success标签。",
              "5. H11/H12、P11/P12长期偏好可跨会话；P13/P14临时要求不写长期记忆。",
              "6. P15/P16修改Wiki后，读取和报告是否使用新目标。",
              "7. P17-P20错误命令、权限、未来资料、只解释不执行是否处理清楚。", "",
              "## 文档顺序", "", f"- {link('结果总表与真实耗时', DOCS / 'persona_results_cn.md')}",
              f"- {link('32条测试问题及预期', DOCS / 'persona_query_set_cn.md')}",
              f"- {link('真实问答全文', DOCS / 'persona_answers_cn.md')}",
              f"- {link('逐条Trace与生命周期', DOCS / 'persona_trace_report_cn.md')}",
              f"- {link('修复、决策与剩余边界', DOCS / 'persona_changes_and_limits_cn.md')}",
              f"- {link('前端操作验收', DOCS / 'persona_ui_acceptance_cn.md')}",
              f"- {link('测试规范与覆盖范围', DOCS / 'persona_functional_test_plan_cn.md')}",
              f"- {link('自动化回归记录', DOCS / 'persona_regression_checks_cn.md')}"]
    write("persona_start_here_cn.md", guide)


if __name__ == "__main__":
    main()
