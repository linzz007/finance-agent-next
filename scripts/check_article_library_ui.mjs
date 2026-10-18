import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, sep } from "node:path";

const base = process.env.WORKBENCH_BASE_URL || "http://127.0.0.1:8503/";
const chromePath = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const port = 9400 + Math.floor(Math.random() * 400);
const profile = join(tmpdir(), `workbench-article-ui-${Date.now()}`);
const screenshotDir = resolve("data/acceptance/screenshots");
const wait = (ms) => new Promise((done) => setTimeout(done, ms));

async function waitFor(url) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try { const response = await fetch(url); if (response.ok) return response.json(); }
    catch { /* Browser is still starting. */ }
    await wait(200);
  }
  throw new Error(`Chrome DevTools did not start at ${url}`);
}

class Cdp {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.pending = new Map();
    this.nextId = 1;
  }
  async open() {
    this.socket.onmessage = ({ data }) => {
      const message = JSON.parse(data);
      const entry = this.pending.get(message.id);
      if (!entry) return;
      this.pending.delete(message.id);
      if (message.error) entry.reject(new Error(JSON.stringify(message.error)));
      else entry.resolve(message.result);
    };
    await new Promise((done, fail) => { this.socket.onopen = done; this.socket.onerror = fail; });
  }
  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolveSend, reject) => {
      this.pending.set(id, { resolve: resolveSend, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  }
  close() { this.socket.close(); }
}

async function main() {
  assert.ok(existsSync(chromePath), `Chrome is missing: ${chromePath}`);
  mkdirSync(screenshotDir, { recursive: true });
  const chrome = spawn(chromePath, [
    "--headless=new", "--disable-gpu", "--no-first-run", `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`, "about:blank",
  ], { stdio: "ignore" });
  let cdp;
  try {
    await waitFor(`http://127.0.0.1:${port}/json/version`);
    const tab = await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(base)}`, { method: "PUT" })).json();
    cdp = new Cdp(tab.webSocketDebuggerUrl);
    await cdp.open();
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1365, height: 900, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: base });
    await wait(2000);
    await cdp.evaluate("localStorage.setItem('workbench.selectedWorkspace', 'finance_observer'); location.reload(); true");
    await wait(3200);
    await cdp.evaluate("document.querySelector('#article-tab').click(); true");
    await wait(1600);
    const initial = await cdp.evaluate(`(() => ({
      workspace: document.querySelector('#company-select')?.value,
      articleTabVisible: getComputedStyle(document.querySelector('#article-tab')).display !== 'none',
      count: document.querySelectorAll('.article-item').length,
      detail: document.querySelector('#article-detail')?.innerText?.slice(0, 100),
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 2,
    }))()`);
    assert.equal(initial.workspace, "finance_observer");
    assert.equal(initial.articleTabVisible, true);
    assert.equal(initial.count, 69);
    assert.equal(initial.horizontalOverflow, false);

    await cdp.evaluate(`(() => {
      const filter = document.querySelector('#article-author-filter');
      filter.value = '培风客'; filter.dispatchEvent(new Event('change', { bubbles: true }));
      document.querySelector('[data-article-mode="source"]')?.click();
      return true;
    })()`);
    await wait(850);
    const author = await cdp.evaluate(`(() => ({
      count: document.querySelectorAll('.article-item').length,
      title: document.querySelector('#article-detail h2')?.innerText,
      sourceLines: document.querySelectorAll('.article-source-line').length,
      toolbarVisible: Boolean(document.querySelector('#article-add')),
    }))()`);
    assert.equal(author.count, 31);
    assert.ok(author.title);
    assert.ok(author.sourceLines > 10);
    assert.equal(author.toolbarVisible, true);
    const desktop = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    const desktopPath = join(screenshotDir, "finance-article-library-desktop.png");
    writeFileSync(desktopPath, Buffer.from(desktop.data, "base64"));

    await cdp.evaluate("document.querySelector('#article-add').click(); true");
    const form = await cdp.evaluate(`(() => ({ open: document.querySelector('#article-dialog')?.open,
      author: Boolean(document.querySelector('#article-author')),
      file: Boolean(document.querySelector('#article-file')),
      content: Boolean(document.querySelector('#article-content')) }))()`);
    assert.deepEqual(form, { open: true, author: true, file: true, content: true });
    await cdp.evaluate("document.querySelector('#article-dialog').close(); true");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 780, deviceScaleFactor: 1, mobile: true });
    await wait(400);
    const mobile = await cdp.evaluate(`(() => ({
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 2,
      selectedArticleVisible: document.querySelector('#article-detail')?.getBoundingClientRect().width > 0,
      tabVisible: getComputedStyle(document.querySelector('#article-tab')).display !== 'none',
    }))()`);
    assert.equal(mobile.horizontalOverflow, false);
    assert.equal(mobile.selectedArticleVisible, true);
    assert.equal(mobile.tabVisible, true);
    const screenshot = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    const mobilePath = join(screenshotDir, "finance-article-library-mobile.png");
    writeFileSync(mobilePath, Buffer.from(screenshot.data, "base64"));
    console.log(JSON.stringify({ passed: true, initial, author, form, mobile, desktopPath, mobilePath }, null, 2));
  } finally {
    cdp?.close();
    chrome.kill("SIGKILL");
    await wait(300);
    const tempRoot = resolve(tmpdir()) + sep;
    if (resolve(profile).startsWith(tempRoot)) rmSync(profile, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
