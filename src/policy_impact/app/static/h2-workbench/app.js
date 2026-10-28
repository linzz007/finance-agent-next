"use strict";
const base = "/api/h2-workbench";
const el = (id) => document.getElementById(id);
const state = {session: "", run: "", cursor: "0", events: new Map(), callSignature: "", busy: false, polling: false, config: null, revision: "", archive: "", recordMode: "sessions", archiveLoaded: false};
const terminal = new Set(["succeeded", "partial", "failed", "cancelled", "timed_out"]);
const labels = {pending_dispatch: "等待派发", queued: "已入队", running: "执行中", waiting: "等待子任务", succeeded: "完成", partial: "部分完成", failed: "失败", cancelled: "已停止", timed_out: "超时", suspended: "已保存", expired: "租约过期", leased: "已领取", blocked: "受阻", backpressured: "队列背压"};
function icons() { if (window.lucide) window.lucide.createIcons(); }
function node(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
function error(message = "") { el("error").textContent = message; el("error").hidden = !message; }
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
}
async function sessions() {
  const data = await api("/sessions");
  el("sessions").replaceChildren();
  for (const s of data.sessions) {
    const b = node("button", s.session_id === state.session ? "active" : "", s.title);
    b.onclick = () => selectSession(s.session_id);
    el("sessions").append(b);
    if (s.session_id === state.session) el("session-title").textContent = s.title;
  }
}
function setRun(id) {
  if (id === state.run) return;
  state.run = id; state.cursor = "0"; state.events.clear();
  el("events").replaceChildren();
  const calls = node("div", "trace-calls"); calls.id = "trace-calls";
  el("events").append(calls);
  el("event-count").textContent = "0"; state.callSignature = "";
}
async function selectSession(id) {
  state.archive = ""; state.archiveLoaded = false;
  recordMode("sessions"); archiveUrl(""); error();
  state.session = id; state.revision = ""; setRun("");
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
  el("archive-banner").hidden = !state.archive;
  if (state.archive) el("cancel").hidden = true;
}
async function archives() {
  const data = await api("/archives");
  const target = el("archives"); target.replaceChildren();
  let batch = "";
  for (const item of data.archives) {
    if (item.batch !== batch) { target.append(node("div", "archive-batch", item.batch)); batch = item.batch; }
    const button = node("button", item.archive_id === state.archive ? "active" : "");
    button.append(node("b", "", `${item.case} · ${labels[item.status] || item.status}`), node("span", "", item.query));
    button.title = `${item.batch} / ${item.case}`;
    button.onclick = () => selectArchive(item.archive_id).catch(e => error(e.message));
    target.append(button);
  }
  if (!data.archives.length) target.append(node("div", "empty-records", "暂无测试记录"));
}
async function selectArchive(id) {
  state.archive = id; state.archiveLoaded = false; state.revision = "";
  setRun(""); state.busy = false; error(); recordMode("archives"); archiveUrl(id);
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
  const nearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 100;
  if (!runs.length) {
    scroller.replaceChildren(node("div", "empty", "新的金融研究")); return;
  }
  scroller.replaceChildren();
  for (const r of runs) {
    const question = node("article", "message user");
    const label = node("div", "message-label", "你");
    const trace = node("button", "trace-link", "查看本轮 Trace");
    trace.onclick = async () => { setRun(r.run_id); await refresh(); };
    label.append(trace); question.append(label, node("div", "body", r.query));
    scroller.append(question);
    const answer = node("article", "message assistant");
    answer.append(node("div", "message-label", `${r.profile} · ${labels[r.status] || r.status}`));
    const body = node("div", "body");
    if (r.answer) markdown(body, r.answer);
    else body.append(node("div", "pending", terminal.has(r.status) ? "正在读取已保存的结果…" : "研究进行中…"));
    answer.append(body);
    const notes = [...(r.metadata.warnings || []), ...(r.metadata.gaps || [])];
    if (r.metadata.metadata_status === "incomplete_metadata") notes.unshift("审核或覆盖元数据未完整记录");
    if (notes.length) answer.append(node("div", "disclosure", notes.join("\n")));
    scroller.append(answer);
  }
  if (nearBottom) scroller.scrollTop = scroller.scrollHeight;
}
function renderTasks(view) {
  const tasks = view.status.tasks;
  const calls = Object.values(view.metrics.model_calls).reduce((a, b) => a + b, 0);
  const attempts = tasks.reduce((n, t) => n + t.attempts.length, 0);
  el("stats").replaceChildren();
  for (const [value, label] of [[calls,"模型请求"],[tasks.length,"任务"],[attempts,"Attempt"]]) {
    const stat = node("div"); stat.append(node("b", "", String(value)), node("span", "", label)); el("stats").append(stat);
  }
  el("run-id").textContent = view.run_id;
  el("run-model").textContent = `${view.model_profile} · ${view.transport_backend}`;
  const c = view.budget || state.config.budget;
  el("budget").textContent = `主任务 ≤ ${c.root} 次 · 每个子任务 ≤ ${c.child} 次 · 整轮 ≤ ${c.run} 次 / ${c.minutes} 分钟`;
  const openTasks = new Set([...el("tasks").querySelectorAll("details[open]")].map(d => d.dataset.task));
  el("tasks").replaceChildren();
  for (const t of tasks) {
    const box = node("article", "task " + (t.depth === 1 ? "child" : t.depth === 2 ? "grandchild" : ""));
    const heading = node("div", "task-heading");
    heading.append(node("b", "", t.depth === 0 ? "主 Agent" : `${t.depth === 2 ? "二级" : "子"} Agent · ${t.role}`), node("span", `badge ${t.status}`, labels[t.status] || t.status));
    box.append(heading, node("code", "", `Thread: ${t.thread_id}`), node("code", "", `Task: ${t.task_id}`));
    if (t.objective) {
      const objective = node("details"); objective.dataset.task = `objective:${t.task_id}`;
      objective.open = openTasks.has(objective.dataset.task);
      objective.append(node("summary", "", "任务目标"), node("p", "task-objective", t.objective));
      box.append(objective);
    }
    const b = t.budget;
    box.append(node("small", "", `已用 ${b.consumed ?? 0} 次 · 剩余 ${b.remaining ?? "—"} 次 · ${t.attempts.length} 个 Attempt`));
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
  const isBusy = !view.readonly && root && !terminal.has(root.status);
  state.busy = Boolean(isBusy);
  el("run-status").textContent = root ? (labels[root.status] || root.status) : "就绪";
  el("cancel").hidden = !isBusy;
  el("send").disabled = isBusy;
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
}
let artifact = {ref: "", offset: 0, run: "", session: "", archive: ""};
async function openArtifact(ref) {
  artifact = {ref, offset: 0, run: state.run, session: state.session, archive: state.archive};
  el("artifact-title").textContent = ref; el("artifact-body").textContent = "";
  el("artifact-dialog").showModal(); await moreArtifact();
}
async function moreArtifact() {
  const a = artifact;
  const path = a.archive ? `/archives/${a.archive}/artifacts/${a.ref}?offset=${a.offset}`
    : `/runs/${a.run}/artifacts/${a.ref}?session_id=${encodeURIComponent(a.session)}&offset=${a.offset}`;
  const page = await api(path);
  if (a !== artifact) return;
  el("artifact-body").textContent += page.content; a.offset = page.next_offset;
  el("more-artifact").hidden = !page.has_more;
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
      state.busy = false; el("send").disabled = false; el("cancel").hidden = true;
      el("tasks").replaceChildren(); el("run-id").textContent = "尚未创建"; el("run-model").textContent = "";
      el("run-status").textContent = "就绪";
    }
  } catch (e) { error(`读取 H2 状态失败：${e.message}`); }
  finally { state.polling = false; }
}
el("composer").addEventListener("submit", async (event) => {
  event.preventDefault(); if (state.busy || state.archive) return;
  const query = el("query").value.trim(); if (!query) return;
  state.busy = true;
  const session = state.session, profile = el("model").value;
  const storageKey = `finance.h2.pending.${session}`;
  let pending = JSON.parse(localStorage.getItem(storageKey) || "null");
  if (!pending || pending.query !== query || pending.model_profile !== profile) pending = {query, model_profile: profile, request_id: crypto.randomUUID()};
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
el("tab-sessions").onclick = () => (state.session ? selectSession(state.session) : newSession()).catch(e => error(e.message));
el("tab-archives").onclick = () => { recordMode("archives"); archives().catch(e => error(e.message)); };
el("close-dialog").onclick = () => el("artifact-dialog").close();
el("more-artifact").onclick = () => moreArtifact().catch(e => error(e.message));
for (const name of ["tasks", "events"]) el(`tab-${name}`).onclick = () => {
  for (const tab of ["tasks", "events"]) { el(tab).hidden = tab !== name; el(`tab-${tab}`).setAttribute("aria-selected", String(tab === name)); }
};
async function init() {
  icons(); state.config = await api("/config");
  el("backend").textContent = state.config.transport_backend === "redis" ? "SQLite · Redis Streams" : "SQLite · FakeRedis（本地）";
  for (const m of state.config.models) {
    const option = node("option", "", `${m.name}${m.configured ? "" : "（未配置密钥）"}`);
    option.value = m.id; option.disabled = !m.configured; option.selected = m.id === state.config.default_profile; el("model").append(option);
  }
  const data = await api("/sessions");
  const saved = localStorage.getItem("finance.h2.session");
  const selected = data.sessions.find(s => s.session_id === saved) || data.sessions[0];
  const archiveId = new URL(location.href).searchParams.get("archive");
  if (selected) state.session = selected.session_id;
  if (archiveId) await selectArchive(archiveId);
  else if (selected) await selectSession(selected.session_id); else await newSession();
  setInterval(refresh, 1500);
}
init().catch(e => error(`H2 工作台启动失败：${e.message}`));
