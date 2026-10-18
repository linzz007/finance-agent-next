import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import test from "node:test";
import vm from "node:vm";

const appSource = readFileSync(new URL("../src/policy_impact/app/static/workbench/app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../src/policy_impact/app/static/workbench/index.html", import.meta.url), "utf8");

function field(value = "") {
  return {
    value, disabled: false, dataset: {}, textContent: "", innerHTML: "", open: false,
    classList: { toggle() {}, add() {}, remove() {} },
    setAttribute() {}, addEventListener() {}, focus() {}, scrollIntoView() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    reportValidity() { return true; }, reset() {},
    showModal() { this.open = true; }, close() { this.open = false; },
  };
}

function harness(responder = async () => ({}), storage = undefined) {
  const elements = new Map();
  const calls = [];
  const context = vm.createContext({
    document: {
      getElementById: (id) => elements.get(id) || null,
      querySelectorAll: () => [],
    },
    window: { confirm: () => true, localStorage: storage }, crypto: webcrypto, URLSearchParams,
    setTimeout: () => 1, clearTimeout() {},
    fetch: async (path, options = {}) => {
      const call = { path, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null };
      calls.push(call);
      const result = await responder(call);
      return { ok: result.status ? result.status < 400 : true, statusText: "Error", text: async () => JSON.stringify(result.data ?? result) };
    },
  });
  assert.match(appSource, /\ninit\(\);\s*$/);
  vm.runInContext(appSource.replace(/\ninit\(\);\s*$/, ""), context);
  vm.runInContext("globalThis.uiState = state", context);
  const renderWiki = context.renderWiki;
  context.renderAll = () => {};
  context.renderCompanies = () => {};
  context.renderWiki = () => {};
  context.toast = (message) => { context.lastToast = message; };
  return {
    context, calls, state: context.uiState, renderWiki,
    add(id, value = "") { const item = field(value); elements.set(id, item); return item; },
  };
}

const json = (value) => JSON.parse(JSON.stringify(value));

function sessionHarness(responder) {
  const h = harness(responder);
  h.state.companyId = "finance_observer";
  h.state.currentSessionId = "old";
  h.state.sessions = [{ session_id: "old", title: "Previous report", message_count: 2 }];
  h.state.messages = [{ content: "Previous answer" }];
  h.state.trace = { run_id: "old-run" };
  h.state.tracesByRun = { "old-run": h.state.trace };
  h.context.renderHeader = () => {};
  h.context.closeMobileSidebar = () => {};
  h.context.closeSlashPalette = () => { h.state.slashOpen = false; };
  h.context.setPromptText = (value) => { h.context.prompt = value; };
  h.add("session-search", "Previous report");
  h.add("prompt-input");
  return h;
}

test("new conversation selects an empty chat and clears previous run state", async () => {
  const h = sessionHarness(async () => ({ session_id: "new", title: "新对话", message_count: 0 }));
  h.state.currentView = "wiki";
  h.state.lastError = "old error";
  h.state.slashOpen = true;
  await h.context.createSession();
  assert.equal(h.state.currentSessionId, "new");
  assert.equal(h.state.currentView, "chat");
  assert.equal(h.state.messages.length, 0);
  assert.equal(h.state.trace, null);
  assert.deepEqual(json(h.state.tracesByRun), {});
  assert.equal(h.state.sessions[0].session_id, "new");
  assert.equal(h.context.document.getElementById("session-search").value, "");
  assert.equal(h.context.prompt, "");
  assert.equal(h.state.slashOpen, false);
  assert.equal(h.state.lastError, "");
  assert.equal(h.state.sessionCreating, false);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].method, "POST");
});

test("refresh preserves an explicitly selected empty conversation", async () => {
  const h = sessionHarness(async () => ({ sessions: [
    { session_id: "old", message_count: 2 }, { session_id: "new", message_count: 0 },
  ] }));
  h.state.currentSessionId = "new";
  await h.context.loadSessions();
  assert.equal(h.state.currentSessionId, "new");
});

test("first message in a new chat displays streaming progress instead of the welcome screen", () => {
  const h = sessionHarness();
  const stream = h.add("chat-stream");
  h.state.messages = [];
  h.state.trace = null;
  h.state.streaming = {
    userMessage: "First question", content: "Partial answer", runId: "", artifacts: {},
    status: "Working", startedAt: new Date().toISOString(),
  };
  h.context.renderChat();
  assert.match(stream.innerHTML, /First question/);
  assert.match(stream.innerHTML, /Partial answer/);
  assert.match(stream.innerHTML, /Working/);
  assert.doesNotMatch(stream.innerHTML, /empty-state/);
});

test("finance chat shows a run panel above every assistant answer, including older turns", () => {
  const h = sessionHarness();
  const stream = h.add("chat-stream");
  h.state.messages = [
    { role: "user", content: "最近金价怎么样？" },
    { role: "assistant", content: "第一轮回答", metadata: { run_id: "old-run" } },
    { role: "user", content: "为什么？" },
    { role: "assistant", content: "第二轮回答", metadata: { run_id: "latest-run" } },
  ];
  h.state.trace = { run_id: "latest-run", status: "done", steps: [] };
  h.state.tracesByRun = { "latest-run": h.state.trace };
  h.context.renderChat();
  assert.equal((stream.innerHTML.match(/thinking-title">运行过程/g) || []).length, 2);
  assert.match(stream.innerHTML, /trace-pending[^>]*data-run-id="old-run"/);
  assert.doesNotMatch(stream.innerHTML, /message-trace-trigger/);
  assert.ok(stream.innerHTML.indexOf("trace-pending") < stream.innerHTML.indexOf("第一轮回答"));
});

test("finance chat automatically loads historical run traces without replacing the latest", async () => {
  const oldTrace = { run_id: "old-run", status: "done", steps: [{ step_index: 1, title: "工具调用", status: "done" }] };
  const h = sessionHarness(async ({ path }) => {
    if (path.endsWith("/messages")) return { messages: [
      { role: "assistant", content: "旧回答", metadata: { run_id: "old-run" } },
      { role: "assistant", content: "新回答", metadata: { run_id: "new-run" } },
    ] };
    if (path.endsWith("/trace")) return { data: path.includes("/runs/old-run/") ? oldTrace
      : { run_id: "new-run", status: "done", steps: [] } };
    return {};
  });
  let renders = 0;
  let rendered;
  const hydrated = new Promise((resolve) => { rendered = resolve; });
  h.context.renderChat = () => { renders += 1; rendered(); };
  await h.context.loadSessionData();
  await hydrated;
  assert.equal(h.state.trace.run_id, "new-run");
  assert.equal(h.state.tracesByRun["old-run"].run_id, "old-run");
  assert.equal(renders, 1);
  assert.ok(h.calls.some(({ path }) => path.includes("/runs/old-run/trace")));
});

test("historical trace responses are ignored after switching conversations", async () => {
  let finishOldTrace;
  let signalOldTrace;
  const oldTraceRequested = new Promise((resolve) => { signalOldTrace = resolve; });
  const h = sessionHarness(async ({ path }) => {
    if (path.endsWith("/messages")) return { messages: [
      { role: "assistant", content: "Previous answer", metadata: { run_id: "old-run" } },
      { role: "assistant", content: "Latest answer", metadata: { run_id: "latest-run" } },
    ] };
    if (path.includes("/runs/old-run/trace")) {
      signalOldTrace();
      return new Promise((resolve) => { finishOldTrace = resolve; });
    }
    if (path.endsWith("/trace")) return { data: { run_id: "latest-run", status: "done", steps: [] } };
    return {};
  });
  h.context.renderChat = () => {};
  await h.context.loadSessionData();
  await oldTraceRequested;
  h.state.currentSessionId = "next";
  h.state.sessionLoadEpoch += 1;
  h.state.tracesByRun = {};
  finishOldTrace({ data: { run_id: "old-run", status: "done", steps: [] } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(json(h.state.tracesByRun), {});
});

test("initial session selection still prefers visible populated history", async () => {
  const h = sessionHarness(async () => ({ sessions: [
    { session_id: "empty", message_count: 0 }, { session_id: "old", message_count: 2 },
  ] }));
  h.state.currentSessionId = "";
  await h.context.loadSessions();
  assert.equal(h.state.currentSessionId, "old");
});

for (const fail of [false, true]) {
  test(`workspace controls unlock after a ${fail ? "failed" : "successful"} message`, async () => {
    const h = sessionHarness();
    h.context.getPromptText = () => "Test question";
    h.context.setPromptDisabled = () => {};
    h.context.modelAvailable = () => true;
    for (const name of ["loadModelStatus", "updateSessionSettings", "loadSessions", "loadSessionData",
      "renderSessions", "renderSlashPalette", "renderWorkspace"]) h.context[name] = async () => {};
    h.context.streamApi = async () => { if (fail) throw new Error("offline"); };
    const renderingStates = [];
    h.context.renderCompanies = () => renderingStates.push(h.state.sending);
    await h.context.sendMessage();
    assert.equal(h.state.sending, false);
    assert.equal(renderingStates.at(-1), false);
    assert.equal(h.state.lastError, fail ? "offline" : "");
  });
}

test("active empty conversation remains visible above long populated history", () => {
  const h = sessionHarness();
  h.add("session-search", "");
  const list = h.add("session-list");
  h.add("toggle-test-sessions");
  const updated_at = new Date().toISOString();
  h.state.sessions = Array.from({ length: 65 }, (_, i) => ({ session_id: `old-${i}`, message_count: 2, updated_at }));
  h.state.sessions.push({ session_id: "new", title: "新对话", message_count: 0, updated_at });
  h.state.currentSessionId = "new";
  h.context.renderSessions();
  assert.match(list.innerHTML, /session-item active.*data-session-id="new"/);
  assert.ok(list.innerHTML.indexOf('data-session-id="new"') < list.innerHTML.indexOf('data-session-id="old-0"'));
});

test("double click creates one session and a failed creation leaves history intact", async () => {
  let resolve;
  const h = sessionHarness(() => new Promise((r) => { resolve = r; }));
  const pending = h.context.createSession();
  await h.context.createSession();
  assert.equal(h.calls.length, 1);
  assert.equal(h.state.sessionCreating, true);
  resolve({ status: 503, data: { detail: "temporarily unavailable" } });
  await pending;
  assert.equal(h.state.currentSessionId, "old");
  assert.equal(h.state.messages[0].content, "Previous answer");
  assert.equal(h.state.trace.run_id, "old-run");
  assert.equal(h.state.sessionCreating, false);
  assert.match(h.context.lastToast, /新建对话失败/);
});

test("late session creation cannot steal navigation to another workspace", async () => {
  let resolve;
  const h = sessionHarness(() => new Promise((r) => { resolve = r; }));
  const pending = h.context.createSession();
  h.state.companyId = "other_space";
  h.state.currentSessionId = "other_chat";
  resolve({ session_id: "new", message_count: 0 });
  await pending;
  assert.equal(h.state.currentSessionId, "other_chat");
  assert.equal(h.state.companyId, "other_space");
  assert.equal(h.state.sessionCreating, false);
});

test("late history refresh does not leak sessions across workspaces", async () => {
  let resolve;
  const h = sessionHarness(() => new Promise((r) => { resolve = r; }));
  const pending = h.context.loadSessions();
  h.state.companyId = "other_space";
  h.state.sessions = [{ session_id: "other_chat", message_count: 1 }];
  h.state.currentSessionId = "other_chat";
  resolve({ sessions: [{ session_id: "old", message_count: 2 }] });
  await pending;
  assert.equal(h.state.sessions[0].session_id, "other_chat");
  assert.equal(h.state.currentSessionId, "other_chat");
});

test("selected workspace survives a page reload without storing profile contents", () => {
  const values = new Map();
  const storage = { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) };
  const first = harness(undefined, storage);
  first.context.rememberWorkspace("accept_personal");
  const reloaded = harness(undefined, storage);
  assert.equal(reloaded.state.companyId, "accept_personal");
  assert.deepEqual([...values], [["workbench.selectedWorkspace", "accept_personal"]]);
});

test("unavailable browser storage does not break workspace initialization", () => {
  const denied = { getItem() { throw new Error("Denied"); }, setItem() { throw new Error("Denied"); } };
  const h = harness(undefined, denied);
  assert.equal(h.state.companyId, "company_001");
  assert.doesNotThrow(() => h.context.rememberWorkspace("accept_personal"));
});
const submitEvent = () => ({ preventDefault() {} });
const knownFact = (id = "profile.goal", value = "Agent learning") => ({
  fact_id: id, value, importance: 3, confidence: 0, source: "user_confirmed", policy_relevance: [], note: "",
});

function emptyWiki(companyId = "test_space") {
  return {
    company_id: companyId, profile: { company_id: companyId, company_name: "Test", profile_type: "personal" },
    pages: [
      { path: "wiki/00_background.md", title: "\u80cc\u666f", facts: [] },
      { path: "wiki/01_goals.md", title: "\u76ee\u6807", facts: [] },
      { path: "wiki/02_constraints.md", title: "\u7ea6\u675f", facts: [] },
    ], facts: [], stats: { fact_count: 0, page_count: 3 },
  };
}

function workspaceForm(h) {
  for (const id of ["workspace-create-form", "workspace-identity-fields", "workspace-dialog", "workspace-create-error", "workspace-create-status", "workspace-create-submit"]) h.add(id);
  h.add("workspace-id", "test_space");
  h.add("workspace-name", "Test");
  h.add("workspace-profile-type", "personal");
  return Object.fromEntries(["background", "goals", "constraints"].map((key) => [key, h.add(`workspace-${key}`)]));
}

function pageForm(h, title = "Research") {
  for (const id of ["wiki-page-create-form", "wiki-page-dialog", "wiki-page-create-error", "wiki-page-create-submit"]) h.add(id);
  h.add("wiki-page-title", title);
  h.add("wiki-page-description", "");
  h.state.companyId = "test_space";
  h.state.pageCreationCompanyId = "test_space";
  h.state.wiki = emptyWiki();
}

test("native forms and Chinese workspace entry are present", () => {
  for (const id of ["workspace-dialog", "wiki-page-dialog"]) assert.match(html, new RegExp(`<dialog id="${id}"`));
  for (const id of ["workspace-create-form", "wiki-page-create-form"]) assert.match(html, new RegExp(`<form id="${id}"`));
  assert.match(html, /name="profile_type"/);
  assert.match(html, /value="company"/);
  assert.match(html, /value="personal"/);
  assert.ok(html.includes("\u5de5\u4f5c\u7a7a\u95f4"));
  assert.doesNotMatch(html, /<span>\u4f01\u4e1a\u7a7a\u95f4<\/span>/);
  for (const [, pattern] of html.matchAll(/pattern="([^"]+)"/g)) assert.doesNotThrow(() => new RegExp(pattern, "v"));
});

test("duplicate company option names append IDs without dropping evidence", () => {
  const h = harness();
  h.state.companies = [
    { company_id: "baseline", company_name: "Persona", short_name: "Persona" },
    { company_id: "round2", company_name: "Persona", short_name: "Persona" },
    { company_id: "final", company_name: "Persona", short_name: "Persona" },
    { company_id: "unique", company_name: "Other", short_name: "Other" },
  ];
  const labels = h.state.companies.map(h.context.companyOptionLabel);
  assert.equal(new Set(labels).size, 4);
  assert.equal(labels[0], "Persona \u00b7 baseline");
  assert.equal(labels[2], "Persona \u00b7 final");
  assert.equal(labels[3], "Other");
  assert.equal(h.state.companies.length, 4);
});

test("workspace selector defaults to formal workspaces and hides acceptance fixtures", () => {
  const h = harness();
  h.state.showTestSessions = false;
  h.state.companyId = "accept_20260909_baseline_personal";
  h.state.companies = [
    { company_id: "accept_20260909_baseline_personal", company_name: "个人 AI 工程师（验收画像）" },
    { company_id: "company_001", company_name: "同花顺", short_name: "同花顺", stock_code: "300033.SZ" },
    { company_id: "agent_self_evolution", company_name: "Agent 自进化学习与验证", short_name: "Agent 自进化" },
    { company_id: "shared_discovery_release_20260910_personal", company_name: "Agent 自我进化学习与验证（验收画像）" },
    { company_id: "company_test", company_name: "company_test" },
  ].map(h.context.normalizeCompany);

  assert.deepEqual(
    h.context.visibleWorkspaceCompanies().map((company) => company.company_id),
    ["company_001", "agent_self_evolution"],
  );
  assert.equal(h.context.preferredWorkspaceId(), "company_001");

  h.state.showTestSessions = true;
  assert.equal(h.context.visibleWorkspaceCompanies().length, 5);
});

test("empty company list never invents company_001 or creates a session", async () => {
  const h = harness(async () => ({ companies: [] }));
  await h.context.loadBootstrap();
  assert.equal(h.state.companyId, "");
  assert.equal(h.state.currentView, "wiki");
  assert.deepEqual(h.calls.map((call) => call.path), ["/companies"]);
});

test("empty page serialization does not generate a FACT placeholder", () => {
  const h = harness();
  const body = h.context.assembleStructuredWikiBody("# Background\n\nPending input", [], "wiki/a.md", emptyWiki());
  assert.equal(body, "# Background\n\nPending input");
  assert.doesNotMatch(body, /## FACT:/);
});

test("fact validation catches cross-page and same-page duplicate IDs but permits editing own IDs", () => {
  const h = harness();
  const wiki = { pages: [{ path: "wiki/a.md", facts: [knownFact()] }] };
  assert.throws(() => h.context.assembleStructuredWikiBody("", [knownFact()], "wiki/b.md", wiki), /ID/);
  assert.throws(() => h.context.assembleStructuredWikiBody("", [knownFact(), knownFact()], "wiki/a.md", wiki), /ID/);
  assert.match(h.context.assembleStructuredWikiBody("", [knownFact()], "wiki/a.md", wiki), /## FACT: profile.goal/);
});

test("fact values are quoted and preserve zero confidence without hidden FACT injection", () => {
  const h = harness();
  const fact = knownFact("safe.id", 'nan\n## FACT: hidden\n- value: "quote"');
  const body = h.context.assembleStructuredWikiBody("", [fact], "wiki/a.md", emptyWiki());
  assert.equal(body.match(/^## FACT:/gm).length, 1);
  assert.match(body, /- confidence: 0\n/);
  assert.ok(body.includes(`- value: ${JSON.stringify(fact.value)}`));
  assert.throws(() => h.context.assembleStructuredWikiBody("## FACT: hidden", [], "wiki/a.md"));
  assert.throws(() => h.context.assembleStructuredWikiBody("", [{ ...fact, note: "## FACT: hidden" }], "wiki/a.md"));
  assert.throws(() => h.context.assembleStructuredWikiBody("", [{ ...fact, note: "- value: old value" }], "wiki/a.md"));
});

test("new fact IDs avoid saved facts and unsaved form IDs", () => {
  const h = harness();
  const page = { path: "wiki/page.md" };
  const prefix = h.context.wikiFactIdPrefix(page);
  h.state.wiki = { facts: [{ fact_id: `${prefix}.fact_1` }] };
  h.context.document.querySelectorAll = () => [{ value: `${prefix}.fact_2` }];
  assert.equal(h.context.defaultWikiFact(page).fact_id, `${prefix}.fact_3`);
});

test("invalid page names never become filesystem paths", () => {
  const h = harness();
  for (const title of ["", "../outside", "C:\\escape", "NUL", "bad.", "a:b"]) assert.throws(() => h.context.wikiPagePathFromTitle(title));
  assert.equal(h.context.wikiPagePathFromTitle("\u5b66\u4e60\u8ba1\u5212"), "wiki/\u5b66\u4e60\u8ba1\u5212.md");
});

test("blank workspace sections only call POST /companies, preserving zero facts", async () => {
  const h = harness(async (call) => call.method === "POST"
    ? { company_id: "returned_id", wiki: emptyWiki("returned_id") }
    : { companies: [{ company_id: "returned_id", company_name: "Server name" }] });
  workspaceForm(h);
  let selected;
  h.context.switchWorkspace = async (id) => { selected = id; };
  await h.context.submitWorkspaceCreation(submitEvent());
  assert.equal(selected, "returned_id");
  assert.equal(h.state.companies[0].company_name, "Server name");
  const writes = h.calls.filter((call) => call.method === "POST");
  assert.equal(writes.length, 1);
  assert.equal(writes[0].path, "/companies");
  assert.equal(writes[0].body.profile_type, "personal");
  assert.equal(h.state.workspaceCreation, null);
});

test("partial workspace initialization retries only unfinished API steps", async () => {
  let failed = false;
  const wiki = emptyWiki();
  const h = harness(async (call) => {
    if (call.method === "GET") return { companies: [{ company_id: "test_space", company_name: "Test" }] };
    if (call.path === "/companies") return { company_id: "test_space", wiki };
    if (call.body.path.endsWith("01_goals.md") && !failed) {
      failed = true;
      return { status: 409, data: { detail: "test conflict" } };
    }
    return { wiki };
  });
  const sections = workspaceForm(h);
  sections.background.value = "Verified background";
  sections.goals.value = "Learning goal";
  h.context.switchWorkspace = async () => {};
  await h.context.submitWorkspaceCreation(submitEvent());
  assert.ok(h.state.workspaceCreation.saved.has("background"));
  assert.ok(!h.state.workspaceCreation.saved.has("goals"));
  assert.equal(sections.goals.value, "Learning goal");
  assert.equal(h.state.workspaceBusy, false);
  await h.context.submitWorkspaceCreation(submitEvent());
  assert.equal(h.calls.filter((call) => call.path === "/companies" && call.method === "POST").length, 1);
  assert.equal(h.calls.filter((call) => call.body?.path === "wiki/00_background.md").length, 1);
  assert.equal(h.calls.filter((call) => call.body?.path === "wiki/01_goals.md").length, 2);
  assert.equal(h.calls.filter((call) => call.body?.path === "wiki/02_constraints.md").length, 0);
  assert.equal(h.state.workspaceCreation, null);
});

test("page form submits public create API with no placeholder facts", async () => {
  const h = harness(async (call) => call.method === "POST"
    ? { page: { path: "wiki/Research.md" }, wiki: emptyWiki() }
    : { companies: [{ company_id: "test_space", company_name: "Test" }] });
  pageForm(h);
  await h.context.submitWikiPageCreation(submitEvent());
  const write = h.calls.find((call) => call.method === "POST");
  assert.equal(write.path, "/companies/test_space/workbench/wiki/pages");
  assert.equal(write.body.create, true);
  assert.equal(write.body.body, "# Research");
  assert.equal(h.state.selectedWikiPath, "wiki/Research.md");
});

test("failed new page request retains form fields", async () => {
  const h = harness(async () => ({ status: 409, data: { detail: "duplicate fact_id" } }));
  pageForm(h);
  const description = h.add("wiki-page-description", "User draft");
  await h.context.submitWikiPageCreation(submitEvent());
  assert.equal(description.value, "User draft");
  assert.equal(h.state.wikiAction, "");
  assert.match(h.context.document.getElementById("wiki-page-create-error").textContent, /duplicate fact_id/);
});

test("page metadata/name conflict is rejected before fetch", async () => {
  const h = harness();
  pageForm(h, "00_background");
  await h.context.submitWikiPageCreation(submitEvent());
  assert.equal(h.calls.length, 0);
});

test("search uses public GET API and keeps source/confidence distinct from score", async () => {
  const h = harness(async () => ({ results: [{
    fact: { ...knownFact("x.id", "<script>not markup</script>"), source_kind: "user_confirmed", source_path: "wiki/source.md" },
    retrieval_score: 7.25, matched_term_count: 2,
  }] }));
  h.state.companyId = "test_space";
  h.add("wiki-search-query", "AI & RAG");
  const container = h.add("wiki-search-results");
  await h.context.searchWikiFacts();
  const url = new URL(h.calls[0].path, "http://localhost");
  assert.equal(url.pathname, "/companies/test_space/workbench/wiki/search");
  assert.equal(url.searchParams.get("query"), "AI & RAG");
  assert.equal(h.calls[0].method, "GET");
  assert.match(container.innerHTML, /&lt;script&gt;/);
  assert.doesNotMatch(container.innerHTML, /<script>/);
  assert.ok(container.innerHTML.includes("\u7f6e\u4fe1\u5ea6"));
  assert.ok(container.innerHTML.includes("\u68c0\u7d22\u5206"));
  assert.match(container.innerHTML, /7\.25/);
  assert.match(container.innerHTML, /wiki\/source.md/);
});

test("late search responses cannot overwrite a newer query", async () => {
  const pending = [];
  const h = harness(() => new Promise((resolve) => pending.push(resolve)));
  const query = h.add("wiki-search-query", "first");
  const first = h.context.searchWikiFacts();
  query.value = "second";
  const second = h.context.searchWikiFacts();
  pending[1]({ results: [{ fact: knownFact("second") }] });
  await second;
  pending[0]({ results: [{ fact: knownFact("first") }] });
  await first;
  assert.equal(h.state.wikiSearchResults[0].fact.fact_id, "second");
});

test("clearing search or switching company invalidates pending results", async () => {
  let resolve;
  const h = harness(() => new Promise((done) => { resolve = done; }));
  h.add("wiki-search-query", "first");
  const request = h.context.searchWikiFacts();
  h.context.clearWikiSearch();
  h.state.companyId = "another_space";
  resolve({ results: [{ fact: knownFact() }] });
  await request;
  assert.equal(h.state.wikiSearchResults, null);
  assert.equal(h.state.wikiSearchBusy, false);
});

test("editor drafts persist per workspace and preserve an empty fact list", () => {
  const h = harness();
  h.state.companyId = "personal";
  h.state.selectedWikiPath = "wiki/background.md";
  const form = h.add("wiki-editor-form");
  form.dataset.savedSnapshot = JSON.stringify({ intro: "Original", facts: [] });
  const intro = h.add("wiki-intro-editor", "Edited background");
  h.add("wiki-save-status");
  h.context.captureWikiDraft();
  assert.equal(h.state.wikiDrafts["personal:wiki/background.md"].intro, "Edited background");
  assert.deepEqual(json(h.state.wikiDrafts["personal:wiki/background.md"].facts), []);
  intro.value = "Original";
  h.context.captureWikiDraft();
  assert.equal(h.state.wikiDrafts["personal:wiki/background.md"], undefined);
});

test("raw fact metadata never becomes an editable note that overrides updated values", () => {
  const h = harness();
  const loaded = { ...knownFact(), note: undefined, text: '- value: "Old value"\n- source: user_confirmed\n- importance: 3' };
  assert.equal(h.context.wikiFactNote(loaded), "");
  const body = h.context.assembleStructuredWikiBody("", [{ ...loaded, value: "New value", note: h.context.wikiFactNote(loaded) }], "wiki/a.md", emptyWiki());
  assert.equal(body.match(/^- value:/gm).length, 1);
  assert.doesNotMatch(body, /Old value/);
  assert.equal(h.context.wikiFactNote({ text: "A verified explanatory note", value: "Value" }), "A verified explanatory note");
});

test("workspace creation is unavailable while an SSE turn is running", () => {
  const h = harness();
  h.state.sending = true;
  h.context.openWorkspaceDialog();
  assert.equal(h.calls.length, 0);
  assert.ok(h.context.lastToast);
});

test("new page fact fields serialize user input and reject IDs from other pages", async () => {
  const h = harness(async (call) => call.method === "POST"
    ? { page: { path: "wiki/Research.md" }, wiki: emptyWiki() }
    : { companies: [{ company_id: "test_space", company_name: "Test" }] });
  pageForm(h);
  const values = {
    ".wiki-fact-id-input": field("existing.id"),
    ".wiki-fact-value-input": field("Confirmed content"),
    ".wiki-fact-importance-input": field("3"),
    ".wiki-fact-confidence-input": field("0"),
    ".wiki-fact-source-input": field("user_confirmed"),
    ".wiki-fact-relevance-input": field("AI, RAG"),
    ".wiki-fact-note-input": field("User note"),
  };
  const form = h.context.document.getElementById("wiki-page-create-form");
  form.querySelectorAll = (selector) => selector === ".wiki-fact-editor" ? [{ querySelector: (key) => values[key] }] : [];
  h.state.wiki.pages[0].facts = [knownFact("existing.id")];
  await h.context.submitWikiPageCreation(submitEvent());
  assert.equal(h.calls.length, 0);
  values[".wiki-fact-id-input"].value = "new.id";
  await h.context.submitWikiPageCreation(submitEvent());
  const request = h.calls.find((call) => call.method === "POST");
  assert.match(request.body.body, /## FACT: new.id/);
  assert.match(request.body.body, /- confidence: 0/);
  assert.match(request.body.body, /Confirmed content/);
  assert.match(request.body.body, /\["AI", "RAG"\]/);
});

test("rendered fact ID pattern uses valid native HTML pattern syntax", () => {
  const h = harness();
  const markup = h.context.renderWikiFactEditor(knownFact(), 0);
  for (const [, pattern] of markup.matchAll(/pattern="([^"]+)"/g)) assert.doesNotThrow(() => new RegExp(pattern, "v"));
});

test("Chinese page paths produce distinct stable default fact ID prefixes", () => {
  const h = harness();
  const first = { path: "wiki/\u5b66\u4e60\u5b89\u6392.md" };
  const second = { path: "wiki/\u5b66\u4e60\u590d\u76d8.md" };
  const firstId = h.context.defaultWikiFact(first).fact_id;
  const secondId = h.context.defaultWikiFact(second).fact_id;
  assert.notEqual(firstId, secondId);
  assert.equal(firstId, h.context.defaultWikiFact(first).fact_id);
  assert.match(firstId, /^page_[a-f0-9]{8}\.fact_1$/);
  h.state.wiki = { pages: [{ path: first.path, facts: [{ fact_id: firstId }] }] };
  assert.equal(h.context.defaultWikiFact(first).fact_id, firstId.replace("fact_1", "fact_2"));
});

test("new page fact IDs are based on the actual form title", () => {
  const h = harness();
  const title = "\u5b66\u4e60\u5b89\u6392";
  pageForm(h, title);
  const list = h.add("wiki-page-facts");
  list.insertAdjacentHTML = (_position, content) => { list.innerHTML += content; };
  h.context.addNewPageFact();
  const prefix = h.context.wikiFactIdPrefix({ path: `wiki/${title}.md` });
  assert.ok(list.innerHTML.includes(`${prefix}.fact_1`));
  assert.doesNotMatch(list.innerHTML, /value="page.fact_1"/);
});

test("Wiki header is neutral for personal and company workspaces", () => {
  const h = harness();
  h.state.currentView = "wiki";
  const title = h.add("session-title");
  const subtitle = h.add("session-meta");
  h.context.renderHeader();
  assert.equal(title.textContent, "\u5de5\u4f5c\u7a7a\u95f4 Wiki");
  assert.equal(subtitle.textContent, "\u5f53\u524d\u753b\u50cf\u4e0e\u8d44\u6599");
});

function renderWikiStatistics(h, path) {
  h.state.selectedWikiPath = path;
  const view = h.add("wiki-view");
  h.add("wiki-editor-form");
  h.add("wiki-intro-editor");
  h.renderWiki();
  return view.innerHTML.match(/<div class="wiki-stat-row">([\s\S]*?)<\/div>/)[1];
}

test("empty background page shows zero page facts even when the workspace has five important facts", () => {
  const h = harness();
  const facts = Array.from({ length: 5 }, (_, index) => ({ ...knownFact("fact-" + index), importance: 5 }));
  h.state.wiki = {
    ...emptyWiki(), facts, stats: { fact_count: 5, high_importance_fact_count: 5 },
    pages: [
      { path: "wiki/background.md", title: "Background", facts: [], fact_count: 0 },
      { path: "wiki/profile.md", title: "Profile", facts, fact_count: 5 },
    ],
  };
  const before = JSON.stringify(h.state.wiki);
  const background = renderWikiStatistics(h, "wiki/background.md");
  assert.match(background, /\u672c\u9875 0 \u6761\u4e8b\u5b9e/);
  assert.match(background, /\u672c\u9875 0 \u6761\u91cd\u70b9/);
  assert.doesNotMatch(background, /5 \u6761\u4e8b\u5b9e|5 \u6761\u91cd\u70b9/);
  const profile = renderWikiStatistics(h, "wiki/profile.md");
  assert.match(profile, /\u672c\u9875 5 \u6761\u4e8b\u5b9e/);
  assert.match(profile, /\u672c\u9875 5 \u6761\u91cd\u70b9/);
  assert.equal(JSON.stringify(h.state.wiki), before);
});

test("page statistics use saved page facts and importance >= 4, not stale counts or unsaved drafts", () => {
  const h = harness();
  h.state.wiki = {
    ...emptyWiki(), facts: [knownFact()], stats: { fact_count: 99, high_importance_fact_count: 99 },
    pages: [{ path: "wiki/mixed.md", title: "Mixed", fact_count: 99, facts: [
      { ...knownFact("low"), importance: 3 },
      { ...knownFact("high"), importance: 4 },
      { ...knownFact("highest"), importance: "5" },
      { ...knownFact("unknown"), importance: undefined },
    ] }],
  };
  h.state.wikiDrafts[h.context.wikiDraftKey(h.state.companyId, "wiki/mixed.md")] = { intro: "Draft", facts: [] };
  const stats = renderWikiStatistics(h, "wiki/mixed.md");
  assert.match(stats, /\u672c\u9875 4 \u6761\u4e8b\u5b9e/);
  assert.match(stats, /\u672c\u9875 2 \u6761\u91cd\u70b9/);
  assert.match(stats, /title="\u672c\u9875\u5df2\u4fdd\u5b58\u7684\u4e8b\u5b9e"/);
  assert.match(stats, /\u672a\u4fdd\u5b58/);
  assert.doesNotMatch(stats, /99 \u6761/);
});
