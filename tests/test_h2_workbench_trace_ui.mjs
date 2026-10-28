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
  }

  set textContent(value) { this._text = String(value ?? ""); }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this._text = ""; }
  addEventListener() {}
  setAttribute() {}
  querySelectorAll() { return []; }
}

function harness() {
  const elements = new Map();
  const document = {
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
  const context = vm.createContext({window: {}, document});
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
    budget: {root: 120, child: 60, run: 600, minutes: 60},
  });
  assert.match(elements.get("tasks").textContent, /失败/);
  assert.match(elements.get("tasks").textContent, /contract_error/);
  assert.match(elements.get("tasks").textContent, /Analyze one authorized source/);
  assert.equal(elements.get("cancel").hidden, true);
  assert.match(elements.get("budget").textContent, /120.*60.*600/);
});
