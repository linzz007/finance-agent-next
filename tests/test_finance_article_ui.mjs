import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../src/policy_impact/app/static/workbench/app.js", import.meta.url), "utf8");

function element(value = "") {
  return {
    value, disabled: false, textContent: "", listeners: {},
    classList: { add() {}, remove() {} },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    close() { this.closed = true; },
  };
}

test("article form submits through finance API and selects the newly indexed article", async () => {
  const elements = new Map();
  const calls = [];
  const article = { article_id: "article_new", author: "TT", title: "黄金观点", content: "# 黄金观点\n\n需要核对官方来源。" };
  const context = vm.createContext({
    document: { getElementById: (id) => elements.get(id) || null },
    fetch: async (path, options = {}) => {
      calls.push({ path, method: options.method, payload: JSON.parse(options.body) });
      return { ok: true, text: async () => JSON.stringify({ article }) };
    },
  });
  vm.runInContext(source.replace(/\ninit\(\);\s*$/, ""), context);
  vm.runInContext("globalThis.uiState = state", context);
  context.uiState.companyId = "finance_observer";
  let selectedId = "";
  context.loadArticles = async (id) => { selectedId = id; };
  context.toast = () => {};
  const add = (id, value = "") => elements.set(id, element(value));
  add("article-author", "TT");
  add("article-title", "黄金观点");
  add("article-date", "2026-09-29");
  add("article-source-url", "");
  add("article-content", "需要核对官方来源，至少二十个字符。 ");
  add("article-create-submit");
  add("article-create-error");
  add("article-dialog");
  await context.submitArticle({ preventDefault() {} });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/companies/finance_observer/workbench/finance/articles");
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].payload.author, "TT");
  assert.equal(selectedId, "article_new");
  assert.equal(context.uiState.articleDetail.article_id, "article_new");
  assert.equal(elements.get("article-dialog").closed, true);
  assert.equal(elements.get("article-create-submit").disabled, false);
});
