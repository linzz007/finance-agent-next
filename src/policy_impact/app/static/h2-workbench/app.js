"use strict";
const base = "/api/h2-workbench";
const el = (id) => document.getElementById(id);
const state = {session: "", run: "", cursor: "0", events: new Map(), callSignature: "", busy: false, polling: false, config: null, revision: "", archive: "", recordMode: "sessions", archiveLoaded: false, records: {sessions: [], archives: []}, selectedTab: "tasks", lastEventTime: 0};
const terminal = new Set(["succeeded", "partial", "failed", "cancelled", "timed_out"]);
const labels = {pending_dispatch: "等待派发", queued: "已入队", running: "执行中", waiting: "等待子任务", succeeded: "完成", partial: "部分完成", failed: "失败", cancelled: "已停止", timed_out: "超时", suspended: "已保存", expired: "租约过期", leased: "已领取", blocked: "受阻", backpressured: "队列背压", unknown: "结果未知"};
const roleNames = {main: "主 Agent", analyst: "分析 Agent", verifier: "核验 Agent", skeptic: "质疑 Agent", general: "通用 Agent"};
function icons() { if (window.lucide) window.lucide.createIcons(); }
function node(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
function symbol(name) { const n = node("i"); n.setAttribute("data-lucide", name); n.setAttribute("aria-hidden", "true"); return n; }
function error(message = "") { el("error-text").textContent = message; el("error").hidden = !message; }
function toast(message) { el("toast").textContent = message; el("toast").hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => { el("toast").hidden = true; }, 3000); }
function iconButton(name, title, action) {
  const b = node("button", "icon-button"); b.type = "button"; b.title = title; b.setAttribute("aria-label", title); b.append(symbol(name)); b.onclick = action; return b;
}
function dateLabel(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false});
}
function emptyPanel(target, text, icon = "folder-open") { const box = node("div", "empty-panel"); box.append(symbol(icon), node("span", "", text)); target.replaceChildren(box); }
function emptyResearch() {
  const box = node("div", "empty"); const mark = node("div", "empty-symbol"); mark.append(symbol("book-open"));
  box.append(mark, node("span", "eyebrow", "A NEW PERSPECTIVE"), node("h2", "", "让每一次研究，有据可循。"), node("p", "", "从一篇文章、一个观点，或一个值得追问的问题开始。"));
  const topics = node("div", "empty-topics");
  for (const [label, query] of [["作者观点", "请总结培风客最近三个月文章的核心观点，保留判断条件和不确定性。"], ["对比研究", "请比较培风客与 TT 对黄金和利率的判断，结合各自原文分析。"], ["论证核验", "请分析一篇最近的文章，核对主要论点、推理条件与原文依据。"]]) {
    const b = node("button", "", label); b.onclick = () => { el("query").value = query; el("query").focus(); }; topics.append(b);
  }
  box.append(topics); el("messages").replaceChildren(box); icons();
}
async function api(path, options = {}) {
  const r = await fetch(base + path, {headers: {"Content-Type": "application/json", ...(options.headers || {})}, ...options});
  const payload = await r.json();
  if (!r.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail));
  return payload;
}
function markdown(target, text) {
  if (window.marked && window.DOMPurify) target.innerHTML = window.DOMPurify.sanitize(window.marked.parse(text));
  else { target.textContent = text; target.style.whiteSpace = "pre-wrap"; }
  for (const link of target.querySelectorAll("a")) { link.target = "_blank"; link.rel = "noopener noreferrer"; }
  for (const table of target.querySelectorAll("table")) { const wrap = node("div", "table-scroll"); wrap.tabIndex = 0; wrap.setAttribute("role", "region"); wrap.setAttribute("aria-label", "数据表格"); table.replaceWith(wrap); wrap.append(table); }
  for (const code of target.querySelectorAll("code")) {
    const ref = code.textContent.trim();
    if (/^art_[a-z0-9]+$/i.test(ref)) {
      const b = node("button", "artifact-inline", `查看资料 ${ref.slice(0, 10)}…`); b.title = ref; b.onclick = () => openArtifact(ref).catch(e => error(e.message)); code.replaceWith(b);
    }
  }
}
async function sessions() {
  const data = await api("/sessions");
  state.records.sessions = data.sessions; renderRecordList();
  const selected = data.sessions.find(s => s.session_id === state.session);
  if (selected && !state.archive) el("session-title").textContent = selected.title;
}
function filteredRecords(items, query) { const q = query.trim().toLocaleLowerCase(); return items.filter(s => !q || `${s.title || ""} ${s.case || ""} ${s.query || ""} ${s.batch || ""}`.toLocaleLowerCase().includes(q)); }
function renderRecordList() {
  const mode = state.recordMode, target = el(mode); target.replaceChildren();
  const items = filteredRecords(state.records[mode], el("record-search").value || ""); let batch = "";
  for (const item of items) {
    if (mode === "archives" && item.batch !== batch) { target.append(node("div", "archive-batch", item.batch)); batch = item.batch; }
    const selected = mode === "archives" ? item.archive_id === state.archive : item.session_id === state.session;
    const b = node("button", selected ? "active" : ""); b.setAttribute("aria-current", selected ? "true" : "false");
    const title = node("b"); title.append(node("span", "record-title", mode === "archives" ? item.case : item.title));
    if (mode === "archives") title.append(node("span", "record-state", labels[item.status] || item.status));
    b.append(title, node("span", "record-snippet", mode === "archives" ? item.query : (dateLabel(item.created_at) || "新的研究")));
    b.title = mode === "archives" ? `${item.batch} / ${item.case}` : item.title;
    b.onclick = () => (mode === "archives" ? selectArchive(item.archive_id) : selectSession(item.session_id)).catch(e => error(e.message)); target.append(b);
  }
  el("record-count").textContent = String(items.length); el("record-list-title").textContent = mode === "archives" ? "已保存的测试" : "最近研究";
  el("record-empty").hidden = Boolean(items.length); el("record-empty").textContent = state.records[mode].length ? "没有匹配的记录" : (mode === "archives" ? "暂无已保存的测试记录" : "暂无研究记录");
}
function setRun(id) {
  if (id === state.run) return;
  state.run = id; state.cursor = "0"; state.events.clear();
  el("events").replaceChildren();
  const calls = node("div", "trace-calls"); calls.id = "trace-calls";
  el("events").append(calls);
  el("event-count").textContent = "0"; state.callSignature = "";
  state.lastEventTime = 0; el("source-count").textContent = "0";
  emptyPanel(el("sources"), "暂无来源读取记录", "files");
}
function resetView() {
  state.revision = ""; setRun(""); state.busy = false;
  el("stats").replaceChildren();
  for (const label of ["模型请求", "Agent 任务", "执行步数"]) { const n = node("div"); n.append(node("b", "", "0"), node("span", "", label)); el("stats").append(n); }
  emptyPanel(el("tasks"), "研究开始后显示 Agent 任务", "workflow");
  emptyPanel(el("sources"), "暂无来源读取记录", "files");
  el("activity-chart").hidden = true; el("budget").textContent = ""; el("run-id").textContent = "尚未创建"; el("run-model").textContent = "";
  executionSummary(null); el("run-status").textContent = "就绪"; el("cancel").hidden = true; el("send").disabled = false;
}
async function selectSession(id) {
  state.archive = ""; state.archiveLoaded = false;
  recordMode("sessions"); archiveUrl(""); error();
  state.session = id; resetView(); closeDrawers();
  localStorage.setItem("finance.h2.session", id);
  await sessions(); await refresh();
}
function archiveUrl(id) {
  const url = new URL(location.href);
  if (id) url.searchParams.set("archive", id); else url.searchParams.delete("archive");
  history.replaceState(null, "", url);
}
function recordMode(mode) {
  state.recordMode = mode;
  for (const name of ["sessions", "archives"]) {
    el(name).hidden = name !== mode;
    el(`tab-${name}`).setAttribute("aria-selected", String(name === mode));
  }
  el("composer").hidden = Boolean(state.archive);
  el("model").disabled = Boolean(state.archive);
  el("model-select").hidden = Boolean(state.archive);
  el("archive-banner").hidden = !state.archive;
  if (state.archive) el("cancel").hidden = true;
  renderRecordList();
}
async function archives() {
  const data = await api("/archives");
  state.records.archives = data.archives; renderRecordList();
}
async function selectArchive(id) {
  state.archive = id; state.archiveLoaded = false; state.revision = "";
  resetView(); error(); recordMode("archives"); archiveUrl(id); closeDrawers();
  el("session-title").textContent = "测试记录";
  el("messages").replaceChildren(node("div", "empty", "读取已保存记录…"));
  el("tasks").replaceChildren();
  await archives(); await refresh();
}
async function newSession() {
  const s = await api("/sessions", {method: "POST", body: JSON.stringify({})});
  await selectSession(s.session_id);
}
function renderMessages(runs) {
  const signature = JSON.stringify(runs.map(r => [r.run_id, r.status, r.answer, r.metadata]));
  if (signature === state.revision) return;
  state.revision = signature;
  const scroller = el("messages");
  const nearBottom = scroller.children.length > 0 && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 100;
  const previousScroll = scroller.scrollTop;
  const awaitingReport = scroller.querySelectorAll(".pending").length > 0;
  let latestQuestion;
  const openDetails = new Set([...scroller.querySelectorAll("details[open]")].map(d => d.dataset.report));
  if (!runs.length) {
    emptyResearch(); return;
  }
  scroller.replaceChildren();
  for (const r of runs) {
    const question = node("article", "message user");
    const label = node("div", "message-label", "研究任务");
    const trace = node("button", "trace-link", "查看执行记录");
    trace.onclick = async () => { setRun(r.run_id); if (state.archive) state.archiveLoaded = false; await refresh(); showInspector(); };
    const query = node("details"); query.dataset.report = `query:${r.run_id}`; query.open = openDetails.has(query.dataset.report);
    const summary = node("summary"); summary.append(symbol("chevron-down"), node("span", "", r.query.length > 88 ? r.query.slice(0, 88) + "…" : r.query));
    query.append(summary, node("div", "body", r.query)); label.append(trace); question.append(label, query);
    scroller.append(question);
    latestQuestion = question;
    const answer = node("article", "message assistant");
    const masthead = node("header", "report-masthead"); masthead.append(node("span", "eyebrow", "RESEARCH NOTE / 研究报告"));
    const titleRow = node("div", "report-title-row"); titleRow.append(node("h2", "", "研究报告"));
    const tools = node("div", "report-tools");
    tools.append(iconButton("files", "查看本轮数据来源", async () => { setRun(r.run_id); if (state.archive) state.archiveLoaded = false; await refresh(); inspectionTab("sources"); showInspector(); }), iconButton("download", "导出报告 Markdown", () => downloadReport(r)));
    titleRow.append(tools); masthead.append(titleRow);
    const metadata = node("div", "report-meta"); metadata.append(node("span", `badge ${r.status}`, labels[r.status] || r.status), node("span", "", modelName(r.profile)), node("span", "", dateLabel(r.created_at) || (state.archive ? "历史测试报告" : "本轮研究")));
    masthead.append(metadata); answer.append(masthead);
    answer.append(reviewStatusPanel(r.metadata?.review_statuses || []));
    const body = node("div", "body");
    if (r.answer) markdown(body, r.answer);
    else body.append(node("div", "pending", terminal.has(r.status) ? "正在读取已保存的结果…" : "研究正在进行，结果会在完成后保存。"));
    const headings = [...body.querySelectorAll("h1, h2, h3")];
    if (headings.length > 2) {
      const outline = node("details", "report-outline"); outline.dataset.report = `outline:${r.run_id}`; outline.open = openDetails.has(outline.dataset.report);
      outline.append(node("summary", "", `报告目录 · ${headings.length} 个章节`)); const links = node("div", "outline-links");
      for (const heading of headings) { const b = node("button", "", heading.textContent); b.onclick = () => heading.scrollIntoView({behavior: "smooth", block: "start"}); links.append(b); }
      outline.append(links); answer.append(outline);
    }
    answer.append(body);
    const notes = [...(r.metadata?.warnings || []), ...(r.metadata?.gaps || [])];
    if (r.metadata?.metadata_status === "incomplete_metadata") notes.unshift("审核或覆盖元数据未完整记录");
    if (notes.length) answer.append(node("div", "disclosure", notes.join("\n")));
    scroller.append(answer);
  }
  if (nearBottom && !state.archive && awaitingReport && runs.at(-1)?.answer) {
    scroller.scrollTop = latestQuestion.offsetTop - scroller.offsetTop;
  } else scroller.scrollTop = nearBottom && !state.archive ? scroller.scrollHeight : previousScroll;
  icons();
}
function modelName(id) { return state.config?.models.find(m => m.id === id)?.name || id; }
function reviewStatusDetails(status) {
  const scope = (status.scope_labels || status.scope || []).join("、") || "未记录";
  return `范围：${scope} · 已执行 ${status.rounds ?? 0} 轮${status.draft_revision ? ` · 草稿版本 ${status.draft_revision}` : ""}`;
}
function reviewStatusPanel(statuses, showEmpty = true) {
  const panel = node("section", "review-status-list"); panel.setAttribute("aria-label", "运行记录中的核验结论");
  panel.append(node("div", "section-label", "核验结论 · 运行记录"));
  if (!statuses.length) { if (showEmpty) panel.append(node("p", "", "暂无专业分析的核验结论记录。")); else panel.hidden = true; return panel; }
  for (const status of statuses) {
    const item = node("div", "review-status-item");
    const heading = node("div", "review-status-heading");
    heading.append(symbol(status.status === "passed" ? "shield-check" : "clipboard-list"), node("b", "", status.label));
    item.append(heading);
    if (status.objective) item.append(node("p", "review-objective", status.objective));
    item.append(node("p", "", reviewStatusDetails(status)));
    if (status.draft_ref) item.append(node("code", "", `草稿：${status.draft_ref}`));
    if (status.report_ref) item.append(node("code", "", `核验报告：${status.report_ref}`));
    if (status.result_artifact_ref) {
      const read = node("button", "trace-link", "查看分析快照");
      read.onclick = () => openArtifact(status.result_artifact_ref).catch(e => error(e.message)); item.append(read);
    }
    panel.append(item);
  }
  panel.append(node("p", "review-notice", statuses[0].scope_notice), node("p", "review-notice", statuses[0].snapshot_notice));
  return panel;
}
function reportMarkdown(run) {
  const statuses = run.metadata?.review_statuses || [];
  const review = statuses.length ? statuses.map(s => [
    `### ${s.label}`, s.objective || "", reviewStatusDetails(s),
    s.task_id ? `任务：\`${s.task_id}\`` : "", s.draft_ref ? `草稿：\`${s.draft_ref}\`` : "",
    s.report_ref ? `核验报告：\`${s.report_ref}\`` : "",
  ].filter(Boolean).join("\n\n")).join("\n\n") : "暂无专业分析的核验结论记录。";
  const notice = statuses.length ? `\n\n${statuses[0].scope_notice}\n\n${statuses[0].snapshot_notice}` : "";
  return `# 研究报告\n\n研究问题：${run.query}\n\n模型：${run.profile}\n\n## 核验结论 · 运行记录\n\n${review}${notice}\n\n---\n\n${run.answer}`;
}
function downloadReport(run) {
  if (!run.answer) { toast("报告尚未生成"); return; }
  const blob = new Blob([reportMarkdown(run)], {type: "text/markdown;charset=utf-8"});
  const link = node("a"); const url = URL.createObjectURL(blob); link.href = url; link.download = `research-${run.run_id}.md`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function executionSummary(root) {
  const status = root?.status || "idle";
  const label = root ? (labels[status] || status) : "尚未开始";
  const badge = node("span", `badge ${status}`, root && terminal.has(status) ? `主任务${label}` : label);
  el("execution-summary").replaceChildren(badge, el("updated-at"));
  el("updated-at").textContent = state.lastEventTime ? `更新于 ${dateLabel(state.lastEventTime)}` : (root ? "等待执行记录" : "等待研究任务");
}
function activityChart(tasks, total) {
  const chart = el("activity-chart"); chart.replaceChildren(); chart.hidden = !total;
  if (!total) return;
  const heading = node("div", "chart-heading"); heading.append(node("span", "", "请求分布"), node("span", "", "按 Agent 实际调用"));
  const track = node("div", "activity-track"); track.setAttribute("role", "img");
  const legend = node("div", "chart-legend"); const descriptions = [];
  for (const task of tasks.filter(t => t.budget.consumed > 0)) {
    const name = roleNames[task.role] || task.role; const value = task.budget.consumed;
    const bar = node("span"); bar.style.flex = String(value); bar.title = `${name} ${value} 次`; track.append(bar);
    const text = node("span"); text.append(node("i"), document.createTextNode(`${name} ${value}`)); legend.append(text); descriptions.push(`${name} ${value} 次`);
  }
  track.setAttribute("aria-label", descriptions.join("，")); chart.append(heading, track, legend);
}
function renderTasks(view) {
  const tasks = view.status.tasks;
  for (const span of view.trace?.spans || []) {
    state.lastEventTime = Math.max(state.lastEventTime, span.finished_at || 0, span.started_at || 0);
  }
  const calls = Object.values(view.metrics.model_calls).reduce((a, b) => a + b, 0);
  const attempts = tasks.reduce((n, t) => n + t.attempts.length, 0);
  el("stats").replaceChildren();
  for (const [value, label] of [[calls,"模型请求"],[tasks.length,"Agent 任务"],[attempts,"执行步数"]]) {
    const stat = node("div"); stat.append(node("b", "", String(value)), node("span", "", label)); el("stats").append(stat);
  }
  el("run-id").textContent = view.run_id;
  el("run-model").textContent = `${view.model_profile} · ${view.transport_backend}`;
  const c = view.budget || state.config.budget;
  el("budget").textContent = `主任务 ≤ ${c.root} 次 · 每个子任务 ≤ ${c.child} 次 · 整轮 ≤ ${c.run} 次 / ${c.minutes} 分钟`;
  activityChart(tasks, calls);
  const openTasks = new Set([...el("tasks").querySelectorAll("details[open]")].map(d => d.dataset.task));
  el("tasks").replaceChildren();
  for (const t of tasks) {
    const box = node("article", "task " + (t.depth === 1 ? "child" : t.depth === 2 ? "grandchild" : ""));
    const heading = node("div", "task-heading");
    heading.append(node("b", "", roleNames[t.role] || t.role), node("span", `badge ${t.status}`, labels[t.status] || t.status));
    box.append(heading);
    const ids = node("details", "task-identities"); ids.dataset.task = `ids:${t.task_id}`; ids.open = openTasks.has(ids.dataset.task);
    ids.append(node("summary", "", t.depth === 0 ? "主任务标识" : `${t.depth} 级委派 · 任务标识`), node("code", "", `Thread: ${t.thread_id}`), node("code", "", `Task: ${t.task_id}`)); box.append(ids);
    if (t.objective) {
      const objective = node("details"); objective.dataset.task = `objective:${t.task_id}`;
      objective.open = openTasks.has(objective.dataset.task);
      objective.append(node("summary", "", "任务目标"), node("p", "task-objective", t.objective));
      box.append(objective);
    }
    const b = t.budget;
    box.append(node("small", "", `已用 ${b.consumed ?? 0} 次 · 剩余 ${b.remaining ?? "—"} 次 · ${t.attempts.length} 个 Attempt`));
    if (t.review_status) box.append(reviewStatusPanel([t.review_status]));
    if (t.failure_diagnostic) box.append(node("pre", "", JSON.stringify(t.failure_diagnostic, null, 2)));
    if (t.result_artifact_ref) {
      const read = node("button", "trace-link", "查看结果");
      read.onclick = () => openArtifact(t.result_artifact_ref).catch(e => error(e.message));
      box.append(read);
    }
    const details = node("details"); details.dataset.task = t.task_id; details.open = openTasks.has(t.task_id);
    details.append(node("summary", "", "Attempt 记录"));
    for (const a of t.attempts) {
      const line = node("div", "attempt", `#${a.attempt_no} ${labels[a.status] || a.status}`);
      line.append(node("code", "", a.attempt_id));
      if (a.error_code) line.append(node("pre", "", `${a.error_code}\n${a.error_message || ""}`));
      details.append(line);
    }
    box.append(details); el("tasks").append(box);
  }
  const root = tasks.find(t => t.task_id === view.root_task_id);
  executionSummary(root);
  const isBusy = !view.readonly && root && !terminal.has(root.status);
  state.busy = Boolean(isBusy);
  el("run-status").textContent = root ? (labels[root.status] || root.status) : "就绪";
  el("cancel").hidden = !isBusy;
  el("send").disabled = isBusy;
  if (!tasks.length) emptyPanel(el("tasks"), "研究开始后显示 Agent 任务", "workflow");
  icons();
}
function artifactRefs(value, refs = new Set()) {
  if (typeof value === "string" && /^art_[a-z0-9]+$/i.test(value)) refs.add(value);
  else if (value && typeof value === "object") for (const child of Object.values(value)) artifactRefs(child, refs);
  return refs;
}
function renderTraceCalls(trace) {
  const calls = (trace?.spans || []).filter((span) => span.span_type === "model_call");
  const signature = JSON.stringify(calls);
  if (signature === state.callSignature) return;
  state.callSignature = signature;
  const target = el("trace-calls");
  target.replaceChildren();
  if (!calls.length) return;
  target.append(node("h3", "section-label", `模型请求 · ${calls.length}`));
  for (const call of calls) {
    const row = node("article", "event");
    const path = (call.agent_path || []).join(" → ") || "Agent 未知";
    const summary = node("div", "task-heading");
    summary.append(node("b", "", path), node("span", `badge ${call.status}`, labels[call.status] || call.status));
    row.append(summary);
    row.append(node("code", "", `Attempt #${call.attempt_no ?? "?"} · ${call.model_profile || "模型未记录"}`));
    row.append(node("code", "", `Attempt: ${call.parent_span_id || "未记录"}`));
    row.append(node("code", "", `Call: ${call.span_id}`));
    const usage = call.usage || {};
    const tokens = usage.total_tokens ?? usage.total ?? null;
    row.append(node("small", "", `${call.duration_ms ?? "—"} ms${tokens === null ? "" : ` · ${tokens} tokens`}${call.error_code ? ` · ${call.error_code}` : ""}`));
    target.append(row);
  }
}
function addEvents(events) {
  for (const e of events) {
    if (state.events.has(e.event_id)) continue;
    state.events.set(e.event_id, e);
    if (e.created_at) state.lastEventTime = Math.max(state.lastEventTime, e.created_at);
    const d = node("details", "event");
    const summary = node("summary");
    const path = (e.agent_path || []).join(" → ") || "Agent 未知";
    summary.append(node("span", "", `#${e.event_id}`), document.createTextNode(`${path} · ${e.event_type}`));
    d.append(summary, node("code", "", e.task_id || ""), node("pre", "", JSON.stringify(e.payload, null, 2)));
    const buttons = node("div", "event-actions");
    for (const ref of artifactRefs(e.payload)) {
      const b = node("button", "", `读取 ${ref.slice(0, 16)}…`);
      b.onclick = () => openArtifact(ref).catch(e => error(e.message)); buttons.append(b);
    }
    d.append(buttons); el("events").append(d);
  }
  el("event-count").textContent = String(state.events.size);
  if (state.lastEventTime) el("updated-at").textContent = `更新于 ${dateLabel(state.lastEventTime)}`;
  renderSources();
}
const sourceToolLabels = {"article.read": "文章原文", "article.list": "文章目录", "article.search": "文章检索", "article.claims": "文章观点", "web.open": "网页资料", "web.search": "网页检索", "market.catalog": "行情目录", "market.snapshot": "行情快照", "market.metrics": "行情指标", "market.series": "行情序列", "personal.catalog": "个人资料目录", "context.open": "个人资料", "company_wiki_search": "知识资料检索"};
function sourceEvents(events) { return events.filter(e => e.event_type === "tool.completed" && Object.hasOwn(sourceToolLabels, e.payload?.name || "")); }
function renderSources() {
  const sources = sourceEvents([...state.events.values()]); el("source-count").textContent = String(sources.length);
  const target = el("sources"); target.replaceChildren();
  if (!sources.length) { emptyPanel(target, "暂无来源工具记录；报告中的引用仍可直接查看。", "files"); return; }
  target.append(node("p", "section-label", "来源工具记录 · 保留读取状态与原始资料"));
  for (const e of sources) {
    const item = node("article", "source-item"), heading = node("div", "source-heading");
    heading.append(symbol("file-text"), node("b", "", sourceToolLabels[e.payload.name] || e.payload.name), node("span", `badge ${e.payload.status}`, labels[e.payload.status] || e.payload.status));
    const owner = (e.agent_path || []).map(r => roleNames[r] || r).join(" / ");
    item.append(heading, node("p", "", `${owner} · ${e.created_at ? dateLabel(e.created_at) : `事件 #${e.event_id}`}`));
    const refs = [...artifactRefs(e.payload)];
    for (const ref of refs) { const b = node("button", "trace-link", "查看来源记录"); b.title = ref; b.onclick = () => openArtifact(ref).catch(e => error(e.message)); item.append(b); }
    if (!refs.length) item.append(node("p", "", "该条记录没有可读取的资料引用。"));
    target.append(item);
  }
  icons();
}
let artifact = {ref: "", offset: 0, run: "", session: "", archive: "", text: "", loading: false};
async function openArtifact(ref) {
  artifact = {ref, offset: 0, run: state.run, session: state.session, archive: state.archive, text: "", loading: false};
  el("artifact-title").textContent = ref; el("artifact-body").textContent = "";
  el("artifact-preview").replaceChildren(node("div", "pending", "正在读取资料…")); el("artifact-progress").textContent = ""; el("artifact-completeness").textContent = "";
  el("artifact-review-status").replaceChildren(); el("artifact-review-status").hidden = true;
  el("more-artifact").hidden = true; el("retry-artifact").hidden = true; el("artifact-error").hidden = true;
  artifactTab("preview"); if (!el("artifact-dialog").open) el("artifact-dialog").showModal(); await moreArtifact();
}
function artifactTab(name) {
  el("artifact-preview").hidden = name !== "preview"; el("artifact-body").hidden = name !== "raw";
  el("artifact-preview-tab").setAttribute("aria-selected", String(name === "preview")); el("artifact-raw-tab").setAttribute("aria-selected", String(name === "raw"));
}
function readableArtifact(text) {
  let data;
  try { data = JSON.parse(text); } catch { return {content: text, raw: true}; }
  const queue = [data]; let count = 0;
  while (queue.length && count++ < 60) {
    const current = queue.shift(); if (!current || typeof current !== "object") continue;
    for (const key of ["content", "answer", "text", "body", "markdown"]) if (typeof current[key] === "string" && current[key].trim()) return {content: current[key], raw: false};
    queue.push(...Object.values(current).filter(v => v && typeof v === "object"));
  }
  return {content: JSON.stringify(data, null, 2), raw: true};
}
async function moreArtifact() {
  const a = artifact;
  if (a.loading) return;
  a.loading = true; el("more-artifact").disabled = true; el("retry-artifact").hidden = true; el("artifact-error").hidden = true;
  const path = a.archive ? `/archives/${a.archive}/artifacts/${a.ref}?offset=${a.offset}`
    : `/runs/${a.run}/artifacts/${a.ref}?session_id=${encodeURIComponent(a.session)}&offset=${a.offset}`;
  try {
    const page = await api(path);
    if (a !== artifact) return;
    a.text += page.content; a.offset = page.next_offset;
    const presentation = el("artifact-review-status"); presentation.replaceChildren();
    presentation.hidden = !(page.review_status || page.snapshot_notice);
    if (page.review_status) presentation.append(reviewStatusPanel([page.review_status]));
    else if (page.snapshot_notice) presentation.append(node("p", "review-notice", `${page.draft_revision ? `送审草稿 · 版本 ${page.draft_revision}。` : ""}${page.snapshot_notice}`));
    el("artifact-body").textContent = a.text;
    const readable = readableArtifact(a.text);
    if (page.has_more || readable.raw) { const pre = node("pre", "", readable.content); el("artifact-preview").replaceChildren(pre); }
    else markdown(el("artifact-preview"), readable.content);
    el("artifact-progress").textContent = `已读取 ${a.text.length.toLocaleString()} 字符`;
    el("artifact-completeness").textContent = page.has_more ? "尚有后续内容" : "内容已完整读取";
    el("more-artifact").hidden = !page.has_more; icons();
  } catch (e) {
    if (a !== artifact) return;
    el("artifact-error").textContent = `资料读取失败：${e.message}`; el("artifact-error").hidden = false; el("retry-artifact").hidden = false;
    if (!a.text) emptyPanel(el("artifact-preview"), "资料暂时无法读取，请重试。", "file-warning");
  } finally { a.loading = false; if (a === artifact) el("more-artifact").disabled = false; }
}
async function refresh() {
  if (state.polling || (!state.session && !state.archive)) return;
  state.polling = true;
  const session = state.session;
  const archiveId = state.archive;
  try {
    if (archiveId) {
      if (state.archiveLoaded) return;
      let hasMore;
      do {
        const record = await api(`/archives/${archiveId}?after_event_id=${state.cursor}`);
        if (archiveId !== state.archive) return;
        setRun(record.view.run_id);
        renderMessages([record.run]); renderTasks(record.view); renderTraceCalls(record.view.trace);
        if (state.cursor === "0") el("messages").scrollTop = 0;
        addEvents(record.view.trace.events); state.cursor = record.view.status.next_event_id;
        el("session-title").textContent = `${record.case} · 测试记录`;
        el("archive-description").textContent = `${record.batch} / ${record.case}`;
        hasMore = record.view.status.has_more;
      } while (hasMore);
      state.archiveLoaded = true;
      return;
    }
    const history = await api(`/sessions/${session}`);
    if (session !== state.session || state.archive) return;
    renderMessages(history.runs);
    if (!state.run && history.runs.length) setRun(history.runs.at(-1).run_id);
    if (state.run) {
      const run = state.run;
      for (let i = 0; i < 8; i++) {
        const view = await api(`/runs/${run}?session_id=${session}&after_event_id=${state.cursor}`);
        if (session !== state.session || run !== state.run || state.archive) return;
        renderTasks(view); renderTraceCalls(view.trace); addEvents(view.trace?.events || view.status.events); state.cursor = view.status.next_event_id;
        if (view.driver_error) error(`H2 基础设施异常：${view.driver_error}`);
        if (!view.status.has_more) break;
      }
    } else {
      resetView();
    }
  } catch (e) { error(`读取 H2 状态失败：${e.message}`); }
  finally { state.polling = false; }
}
el("composer").addEventListener("submit", async (event) => {
  event.preventDefault(); if (state.busy || state.archive) return;
  const query = el("query").value.trim(); if (!query) return;
  state.busy = true;
  const session = state.session, profile = el("model").value;
  const mode = el("review-mode")?.value || "";
  const constraints = mode ? {analyst_review_mode: mode} : undefined;
  const storageKey = `finance.h2.pending.${session}`;
  let pending = JSON.parse(localStorage.getItem(storageKey) || "null");
  if (!pending || pending.query !== query || pending.model_profile !== profile || JSON.stringify(pending.dispatch_constraints) !== JSON.stringify(constraints)) pending = {query, model_profile: profile, request_id: crypto.randomUUID(), ...(constraints ? {dispatch_constraints: constraints} : {})};
  localStorage.setItem(storageKey, JSON.stringify(pending));
  el("send").disabled = true; error();
  try {
    const r = await api(`/sessions/${session}/messages`, {method: "POST", body: JSON.stringify(pending)});
    localStorage.removeItem(storageKey); el("query").value = ""; setRun(r.run_id); state.revision = "";
    await sessions(); await refresh();
  } catch (e) { state.busy = false; error(`提交失败：${e.message}`); el("send").disabled = false; }
});
el("cancel").onclick = async () => {
  if (state.archive) return;
  if (!confirm("停止当前研究及其子任务？已发出的模型请求仍可能计费。")) return;
  try { await api(`/runs/${state.run}/cancel?session_id=${state.session}`, {method: "POST", headers: {"Idempotency-Key": crypto.randomUUID()}}); await refresh(); }
  catch (e) { error(e.message); }
};
el("new-session").onclick = () => newSession().catch(e => error(e.message));
el("refresh").onclick = () => { state.archiveLoaded = false; refresh(); };
el("retry-load").onclick = async () => { error(); try { if (!state.config) await init(); else { await sessions(); if (state.recordMode === "archives") await archives(); state.archiveLoaded = false; await refresh(); } } catch (e) { error(e.message); } };
el("tab-sessions").onclick = () => (state.session ? selectSession(state.session) : newSession()).catch(e => error(e.message));
el("tab-archives").onclick = () => { recordMode("archives"); archives().catch(e => error(e.message)); };
el("record-search").addEventListener("input", renderRecordList);
el("close-dialog").onclick = () => el("artifact-dialog").close();
el("more-artifact").onclick = () => moreArtifact().catch(e => error(e.message));
el("retry-artifact").onclick = moreArtifact;
el("artifact-preview-tab").onclick = () => artifactTab("preview");
el("artifact-raw-tab").onclick = () => artifactTab("raw");
function inspectionTab(name) {
  state.selectedTab = name;
  for (const tab of ["tasks", "sources", "events"]) { el(tab).hidden = tab !== name; el(`tab-${tab}`).setAttribute("aria-selected", String(tab === name)); }
}
for (const name of ["tasks", "sources", "events"]) el(`tab-${name}`).onclick = () => inspectionTab(name);
let drawerFocus = null;
function closeDrawers() {
  document.body.classList.remove("nav-open", "inspector-open"); el("drawer-scrim").hidden = true;
  el("open-nav").setAttribute("aria-expanded", "false");
  el("toggle-inspector").setAttribute("aria-expanded", String(window.innerWidth > 1250 && !document.body.classList.contains("inspector-collapsed")));
  for (const id of ["main", "sidebar", "inspector"]) { el(id).inert = false; el(id).removeAttribute("aria-modal"); el(id).removeAttribute("role"); }
  if (drawerFocus) { drawerFocus.focus(); drawerFocus = null; }
}
function openDrawer(name) {
  closeDrawers(); drawerFocus = document.activeElement;
  document.body.classList.add(name === "sidebar" ? "nav-open" : "inspector-open");
  el("drawer-scrim").hidden = false;
  for (const id of ["main", "sidebar", "inspector"]) el(id).inert = id !== name;
  el(name).setAttribute("role", "dialog"); el(name).setAttribute("aria-modal", "true");
  el(name === "sidebar" ? "open-nav" : "toggle-inspector").setAttribute("aria-expanded", "true");
  el(name === "sidebar" ? "close-nav" : "close-inspector").focus();
}
function showInspector() {
  if (window.innerWidth <= 1250) openDrawer("inspector");
  else { document.body.classList.remove("inspector-collapsed"); el("toggle-inspector").setAttribute("aria-expanded", "true"); }
}
el("open-nav").onclick = () => openDrawer("sidebar");
el("close-nav").onclick = closeDrawers;
el("drawer-scrim").onclick = closeDrawers;
el("toggle-inspector").onclick = () => {
  if (window.innerWidth <= 1250) showInspector();
  else { document.body.classList.toggle("inspector-collapsed"); el("toggle-inspector").setAttribute("aria-expanded", String(!document.body.classList.contains("inspector-collapsed"))); }
};
el("close-inspector").onclick = () => { closeDrawers(); if (window.innerWidth > 1250) { document.body.classList.add("inspector-collapsed"); el("toggle-inspector").setAttribute("aria-expanded", "false"); } };
document.addEventListener("keydown", e => {
  if (el("artifact-dialog").open) return;
  const active = document.body.classList.contains("nav-open") ? el("sidebar") : document.body.classList.contains("inspector-open") ? el("inspector") : null;
  if (!active) return;
  if (e.key === "Escape") { e.preventDefault(); closeDrawers(); }
  if (e.key === "Tab") {
    const focusables = [...active.querySelectorAll('button:not(:disabled), input, select, a[href], summary')].filter(n => n.getClientRects().length);
    const first = focusables[0], last = focusables.at(-1);
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});
window.addEventListener("resize", closeDrawers);
let pollTimer;
async function init() {
  icons(); state.config = await api("/config");
  el("backend").textContent = state.config.transport_backend === "redis" ? "SQLite · Redis Streams" : "SQLite · FakeRedis（本地）";
  el("model").replaceChildren();
  for (const m of state.config.models) {
    const option = node("option", "", `${m.name}${m.configured ? "" : "（未配置密钥）"}`);
    option.value = m.id; option.disabled = !m.configured; option.selected = m.id === state.config.default_profile; el("model").append(option);
  }
  const data = await api("/sessions");
  const saved = localStorage.getItem("finance.h2.session");
  const selected = data.sessions.find(s => s.session_id === saved) || data.sessions[0];
  const archiveId = new URL(location.href).searchParams.get("archive");
  state.records.sessions = data.sessions;
  if (selected) state.session = selected.session_id;
  if (archiveId) await selectArchive(archiveId);
  else if (selected) await selectSession(selected.session_id); else await newSession();
  clearInterval(pollTimer); pollTimer = setInterval(refresh, 1500); closeDrawers();
}
init().catch(e => error(`H2 工作台启动失败：${e.message}`));
