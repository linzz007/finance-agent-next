import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../src/policy_impact/app/static/workbench/app.js", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/policy_impact/app/static/workbench/styles.css", import.meta.url), "utf8");
const json = (value) => JSON.parse(JSON.stringify(value));
const profilePath = "wiki/finance_profile.md";
const supportedSymbols = ["xauusd", "xagusd", "spx", "ixic", "dji", "sh000001", "sh000300", "sz399006", "sz399001", "sh000016", "sh000688"];
const profile = () => ({
  domain: "finance", version: 2, status: "draft", display_name: "金融观察", purpose: "学习",
  markets: ["中国宏观", "A股"], symbols: ["sh000001", "sh000300", "sz399006"],
  focus_topics: ["宏观数据"], report_days: 7, timezone: "Asia/Shanghai", constraints: ["不交易"],
  holdings: null, language: "zh-CN", cadence: ["daily", "weekly_review"], preference_basis: "unconfirmed",
});
const wiki = () => ({ pages: [
  { path: profilePath, title: "Finance profile", facts: [], body: "# Raw profile" },
  { path: "wiki/notes.md", title: "研究笔记", facts: [], body: "# Notes" },
], profile: {}, stats: {} });
const status = () => ({
  schema_version: "finance.collection_status.v1", enabled: true, worker_running: false, collecting: false,
  status: "partial", last_status: "partial", interval_minutes: 60, consecutive_failures: 0,
  last_started_at: "2026-09-22T10:00:00+00:00", last_finished_at: "2026-09-22T10:00:09+00:00",
  last_success_at: null, next_run_at: "2026-09-22T11:00:00+00:00", last_error: "Missing source",
  collected_at: "2026-09-22T10:00:08+00:00", source_statuses: [
    { source_id: "index_a", status: "ok", count: 3, latest_date: "2026-09-22", elapsed_ms: 0, detail: "" },
    { source_id: "macro_b", status: "error", count: 0, latest_date: "", elapsed_ms: 20, detail: "Missing document" },
  ], warnings: ["Incomplete coverage"], snapshot_projection_truncated: false,
});

function element(value = "") {
  return {
    value, checked: false, disabled: false, innerHTML: "", textContent: "", dataset: {}, listeners: {},
    classList: { toggle() {}, add() {}, remove() {} },
    addEventListener(event, callback) { this.listeners[event] = callback; },
    querySelectorAll() { return []; }, querySelector() { return null; }, setAttribute() {},
    reportValidity() { return true; }, focus() {}, scrollIntoView() {},
  };
}

function harness(responder = async ({ path }) => path.endsWith("/finance/profile") ? { profile: profile() }
  : path.endsWith("/finance/status") ? { status: status() } : wiki()) {
  const elements = new Map();
  const calls = [];
  const context = vm.createContext({
    document: { getElementById: (id) => elements.get(id) || null, querySelectorAll: () => [] },
    window: { confirm: () => true }, URLSearchParams, setTimeout: () => 1, clearTimeout() {},
    fetch: async (path, options = {}) => {
      const call = { path, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null };
      calls.push(call);
      const data = await responder(call);
      return { ok: !data.httpError, statusText: "Error", text: async () => JSON.stringify(data) };
    },
  });
  vm.runInContext(source.replace(/\ninit\(\);\s*$/, ""), context);
  vm.runInContext("globalThis.uiState = state", context);
  const renderWiki = context.renderWiki;
  context.renderWiki = () => {};
  context.renderCompanies = () => {};
  context.renderAll = () => {};
  context.toast = (message) => { context.lastToast = message; };
  const state = context.uiState;
  state.companyId = "finance_space";
  state.currentView = "wiki";
  const add = (id, value = "") => { const node = element(value); elements.set(id, node); return node; };
  add("wiki-view");
  return { context, state, calls, elements, add, renderWiki };
}

function form(h, data = profile()) {
  h.state.finance = { companyId: h.state.companyId, profile: data, status: status(), profileError: "", statusError: "", saveError: "", actionMessage: "" };
  h.state.wiki = wiki();
  h.state.selectedWikiPath = profilePath;
  for (const id of ["finance-profile-form", "finance-save-state", "finance-save-error", "finance-collect", "finance-enabled", "finance-status-refresh", "finance-profile-reload"]) h.add(id);
  for (const [key, value] of Object.entries(h.context.financeFields(data))) {
    if (key !== "symbols") h.add(`finance-${key}`, value);
  }
  for (const symbol of supportedSymbols) h.add(`finance-${symbol}`).checked = data.symbols.includes(symbol);
}

test("loadWiki asynchronously discovers finance via the profile endpoint without writes", async () => {
  const h = harness();
  await h.context.loadWiki();
  assert.deepEqual(json(h.state.finance.profile), profile());
  assert.deepEqual(json(h.state.finance.status), status());
  assert.equal(h.calls.length, 3);
  assert.ok(h.calls.every((call) => call.method === "GET"));
  h.renderWiki();
  const html = h.elements.get("wiki-view").innerHTML;
  assert.match(html, /草稿 · 待确认/);
  assert.match(html, /id="finance-profile-form"/);
  assert.match(html, /id="wiki-search-form"/);
  assert.match(html, /data-wiki-path="wiki\/notes.md"/);
  assert.equal((html.match(/data-wiki-path="wiki\/finance_profile.md"/g) || []).length, 1);
  assert.doesNotMatch(html, /id="wiki-editor-form"|id="wiki-save"|Raw profile/);
});

test("finance works even before a Markdown projection exists and with empty Wiki pages", async () => {
  const h = harness(async ({ path }) => path.endsWith("/finance/profile") ? { profile: profile() }
    : path.endsWith("/finance/status") ? { status: status() } : { pages: [] });
  await h.context.loadWiki();
  h.renderWiki();
  assert.equal(h.state.selectedWikiPath, profilePath);
  assert.match(h.elements.get("wiki-view").innerHTML, /id="finance-profile-form"/);
});

test("null profile leaves company and personal Wikis unchanged and never requests finance status", async () => {
  for (const companyId of ["company_001", "agent_self_evolution"]) {
    const h = harness(async ({ path }) => path.endsWith("/finance/profile") ? { profile: null }
      : { pages: [wiki().pages[1]], profile: {}, stats: {} });
    h.state.companyId = companyId;
    await h.context.loadWiki();
    h.add("wiki-editor-form");
    h.add("wiki-intro-editor");
    h.renderWiki();
    assert.equal(h.state.finance.profile, null);
    assert.equal(h.calls.length, 2);
    assert.match(h.elements.get("wiki-view").innerHTML, /id="wiki-editor-form"/);
    assert.doesNotMatch(h.elements.get("wiki-view").innerHTML, /finance-profile-form/);
  }
});

test("missing or failed finance profile cannot expose a second Markdown editor", async () => {
  for (const response of [{ profile: null }, { httpError: true, detail: "Unavailable" }]) {
    const h = harness(async ({ path }) => path.endsWith("/finance/profile") ? response : wiki());
    await h.context.loadWiki();
    h.renderWiki();
    const html = h.elements.get("wiki-view").innerHTML;
    assert.doesNotMatch(html, /id="wiki-editor-form"|id="finance-profile-form"/);
    assert.match(html, /重新读取/);
    await h.context.saveSelectedWikiPage();
    assert.ok(h.calls.every((call) => call.method === "GET"));
  }
});

test("all fields have explicit Chinese labels, safe values and a draft default", () => {
  const h = harness();
  const data = profile();
  delete data.status;
  data.display_name = '<img src=x onerror="alert(1)">';
  data.purpose = '" autofocus onfocus="alert(1)';
  form(h, data);
  const html = h.context.renderFinanceWiki();
  assert.match(html, /草稿 · 待确认/);
  assert.match(html, /value="draft" selected/);
  assert.doesNotMatch(html, /<img|value="" autofocus/);
  for (const key of ["display_name", "purpose", "status", "markets", "focus_topics", "report_days", "constraints", "timezone", "holdings", "sh000001", "sh000300", "sz399006"]) {
    assert.ok(html.includes(`for="finance-${key}"`), key);
    assert.ok(html.includes(`id="finance-${key}"`), key);
  }
  assert.match(html, /id="finance-holdings" value="未提供" readonly/);
  assert.match(css, /@media \(max-width: 600px\)/);
  assert.match(css, /@container \(max-width: 520px\)/);
  assert.match(css, /\.finance-fields, \.finance-status-grid \{ grid-template-columns: minmax\(0, 1fr\)/);
});

test("imported historical holdings are not displayed as current positions", () => {
  const h = harness();
  const data = { ...profile(), holdings_history_path: "wiki/finance_holdings_history.md" };
  form(h, data);
  const html = h.context.renderFinanceWiki();
  assert.match(html, /当前未确认；历史记录见「历史持仓」/);
  assert.match(html, /行情观察标的（贵金属采用国际现货参考基准）/);
  assert.equal(h.context.financeProfilePayload(data, h.context.financeFields(data)).holdings, null);
});

test("all eleven benchmarks are selectable with spot units and legacy selections unchanged", () => {
  const h = harness();
  form(h);
  const html = h.context.renderFinanceWiki();
  const checkboxes = [...html.matchAll(/<input id="finance-([a-z0-9]+)" type="checkbox" name="symbols"[^>]*>/g)];
  assert.deepEqual(checkboxes.map((match) => match[1]), supportedSymbols);
  assert.deepEqual(checkboxes.filter((match) => / checked /.test(match[0])).map((match) => match[1]), profile().symbols);
  assert.deepEqual(json(h.context.readFinanceFields().symbols), profile().symbols);
  for (const [symbol, name] of [["xauusd", "国际现货黄金"], ["xagusd", "国际现货白银"]]) {
    assert.ok(html.includes(`<span>${name}<small>${symbol} · USD/troy oz（美元/金衡盎司）</small>`));
  }
  for (const symbol of supportedSymbols.slice(2)) assert.ok(html.includes(`${symbol} · 点 · 价格指数`));
  assert.doesNotMatch(html, /当前仅A股|期货|ETF/);
  assert.equal(h.calls.length, 0);
});

test("global selections save only on explicit submit without inferring holdings or rewriting history", async () => {
  const h = harness(async (call) => ({ profile: { ...call.body.profile, version: 3 } }));
  const data = { ...profile(), holdings_history_path: "wiki/private-position-history.md",
    holdings_history: [{ symbol: "PRIVATE_HISTORICAL_ASSET", quantity: 987654321 }] };
  form(h, data);
  for (const symbol of supportedSymbols) h.elements.get(`finance-${symbol}`).checked = supportedSymbols.slice(0, 5).includes(symbol);
  h.context.captureFinanceDraft();
  assert.equal(h.calls.length, 0);
  const html = h.context.renderFinanceWiki();
  assert.doesNotMatch(html, /PRIVATE_HISTORICAL_ASSET|987654321|private-position-history/);
  assert.match(html, /当前未确认/);
  await h.context.saveFinanceProfile();
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.calls[0].body.profile.symbols, supportedSymbols.slice(0, 5));
  assert.equal(h.calls[0].body.profile.holdings, null);
  assert.deepEqual(h.calls[0].body.profile.holdings_history, data.holdings_history);
  assert.equal(h.calls[0].body.expected_version, 2);
});

test("additional legacy benchmarks round-trip and unsupported benchmarks still fail closed", () => {
  const h = harness();
  const data = { ...profile(), symbols: supportedSymbols.slice(5) };
  form(h, data);
  assert.deepEqual(json(h.context.financeProfilePayload(data, h.context.readFinanceFields()).symbols), data.symbols);
  for (const symbol of ["GC=F", "SI=F", "sh600000", "unknown"]) {
    assert.throws(() => h.context.financeProfilePayload(data, { ...h.context.financeFields(data), symbols: [symbol] }), /行情标的/);
  }
  assert.throws(() => h.context.financeProfilePayload(data, { ...h.context.financeFields(data), symbols: supportedSymbols }), /最多选择 8 项/);
  assert.equal(h.context.financeProfilePayload(data, { ...h.context.financeFields(data), symbols: supportedSymbols.slice(0, 8) }).symbols.length, 8);
  assert.equal(h.calls.length, 0);
});

test("editing observation fields never silently clears existing holdings", () => {
  const h = harness();
  const data = { ...profile(), holdings: [{ symbol: "EXAMPLE", quantity: 2 }] };
  const result = h.context.financeProfilePayload(data, h.context.financeFields(data));
  assert.deepEqual(json(result.holdings), data.holdings);
  form(h, data);
  assert.match(h.context.renderFinanceWiki(), /已记录 1 项；有效性以原记录为准/);
});

test("input only captures a local draft; explicit submit posts the complete profile and expected version", async () => {
  const h = harness(async (call) => ({ profile: { ...call.body.profile, version: 3 } }));
  form(h);
  h.context.bindFinanceWiki();
  h.elements.get("finance-display_name").value = "新档案";
  h.elements.get("finance-status").value = "confirmed";
  h.elements.get("finance-report_days").value = "14";
  h.elements.get("finance-markets").value = "宏观，A股\n宏观";
  h.state.wikiSearchResults = [{ fact: { value: "Old profile" } }];
  h.elements.get("finance-profile-form").listeners.input();
  assert.equal(h.calls.length, 0);
  assert.equal(h.elements.get("finance-save-state").textContent, "未保存");
  await h.context.saveFinanceProfile();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].method, "POST");
  assert.equal(h.calls[0].path, "/companies/finance_space/workbench/finance/profile");
  assert.deepEqual(h.calls[0].body, { profile: { ...profile(), display_name: "新档案", status: "confirmed", report_days: 14, markets: ["宏观", "A股"] }, expected_version: 2 });
  assert.equal(h.state.finance.profile.version, 3);
  assert.equal(h.state.financeDrafts.finance_space, undefined);
  assert.equal(h.state.wikiSearchResults, null);
  assert.match(h.context.renderFinanceWiki(), /finance-badge confirmed/);
});

test("invalid days, blank markets and no symbols fail before POST", async () => {
  for (const [key, value] of [["report_days", "0"], ["report_days", "1.5"], ["report_days", "366"], ["markets", " "], ["display_name", " "]]) {
    const h = harness();
    form(h);
    h.elements.get(`finance-${key}`).value = value;
    await h.context.saveFinanceProfile();
    assert.equal(h.calls.length, 0);
    assert.ok(h.elements.get("finance-save-error").textContent);
  }
  const h = harness();
  form(h);
  for (const symbol of profile().symbols) h.elements.get(`finance-${symbol}`).checked = false;
  await h.context.saveFinanceProfile();
  assert.equal(h.calls.length, 0);
  assert.match(h.elements.get("finance-save-error").textContent, /行情标的/);
});

test("save conflict retains raw user fields and the original expected version even after a fresh GET", async () => {
  const h = harness(async (call) => call.method === "POST" ? { httpError: true, detail: "version conflict" }
    : call.path.endsWith("/finance/profile") ? { profile: { ...profile(), version: 10 } }
      : call.path.endsWith("/finance/status") ? { status: status() } : wiki());
  form(h);
  h.elements.get("finance-constraints").value = "本地修改";
  h.context.captureFinanceDraft();
  await h.context.loadWiki();
  await h.context.saveFinanceProfile();
  const post = h.calls.find((call) => call.method === "POST");
  assert.equal(post.body.expected_version, 2);
  assert.equal(post.body.profile.version, 2);
  assert.equal(h.state.financeDrafts.finance_space.fields.constraints, "本地修改");
  assert.match(h.state.finance.saveError, /version conflict/);
  assert.equal(h.state.wikiAction, "");
});

test("status refresh, collect and schedule cannot save or erase profile drafts", async () => {
  for (const [action, body] of [["collect", {}], ["schedule", { enabled: false }], ["status", null]]) {
    const h = harness(async (call) => call.method === "POST" ? { status: "partial" } : { status: status() });
    form(h);
    h.elements.get("finance-purpose").value = "未保存目的";
    await h.context.runFinanceAction(action, false);
    assert.equal(h.state.financeDrafts.finance_space.fields.purpose, "未保存目的");
    assert.equal(h.state.finance.profile.purpose, "学习");
    assert.equal(h.calls.at(-1).path, "/companies/finance_space/workbench/finance/status");
    if (body) assert.deepEqual(h.calls[0], { path: `/companies/finance_space/workbench/finance/${action}`, method: "POST", body });
    assert.ok(h.calls.every((call) => !call.path.endsWith("/finance/profile")));
    assert.equal(h.state.wikiAction, "");
  }
});

test("enabled is not worker liveness and actual failure/zero values remain visible", () => {
  const h = harness();
  form(h);
  const html = h.context.renderFinanceStatus(h.state.finance);
  assert.match(html, /采集开关<\/dt><dd>已开启/);
  assert.match(html, /Worker 进程<\/dt><dd>未运行/);
  assert.match(html, /上次定时结果<\/dt><dd>部分数据/);
  assert.match(html, /连续失败<\/dt><dd>0/);
  assert.match(html, /快照采集时间<\/dt><dd>2026-09-22T10:00:08\+00:00/);
  assert.match(html, /index_a<\/strong><span>成功<\/span><span>3 条/);
  assert.match(html, /macro_b<\/strong><span>失败<\/span><span>0 条/);
  assert.match(html, /耗时<\/dt><dd>0 ms/);
  assert.match(html, /Incomplete coverage/);
  assert.doesNotMatch(html, /心跳|接口未提供/);
  assert.match(html, /Missing source/);
  assert.match(html, /2026-09-22T10:00:09\+00:00/);
  assert.doesNotMatch(html, />运行中</);
});

test("failed action rereads real state, failed status clears stale liveness and disables the toggle", async () => {
  const h = harness(async () => ({ httpError: true, detail: "offline" }));
  form(h);
  await h.context.runFinanceAction("schedule", false);
  assert.match(h.state.finance.actionMessage, /操作失败/);
  assert.equal(h.state.finance.status, null);
  const html = h.context.renderFinanceStatus(h.state.finance);
  assert.match(html, /id="finance-enabled"[^>]*disabled/);
  assert.match(html, /Worker 进程<\/dt><dd>未知/);
  assert.match(html, /采集状态读取失败/);
});

test("reload requires confirmation before dropping a financial draft", async () => {
  const h = harness();
  form(h);
  h.elements.get("finance-purpose").value = "保留";
  h.context.window.confirm = () => false;
  await h.context.reloadFinanceProfile();
  assert.equal(h.calls.length, 0);
  assert.equal(h.state.financeDrafts.finance_space.fields.purpose, "保留");
  h.context.window.confirm = () => true;
  await h.context.reloadFinanceProfile();
  assert.equal(h.state.financeDrafts.finance_space, undefined);
  assert.equal(h.state.finance.profile.status, "draft");
});

test("navigation and fact search preserve local financial edits", async () => {
  const h = harness();
  form(h);
  h.elements.get("finance-purpose").value = "本地目的";
  await h.context.openWikiSearchResult("wiki/notes.md", "");
  assert.equal(h.state.selectedWikiPath, "wiki/notes.md");
  assert.equal(h.state.financeDrafts.finance_space.fields.purpose, "本地目的");
  h.state.selectedWikiPath = profilePath;
  assert.match(h.context.renderFinanceWiki(), /value="本地目的"/);
  h.state.companyId = "company_001";
  assert.equal(h.context.currentFinance(), null);
  assert.doesNotMatch(h.context.renderFinanceWiki(), /本地目的/);
});

test("late finance responses cannot overwrite a different workspace or a newer same-space request", async () => {
  let release;
  const firstProfile = new Promise((resolve) => { release = resolve; });
  let count = 0;
  const h = harness(async ({ path }) => path.endsWith("/finance/profile")
    ? (++count === 1 ? firstProfile : { profile: null }) : path.endsWith("/finance/status") ? { status: status() } : wiki());
  const first = h.context.loadWiki();
  h.state.companyId = "company_001";
  await h.context.loadWiki();
  release({ profile: profile() });
  await first;
  assert.equal(h.state.finance.companyId, "company_001");
  assert.equal(h.state.finance.profile, null);
  count = 0;
  const pending = new Promise((resolve) => { release = resolve; });
  h.context.fetch = async (path) => ({ ok: true, text: async () => JSON.stringify(path.endsWith("/finance/profile")
    ? (++count === 1 ? await pending : { profile: null }) : { pages: [] }) });
  const older = h.context.loadWiki();
  await h.context.loadWiki();
  release({ profile: profile() });
  await older;
  assert.equal(h.state.finance.profile, null);
});

test("duplicate actions are blocked while a save is pending", async () => {
  let release;
  const response = new Promise((resolve) => { release = resolve; });
  const h = harness(() => response);
  form(h);
  const saving = h.context.saveFinanceProfile();
  await h.context.saveFinanceProfile();
  await h.context.runFinanceAction("collect");
  assert.equal(h.calls.length, 1);
  release({ profile: { ...profile(), version: 3 } });
  await saving;
  assert.equal(h.state.wikiAction, "");
});

test("collect preserves the backend outcome including deduplicated partial data", async () => {
  for (const [outcome, label] of [["ok", "成功"], ["partial", "部分数据"], ["error", "失败"], ["paused", "已暂停"], ["busy", "已有采集任务"], ["retry_wait", "等待重试"], ["deduplicated", "已复用本轮采集（部分数据）"]]) {
    const h = harness(async ({ method }) => method === "POST"
      ? { status: outcome, ...(outcome === "deduplicated" ? { collection_status: "partial" } : {}) }
      : { status: status() });
    form(h);
    await h.context.runFinanceAction("collect");
    assert.equal(h.state.finance.actionMessage, `采集请求已返回：${label}`);
  }
});

test("source errors, warnings and truncation are escaped and no latest snapshot is treated as success", () => {
  const h = harness();
  form(h);
  const s = h.state.finance.status;
  s.source_statuses[0].source_id = '<img src=x onerror="alert(1)">';
  s.source_statuses[0].detail = "<script>alert(1)</script>";
  s.warnings = ["<img src=x>"];
  s.snapshot_projection_truncated = true;
  const html = h.context.renderFinanceStatus(h.state.finance);
  assert.doesNotMatch(html, /<img|<script/);
  assert.match(html, /状态摘要已截断/);
  h.state.finance.status = { enabled: true, worker_running: false, status: "idle", source_statuses: [], warnings: [] };
  const empty = h.context.renderFinanceStatus(h.state.finance);
  assert.match(empty, /定时任务状态<\/dt><dd>尚无定时记录/);
  assert.match(empty, /暂无数据源记录/);
  assert.doesNotMatch(empty, />成功<|>运行中</);
});

test("ordinary Wiki saves in a finance workspace still use the Wiki endpoint", async () => {
  const h = harness(async () => ({ wiki: wiki(), page: wiki().pages[1] }));
  form(h);
  h.elements.delete("finance-profile-form");
  h.state.selectedWikiPath = "wiki/notes.md";
  h.add("wiki-editor-form");
  h.add("wiki-intro-editor", "# Edited notes");
  h.add("wiki-save-status");
  h.add("wiki-save");
  h.context.refreshWorkspaceOptions = async () => {};
  await h.context.saveSelectedWikiPage();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].path, "/companies/finance_space/workbench/wiki/pages");
  assert.deepEqual(h.calls[0].body, { path: "wiki/notes.md", body: "# Edited notes" });
  assert.deepEqual(json(h.state.finance.profile), profile());
});

test("status action handlers send checkbox booleans without triggering form submission", () => {
  const h = harness();
  form(h);
  const actions = [];
  h.context.runFinanceAction = (...args) => actions.push(args);
  h.context.bindFinanceWiki();
  h.elements.get("finance-enabled").listeners.change({ target: { checked: true } });
  h.elements.get("finance-enabled").listeners.change({ target: { checked: false } });
  h.elements.get("finance-collect").listeners.click();
  h.elements.get("finance-status-refresh").listeners.click();
  assert.deepEqual(actions, [["schedule", true], ["schedule", false], ["collect"], ["status"]]);
  assert.equal(h.calls.length, 0);
});

test("raw source details are collapsed by default and only parsed issue counts enter the summary", () => {
  const h = harness();
  form(h);
  h.state.finance.status.source_statuses[0].detail = JSON.stringify({ requests: ["x".repeat(1200)], issues: ["one", "two"] });
  const html = h.context.renderFinanceStatus(h.state.finance);
  assert.match(html, /2 项问题/);
  assert.match(html, /<details class="finance-source-detail"><summary>技术详情<\/summary><pre>/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  assert.doesNotMatch(html.replace(/<details[\s\S]*?<\/details>/g, ""), /x{100}|Missing document/);
  for (const detail of ['{"issues":["truncated', "provider error", '{"issues":"not an array"}']) {
    const fragment = h.context.renderFinanceSourceDetail(detail);
    assert.match(fragment, /技术详情/);
    assert.doesNotMatch(fragment, /\d+ 项问题/);
  }
  assert.match(h.context.renderFinanceSourceDetail('{"issues":[]}'), /0 项问题/);
});

test("an idle scheduled job does not imply that report-collected snapshots are missing", () => {
  const h = harness();
  form(h);
  h.state.finance.status.status = "idle";
  h.state.finance.status.last_status = "idle";
  const html = h.context.renderFinanceStatus(h.state.finance);
  assert.match(html, /定时任务状态<\/dt><dd>尚无定时记录/);
  assert.match(html, /上次定时结果<\/dt><dd>尚无定时记录/);
  assert.match(html, /快照采集时间<\/dt><dd>2026-09-22T10:00:08\+00:00/);
  assert.match(html, /index_a<\/strong><span>成功/);
  assert.doesNotMatch(html, /尚未采集|暂无数据源记录/);
});

test("quality-blocked successful runtime traces disclose nonpublication and default open", () => {
  const h = harness();
  const trace = { run_id: "blocked-report", status: "completed", metadata: { stop_reason: "quality_blocked" },
    steps: [{ step_index: 1, step_type: "gate_check", status: "done", title: "Quality gate", gate_result: { passed: false } }] };
  const before = json(trace);
  const html = h.context.renderThinking(trace);
  assert.match(html, /<details class="thinking-card"[^>]* open>/);
  assert.match(html, /<span class="trace-chip">质量校验未通过 \/ 未发布报告<\/span>/);
  assert.doesNotMatch(html, /流程完成/);
  assert.deepEqual(trace, before);
});

test("other trace completion, running and error states retain their previous labels and expansion", () => {
  const h = harness();
  for (const status of ["completed", "done", "success", "ok", "running", "error"]) {
    for (const metadata of [undefined, {}, { stop_reason: "final" }]) {
      const html = h.context.renderThinking({ run_id: "ordinary", status, metadata,
        steps: [{ step_index: 1, status: "done", title: "Existing step" }] });
      assert.ok(html.includes(`<span class="trace-chip">流程${h.context.statusLabel(status)}</span>`));
      assert.equal(/<details class="thinking-card"[^>]* open>/.test(html), ["running", "error"].includes(status));
      assert.doesNotMatch(html, /质量校验未通过/);
    }
  }
  assert.match(h.context.renderThinking({ run_id: "empty", steps: [], metadata: { stop_reason: "quality_blocked" } }),
    /本轮没有记录到执行步骤/);
});
