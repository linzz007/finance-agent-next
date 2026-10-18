"""Render the code-verified three-workspace execution map as a standalone SVG."""

from html import escape
from pathlib import Path
import re
import unicodedata


OUT = Path(__file__).with_name("workbench_three_workspace_execution_cn.svg")
W, H = 2880, 3000
parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">
<title id="title">Agent Workbench 三个工作空间的真实执行全景</title>
<desc id="desc">同花顺、Agent 自进化和金融观察共用 Runtime、Skill 路由、上下文与存储机制。同花顺和 Agent 自进化共用资料研判流程，金融观察使用行情与归因核查流程。包括普通对话、前置委派、循环内委派、固定报告工作流、只读追问、权限、Trace 和产物。</desc>
<defs>
 <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/></marker>
 <marker id="return" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#087d70"/></marker>
 <style>
  text {{ font-family: 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif; fill:#18252c; letter-spacing:0; }}
  .title {{ font-size:44px; font-weight:700; }}
  .section {{ font-size:29px; font-weight:700; }}
  .node-title {{ font-size:25px; font-weight:700; }}
  .body {{ font-size:23px; }}
  .small {{ font-size:20px; fill:#43545d; }}
  .code {{ font-family: Consolas,'Microsoft YaHei',monospace; font-size:19px; fill:#425762; }}
  .port-label {{ font-size:21px; font-weight:700; }}
  .flow {{ stroke:#64748b; stroke-width:2.6; fill:none; marker-end:url(#arrow); }}
  .return {{ stroke:#087d70; stroke-width:2.6; fill:none; stroke-dasharray:8 6; marker-end:url(#return); }}
 </style>
</defs>
<rect width="2880" height="3000" fill="#ffffff"/>
''']
PAL = {
    "shared": ("#eff7f5", "#a7cec5", "#087d70"),
    "info": ("#f0f5fc", "#b4c8e1", "#356899"),
    "finance": ("#fff7e9", "#e2c994", "#9a6518"),
    "sub": ("#f6f2fa", "#cbbbdb", "#79539b"),
    "neutral": ("#f6f8fa", "#cbd4d9", "#526975"),
    "warning": ("#fff2ed", "#e2b3a1", "#a45135"),
}


def text(x, y, value, cls="body", **attrs):
    sizes = {"title": 44, "section": 29, "node-title": 25, "body": 23, "small": 20, "code": 19, "port-label": 21}
    attrs.setdefault("font-size", sizes[cls])
    attrs.setdefault("font-family", "Microsoft YaHei, Noto Sans CJK SC, sans-serif")
    attrs.setdefault("font-weight", 700 if cls in {"title", "section", "node-title", "port-label"} else 400)
    attrs.setdefault("fill", "white" if attrs.get("style") == "fill:white" else "#43545d" if cls in {"small", "code"} else "#18252c")
    extra = " ".join(f'{k.replace("_", "-")}="{escape(str(v), quote=True)}"' for k, v in attrs.items())
    parts.append(f'<text x="{x}" y="{y}" class="{cls}" {extra}>{escape(value)}</text>')


def wrap(value, width, font_size):
    line, units = "", 0.0
    capacity = width / font_size
    result = []
    for c in value:
        size = 1 if unicodedata.east_asian_width(c) in "WF" else 0.56
        if line and units + size > capacity:
            result.append(line)
            line, units = "", 0.0
        line += c
        units += size
    return [*result, line] if line else result


def block(key, x, y, width, height, title, lines, tone="shared", code=None, size=23):
    fill, border, accent = PAL[tone]
    parts.append(f'<g id="{key}" data-node="true"><rect x="{x}" y="{y}" width="{width}" height="{height}" rx="8" fill="{fill}" stroke="{border}" stroke-width="1.5"/>')
    parts.append(f'<rect x="{x}" y="{y + 16}" width="5" height="32" rx="2" fill="{accent}"/>')
    text(x + 23, y + 39, title, "node-title")
    cy = y + 75
    for line in lines:
        for chunk in wrap(line, width - 46, size):
            text(x + 23, cy, chunk, "body" if size == 23 else "small")
            cy += 31 if size == 23 else 27
    if code:
        cy += 3
        for line in code:
            for chunk in wrap(line, width - 46, 19):
                text(x + 23, cy, chunk, "code")
                cy += 26
    if cy > y + height + 9:
        raise ValueError(f"Node {key}: text too tall {cy} > {y + height}")
    parts.append('</g>')


def path(d, returning=False):
    color = "#087d70" if returning else "#64748b"
    dash = ' stroke-dasharray="8 6"' if returning else ""
    parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.6"{dash}/>')
    # Explicit arrowheads also render in SVG readers without marker support.
    x, y, previous = 0.0, 0.0, (0.0, 0.0)
    for command, numbers in re.findall(r"([MHV])([0-9 .-]+)", d):
        values = [float(v) for v in numbers.split()]
        previous = x, y
        if command == "M":
            x, y = values
        elif command == "H":
            x = values[0]
        else:
            y = values[0]
    dx, dy = x - previous[0], y - previous[1]
    length = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / length, dy / length
    points = [(x, y), (x - 12 * ux + 5 * uy, y - 12 * uy - 5 * ux),
              (x - 12 * ux - 5 * uy, y - 12 * uy + 5 * ux)]
    parts.append(f'<polygon points="{" ".join(f"{px:g},{py:g}" for px, py in points)}" fill="{color}"/>')


def section(x, y, label, subtitle=""):
    text(x, y, label, "section")
    if subtitle:
        text(x, y + 33, subtitle, "small")


def port(x, y, label, tone="shared"):
    _, _, accent = PAL[tone]
    parts.append(f'<circle cx="{x}" cy="{y}" r="19" fill="{accent}"/>')
    text(x, y + 7, label, "port-label", text_anchor="middle", style="fill:white")


text(60, 66, "Agent Workbench · 三个工作空间执行全景", "title")
text(60, 108, "按当前代码核对 · 2026-09-24  |  同色表示同一类组件，箭头表示执行顺序，虚线表示回填或辅助数据流", "small")
text(2060, 108, "A / B / C 为跨区连接端口，不是新增步骤", "small")

block("ths", 60, 150, 890, 180, "同花顺 · 企业研判", [
    "Wiki：企业、产品边界、业务事实、关注主题与限制。",
    "问题：政策、行业事件、技术变化与企业有什么关系？",
], "info", ["company_001 → 共享资料研判管线 A"])
block("agent", 990, 150, 900, 180, "Agent 自进化 · 个人技术调研", [
    "Wiki：个人身份、技术兴趣、研究方向与筛选偏好。",
    "问题：哪些论文、开源框架与技术值得研究？",
], "info", ["agent_self_evolution → 同一条共享资料管线 A"])
block("finance", 1930, 150, 890, 180, "个人金融观察 · 宏观与跨资产", [
    "Wiki + 金融档案：关注资产、区间、口径与偏好。",
    "问题：美股、黄金和白银怎样变化？原因有哪些证据？",
], "finance", ["finance_observer → 启用金融能力 B"])

# A workspace supplies scope, not a separate runtime implementation.
path("M505 330 V356 H1440")
path("M2375 330 V356 H1440")
path("M1440 330 V378 H32 V558 H60")
text(1570, 386, "切换 company_id：换资料、历史与能力条件，不复制一套 Runtime", "small")
section(60, 423, "01  共用入口：一轮请求如何被接住")
block("entry", 60, 455, 470, 208, "页面 / API / Service", [
    "选择空间、会话、模型；发送 query。",
    "SSE 与普通 POST 共用执行内核。",
], code=["api.py → ChatWorkbenchService", "company_id / session_id / model_id"])
block("runtime", 595, 455, 490, 208, "AgentRuntime → TurnCoordinator", [
    "校验请求、BeforeTurn Hook。",
    "prepare / execute 分离；防重复执行。",
], code=["run_turn / prepare_turn", "execute_prepared_turn → _execute_locked"])
block("skill", 1150, 455, 480, 208, "Skill 路由 + 创建 AgentRun", [
    "解析 /report；默认 general_chat。",
    "金融空间可识别自然语言报告请求。",
], code=["SkillRegistry → SkillManifest", "executor / tools / roles / budgets"])
block("context", 1695, 455, 525, 208, "按本轮任务构建 Context", [
    "历史与摘要、相关 Memory、Wiki 事实。",
    "报告证据按需回读；记录可见范围/预算。",
], code=["_build_context → compile_chat_context", "ContextManifest（并非每轮全量注入）"])
block("executor", 2285, 455, 535, 208, "ExecutorRegistry.execute", [
    "传入 SkillExecutionContext。",
    "同一入口分为普通对话与报告执行器。",
], code=["general_chat / external_impact_report", "最终统一返回 SkillResult"])
for a, b in [(530, 595), (1085, 1150), (1630, 1695), (2220, 2285)]:
    path(f"M{a} 558 H{b}")
path("M2552 663 V701 H810 V778")
path("M2552 701 V778")
text(810, 736, "默认普通对话", "small", text_anchor="middle")
text(2340, 736, "/report 或金融报告意图", "small", text_anchor="middle")

section(60, 800, "02  普通聊天：由 Agent 决策、观察、继续", "三个空间共用；金融档案存在时，额外暴露金融分析与只读工具")
section(1690, 800, "03  报告 Skill：把固定流程嵌入 Loop", "不是与对话分离的另一个应用，也不是随意自由规划所有阶段")

block("predelegate", 60, 862, 1530, 129, "当前代码还保留：显式多角度请求的前置委派", [
    "_should_predelegate_subagents 命中 → 规划任务 → 调用同一 SubagentRunner → 将结果注入主循环。",
    "这条路径在主 Loop 之前；另一条动态 delegate 路径在 Loop 内。两条都画出，不把意图当作实现。",
], "warning", size=20)
path("M385 991 V1030")

block("mainloop", 60, 1030, 665, 240, "主 AgentLoop / ModelGateway", [
    "本轮问题 + 可见证据 + 角色目录。",
    "模型输出 final / delegate / tool_call。",
    "每次收到 observation 后再次决定。",
], code=["_run_general_chat_agent_loop", "受总步数、模型调用数与超时预算约束"])
block("subagent", 865, 1030, 725, 240, "SubagentRunner：隔离子任务", [
    "collector / analyst / skeptic / verifier。",
    "TaskBrief → 角色与调用方工具权限取交集。",
    "只读委派 artifact → 子 Loop → 输出 Schema。",
    "结果/失败原因/parent span 回填主 Agent。",
], "sub", ["manifests.json + roles.json + 角色 Prompt"])
path("M725 1094 H865")
text(795, 1076, "delegate", "code", text_anchor="middle")
path("M1180 1270 V1300 H460 V1270", True)
text(715, 1328, "SubagentResult → observation → 下一次决策", "small")
path("M385 1270 V1362")
text(210, 1330, "tool_call", "code")
block("finance-chat-tools", 60, 1362, 1050, 178, "金融空间的循环工具", [
    "finance.analyze → B：新分析，默认不发布 HTML。",
    "finance.report_read：只读本会话授权快照；刷新须明确重新分析。",
], "finance", ["普通概念问题可直接 final；非金融聊天不默认联网研究"])
path("M60 1438 H34 V1150 H60", True)
port(1170, 1415, "B", "finance")
path("M1110 1415 H1150")
port(1450, 1438, "C")
text(1250, 1400, "final + 本路径校验", "small")
text(1240, 1493, "进入统一收尾 C", "small")
path("M725 1210 H825 V1345 H1450 V1419")
text(1480, 1360, "循环结束", "small")

block("reportloop", 1690, 862, 1130, 157, "/report 对应 external_impact_report", [
    "_run_external_impact_report 创建受限的外层 AgentLoop。",
    "第一步由 Runtime 固定注入 report_workflow.run，并非模型自由选择。",
], "info", ["_ForcedFirstActionModelAdapter · tool_call → observation → final"])
path("M2255 1019 V1053")
block("workflow-gateway", 1690, 1053, 1130, 143, "报告工具网关 → _run_external_impact_workflow", [
    "A/B 均用 ReportStageGraph 管节点顺序；Gate 的 allow / ask / deny 决定转移。",
    "外层 Loop 不能用通用回答覆盖工作流的失败或边界结论。",
], "info")
path("M2255 1196 V1232")
block("domain-router", 1690, 1232, 1130, 181, "按当前空间能力选择业务管线", [
    "存在 load_profile(company_id) 金融档案 → 金融管线 B。",
    "否则 → 共享资料管线 A；数据源盘点请求走 source_inventory。",
    "旧政策/新闻/研究实现仍在仓库，不是三套独立公开主入口。",
], "info")
path("M1940 1413 V1472 H1920")
path("M2570 1413 V1472 H2560")
port(1900, 1472, "A", "info")
port(2540, 1472, "B", "finance")
text(1940, 1480, "共享资料 Workflow", "small")
text(2580, 1480, "金融 Workflow", "small")

port(80, 1585, "A", "info")
section(118, 1593, "同花顺 + Agent 自进化：同一条资料研判管线")
text(60, 1631, "run_recent_news_report · 差别由 Query + Wiki 决定；不把企业资料默认变成 Agent 新闻", "small")
port(1500, 1585, "B", "finance")
section(1538, 1593, "金融观察：同一份分析支持聊天与报告")
text(1480, 1631, "runtime_adapter.execute → run_finance_report；沿用旧分析时可跳过采集与重新分析", "small")

block("info-wiki", 60, 1660, 630, 163, "A1  Wiki → 本轮研判请求", [
    "身份/事实 + Query → ReportRequest。",
    "时间、主题、来源要求、数量与排除项。",
], "info", ["wiki_context / discovery_plan"])
block("info-source", 770, 1660, 630, 163, "A2  共享来源池 → 原始快照", [
    "政策官网 / GitHub / 论文 / RSS / 新闻。",
    "按计划探查；保留来源状态与失败信息。",
], "info", ["source_collection · config/sources.json"])
block("info-filter", 60, 1880, 630, 169, "A3  清洗、结构化、筛选", [
    "合并后二次去重 → 事件结构。",
    "按时间/正文质量/原文要求/主题过滤。",
    "保留排除原因；无证据不为凑数补齐。",
], "info")
block("info-analysis", 770, 1880, 630, 169, "A4  Wiki 事实匹配 + 影响分析", [
    "规则初筛和排序 → 模型中文摘要与研判。",
    "输出条件、影响、建议、限制及引用。",
    "校验 Schema / fact / source 关联。",
], "info")
block("info-gate", 60, 2106, 630, 167, "A5  质量与覆盖门控", [
    "filtering 与 impact_analysis 两处 Gate。",
    "证据缺失走 ask → 受限报告，状态 partial。",
    "未来窗口仅说明未发布，不编造结论。",
], "info")
block("info-output", 770, 2106, 630, 167, "A6  HTML / Markdown / RunArtifact", [
    "结论、信息摘要、影响建议、来源附录。",
    "保存本次 Wiki、来源快照与阶段结果。",
    "返回上层报告 Loop，支持下一轮追问。",
], "info")

block("fin-collect", 1480, 1660, 630, 163, "B1  固定请求 → finance.collect", [
    "资产 / 日期 / session / quality policy。",
    "行情供应商 + 宏观或机构原文 → 快照。",
], "finance", ["profile / acquisition · finance_sources.json"])
block("fin-metrics", 2190, 1660, 630, 163, "B2  数据校验 → 指标 → 覆盖门控", [
    "Schema、品种、时区、完整日线、基准。",
    "代码计算行情；严格证据不足在 coverage 阻断。",
], "finance", ["validation / metrics / coverage gate"])
block("fin-audit", 1480, 1880, 630, 169, "B3  finance.research_audit", [
    "文献相对行情时间、证据用途和相关性。",
    "逐资产反向观察、资料缺口、口径限制。",
    "区分事实、候选解释、因果未识别。",
], "finance")
block("fin-analysis", 2190, 1880, 630, 169, "B4  模型分析 → 业务验证", [
    "逐资产假设、依据、缺口与检验计划。",
    "Schema / 引文 / 机制与检验一致性校验。",
    "语义复核 + 有限修复；失败不得伪成功。",
], "finance")
block("fin-saved", 1480, 2106, 630, 167, "B5  保存已校验分析与证据", [
    "report_id / snapshot_id / analysis。",
    "数据、分析、归因质量状态分开记录。",
    "追问由 report_read 按需展开同一快照。",
], "finance")
block("fin-output", 2190, 2106, 630, 167, "B6  按入口交付", [
    "普通分析：确定性投影聊天答案，无 HTML。",
    "报告请求：finance.publish → HTML/MD/JSON。",
    "沿用报告：授权 + 发布校验，不重新采集。",
], "finance")
for x in (0, 1420):
    path(f"M{690 + x} 1741 H{770 + x}")
    path(f"M{1085 + x} 1823 V1852 H{375 + x} V1880")
    path(f"M{690 + x} 1964 H{770 + x}")
    path(f"M{1085 + x} 2049 V2077 H{375 + x} V2106")
    path(f"M{690 + x} 2189 H{770 + x}")
text(60, 2320, "A 产物 → 报告 Loop 的 observation → final；后续聊天通过本会话历史与 report_context 回读报告证据。", "small")
text(1480, 2320, "B 分析 → 主 Loop 的 observation；首次答案用已验证字段生成，追问另做数字与语义复核。", "small")

port(80, 2380, "C")
section(118, 2388, "04  统一收尾：成功、失败、产物与对话都回到同一个 Run")
block("result", 60, 2420, 510, 160, "执行器返回 SkillResult", [
    "answer / artifacts / citations。",
    "Memory 写入先提出计划，不立即提交。",
], code=["暂存 assistant message"])
block("stop", 625, 2420, 510, 160, "Stop Hook 与终止判断", [
    "执行失败、取消、超时或 ask / deny。",
    "未通过时不发布正常成功回答。",
], code=["TurnCoordinator._execute_locked"])
block("commit", 1190, 2420, 510, 160, "原子提交终态与记忆计划", [
    "AgentRun / AgentStep / 消息 / 引用。",
    "仅成功路径提交批准的 Memory 写入。",
], code=["commit_agent_run_terminal"])
block("sse", 1755, 2420, 505, 160, "SSE → 前端运行记录", [
    "执行中推送进度；完成后分块发答案。",
    "不是模型 token 原样实时透传。",
], code=["progress / result / chunk / trace / done"])
block("next", 2315, 2420, 505, 160, "用户看报告，继续追问", [
    "同空间 + 同会话 → 新一轮 Run。",
    "追问复用；新分析 / 刷新进入采集。",
], code=["回到 01；不重新创建一个应用"])
for a, b in [(570, 625), (1135, 1190), (1700, 1755), (2260, 2315)]:
    path(f"M{a} 2500 H{b}")

section(60, 2660, "05  贯穿上述链路的共享支撑（不是执行完报告后才开始）")
block("configuration", 60, 2700, 890, 217, "执行语义与配置分离", [
    "plugins：Skill / 工具注册、角色、Prompt 与策略。",
    "Runtime：循环、预算、权限判定和生命周期。",
    "ToolGateway / PermissionEngine：deny → ask → allow。",
    "数据源配置不随 Wiki 复制，按本轮计划筛选。",
], "neutral", size=20, code=["plugins/ · config/sources.json · finance_sources.json"])
block("storage", 990, 2700, 900, 217, "空间资料与后台采集", [
    "每个 company_id 分开：Wiki、会话、Memory、报告与运行证据。",
    "Wiki 页面经 API 修改并索引；金融档案有版本与专用校验。",
    "金融 Worker 默认每 60 分钟采集，进程停止就不再采集。",
    "只存金融快照/任务状态，不自动调用模型或发送报告。",
], "neutral", size=20, code=["data/companies/{company_id}/ · scheduler.py"])
block("observability", 1930, 2700, 890, 217, "Trace 与 Artifact 分工", [
    "Trace：谁调用谁、上下文清单、工具、Gate、模型及错误。",
    "Artifact：来源快照、结构化分析、可阅读的 HTML 报告。",
    "靠 run_id / task_id / span / artifact 引用关联复盘。",
    "金融复核通过 ≠ 价格独立认证 ≠ 因果已经证实。",
], "neutral", size=20, code=["不展示模型隐式思维；记录的是执行证据与输出"])
text(60, 2968, "阅读顺序：先看 01 的共同入口，再选普通对话或 /report 分支，沿 A / B 进入具体业务，最后在 C 汇合。", "small")
text(1840, 2968, "范围：当前 Workbench 用户路径；不展开旧兼容接口与全部测试函数。", "small")
parts.append("</svg>\n")
OUT.write_text("\n".join(parts), encoding="utf-8")
print(OUT)
