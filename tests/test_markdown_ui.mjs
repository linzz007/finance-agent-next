import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../src/policy_impact/app/static/workbench/app.js", import.meta.url), "utf8");
const context = vm.createContext({ window: {}, URL });
vm.runInContext(source.replace(/\ninit\(\);\s*$/, ""), context);
const { formatMessage, formatInlineMarkdown, safeMarkdownHref, escapeHtml } = context;
const quoteUrl = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,2026-09-01,2026-09-22,320,qfq&token=query";
const hrefs = (html) => [...html.matchAll(/<a href="([^"]*)"/g)].map((match) => match[1]);

test("finance report headings become semantic headings, not literal hash-prefixed text", () => {
  const html = formatMessage("## 金融观察报告\n### 指数概览\n正文");
  assert.match(html, /<h2 class="message-heading">金融观察报告<\/h2>/);
  assert.match(html, /<h3 class="message-heading">指数概览<\/h3>/);
  assert.doesNotMatch(html, />##/);
  assert.match(html, /<div class="message-line">正文<\/div>/);
});

test("ATX headings require a line boundary, at most six hashes and following whitespace", () => {
  for (let level = 1; level <= 6; level += 1) {
    assert.match(formatMessage(`   ${"#".repeat(level)} 标题 ###`), new RegExp(`<h${level} class="message-heading">标题</h${level}>`));
  }
  for (const text of ["正文 ## 标题", "##没有空格", "####### 标题", "    ## 缩进正文"]) {
    assert.doesNotMatch(formatMessage(text), /<h[1-6]\b/);
  }
  assert.match(formatMessage("## 指数 C#"), />指数 C#<\/h2>/);
});

test("bare Tencent quote URL preserves q/u/o/t, full path and query parameters", () => {
  const html = formatMessage(`数据来源：${quoteUrl}。`);
  assert.deepEqual(hrefs(html), [escapeHtml(quoteUrl)]);
  assert.ok(html.includes(`>${escapeHtml(quoteUrl)}</a>。`));
  assert.doesNotMatch(html, /href="https:\/\/web\.ifz"/);
  assert.match(html, /target="_blank" rel="noopener noreferrer"/);
});

test("URL punctuation boundaries preserve balanced parentheses, fragments and IPv6", () => {
  const urls = ["https://example.org/a(b)?q=quote#section", "https://example.org/x_y?q=a%20b&count=10", "http://[::1]:8503/path"];
  for (const url of urls) {
    const html = formatInlineMarkdown(`(${url}).`);
    assert.deepEqual(hrefs(html), [escapeHtml(url)]);
    assert.ok(html.endsWith("</a>)."));
  }
  const html = formatInlineMarkdown(`${quoteUrl}，https://example.org/two；完毕`);
  assert.deepEqual(hrefs(html), [escapeHtml(quoteUrl), "https://example.org/two"]);
});

test("explicit Markdown links and angle autolinks are not nested or reprocessed", () => {
  const html = formatInlineMarkdown(`[**行情**](<${quoteUrl}>) <https://example.org/a(b)>`);
  assert.deepEqual(hrefs(html), [escapeHtml(quoteUrl), "https://example.org/a(b)"]);
  assert.match(html, /<strong>行情<\/strong><\/a>/);
  assert.deepEqual(hrefs(formatInlineMarkdown(`[https://example.org/label](${quoteUrl})`)), [escapeHtml(quoteUrl)]);
  assert.deepEqual(hrefs(formatInlineMarkdown("[括号](https://example.org/a(b))")), ["https://example.org/a(b)"]);
});

test("inline code protects URLs, HTML and Markdown delimiters from formatting", () => {
  const literal = `## 标题 **粗体** <img src=x> ${quoteUrl}`;
  const html = formatInlineMarkdown(`\`${literal}\``);
  assert.equal(html, `<code>${escapeHtml(literal)}</code>`);
  assert.deepEqual(hrefs(html), []);
  assert.equal(formatInlineMarkdown("``a ` b``"), "<code>a ` b</code>");
  assert.match(formatMessage("## **指数** `sh000001`"), /<h2[^>]*><strong>指数<\/strong> <code>sh000001<\/code><\/h2>/);
});

test("fenced code is escaped and cannot create headings, tables or links, including incomplete streams", () => {
  const literal = `## 标题\n${quoteUrl}\n<img src=x onerror=alert(1)>\n| a | b |\n| --- | --- |`;
  for (const fence of ["```", "~~~"]) {
    for (const ending of ["", `\n${fence}`]) {
      const html = formatMessage(`${fence}text\n${literal}${ending}`);
      assert.equal(html, `<pre class="message-code"><code>${escapeHtml(literal)}</code></pre>`);
    }
  }
});

test("HTTP and HTTPS are the only allowed link protocols and raw HTML cannot execute", () => {
  for (const url of ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>alert(1)</script>", "file:///C:/private", "vbscript:msgbox(1)", "//example.org", "https://", "https://example.org/\nattack", 'https://example.org/"onclick="bad', "https://example.org/\\evil"]) {
    assert.equal(safeMarkdownHref(url), "", url);
    assert.deepEqual(hrefs(formatInlineMarkdown(`[unsafe](${url})`)), [], url);
  }
  const html = formatMessage('## <img src=x onerror="alert(1)">\n<script>alert(1)</script>');
  assert.doesNotMatch(html, /<img|<script|<iframe/);
  assert.match(html, /&lt;img/);
  const url = "https://example.org/?q=&quot;onload=bad&x=%22";
  assert.deepEqual(hrefs(formatInlineMarkdown(url)), [escapeHtml(url)]);
});

test("finance tables support header alignment, inline code, bold and complete quote links", () => {
  const html = formatMessage(`### 指数概览\n| 指数 | 涨跌 | 来源 |\n| :--- | ---: | :---: |\n| \`sh000001\` | **1.2%** | [行情](${quoteUrl}) |\n\n后文`);
  assert.match(html, /<table><thead><tr><th scope="col" class="message-cell-left">指数/);
  assert.match(html, /class="message-cell-right">涨跌/);
  assert.match(html, /class="message-cell-center">来源/);
  assert.match(html, /<code>sh000001<\/code>/);
  assert.match(html, /<strong>1.2%<\/strong>/);
  assert.deepEqual(hrefs(html), [escapeHtml(quoteUrl)]);
  assert.match(html, /<div class="message-line">后文<\/div>/);
});

test("table cells handle escaped pipes and code pipes without shifting columns", () => {
  const html = formatMessage("名称 | 内容\n--- | ---\nA\\|B | `x|y`\nC | ``x ` | y``");
  assert.equal((html.match(/<td\b/g) || []).length, 4);
  assert.match(html, />A\|B<\/td>/);
  assert.match(html, /<code>x\|y<\/code>/);
  assert.match(html, /<code>x ` \| y<\/code>/);
  assert.doesNotMatch(formatMessage("a | b\nnot a separator\nx | y"), /<table>/);
});

test("existing lists, bold, artifact hiding and local-path redaction remain intact", () => {
  const html = formatMessage("- **风险**\n2. 第二项\n\nHTML 报告：C:\\secret\\report.html\n文件 C:\\secret\\data.json", { html_report: "C:\\secret\\report.html" });
  assert.equal((html.match(/class="message-list-item"/g) || []).length, 2);
  assert.match(html, /<strong>风险<\/strong>/);
  assert.match(html, /<span>2\.<\/span>/);
  assert.doesNotMatch(html, /HTML 报告|secret|report.html/);
  assert.match(html, /本地产物: data.json/);
});
