import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../src/policy_impact/app/static/workbench/app.js", import.meta.url), "utf8");
const context = vm.createContext({ window: {} });
vm.runInContext(source.replace(/\ninit\(\);\s*$/, ""), context);

test("shared-source totals and event counts are visible without invented category counts", () => {
  const summary = context.sourceSummary({ source_summary: {
    route: "news", source_count: 17, successful_source_count: 8, failed_source_count: 9,
    snapshot_item_count: 66, news_source_count: null, policy_source_count: null,
  } });
  assert.match(summary.detail, /17/);
  assert.match(summary.detail, /8/);
  assert.match(summary.detail, /9/);
  assert.match(summary.detail, /66/);
  assert.doesNotMatch(summary.detail, /0\/0/);
});

test("legacy counts distinguish missing enabled counts from explicit zero", () => {
  const summary = context.sourceSummary({ source_summary: {
    news_source_count: 4, enabled_news_source_count: null,
    policy_source_count: 0, enabled_policy_source_count: 0, snapshot_news_count: 0,
  } });
  assert.match(summary.detail, /未记录\/4/);
  assert.match(summary.detail, /政策 0\/0/);
  assert.match(summary.detail, /新闻快照 0/);
});

test("route alone does not imply zero observed sources", () => {
  const summary = context.sourceSummary({ source_summary: { route: "news", recorded: false } });
  assert.equal(summary.detail, "来源采集统计未记录");
});

test("financial trace distinguishes partial sources and saved series from news", () => {
  const summary = context.sourceSummary({ source_summary: {
    route: "finance", route_label: "金融观察", recorded: true,
    source_count: 5, successful_source_count: 1, partial_source_count: 4, failed_source_count: 0,
    series_count: 3, document_count: 15,
  } });
  assert.equal(summary.value, "金融观察");
  assert.match(summary.detail, /部分 4/);
  assert.match(summary.detail, /指数序列 3/);
  assert.match(summary.detail, /资料 15/);
  assert.doesNotMatch(summary.detail, /新闻快照|未进入/);
});
