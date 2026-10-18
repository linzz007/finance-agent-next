import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const appSource = readFileSync(new URL("../src/policy_impact/app/static/workbench/app.js", import.meta.url), "utf8");
const p10 = JSON.parse(readFileSync(new URL("../data/acceptance/persona_20260909_final/cases/P10/trace.json", import.meta.url), "utf8"));

function harness() {
  const context = vm.createContext({ window: {} });
  assert.match(appSource, /\ninit\(\);\s*$/);
  vm.runInContext(appSource.replace(/\ninit\(\);\s*$/, ""), context);
  return context;
}

const json = (value) => JSON.parse(JSON.stringify(value));
const modelStep = (metadata = {}, output = {}) => ({
  step_type: "model_call", status: "done", input_payload: {},
  output_payload: output, metadata,
});
const providerStep = (metadata = {}) => modelStep({
  provider: "test-provider", model_id: "actual-model", attempts: 1, ...metadata,
});
const contextStep = (manifest, input = {}) => ({
  step_type: "subagent_context", input_payload: input,
  output_payload: { context_manifest: manifest },
});
const decodeHtml = (value) => value
  .replaceAll("&quot;", '"').replaceAll("&#039;", "'")
  .replaceAll("&lt;", "<").replaceAll("&gt;", ">").replaceAll("&amp;", "&");
const rawResults = (markup) => [...markup.matchAll(/<pre class="json-box">([\s\S]*?)<\/pre>/g)]
  .map((match) => JSON.parse(decodeHtml(match[1])));
const modelCell = (markup) => markup.match(/<span>\u6a21\u578b<\/span>[\s\S]*?<\/small>/)?.[0] || "";

test("P10 counts three provider requests and four runtime actions despite polluted summaries", () => {
  const h = harness();
  const before = JSON.stringify(p10);
  assert.equal(p10.model_summary.call_count, 7);
  assert.equal(p10.trace_summary.model_call_count, 7);
  const metrics = h.summarizeTraceModels(p10);
  assert.equal(metrics.callCount, 3);
  assert.equal(metrics.runtimeActionCount, 4);
  assert.equal(metrics.unclassifiedCount, 0);
  assert.equal(metrics.totalAttempts, 3);
  assert.equal(metrics.attemptsComplete, true);
  assert.equal(metrics.retriedCallCount, 0);
  assert.ok(Math.abs(metrics.totalLatencyMs - 13471.42) < 0.001);
  assert.deepEqual(json(metrics.modelIds), ["deepseek-v4-flash"]);
  const markup = h.renderThinking(p10);
  const cell = modelCell(markup);
  assert.match(cell, /3 \u6b21\u6a21\u578b\u8bf7\u6c42/);
  assert.match(cell, /4 \u6b21\u8fd0\u884c\u65f6\u52a8\u4f5c/);
  assert.match(cell, /3 \u6b21\u5df2\u8bb0\u5f55 provider \u5c1d\u8bd5/);
  assert.doesNotMatch(cell, /7 \u6b21|\u672a\u8c03\u7528\u6a21\u578b/);
  assert.equal((markup.match(/class="step-type">\u8fd0\u884c\u65f6\u52a8\u4f5c</g) || []).length, 4);
  assert.equal((markup.match(/class="step-type">\u6a21\u578b\u8bf7\u6c42</g) || []).length, 3);
  assert.equal(JSON.stringify(p10), before);
});

test("runtime markers take precedence over inherited provider metadata and need no adapter allowlist", () => {
  const h = harness();
  for (const step of [
    modelStep({ deterministic_action: true, provider: "inherited" }),
    modelStep({}, { metadata: { deterministic_action: true, provider: "inherited" } }),
    modelStep({ provider: "inherited", deterministic_action: false }, { response_id: "runtime_legacy_1" }),
    modelStep({ deterministic_action: false }, { metadata: { deterministic_action: true } }),
  ]) {
    assert.equal(h.modelStepKind(step), "runtime");
    assert.match(h.modelCallSummary(step), /\u786e\u5b9a\u6027\u52a8\u4f5c\uff0c\u672a\u8bf7\u6c42\u6a21\u578b/);
    assert.equal(h.observationSummary(step)[0].label, "\u8fd0\u884c\u65f6");
    assert.match(h.renderStep(step), /class="step-type">\u8fd0\u884c\u65f6\u52a8\u4f5c</);
  }
  assert.equal(h.modelStepKind({ step_type: "tool_call", metadata: { provider: "test" } }), "");
});

test("provider metadata can live inside output and real responses from wrapped adapters still count", () => {
  const h = harness();
  const legacy = modelStep({ loop_phase: "think" }, {
    response_id: "provider-1",
    metadata: { provider: "test", model_id: "nested-model", attempts: 2, total_latency_ms: 120, latency_ms: 20 },
  });
  const wrapped = modelStep({ adapter: "_ForcedFirstActionModelAdapter", provider: "test", attempts: 1 });
  const adapterOnly = modelStep({ adapter: "AnthropicCompatibleModelAdapter" });
  const metrics = h.summarizeTraceModels({ steps: [legacy, wrapped, adapterOnly] });
  assert.equal(metrics.callCount, 3);
  assert.equal(metrics.totalAttempts, 3);
  assert.equal(metrics.attemptsComplete, false);
  assert.equal(metrics.retriedCallCount, 1);
  assert.equal(metrics.totalLatencyMs, 120);
  assert.deepEqual(json(metrics.modelIds), ["nested-model"]);
});

test("retry and latency totals use provider steps only, prefer total latency, and preserve explicit zero", () => {
  const h = harness();
  const trace = { steps: [
    providerStep({ attempts: 3, total_latency_ms: 300, latency_ms: 70 }),
    providerStep({ attempts: 0, total_latency_ms: 0, latency_ms: 90 }),
    providerStep({ attempts: 1, latency_ms: 40 }),
    modelStep({ deterministic_action: true, attempts: 100, latency_ms: 9000 }),
  ], model_summary: { call_count: 4, total_attempts: 104, total_latency_ms: 9999 } };
  const metrics = h.summarizeTraceModels(trace);
  assert.equal(metrics.callCount, 3);
  assert.equal(metrics.runtimeActionCount, 1);
  assert.equal(metrics.totalAttempts, 4);
  assert.equal(metrics.retriedCallCount, 1);
  assert.equal(metrics.totalLatencyMs, 340);
  const markup = modelCell(h.renderTraceEvidence(trace));
  assert.match(markup, /4 \u6b21\u5df2\u8bb0\u5f55 provider \u5c1d\u8bd5/);
  assert.match(markup, /1 \u4e2a\u8bf7\u6c42\u53d1\u751f\u91cd\u8bd5/);
  assert.match(markup, /340 ms \u7d2f\u8ba1\u8017\u65f6/);
  assert.doesNotMatch(markup, /104|9999/);
});

test("model ids, response ids, usage and aggregate counts alone do not prove provider requests", () => {
  const h = harness();
  const unknown = modelStep({ model_id: "selected", usage: { input_tokens: 100 } }, { response_id: "scripted_1" });
  unknown.input_payload = { model_id: "selected", prompt_contract: { contract_id: "unknown-only" } };
  const trace = { steps: [unknown], model_summary: { call_count: 7, total_attempts: 7 } };
  const metrics = h.summarizeTraceModels(trace);
  assert.equal(metrics.callCount, 0);
  assert.equal(metrics.unclassifiedCount, 7);
  assert.deepEqual(json(metrics.contractIds), []);
  assert.match(h.renderStep(unknown), /\u6a21\u578b\u8bb0\u5f55\uff08\u6765\u6e90\u672a\u786e\u8ba4\uff09/);
  const cell = modelCell(h.renderTraceEvidence(trace));
  assert.match(cell, /0 \u6b21\u6a21\u578b\u8bf7\u6c42\uff08\u5df2\u786e\u8ba4\uff09/);
  assert.match(cell, /7 \u6761\u6a21\u578b\u8bb0\u5f55\u6765\u6e90\u672a\u786e\u8ba4/);
  assert.doesNotMatch(cell, /\u672c\u8f6e\u672a\u8c03\u7528\u6a21\u578b|7 \u6b21\u8c03\u7528|unknown-only/);
});

test("summary-only and incomplete traces disclose missing evidence rather than inferring provider calls", () => {
  const h = harness();
  const trace = { model_summary: { call_count: 7 }, trace_summary: { model_used: true } };
  assert.equal(h.summarizeTraceModels(trace).unclassifiedCount, 7);
  const partial = { ...trace, steps: [providerStep(), modelStep({ deterministic_action: true })] };
  assert.equal(h.summarizeTraceModels(partial).unclassifiedCount, 5);
  assert.match(modelCell(h.renderTraceEvidence(partial)), /1 \u6b21\u6a21\u578b\u8bf7\u6c42\uff08\u5df2\u786e\u8ba4\uff09/);
  assert.match(h.renderTraceEvidence({}), /0 \u6b21\u6a21\u578b\u8bf7\u6c42/);
});

test("missing retry metadata is not invented and runtime-only traces do not announce a model call", () => {
  const h = harness();
  const trace = { steps: [providerStep({ attempts: undefined })] };
  assert.match(h.renderTraceEvidence(trace), /\u90e8\u5206\u6b21\u6570\u672a\u8bb0\u5f55/);
  const runtimeOnly = {
    steps: [modelStep({ deterministic_action: true })],
    trace_summary: { model_used: true, model_call_count: 1 },
  };
  assert.match(h.renderThinking(runtimeOnly), /\u672a\u8bb0\u5f55\u6a21\u578b\u8bf7\u6c42 \u00b7 1 \u6b21\u8fd0\u884c\u65f6\u52a8\u4f5c/);
  assert.match(modelCell(h.renderTraceEvidence(runtimeOnly)), /0 \u6b21\u6a21\u578b\u8bf7\u6c42/);
  const stale = { steps: [providerStep()], trace_summary: { model_used: false } };
  assert.doesNotMatch(h.renderThinking(stale), /\u672a\u8bb0\u5f55\u6a21\u578b\u8bf7\u6c42|\u786e\u5b9a\u6027\u6267\u884c/);
});

test("P10 nested subagent manifests expose two blocks, delegated reference and separate execution budgets", () => {
  const h = harness();
  const steps = p10.steps.filter((step) => step.step_type === "subagent_context");
  assert.equal(steps.length, 2);
  for (const [index, step] of steps.entries()) {
    const summary = h.contextSummary(step);
    assert.match(summary, /2 \u4e2a\u53ef\u89c1\u5757/);
    assert.match(summary, /\u59d4\u6d3e\u5f15\u7528 1 \u4e2a\uff1amain_chat_context:dc7f5dc43a46/);
    assert.ok(summary.includes(index === 0 ? "\u6b65\u9aa4\u4e0a\u9650 6" : "\u6b65\u9aa4\u4e0a\u9650 8"));
    assert.ok(summary.includes(index === 0 ? "\u8c03\u7528\u9884\u7b97 3\uff08\u542b\u8fd0\u884c\u65f6\u52a8\u4f5c\uff09" : "\u8c03\u7528\u9884\u7b97 4\uff08\u542b\u8fd0\u884c\u65f6\u52a8\u4f5c\uff09"));
    assert.ok(summary.includes(index === 0 ? "\u8d85\u65f6 60 \u79d2" : "\u8d85\u65f6 90 \u79d2"));
    assert.doesNotMatch(summary, /0 \u4e2a\u53ef\u89c1\u5757|Token \u9884\u7b97/);
    assert.ok(h.renderStep(step).includes(h.escapeHtml(summary)));
  }
  const panel = h.renderSubagentPanel(p10);
  assert.equal((panel.match(/\u59d4\u6d3e\u4e0a\u4e0b\u6587\uff1a2 \u4e2a\u53ef\u89c1\u5757/g) || []).length, 2);
});

test("flat main contexts retain visible blocks and token budgets without adopting subagent units", () => {
  const h = harness();
  const main = p10.steps.find((step) => step.step_type === "context_build");
  assert.match(h.contextSummary(main), /10 \u4e2a\u53ef\u89c1\u5757/);
  assert.match(h.contextSummary(main), /\u4f30\u7b97 809 tokens/);
  assert.match(h.contextSummary(main), /Token \u9884\u7b97 7900/);
  const flat = contextStep({
    visible_keys: ["task_brief"], token_estimates: { task: 2 }, token_budget: { task: 10 },
    visible_content: { task_brief: { max_steps: 0, max_model_calls: 0, timeout_seconds: 0 } },
  });
  assert.match(h.contextSummary(flat), /1 \u4e2a\u53ef\u89c1\u5757 \u00b7 \u4f30\u7b97 2 tokens \u00b7 Token \u9884\u7b97 10/);
  assert.match(h.contextSummary(flat), /\u6b65\u9aa4\u4e0a\u9650 0 \u00b7 \u8c03\u7528\u9884\u7b97 0\uff08\u542b\u8fd0\u884c\u65f6\u52a8\u4f5c\uff09 \u00b7 \u8d85\u65f6 0 \u79d2/);
});

test("absent visible keys stay unknown while flat/nested task references and input fallbacks remain visible", () => {
  const h = harness();
  const fromBrief = contextStep({ metadata: { visible_content: {
    task_brief: { input_artifact_refs: ["ref:one", "ref:two"] },
  } } });
  assert.match(h.contextSummary(fromBrief), /\u53ef\u89c1\u5757\u672a\u8bb0\u5f55 \u00b7 \u59d4\u6d3e\u5f15\u7528 2 \u4e2a\uff1aref:one \/ ref:two/);
  assert.doesNotMatch(h.contextSummary(fromBrief), /0 \u4e2a\u53ef\u89c1\u5757/);
  assert.match(h.contextSummary(contextStep({}, { artifact_ref: "legacy:ref" })), /\u59d4\u6d3e\u5f15\u7528 1 \u4e2a\uff1alegacy:ref/);
  assert.match(h.contextSummary(contextStep({}, { input_artifact_refs: ["legacy:one", "legacy:two"] })), /\u59d4\u6d3e\u5f15\u7528 2 \u4e2a/);
  const empty = contextStep({ visible_keys: [], visible_content: { input_artifact_refs: [] } }, { artifact_ref: "stale:ref" });
  assert.equal(h.contextSummary(empty), "0 \u4e2a\u53ef\u89c1\u5757 \u00b7 \u59d4\u6d3e\u5f15\u7528 0 \u4e2a");
  assert.equal(h.contextSummary({}), "");
});

test("P10 result summaries use Chinese field labels without losing evidence refs or confidence", () => {
  const h = harness();
  const analyst = p10.subagent_summary.results.find((result) => result.role === "analyst");
  const rows = h.flattenSubagentOutput(analyst.output);
  assert.equal(rows[0].label, "\u7ed3\u8bba 1");
  assert.match(rows[0].value, /\u7ed3\u8bba\u5f15\u7528\uff1aclaim_1/);
  assert.match(rows[0].value, /\u7f6e\u4fe1\u5ea6\uff1a0.95/);
  assert.match(rows[0].value, /\u8bc1\u636e\u5f15\u7528\uff1amain_chat_context:dc7f5dc43a46/);
  assert.ok(rows[0].value.includes(analyst.output.claims[0].statement));
  assert.doesNotMatch(rows[0].value, /"claim_ref"|^\{/);
  const markup = h.renderSubagentPanel(p10);
  assert.match(markup, /\u8d28\u7591 1/);
  assert.match(markup, /\u8865\u5145\u8bf4\u660e 1/);
  const raw = rawResults(markup);
  assert.equal(raw.length, 2);
  for (const result of p10.subagent_summary.results) {
    assert.deepEqual(raw.find((item) => item.task_id === result.task_id), result);
  }
});

test("result raw JSON preserves all array entries, unknown fields, zero and false beyond the preview", () => {
  const h = harness();
  const result = { role: "analyst", task_id: "task-many", status: "done", output: {
    claims: Array.from({ length: 10 }, (_, index) => ({
      claim_ref: "claim-" + index, statement: "Statement " + index,
      confidence: 0, evidence_refs: ["evidence-" + index],
    })),
    future_field: { enabled: false, score: 0, extra: ["last-evidence"] },
  } };
  const before = JSON.stringify(result);
  const markup = h.renderSubagentPanel({ subagent_summary: { results: [result] } });
  assert.match(markup, /\u8f93\u51fa\u6458\u8981\uff1a8 \/ 11 \u9879/);
  assert.equal((markup.match(/class="subagent-output-row"/g) || []).length, 8);
  assert.deepEqual(rawResults(markup), [result]);
  assert.match(h.summarizeSubagentValue(result.output.future_field), /enabled\uff1a\u5426\uff1bscore\uff1a0/);
  assert.equal(JSON.stringify(result), before);
});

test("collector and verifier fields are readable and all result HTML is escaped", () => {
  const h = harness();
  const output = {
    evidence: [{ source_ref: "source:1", summary: "<script>alert(1)</script>", source_url: "https://example.com/?a=1&b=2" }],
    verdicts: [{ claim_ref: "claim:1", decision: "fail", reason: "Insufficient evidence", evidence_refs: ["source:1"] }],
    custom: { literal: "</pre><img src=x onerror=alert(1)>", value: false },
  };
  const result = { role: "verifier", output };
  const rows = h.flattenSubagentOutput(output);
  assert.match(rows[0].value, /\u6765\u6e90\u5f15\u7528\uff1asource:1\uff1b\u6458\u8981\uff1a/);
  assert.match(rows[1].value, /\u6838\u9a8c\u5224\u5b9a\uff1a\u672a\u901a\u8fc7\uff08fail\uff09/);
  const markup = h.renderSubagentPanel({ subagent_summary: { results: [result] } });
  assert.doesNotMatch(markup, /<script>|<img /);
  assert.match(markup, /&lt;script&gt;/);
  assert.deepEqual(rawResults(markup), [result]);
});

test("result JSON keeps the existing local-path redaction policy", () => {
  const h = harness();
  const result = { role: "collector", output: { evidence_refs: ["C:\\private\\report.json", "artifact:stable"] } };
  const raw = rawResults(h.renderSubagentPanel({ subagent_summary: { results: [result] } }))[0];
  assert.deepEqual(raw.output.evidence_refs, ["[\u672c\u5730\u4ea7\u7269: report.json]", "artifact:stable"]);
});
