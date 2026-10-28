import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const appSource = readFileSync(new URL(
  "../src/policy_impact/app/static/h2-workbench/app.js", import.meta.url,
), "utf8");

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.className = "";
    this._text = "";
    this.dataset = {};
    this.style = {};
    this.value = "";
    this.classList = {add() {}, remove() {}, contains() { return false; }};
  }

  set textContent(value) { this._text = String(value ?? ""); }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this._text = ""; }
  addEventListener() {}
  setAttribute() {}
  removeAttribute() {}
  querySelectorAll() { return []; }
}

function harness() {
  const elements = new Map();
  const document = {
    body: new Element("body"),
    addEventListener() {},
    createElement: (tag) => new Element(tag),
    createTextNode: (text) => {
      const element = new Element("text");
      element.textContent = text;
      return element;
    },
    getElementById: (id) => {
      if (!elements.has(id)) elements.set(id, new Element("div"));
      return elements.get(id);
    },
  };
  const context = vm.createContext({window: {addEventListener() {}, innerWidth: 1440}, document});
  const initIndex = appSource.lastIndexOf("\ninit()");
  assert.notEqual(initIndex, -1);
  vm.runInContext(appSource.slice(0, initIndex), context);
  return {context, elements};
}

test("Trace panel renders agent paths, model-call Attempt links, status, and event owner", () => {
  const {context, elements} = harness();
  const trace = {
    spans: [{
      span_type: "model_call", span_id: "call_fixture", parent_span_id: "attempt_fixture",
      attempt_no: 2, model_profile: "fixture", status: "succeeded", duration_ms: 18,
      usage: {total_tokens: 42}, agent_path: ["main", "analyst", "verifier"],
    }],
  };
  context.renderTraceCalls(trace);
  context.addEvents([{
    event_id: "17", event_type: "task.spawned", task_id: "task_verifier",
    agent_path: ["main", "analyst", "verifier"], payload: {initial_status: "queued"},
  }]);

  const calls = elements.get("trace-calls").textContent;
  const events = elements.get("events").textContent;
  assert.match(calls, /main → analyst → verifier/);
  assert.match(calls, /Attempt #2/);
  assert.match(calls, /fixture/);
  assert.match(calls, /Attempt: attempt_fixture/);
  assert.match(calls, /Call: call_fixture/);
  assert.match(calls, /18 ms · 42 tokens/);
  assert.match(events, /#17main → analyst → verifier · task.spawned/);
  assert.match(elements.get("event-count").textContent, /^1$/);
});

test("Record filtering searches real titles, questions and batch names without inventing items", () => {
  const {context} = harness();
  const records = [{title: "黄金研究", batch: "r5"}, {case: "SCH-02", query: "核对作者条件", batch: "r4"}];
  assert.equal(context.filteredRecords(records, "黄金").length, 1);
  assert.equal(context.filteredRecords(records, "  sch-02  ")[0].case, "SCH-02");
  assert.equal(context.filteredRecords(records, "r4").length, 1);
  assert.equal(context.filteredRecords(records, "不存在").length, 0);
  assert.equal(context.filteredRecords(records, "").length, 2);
});

test("Source panel excludes arbitrary artifacts and preserves failed source tool receipts", () => {
  const {context, elements} = harness();
  context.addEvents([
    {event_id: "1", event_type: "artifact.created", payload: {artifact_ref: "art_arbitrary"}},
    {event_id: "2", event_type: "tool.completed", agent_path: ["main"], payload: {name: "article.read", status: "succeeded", observation_ref: "art_source"}},
    {event_id: "3", event_type: "tool.completed", agent_path: ["main"], payload: {name: "web.open", status: "failed", observation_ref: "art_failed"}},
    {event_id: "4", event_type: "tool.completed", payload: {name: "tool.describe", status: "succeeded"}},
  ]);
  assert.equal(elements.get("source-count").textContent, "2");
  assert.match(elements.get("sources").textContent, /文章原文/);
  assert.match(elements.get("sources").textContent, /失败/);
  context.setRun("another_run");
  assert.equal(elements.get("source-count").textContent, "0");
  assert.doesNotMatch(elements.get("sources").textContent, /文章原文/);
});

test("Artifact reader finds nested saved result content and preserves non-report JSON", () => {
  const {context} = harness();
  const research = {schema_version: "fixture", result: {content: "# Real report\nEvidence and gaps."}};
  assert.equal(context.readableArtifact(JSON.stringify(research)).content, research.result.content);
  assert.equal(context.readableArtifact('{"count":2}').raw, true);
  assert.equal(context.readableArtifact('{"result":').content, '{"result":');
});

test("New research clears prior task metrics, chart, budget and sources", () => {
  const {context, elements} = harness();
  context.setRun("previous_run");
  context.resetView();
  assert.equal(elements.get("run-id").textContent, "尚未创建");
  assert.equal(elements.get("activity-chart").hidden, true);
  assert.equal(elements.get("budget").textContent, "");
  assert.equal(elements.get("send").disabled, false);
  assert.equal(elements.get("cancel").hidden, true);
  assert.match(elements.get("tasks").textContent, /研究开始后/);
  assert.match(elements.get("execution-summary").textContent, /尚未开始/);
});

test("Switching Runs keeps the model-request container addressable", () => {
  const {context, elements} = harness();
  context.setRun("run_one");
  assert.equal(elements.get("events").children[0].id, "trace-calls");
  context.setRun("run_two");
  assert.equal(elements.get("events").children.length, 1);
  assert.equal(elements.get("events").children[0].id, "trace-calls");
});

test("Archived failed Tasks retain Attempt errors and never expose cancellation", () => {
  const {context, elements} = harness();
  const tasks = [{
    task_id: "task_failed", thread_id: "thread_failed", depth: 1, role: "analyst", status: "failed",
    objective: "Analyze one authorized source", budget: {consumed: 2, remaining: 58},
    attempts: [{attempt_no: 2, attempt_id: "attempt_failed", status: "failed", error_code: "contract_error"}],
  }];
  context.renderTasks({
    readonly: true, run_id: "run_saved", root_task_id: "task_failed", model_profile: "fixture",
    transport_backend: "fakeredis", status: {tasks}, metrics: {model_calls: {succeeded: 2}},
    trace: {spans: [{started_at: 1793163600000, finished_at: 1793163660000}]},
    budget: {root: 120, child: 60, run: 600, minutes: 60},
  });
  assert.match(elements.get("tasks").textContent, /失败/);
  assert.match(elements.get("tasks").textContent, /contract_error/);
  assert.match(elements.get("tasks").textContent, /Analyze one authorized source/);
  assert.equal(elements.get("cancel").hidden, true);
  assert.match(elements.get("budget").textContent, /120.*60.*600/);
  assert.match(elements.get("updated-at").textContent, /更新于/);
});

test("Artifact read retry preserves the original offset and appends later pages exactly once", async () => {
  const {context, elements} = harness();
  const paths = [];
  const responses = [
    new Error("temporary read failure"),
    {content: "first ", next_offset: 6, has_more: true},
    {content: "page", next_offset: 10, has_more: false},
  ];
  context.fetch = async (path) => {
    paths.push(path);
    const response = responses.shift();
    if (response instanceof Error) throw response;
    return {ok: true, json: async () => response};
  };
  vm.runInContext('artifact = {ref: "art_source", archive: "saved", offset: 0, text: "", loading: false}', context);

  await context.moreArtifact();
  assert.equal(elements.get("retry-artifact").hidden, false);
  assert.match(elements.get("artifact-error").textContent, /temporary read failure/);
  await context.moreArtifact();
  assert.equal(elements.get("artifact-body").textContent, "first ");
  assert.equal(elements.get("more-artifact").hidden, false);
  await context.moreArtifact();
  assert.equal(elements.get("artifact-body").textContent, "first page");
  assert.equal(elements.get("more-artifact").hidden, true);
  assert.equal(elements.get("retry-artifact").hidden, true);
  assert.equal(elements.get("artifact-completeness").textContent, "内容已完整读取");
  assert.match(paths[0], /offset=0$/);
  assert.equal(paths[1], paths[0]);
  assert.match(paths[2], /offset=6$/);
});

test("Duplicate artifact reads are blocked and late replies cannot overwrite a newly selected source", async () => {
  const {context, elements} = harness();
  let resolveResponse;
  let requests = 0;
  context.fetch = () => {
    requests++;
    return new Promise(resolve => { resolveResponse = resolve; });
  };
  vm.runInContext('artifact = {ref: "art_old", archive: "saved", offset: 0, text: "", loading: false}', context);
  const pending = context.moreArtifact();
  await context.moreArtifact();
  assert.equal(requests, 1);
  vm.runInContext('artifact = {ref: "art_new", archive: "saved", offset: 0, text: "", loading: false}', context);
  resolveResponse({ok: true, json: async () => ({content: "old source", next_offset: 10, has_more: false})});
  await pending;
  assert.equal(elements.get("artifact-body")?.textContent || "", "");
});
