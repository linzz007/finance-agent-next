"""Run formal Workbench acceptance cases against the two real demo workspaces.

The runner uses only HTTP APIs for Workbench operations. It creates clean,
user-visible sessions, sends Chinese user queries through SSE, saves traces and
artifacts, then writes a concise acceptance report for handoff review.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request


ROOT = Path(__file__).resolve().parents[1]

WORKSPACES = {
    "ths": {
        "company_id": "company_001",
        "label": "企业空间：同花顺",
    },
    "personal": {
        "company_id": "agent_self_evolution",
        "label": "个人空间：Agent 自进化",
    },
}

CASES: list[dict[str, Any]] = [
    {
        "id": "H01",
        "persona": "ths",
        "session": "enterprise-main",
        "title": "演示-企业-默认报告",
        "query": "/report 生成一份报告。",
        "expect_skill": "external_impact_report",
        "expect_report": True,
        "expect": "根据同花顺 Wiki 自动确定金融数据、问财/iFinD、智能投研、合规与数据安全相关关注点，生成带来源 HTML 报告；不能套个人学习画像。",
        "semantic_must": ("同花顺",),
        "semantic_any": ("金融", "问财", "iFinD", "投研", "数据安全", "合规"),
        "semantic_forbid": ("两小时学习", "个人学习", "Agent 自进化学习"),
    },
    {
        "id": "H02",
        "persona": "ths",
        "session": "enterprise-main",
        "title": "演示-企业-报告追问",
        "query": "刚才报告里最值得关注的一条是什么？给我对应的原文链接、发布时间、抓取时间、关联的 Wiki 条目，并区分事实和你的推断。",
        "expect_skill": "general_chat",
        "expect_report": False,
        "expect": "追问上一份企业报告，能从历史 artifact / trace 中取回来源、发布时间、抓取时间和 Wiki 关联；不能凭空换一份报告。",
        "semantic_any": ("原文", "链接", "发布时间", "抓取", "Wiki", "事实", "推断", "证据"),
    },
    {
        "id": "H03",
        "persona": "ths",
        "session": "enterprise-main",
        "title": "演示-企业-子智能体核验",
        "query": "请从质疑和核验两个角度判断：刚才报告是否能证明同花顺已经生产采用某个最新 Agent 框架并带来收益？如果证据不足请明确指出。",
        "expect_skill": "general_chat",
        "expect_report": False,
        "expect_roles": ("skeptic", "verifier"),
        "expect": "普通对话中由主 AgentLoop 自动委派 skeptic/verifier，指出报告证据不能推出内部采用和收益事实。",
        "semantic_any": ("证据不足", "不能证明", "未能证明", "无法证明", "不支持"),
    },
    {
        "id": "H04",
        "persona": "ths",
        "session": "enterprise-policy",
        "title": "演示-企业-政策专题",
        "query": "/report 只看最近90天正式发布、与金融数据、投研、金融信息服务有关的数据治理官方政策；不用新闻报道冒充政策原文，最多3条。",
        "expect_skill": "external_impact_report",
        "expect_report": True,
        "expect": "政策专题必须优先使用官方政策源，区分政策原文和新闻线索；若来源不足，需要披露不足。",
        "semantic_any": ("政策", "官方", "原文", "金融", "数据", "投研", "不足"),
    },
    {
        "id": "H05",
        "persona": "ths",
        "session": "enterprise-sources",
        "title": "演示-企业-来源盘点",
        "query": "/report 盘点目前接入的数据源及最近采集状态。论文、GitHub、新闻和政策分别能覆盖什么，哪些无法获取？",
        "expect_skill": "external_impact_report",
        "expect_report": False,
        "expect": "只盘点共享来源池和快照状态，不生成影响报告；说明 GitHub、arXiv、新闻、政策各自边界。",
        "semantic_any": ("GitHub", "arXiv", "新闻", "政策", "数据源", "快照", "无法获取"),
    },
    {
        "id": "P01",
        "persona": "personal",
        "session": "personal-main",
        "title": "演示-个人-默认报告",
        "query": "/report 生成一份报告。",
        "expect_skill": "external_impact_report",
        "expect_report": True,
        "expect": "根据 Agent 自进化 Wiki 自动确定 skill/memory/workflow/eval/trace 关注点，生成可执行学习建议；不能套同花顺金融产品。",
        "semantic_any": ("Agent", "自进化", "Trace", "Eval", "Memory", "Skill", "Workflow", "两小时"),
        "semantic_forbid": ("同花顺", "问财", "iFinD", "金融产品"),
    },
    {
        "id": "P02",
        "persona": "personal",
        "session": "personal-main",
        "title": "演示-个人-报告追问",
        "query": "刚才报告中最适合我今晚两小时学习的一项是什么？给出原文链接、发布日期、实际读取范围，以及一个不重写项目的小实验。",
        "expect_skill": "general_chat",
        "expect_report": False,
        "expect": "追问上一份个人报告，能取回来源和读取范围，并落到两小时内的小实验。",
        "semantic_any": ("两小时", "原文", "链接", "发布日期", "读取范围", "小实验"),
        "semantic_forbid": ("问财", "iFinD"),
    },
    {
        "id": "P03",
        "persona": "personal",
        "session": "personal-main",
        "title": "演示-个人-动态子智能体",
        "query": "请从分析、质疑、核验三个角度判断：我是否应该宣称这个项目已经支持 Docker 沙箱并让准确率提升90%？",
        "expect_skill": "general_chat",
        "expect_report": False,
        "expect_roles": ("analyst", "skeptic", "verifier"),
        "expect": "普通对话由主 AgentLoop 自动委派分析/质疑/核验角色，撤回无证据的 Docker 沙箱和90%收益说法。",
        "semantic_any": ("不能宣称", "证据不足", "不能证明", "未支持", "90%"),
    },
    {
        "id": "P04",
        "persona": "personal",
        "session": "personal-topic",
        "title": "演示-个人-自进化专题",
        "query": "/report 本轮关注 Agent 从任务反馈改进 skill、memory 和 workflow，并通过 Eval/Trace 回归验证的技术发布和研究论文。只看最近30天，最多3条。",
        "expect_skill": "external_impact_report",
        "expect_report": True,
        "expect": "按个人 Wiki 和本轮 query 聚焦 Agent 自进化，不把最新等同热门，不用无关新闻填满。",
        "semantic_any": ("skill", "memory", "workflow", "Eval", "Trace", "Agent"),
        "semantic_forbid": ("问财", "iFinD"),
    },
    {
        "id": "P05",
        "persona": "personal",
        "session": "personal-boundary",
        "title": "演示-个人-边界拒绝",
        "query": "/report 只分析2099年1月1日至2099年1月7日已经发布的 Agent 论文，不允许使用窗口外资料。",
        "expect_skill": "external_impact_report",
        "expect_report": False,
        "expect": "未来时间窗口无可用资料时不生成假报告，不编未来论文。",
        "semantic_any": ("无法", "没有", "未来", "不可得", "窗口外", "不生成", "不足"),
    },
]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class Api:
    def __init__(self, base_url: str, out: Path) -> None:
        self.base = base_url.rstrip("/")
        self.out = out
        self.requests: list[dict[str, Any]] = []

    def call(self, path: str, payload: Any = None, *, raw: bool = False, timeout: int = 480) -> tuple[int, Any]:
        started = time.perf_counter()
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            response = request.urlopen(req, timeout=timeout)
        except error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            status = response.status
        value = body if raw else (json.loads(body.decode("utf-8")) if body else {})
        self.requests.append(
            {
                "method": req.get_method(),
                "path": path,
                "status": status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        )
        write_json(self.out / "api_requests.json", self.requests)
        return status, value

    def ok(self, path: str, payload: Any = None, *, timeout: int = 480) -> Any:
        status, data = self.call(path, payload, timeout=timeout)
        if status >= 400:
            raise RuntimeError(f"HTTP {status} {path}: {data}")
        return data

    def stream(self, path: str, message: str, *, timeout: int = 600) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        started = time.perf_counter()
        req = request.Request(
            self.base + path,
            data=json.dumps({"message": message}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        )
        events: list[dict[str, Any]] = []
        chunks: list[str] = []
        result: dict[str, Any] = {}
        event = "message"
        data_lines: list[str] = []
        with request.urlopen(req, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif not line and data_lines:
                    payload = json.loads("\n".join(data_lines))
                    events.append(
                        {
                            "event": event,
                            "at_ms": round((time.perf_counter() - started) * 1000, 2),
                            "data": payload,
                        }
                    )
                    if event == "chunk":
                        chunks.append(str(payload.get("content") or ""))
                    elif event == "result":
                        result.update(payload)
                    elif event == "error":
                        result["stream_error"] = payload
                    event = "message"
                    data_lines = []
        result["answer"] = "".join(chunks)
        self.requests.append(
            {
                "method": "POST",
                "path": path,
                "status": 200,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        )
        write_json(self.out / "api_requests.json", self.requests)
        return result, events


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.lower() in text.lower() for term in terms)


def _roles_from_trace(trace: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    for step in trace.get("steps") or []:
        if step.get("step_type") != "subagent_delegate":
            continue
        payloads = [step.get("output_payload") or {}, step.get("input_payload") or {}]
        for payload in payloads:
            role = str(payload.get("role") or "")
            if role and role not in roles:
                roles.append(role)
            finding = payload.get("finding") or {}
            role = str(finding.get("role") or "")
            if role and role not in roles:
                roles.append(role)
    return roles


def save_artifacts(api: Api, company_id: str, case_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    saved: dict[str, Any] = {}
    for kind, value in (result.get("artifacts") or {}).items():
        if not value:
            continue
        query = parse.urlencode({"path": str(value)})
        status, content = api.call(f"/companies/{company_id}/workbench/artifact?{query}", raw=True)
        suffix = ".html" if "html" in kind or str(value).endswith(".html") else ".md" if str(value).endswith(".md") else ".json"
        local_path = case_dir / f"artifact_{kind}{suffix}"
        if status == 200:
            local_path.write_bytes(content)
        saved[kind] = {
            "source_path": str(value),
            "http_status": status,
            "saved_path": str(local_path) if status == 200 else "",
            "artifact_url": f"{api.base}/companies/{company_id}/workbench/artifact?{query}",
            "preview": content[:1200].decode("utf-8", errors="ignore") if status == 200 else "",
        }
    return saved


def check_case(
    case: dict[str, Any],
    result: dict[str, Any],
    trace: dict[str, Any],
    events: list[dict[str, Any]],
    artifacts: dict[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: Any = "") -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    answer = str(result.get("answer") or "")
    combined_text = "\n".join(
        [answer, *[str(item.get("preview") or "") for item in artifacts.values()]]
    )
    step_types = [str(step.get("step_type") or "") for step in trace.get("steps") or []]
    roles = _roles_from_trace(trace)

    add("answer_not_empty", bool(answer.strip()), len(answer))
    add("expected_skill", result.get("selected_skill_id") == case["expect_skill"], result.get("selected_skill_id"))
    add("trace_has_steps", len(step_types) >= 3, len(step_types))
    add("sse_has_result", any(item["event"] == "result" for item in events), [item["event"] for item in events[-5:]])
    add("sse_has_chunk", any(item["event"] == "chunk" for item in events), [item["event"] for item in events[:8]])

    if case.get("expect_report"):
        add("html_report_artifact", any("html" in key and item["http_status"] == 200 for key, item in artifacts.items()), artifacts)
        add("workflow_stage_recorded", "workflow_stage" in step_types, step_types)
        add("artifact_write_recorded", "artifact_write" in step_types, step_types)
    else:
        add("no_unexpected_report_artifact", not any("report" in key for key in artifacts), artifacts)

    expected_roles = tuple(case.get("expect_roles") or ())
    if expected_roles:
        for role in expected_roles:
            add(f"subagent_role_{role}", role in roles, roles)
        add("subagent_plan_recorded", "subagent_plan" in step_types, step_types)
        add("subagent_context_recorded", "subagent_context" in step_types, step_types)
        add("subagent_delegate_recorded", "subagent_delegate" in step_types, step_types)
    else:
        add("no_required_subagent_roles", True, roles)

    must = tuple(case.get("semantic_must") or ())
    if must:
        for term in must:
            add(f"semantic_must_{term}", term.lower() in combined_text.lower())
    any_terms = tuple(case.get("semantic_any") or ())
    if any_terms:
        add("semantic_any_terms", _contains_any(combined_text, any_terms), any_terms)
    forbid = tuple(case.get("semantic_forbid") or ())
    if forbid:
        add("semantic_forbid_terms", not _contains_any(combined_text, forbid), forbid)

    add("model_config_secret_redacted", not re.search(r"sk-[A-Za-z0-9]{12,}", json.dumps(result, ensure_ascii=False)))
    return checks


def run_case(api: Api, out: Path, sessions: dict[str, str], case: dict[str, Any]) -> dict[str, Any]:
    workspace = WORKSPACES[case["persona"]]
    company_id = workspace["company_id"]
    base = f"/companies/{company_id}/workbench"
    key = f"{case['persona']}:{case['session']}"
    case_dir = out / "cases" / case["id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    write_json(case_dir / "case.json", case)

    if key not in sessions:
        session = api.ok(
            base + "/sessions",
            {"title": case["title"], "mode": "auto", "model_id": "deepseek-v4-flash", "active_skill_id": ""},
        )
        sessions[key] = session["session_id"]
        write_json(out / "sessions.json", sessions)

    session_id = sessions[key]
    wiki = api.ok(base + "/wiki")
    write_json(case_dir / "wiki_snapshot.json", wiki)

    started = time.perf_counter()
    result, events = api.stream(base + f"/sessions/{session_id}/messages/stream", case["query"])
    elapsed = round(time.perf_counter() - started, 3)
    write_json(case_dir / "sse.json", events)
    write_json(case_dir / "response.json", result)
    (case_dir / "answer.md").write_text(str(result.get("answer") or ""), encoding="utf-8")

    trace = api.ok(base + f"/sessions/{session_id}/runs/{result['run_id']}/trace") if result.get("run_id") else {}
    write_json(case_dir / "trace.json", trace)
    messages = api.ok(base + f"/sessions/{session_id}/messages")
    write_json(case_dir / "messages.json", messages)
    artifacts = save_artifacts(api, company_id, case_dir, result)

    checks = check_case(case, result, trace, events, artifacts)
    step_types = [str(step.get("step_type") or "") for step in trace.get("steps") or []]
    model_steps = [step for step in trace.get("steps") or [] if step.get("step_type") == "model_call"]
    usage = [step.get("metadata", {}).get("usage", {}) for step in model_steps]
    record = {
        "id": case["id"],
        "persona": case["persona"],
        "workspace": workspace["label"],
        "company_id": company_id,
        "session_id": session_id,
        "run_id": result.get("run_id"),
        "query": case["query"],
        "expected": case["expect"],
        "elapsed_seconds": elapsed,
        "selected_skill_id": result.get("selected_skill_id"),
        "roles": _roles_from_trace(trace),
        "trace_step_count": len(step_types),
        "trace_step_types": step_types,
        "artifacts": artifacts,
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
        "answer_preview": str(result.get("answer") or "")[:800],
        "sse_first_chunk_ms": next((item["at_ms"] for item in events if item["event"] == "chunk"), None),
        "input_tokens": sum(int(item.get("input_tokens", item.get("prompt_tokens", 0)) or 0) for item in usage),
        "output_tokens": sum(int(item.get("output_tokens", item.get("completion_tokens", 0)) or 0) for item in usage),
        "evidence_dir": str(case_dir),
    }
    write_json(case_dir / "checks.json", record)
    return record


def write_markdown_report(out: Path, results: list[dict[str, Any]], base_url: str) -> Path:
    passed = sum(1 for item in results if item.get("passed"))
    times = [float(item["elapsed_seconds"]) for item in results if item.get("elapsed_seconds") is not None]
    lines = [
        "# 正式 Workbench 验收记录",
        "",
        f"- 验收时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- API：{base_url}",
        f"- 覆盖空间：同花顺企业空间、Agent 自进化个人空间",
        f"- 用例数：{len(results)}，通过：{passed}/{len(results)}",
        f"- 中位耗时：{statistics.median(times):.2f}s" if times else "- 中位耗时：无",
        "",
        "## 总体判断",
        "",
    ]
    if passed == len(results):
        lines.append("本轮结构验收通过：企业/个人报告、报告追问、动态 Subagent、来源盘点和边界拒绝均有可查 trace 与产物证据。")
    else:
        failed = ", ".join(item["id"] for item in results if not item.get("passed"))
        lines.append(f"本轮仍有未通过项：{failed}。需要优先查看对应 case 目录下的 `answer.md` 与 `trace.json`。")
    lines.extend(["", "## 企业报告应有形态", ""])
    lines.append(
        "企业报告应围绕同花顺 Wiki 中的金融数据、问财/iFinD、智能投研、数据安全和合规边界展开；"
        "外部来源只是输入，最终要输出“对业务/产品/合规的条件性影响、证据链、待确认问题和行动建议”，"
        "不能编造内部采用、收益提升或生产指标。"
    )
    lines.extend(["", "## 个人报告应有形态", ""])
    lines.append(
        "个人报告应围绕 Agent 自进化学习目标展开，关注 skill、memory、workflow、Eval、Trace、Subagent Harness 等资料；"
        "最终建议要落到两小时内可执行的小实验或阅读路径，不能套用同花顺金融产品语境。"
    )
    lines.extend(["", "## 用例明细", ""])
    lines.append("| 用例 | 空间 | Skill | Subagent | 报告 | 通过 | 证据目录 |")
    lines.append("|---|---|---|---|---|---|---|")
    for item in results:
        artifact_links = []
        for kind, artifact in (item.get("artifacts") or {}).items():
            if artifact.get("artifact_url"):
                artifact_links.append(f"[{kind}]({artifact['artifact_url']})")
        lines.append(
            "| {id} | {workspace} | `{skill}` | {roles} | {artifacts} | {passed} | `{evidence}` |".format(
                id=item["id"],
                workspace=item["workspace"],
                skill=item.get("selected_skill_id") or "",
                roles=", ".join(item.get("roles") or []) or "-",
                artifacts="<br>".join(artifact_links) or "-",
                passed="OK" if item.get("passed") else "FAIL",
                evidence=item["evidence_dir"],
            )
        )
    lines.extend(["", "## 失败检查项", ""])
    for item in results:
        failed = [check for check in item.get("checks") or [] if not check.get("passed")]
        if not failed:
            continue
        lines.append(f"### {item['id']}")
        lines.append("")
        for check in failed:
            lines.append(f"- {check['check']}：{check.get('detail', '')}")
        lines.append("")

    report_path = ROOT / "docs" / "acceptance" / "formal_workbench_delivery_acceptance_cn.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "formal_workbench_delivery_acceptance_cn.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return report_path


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8503")
    parser.add_argument("--label", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--cases", default="")
    args = parser.parse_args()

    out = ROOT / "data" / "acceptance" / f"formal_{args.label}"
    out.mkdir(parents=True, exist_ok=True)
    api = Api(args.base_url, out)

    model = api.ok("/companies/company_001/workbench/models")["models"][0]
    if not model.get("metadata", {}).get("available"):
        raise RuntimeError(f"model is not available: {model.get('metadata')}")
    write_json(out / "model_status.json", model)

    for workspace in WORKSPACES.values():
        wiki = api.ok(f"/companies/{workspace['company_id']}/workbench/wiki")
        write_json(out / f"wiki_{workspace['company_id']}.json", wiki)

    wanted = {case_id.strip() for case_id in args.cases.split(",") if case_id.strip()}
    cases = [case for case in CASES if not wanted or case["id"] in wanted]
    sessions: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    for case in cases:
        print(f"RUN {case['id']} {case['query']}", flush=True)
        try:
            record = run_case(api, out, sessions, case)
        except Exception as exc:  # noqa: BLE001
            record = {
                "id": case["id"],
                "persona": case["persona"],
                "query": case["query"],
                "passed": False,
                "error": repr(exc),
            }
        results.append(record)
        write_json(out / "results.json", results)
        print(
            json.dumps(
                {
                    "id": record.get("id"),
                    "passed": record.get("passed"),
                    "skill": record.get("selected_skill_id"),
                    "roles": record.get("roles"),
                    "elapsed_seconds": record.get("elapsed_seconds"),
                    "run_id": record.get("run_id"),
                    "error": record.get("error"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    summary = {
        "case_count": len(results),
        "passed": sum(1 for item in results if item.get("passed")),
        "base_url": args.base_url,
        "out_dir": str(out),
        "workspaces": WORKSPACES,
    }
    write_json(out / "summary.json", summary)
    report_path = write_markdown_report(out, results, args.base_url)
    print(json.dumps({**summary, "report_path": str(report_path)}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
