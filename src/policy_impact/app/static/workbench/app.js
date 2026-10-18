const DEFAULT_MODEL_ID = "deepseek-v4-flash";
const WORKSPACE_STORAGE_KEY = "workbench.selectedWorkspace";

function rememberedWorkspace() {
  try {
    return window.localStorage?.getItem(WORKSPACE_STORAGE_KEY) || "company_001";
  } catch {
    return "company_001";
  }
}

function rememberWorkspace(companyId) {
  try {
    window.localStorage?.setItem(WORKSPACE_STORAGE_KEY, companyId);
  } catch {
    // Storage restrictions must not prevent chat or workspace switching.
  }
}

const state = {
  companies: [],
  companyId: rememberedWorkspace(),
  sessions: [],
  currentSessionId: "",
  skills: [],
  models: [],
  modelStatus: null,
  messages: [],
  trace: null,
  tracesByRun: {},
  traceSessionId: "",
  traceLoadErrors: {},
  wiki: null,
  wikiAction: "",
  wikiDrafts: {},
  wikiLoadEpoch: 0,
  finance: null,
  financeDrafts: {},
  wikiSearchQuery: "",
  wikiSearchResults: null,
  wikiSearchError: "",
  wikiSearchBusy: false,
  wikiSearchEpoch: 0,
  workspaceBusy: false,
  workspaceCreation: null,
  pageCreationCompanyId: "",
  currentView: "chat",
  selectedWikiPath: "",
  articles: [],
  articleAuthors: [],
  articleSyncIssues: [],
  articleDetail: null,
  selectedArticleId: "",
  articleAuthorFilter: "",
  articleFilter: "",
  articleDisplay: "reading",
  articleBusy: false,
  articleError: "",
  articleLoadEpoch: 0,
  sending: false,
  sessionCreating: false,
  slashOpen: false,
  slashIndex: 0,
  slashQuery: "",
  slashMatches: [],
  showTestSessions: false,
  sessionLoadEpoch: 0,
  lastError: "",
  streaming: null,
  composing: false,
};

const $ = (id) => document.getElementById(id);

const labels = {
  modes: {
    auto: "默认对话",
    chat: "普通对话",
    skill: "指定技能",
    impact: "影响分析",
    policy: "政策分析",
    news: "新闻报告",
    research: "调研报告",
    wiki: "知识库规划",
  },
  skills: {
    "": "默认对话",
    general_chat: "普通对话",
    external_impact_report: "外部变化影响分析",
    policy_weekly_impact: "政策影响分析",
    recent_news_report: "新闻影响报告",
    research_report: "调研报告生成",
    company_wiki_blueprint: "企业知识库规划",
  },
  status: {
    running: "运行中",
    done: "完成",
    failed: "失败",
    blocked: "阻塞",
  },
  stepTypes: {
    intent_classification: "意图识别",
    skill_selection: "技能选择",
    context_build: "上下文构建",
    memory_read: "读取记忆",
    memory_write: "写入记忆",
    evidence_replay: "复现证据",
    tool_call: "工具调用",
    gate_check: "门控检查",
    model_call: "模型调用",
    subagent_plan: "子智能体规划",
    subagent_context: "子智能体上下文",
    subagent_delegate: "子智能体执行",
    artifact_write: "写入产物",
    workflow_stage: "工作流阶段",
    final_answer: "最终回答",
  },
  loopPhases: {
    think: "思考",
    act: "执行",
    observe: "观察",
    answer: "回答",
  },
  artifact: {
    report: "Markdown 报告",
    html_report: "HTML 报告",
    run_artifact: "运行产物",
    wiki_blueprint: "知识库规划",
  },
};

const slashCommands = [];

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.detail || response.statusText);
  }
  return data;
}

function parseSseBlock(block) {
  const event = { event: "message", data: "" };
  block.split(/\r?\n/).forEach((line) => {
    if (line.startsWith("event:")) event.event = line.slice(6).trim();
    if (line.startsWith("data:")) event.data += line.slice(5).trim();
  });
  if (!event.data) return null;
  try {
    return { type: event.event, payload: JSON.parse(event.data) };
  } catch (error) {
    return { type: "error", payload: { detail: `SSE JSON 解析失败：${error.message}` } };
  }
}

async function streamApi(path, body, onEvent) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_error) {
      data = { detail: text };
    }
    throw new Error(data.detail || response.statusText);
  }
  if (!response.body?.getReader) {
    throw new Error("当前浏览器不支持流式响应读取");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (value) {
      buffer += decoder.decode(value, { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        const event = parseSseBlock(block);
        if (event) await onEvent(event.type, event.payload);
      }
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const event = parseSseBlock(buffer.trim());
    if (event) await onEvent(event.type, event.payload);
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function currentSession() {
  return state.sessions.find((item) => item.session_id === state.currentSessionId) || null;
}

function currentSkillId() {
  return currentSession()?.active_skill_id || "";
}

function skillById(skillId) {
  return state.skills.find((skill) => skill.id === skillId) || null;
}

function skillLabel(skillId) {
  return labels.skills[skillId] || skillById(skillId)?.name || skillId || "自动选择";
}

function statusLabel(status) {
  return labels.status[status] || status || "未知";
}

function stepTypeLabel(stepType) {
  return labels.stepTypes[stepType] || stepType || "步骤";
}

function loopPhaseLabel(phase) {
  return labels.loopPhases[phase] || phase || "阶段";
}

function artifactLabel(name) {
  return labels.artifact[name] || name;
}

function shortId(value) {
  const text = String(value || "");
  return text.length > 12 ? `${text.slice(0, 8)}...${text.slice(-4)}` : text;
}

function artifactDisplayValue(value) {
  const text = String(value || "");
  const parts = text.split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : text;
}

function artifactHref(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  return `/companies/${encodeURIComponent(state.companyId)}/workbench/artifact?path=${encodeURIComponent(text)}`;
}

function artifactActionLabel(key) {
  if (key === "html_report") return "打开 HTML 报告";
  if (key === "report") return "查看 Markdown";
  if (key === "run_artifact") return "查看 RunArtifact";
  return "查看产物";
}

function redactLocalPaths(value) {
  return String(value || "")
    .replace(/[A-Za-z]:\\[^\s<>"']+/g, (path) => {
      const name = path.split(/[/\\]/).filter(Boolean).pop() || "本地产物";
      return `[本地产物: ${name}]`;
    });
}

function redactTraceValue(value) {
  if (Array.isArray(value)) return value.map(redactTraceValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redactTraceValue(item)]));
  }
  return typeof value === "string" ? redactLocalPaths(value) : value;
}

function summarizeValue(value) {
  if (value === null || value === undefined || value === "") return "空";
  if (Array.isArray(value)) return `${value.length} 项`;
  if (typeof value === "object") {
    const keys = Object.keys(value);
    return keys.length ? `${keys.slice(0, 4).join(" / ")}${keys.length > 4 ? " ..." : ""}` : "空对象";
  }
  const text = String(value).replace(/\s+/g, " ").trim();
  return text.length > 110 ? `${text.slice(0, 110)}...` : text;
}

function sumNumericValues(value) {
  if (!value || typeof value !== "object") return 0;
  return Object.values(value).reduce((total, item) => total + (Number(item) || 0), 0);
}

function uniqueNames(items, key, limit = 4) {
  const values = [];
  items.forEach((item) => {
    const value = item?.[key];
    if (value && !values.includes(value)) values.push(value);
  });
  const visible = values.slice(0, limit);
  const suffix = values.length > limit ? ` +${values.length - limit}` : "";
  return visible.join(" / ") + suffix;
}

function renderCompactDict(obj, emptyText = "无") {
  const entries = Object.entries(obj || {}).filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (!entries.length) return `<div class="detail-empty">${escapeHtml(emptyText)}</div>`;
  return entries
    .slice(0, 10)
    .map(
      ([key, value]) => `
        <div class="detail-kv">
          <span>${escapeHtml(key)}</span>
          <strong>${escapeHtml(summarizeValue(value))}</strong>
        </div>
      `,
    )
    .join("");
}

function commandByToken(token) {
  const normalized = String(token || "").trim().toLowerCase();
  return slashCommands.find((item) => item.command === normalized) || null;
}

function getPromptEditor() {
  return $("prompt-input");
}

function getPromptText() {
  return (getPromptEditor()?.textContent || "").replace(/\u00a0/g, " ");
}

function getCaretOffset(editor = getPromptEditor()) {
  const selection = window.getSelection();
  if (!editor || !selection?.rangeCount || !editor.contains(selection.anchorNode)) {
    return getPromptText().length;
  }
  const range = selection.getRangeAt(0).cloneRange();
  range.selectNodeContents(editor);
  range.setEnd(selection.anchorNode, selection.anchorOffset);
  return range.toString().length;
}

function setCaretOffset(editor, offset) {
  if (!editor) return;
  const target = Math.max(0, Number(offset) || 0);
  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let remaining = target;
  let node = walker.nextNode();
  while (node) {
    const length = node.textContent.length;
    if (remaining <= length) {
      range.setStart(node, remaining);
      range.collapse(true);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      return;
    }
    remaining -= length;
    node = walker.nextNode();
  }
  range.selectNodeContents(editor);
  range.collapse(false);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

function highlightPromptText(text) {
  const raw = String(text || "");
  if (!raw) return "";
  const pattern = /(^|[\s\u3000])(\/[A-Za-z_][\w-]*)/g;
  let html = "";
  let index = 0;
  let match = pattern.exec(raw);
  while (match) {
    const prefix = match[1] || "";
    const token = match[2] || "";
    const tokenStart = match.index + prefix.length;
    if (commandByToken(token)) {
      html += escapeHtml(raw.slice(index, tokenStart));
      html += `<span class="inline-command-token">${escapeHtml(token)}</span>`;
      index = tokenStart + token.length;
    }
    match = pattern.exec(raw);
  }
  html += escapeHtml(raw.slice(index));
  return html;
}

function renderPromptSyntax(caretOffset = getCaretOffset()) {
  const editor = getPromptEditor();
  if (!editor || state.composing) return;
  const raw = getPromptText();
  editor.innerHTML = highlightPromptText(raw);
  if (document.activeElement === editor) {
    setCaretOffset(editor, Math.min(caretOffset, raw.length));
  }
  autoGrow(editor);
}

function setPromptText(text, caretOffset = String(text || "").length) {
  const editor = getPromptEditor();
  if (!editor) return;
  editor.textContent = String(text || "");
  renderPromptSyntax(caretOffset);
}

function slashQueryRange(raw = getPromptText(), caretOffset = getCaretOffset()) {
  const beforeCursor = raw.slice(0, caretOffset);
  const match = beforeCursor.match(/(^|[\s\u3000])(\/[A-Za-z_][\w-]*)$/) || beforeCursor.match(/(^|[\s\u3000])(\/)$/);
  if (!match) return null;
  const token = match[2] || "";
  return {
    start: caretOffset - token.length,
    end: caretOffset,
    query: token.toLowerCase(),
  };
}

function replacePromptRange(start, end, insertion) {
  const raw = getPromptText();
  const safeStart = Math.max(0, Math.min(start, raw.length));
  const safeEnd = Math.max(safeStart, Math.min(end, raw.length));
  const next = `${raw.slice(0, safeStart)}${insertion}${raw.slice(safeEnd)}`;
  setPromptText(next, safeStart + insertion.length);
}

function extractInlineSlashCommand(raw) {
  const text = String(raw || "").trim();
  const pattern = /(^|[\s\u3000])(\/[A-Za-z_][\w-]*)(?=$|[\s\u3000])/g;
  let match = pattern.exec(text);
  while (match) {
    const command = commandByToken(match[2]);
    if (command) {
      const tokenStart = match.index + (match[1] || "").length;
      const tokenEnd = tokenStart + match[2].length;
      const message = `${text.slice(0, tokenStart)} ${text.slice(tokenEnd)}`
        .replace(/\s+/g, " ")
        .trim();
      return {
        command,
        message: message || `请执行${command.name}。`,
      };
    }
    match = pattern.exec(text);
  }
  return { command: null, message: text };
}

function normalizePromptForRuntime(raw) {
  const parsed = extractInlineSlashCommand(raw);
  return parsed.command ? `${parsed.command.command} ${parsed.message}`.trim() : parsed.message;
}

function setPromptDisabled(disabled) {
  const editor = getPromptEditor();
  if (!editor) return;
  editor.setAttribute("contenteditable", disabled ? "false" : "true");
  editor.classList.toggle("is-disabled", Boolean(disabled));
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.remove("hidden");
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => node.classList.add("hidden"), 3000);
}

function modelAvailable() {
  return Boolean(state.modelStatus?.available);
}

function activeModelId() {
  return state.modelStatus?.configuredModel || state.models[0]?.model_id || DEFAULT_MODEL_ID;
}

function modelStatusText() {
  if (!state.modelStatus) return "模型状态读取中";
  if (state.modelStatus.available) return `${activeModelId()} 已就绪`;
  const missing = (state.modelStatus.missing || []).join("、") || "模型鉴权";
  return `${activeModelId()} 未就绪：缺少 ${missing}`;
}

function isBrokenText(value) {
  const text = String(value || "").trim();
  if (!text) return false;
  if (text.includes("�")) return true;
  const questionMarks = (text.match(/\?/g) || []).length;
  const meaningful = text.replace(/[?\s_\-./:]/g, "");
  return questionMarks >= 3 && (questionMarks / text.length >= 0.2 || meaningful.length < 4);
}

function sessionTitle(session) {
  const title = String(session?.title || "").trim();
  const genericTitle = new Set(["policy", "news", "research", "chat", "auto"]).has(title.toLowerCase());
  if (title && !isBrokenText(title) && title !== "新对话" && !genericTitle) return title;
  const firstMessage = String(session?.first_user_message || "").replace(/\s+/g, " ").trim();
  if (firstMessage && !isBrokenText(firstMessage)) {
    return firstMessage.length > 26 ? `${firstMessage.slice(0, 26)}…` : firstMessage;
  }
  return title && !isBrokenText(title) ? title : "未命名对话";
}

function isTestSession(session) {
  const title = `${session?.title || ""} ${session?.first_user_message || ""}`.toLowerCase();
  return [
    "api test",
    "blind test",
    "test -",
    "测试用例",
    "acceptance",
    "benchmark",
    "fullcap",
    "multi-turn",
    "dialogue acceptance",
    "credential check",
    "design check",
    "no credential",
    "no model fetch",
    "please analyze recent policy impact",
  ].some((marker) => title.includes(marker));
}

function sessionGroup(session) {
  const raw = session.last_message_at || session.updated_at || session.created_at;
  const value = new Date(raw);
  if (Number.isNaN(value.getTime())) return "更早";
  const today = new Date();
  const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const startValue = new Date(value.getFullYear(), value.getMonth(), value.getDate());
  const days = Math.round((startToday - startValue) / 86400000);
  if (days <= 0) return "今天";
  if (days === 1) return "昨天";
  if (days <= 7) return "最近 7 天";
  return "更早";
}

function normalizeCompany(item) {
  if (typeof item === "string") {
    return {
      company_id: item,
      company_name: item,
      short_name: item,
      stock_code: "",
      business_keywords: [],
      fact_count: 0,
      high_importance_fact_count: 0,
      open_question_count: 0,
    };
  }
  return {
    company_id: item.company_id || item.id || "company_001",
    company_name: item.company_name || item.name || item.company_id || "company_001",
    short_name: item.short_name || item.company_name || item.company_id || "company_001",
    stock_code: item.stock_code || "",
    business_keywords: Array.isArray(item.business_keywords) ? item.business_keywords : [],
    fact_count: Number(item.fact_count || 0),
    high_importance_fact_count: Number(item.high_importance_fact_count || 0),
    open_question_count: Number(item.open_question_count || 0),
  };
}

function companyIdOf(company) {
  return typeof company === "string" ? company : company.company_id;
}

function isTestCompany(company) {
  const item = normalizeCompany(company);
  const text = `${item.company_id} ${item.company_name} ${item.short_name}`.toLowerCase();
  return (
    text.includes("验收画像") ||
    item.company_id === "company_test" ||
    item.company_id.startsWith("accept_") ||
    item.company_id.startsWith("shared_discovery_")
  );
}

function visibleWorkspaceCompanies() {
  return state.companies.filter((company) => state.showTestSessions || !isTestCompany(company));
}

function preferredWorkspaceId() {
  const visible = visibleWorkspaceCompanies();
  if (visible.some((company) => company.company_id === state.companyId)) return state.companyId;
  const preferred = ["company_001", "agent_self_evolution"];
  for (const companyId of preferred) {
    if (visible.some((company) => company.company_id === companyId)) return companyId;
  }
  return visible[0]?.company_id || state.companies[0]?.company_id || "";
}

function currentCompany() {
  return state.companies.find((company) => companyIdOf(company) === state.companyId) || normalizeCompany(state.companyId);
}

function companyOptionLabel(company) {
  const item = normalizeCompany(company);
  const code = item.stock_code ? ` · ${item.stock_code}` : "";
  const label = `${item.short_name || item.company_id}${code}`;
  const duplicates = state.companies.filter((candidate) => {
    const other = normalizeCompany(candidate);
    return `${other.short_name || other.company_id}${other.stock_code ? ` · ${other.stock_code}` : ""}` === label;
  });
  return duplicates.length > 1 ? `${label} · ${item.company_id}` : label;
}

function renderCompanies() {
  const visibleCompanies = visibleWorkspaceCompanies();
  $("company-select").innerHTML = visibleCompanies
    .map((company) => {
      const item = normalizeCompany(company);
      return `<option value="${escapeHtml(item.company_id)}">${escapeHtml(companyOptionLabel(item))}</option>`;
    })
    .join("") || '<option value="">暂无工作空间</option>';
  $("company-select").value = state.companyId;
  $("company-select").disabled = state.sending || state.workspaceBusy || Boolean(state.wikiAction) || !state.companies.length;
  $("create-workspace").disabled = state.sending || state.workspaceBusy || Boolean(state.wikiAction);
  if (!state.companyId) {
    $("company-card").innerHTML = "";
    return;
  }
  const company = normalizeCompany(currentCompany());
  const keywordText = company.business_keywords.slice(0, 3).join(" / ");
  $("company-card").innerHTML = `
    <div class="company-name">${escapeHtml(company.company_name)}</div>
    <div class="company-meta">${escapeHtml([company.company_id, company.stock_code].filter(Boolean).join(" · "))}</div>
    <div class="company-metrics">
      <span>${escapeHtml(String(company.fact_count))} 条事实</span>
      <span>${escapeHtml(String(company.high_importance_fact_count))} 条重点</span>
      <span>${escapeHtml(String(company.open_question_count))} 项待确认</span>
    </div>
    ${keywordText ? `<div class="company-keywords">${escapeHtml(keywordText)}</div>` : ""}
  `;
}

function renderSessions() {
  const query = $("session-search").value.trim().toLowerCase();
  const sessions = state.sessions
    .slice()
    .sort((a, b) => {
      const aIsNew = a.session_id === state.currentSessionId && Number(a.message_count || 0) === 0;
      const bIsNew = b.session_id === state.currentSessionId && Number(b.message_count || 0) === 0;
      if (aIsNew !== bIsNew) return aIsNew ? -1 : 1;
      const aHasMessages = Number(a.message_count || 0) > 0 ? 1 : 0;
      const bHasMessages = Number(b.message_count || 0) > 0 ? 1 : 0;
      if (aHasMessages !== bHasMessages) return bHasMessages - aHasMessages;
      return String(b.last_message_at || b.updated_at || "").localeCompare(String(a.last_message_at || a.updated_at || ""));
    })
    .filter((session) => {
      const haystack = [
        session.title || "",
        session.first_user_message || "",
        sessionTitle(session),
        session.session_id || "",
        session.active_skill_id || "",
        skillLabel(session.active_skill_id || ""),
      ]
        .join(" ")
        .toLowerCase();
      const matchesQuery = !query || haystack.includes(query);
      const visibleByType = state.showTestSessions || !isTestSession(session) || Boolean(query);
      const hasMessages = Number(session.message_count || 0) > 0;
      const visibleEmptySession = session.session_id === state.currentSessionId;
      return matchesQuery && visibleByType && (hasMessages || visibleEmptySession);
    })
    .slice(0, 60);
  const filterButton = $("toggle-test-sessions");
  filterButton.textContent = state.showTestSessions ? "隐藏测试" : "显示测试";
  filterButton.setAttribute("aria-pressed", String(state.showTestSessions));
  if (!sessions.length) {
    $("session-list").innerHTML = `
      <div class="session-empty">
        <strong>暂无匹配会话</strong>
        <span>清空搜索词，或点击上方新建对话。</span>
      </div>
    `;
    return;
  }
  const groups = ["今天", "昨天", "最近 7 天", "更早"];
  $("session-list").innerHTML = groups
    .map((group) => {
      const items = sessions.filter((session) => sessionGroup(session) === group);
      if (!items.length) return "";
      return `
        <div class="session-group-label">${escapeHtml(group)}</div>
        ${items
          .map((session) => {
            const active = session.session_id === state.currentSessionId ? "active" : "";
            const meta = [skillLabel(session.active_skill_id || ""), formatTime(session.last_message_at || session.updated_at)]
              .filter(Boolean)
              .join(" · ");
            return `
              <button class="session-item ${active}" data-session-id="${escapeHtml(session.session_id)}" ${state.sending ? "disabled" : ""}>
                <span class="session-name">${escapeHtml(sessionTitle(session))}</span>
                <span class="session-meta-line">${escapeHtml(meta)}</span>
              </button>
            `;
          })
          .join("")}
      `;
    })
    .join("");
  document.querySelectorAll(".session-item").forEach((button) => {
    button.addEventListener("click", () => selectSession(button.dataset.sessionId));
  });
}

function renderCommands() {
  const list = $("command-list");
  if (!list) return;
  list.innerHTML = slashCommands
    .map(
      (item) => `
        <div class="command-row">
          <span class="slash-code">${escapeHtml(item.command)}</span>
          <span class="command-name">${escapeHtml(item.name)}</span>
        </div>
      `,
    )
    .join("");
}

function renderTools() {
  const sessionSkillId = currentSkillId();
  const activeSkillId = sessionSkillId || "general_chat";
  const activeChip = $("active-skill-chip");
  if (activeChip) activeChip.textContent = skillLabel(sessionSkillId);
  const composerChip = $("composer-skill-chip");
  if (composerChip) composerChip.textContent = state.currentView === "wiki" ? "Wiki 数据页" : skillLabel(sessionSkillId);
  const skill = skillById(activeSkillId);
  const tools = Object.entries(skill?.tool_policy || {}).flatMap(([stage, values]) =>
    (values || []).map((tool) => ({ stage, tool })),
  );
  const toolList = $("tool-list");
  if (!toolList) return;
  toolList.innerHTML = tools.length
    ? tools
        .map(
          (item) => `
            <div class="tool-row">
              <div class="tool-name">${escapeHtml(item.tool)}</div>
              <div class="tool-meta">${escapeHtml(item.stage)}</div>
            </div>
          `,
        )
        .join("")
    : `<div class="tool-row"><div class="tool-name">本技能不调用工具</div><div class="tool-meta">仅使用已构建上下文和模型边界</div></div>`;
}

function renderModelPicker() {
  const picker = $("model-picker");
  const available = state.models.filter((model) => model.metadata?.available);
  picker.innerHTML = available.length
    ? available
        .map(
          (model) => `<option value="${escapeHtml(model.model_id)}">${escapeHtml(model.model_id)}</option>`,
        )
        .join("")
    : `<option value="">无可用模型</option>`;
  picker.value = available.some((model) => model.model_id === activeModelId()) ? activeModelId() : available[0]?.model_id || "";
  picker.disabled = !available.length;
  const wrap = picker.closest(".model-picker-wrap");
  if (wrap) wrap.classList.toggle("blocked", !available.length);
}

function renderHeader() {
  const session = currentSession();
  $("session-title").textContent = state.currentView === "articles"
    ? "文章资料库"
    : state.currentView === "wiki" ? "工作空间 Wiki" : session ? sessionTitle(session) : "金融研究";
  if (state.currentView === "articles") {
    $("session-meta").textContent = "金融研判 / 原文资料";
  } else if (state.currentView === "wiki") {
    $("session-meta").textContent = "当前画像与资料";
  } else if (session) {
    const metaParts = [...new Set([
      skillLabel(session.active_skill_id || ""),
      labels.modes[session.mode] || session.mode,
    ].filter(Boolean))];
    $("session-meta").textContent = metaParts.join(" / ");
  } else {
    $("session-meta").textContent = "";
  }

  const node = $("runtime-status");
  if (node) {
    const runtimeState = state.sending ? "running" : modelAvailable() ? "ready" : "blocked";
    const runtimeText = state.sending ? "运行中" : modelAvailable() ? "就绪" : "不可用";
    node.innerHTML = `
      <div class="runtime-status-pill ${runtimeState}">
        <span class="status-dot"></span>
        <span>${runtimeText}</span>
      </div>
    `;
  }
  const sendButton = $("send-message");
  if (sendButton) {
    sendButton.disabled = state.sending || state.sessionCreating || !state.currentSessionId || !modelAvailable();
    sendButton.title = state.sending ? "正在执行" : modelAvailable() ? "发送" : "当前没有可用模型";
  }
  const promptInput = $("prompt-input");
  if (promptInput) promptInput.setAttribute("aria-busy", String(state.sending));
  const newSessionButton = $("new-session");
  if (newSessionButton) {
    newSessionButton.disabled = state.sending || state.sessionCreating;
    newSessionButton.setAttribute("aria-busy", String(state.sessionCreating));
  }
}

function renderChat(options = {}) {
  const stream = $("chat-stream");
  const previousScrollTop = stream.scrollTop;
  const openRunIds = new Set(Array.from(stream.querySelectorAll(".thinking-card[open][data-run-id]"))
    .map((card) => card.dataset.runId));
  if (!state.messages.length && !state.streaming) {
    stream.innerHTML = `
      <div class="empty-state">
        <div>
          <h2>金融研究</h2>
          <div class="starter-list">
            <button data-prompt="请基于代表性文章总结 TT 的核心分析框架，说明实际阅读范围。">作者画像</button>
            <button data-prompt="请逐篇分析 TT 最近五篇文章，保留各篇的日期、观点、条件和原文依据。">最近五篇文章</button>
            <button data-prompt="TT 和培风客最近对黄金有哪些共识和分歧？请区分作者观点与得到外部证据支持的事实。">观点对比</button>
            <button data-prompt="结合我的研究偏好，最近的文章中有哪些值得继续跟踪的问题？">跟踪方向</button>
          </div>
          ${renderErrorNotice()}
        </div>
      </div>
    `;
    stream.querySelectorAll("[data-prompt]").forEach((button) => {
      button.addEventListener("click", () => {
        const input = $("prompt-input");
        setPromptText(button.dataset.prompt || "");
        input.focus();
      });
    });
    return;
  }

  const latestRunId = state.trace?.run_id || "";
  const visibleMessages = state.streaming
    ? [
      ...state.messages,
      {
        role: "user",
        content: state.streaming.userMessage,
        metadata: { streaming: true },
      },
      {
        role: "assistant",
        content: state.streaming.content || "正在生成...",
        metadata: {
          run_id: state.streaming.runId || "",
          artifacts: state.streaming.artifacts || {},
          streaming: true,
        },
      },
    ]
    : state.messages;
  stream.innerHTML = `
    ${renderErrorNotice()}
    ${visibleMessages
      .map((message) => {
        const runId = message.metadata?.run_id || "";
        const cachedTrace = runId === latestRunId ? state.trace : state.tracesByRun[runId];
        const showTrace = message.role === "assistant" && runId && cachedTrace;
        const showStreamingTrace = message.role === "assistant" && message.metadata?.streaming && !cachedTrace;
        const showPendingTrace = message.role === "assistant" && runId && !cachedTrace && !message.metadata?.streaming;
        return `
          ${showTrace ? renderThinking(cachedTrace) : ""}
          ${showStreamingTrace ? renderStreamingThinking(state.streaming) : ""}
          ${showPendingTrace ? renderPendingTrace(runId, state.traceLoadErrors[runId]) : ""}
          <div class="message-block">
            <div class="message ${message.role === "assistant" ? "assistant" : "user"}">
              <div class="avatar">${message.role === "assistant" ? "A" : "你"}</div>
              <div class="bubble ${message.metadata?.streaming ? "streaming-bubble" : ""}">
                ${formatMessage(message.content, message.metadata?.artifacts || {})}
                ${renderMessageArtifacts(message.metadata?.artifacts || {})}
              </div>
            </div>
          </div>
        `;
      })
      .join("")}
  `;
  stream.querySelectorAll("[data-run-trace]").forEach((button) => {
    button.addEventListener("click", () => loadHistoricalTrace(button.dataset.runTrace, button));
  });
  stream.querySelectorAll(".thinking-card[data-run-id]").forEach((card) => {
    if (openRunIds.has(card.dataset.runId)) card.open = true;
  });
  if (options.preserveScroll) {
    stream.scrollTop = previousScrollTop;
    return;
  }
  const thinkingCards = stream.querySelectorAll(".thinking-card");
  const requestedThinking = options.scrollRunId
    ? stream.querySelector(`.thinking-card[data-run-id="${options.scrollRunId}"]`)
    : null;
  const targetThinking = requestedThinking || thinkingCards[thinkingCards.length - 1];
  if (targetThinking) {
    const streamTop = stream.getBoundingClientRect().top;
    const cardTop = targetThinking.getBoundingClientRect().top;
    stream.scrollTop = Math.max(0, stream.scrollTop + cardTop - streamTop - 12);
  } else {
    stream.scrollTop = stream.scrollHeight;
  }
}

function renderWorkspace(options = {}) {
  const chatActive = state.currentView === "chat";
  const wikiActive = state.currentView === "wiki";
  $("chat-stream").classList.toggle("hidden", !chatActive);
  $("wiki-view").classList.toggle("hidden", !wikiActive);
  $("article-view").classList.toggle("hidden", state.currentView !== "articles");
  $("composer").classList.toggle("hidden", !chatActive);
  $("article-tab").classList.toggle("hidden", state.companyId !== "finance_observer");
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.currentView;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  if (chatActive) renderChat(options);
  else if (wikiActive) renderWiki();
  else renderArticles();
}

function filteredArticles() {
  const query = state.articleFilter.trim().toLocaleLowerCase();
  return state.articles.filter((article) =>
    (!state.articleAuthorFilter || article.author === state.articleAuthorFilter)
    && (!query || `${article.title} ${article.author} ${article.published_at || ""}`.toLocaleLowerCase().includes(query)));
}

function renderArticles() {
  const view = $("article-view");
  const articles = filteredArticles();
  view.innerHTML = `
    <div class="article-toolbar">
      <div><h2>文章资料库</h2><span>${state.articles.length} 篇原文 · ${state.articleAuthors.length} 位作者</span></div>
      <div class="article-toolbar-actions">
        <button id="article-refresh" class="wiki-secondary-button" type="button" ${state.articleBusy ? "disabled" : ""}>刷新</button>
        <button id="article-add" class="wiki-primary-button" type="button" ${state.articleBusy ? "disabled" : ""}>添加文章</button>
      </div>
    </div>
    ${state.articleError ? `<p class="wiki-form-error" role="alert">${escapeHtml(state.articleError)}</p>` : ""}
    ${state.articleSyncIssues.length ? `<p class="wiki-form-error" role="alert">${state.articleSyncIssues.length} 篇文件缺少可读取的标题、日期或正文，未进入文章库。请检查文件格式。</p>` : ""}
    <div class="article-shell">
      <section class="article-index" aria-label="文章目录">
        <div class="article-filters">
          <label class="sr-only" for="article-author-filter">筛选博主</label>
          <select id="article-author-filter"><option value="">全部博主</option>${state.articleAuthors.map((author) => `<option value="${escapeHtml(author)}" ${author === state.articleAuthorFilter ? "selected" : ""}>${escapeHtml(author)}</option>`).join("")}</select>
          <label class="sr-only" for="article-filter">筛选标题或博主</label>
          <input id="article-filter" type="search" placeholder="筛选标题或博主" value="${escapeHtml(state.articleFilter)}" />
        </div>
        <div id="article-list" class="article-list"></div>
      </section>
      <section id="article-detail" class="article-detail" aria-label="文章正文"></section>
    </div>`;
  $("article-refresh").addEventListener("click", () => loadArticles());
  $("article-add").addEventListener("click", openArticleDialog);
  $("article-author-filter").addEventListener("change", (event) => {
    state.articleAuthorFilter = event.target.value;
    renderArticleList();
  });
  $("article-filter").addEventListener("input", (event) => {
    state.articleFilter = event.target.value;
    renderArticleList();
  });
  renderArticleList(articles);
}

function renderArticleList(articles = filteredArticles()) {
  const list = $("article-list");
  if (!list) return;
  if (!articles.some((item) => item.article_id === state.selectedArticleId)) {
    state.selectedArticleId = articles[0]?.article_id || "";
    state.articleDetail = null;
    if (state.selectedArticleId) loadArticle(state.selectedArticleId);
  }
  list.innerHTML = articles.length ? articles.map((article) => `
    <button class="article-item ${article.article_id === state.selectedArticleId ? "active" : ""}"
      type="button" data-article-id="${escapeHtml(article.article_id)}" aria-current="${article.article_id === state.selectedArticleId}">
      <strong>${escapeHtml(article.title)}</strong>
      <span>${escapeHtml(article.author)} · ${escapeHtml(article.published_at || "未标日期")}</span>
    </button>`).join("") : '<p class="article-empty">没有匹配的文章</p>';
  list.querySelectorAll("[data-article-id]").forEach((button) => button.addEventListener("click", () => {
    const id = button.dataset.articleId;
    if (id !== state.selectedArticleId) {
      state.selectedArticleId = id;
      state.articleDetail = null;
      renderArticleList();
      loadArticle(id);
    }
  }));
  renderArticleDetail();
}

function renderArticleDetail() {
  const target = $("article-detail");
  if (!target) return;
  const article = state.articleDetail?.article_id === state.selectedArticleId ? state.articleDetail : null;
  if (!article) {
    target.innerHTML = `<div class="article-empty">${state.selectedArticleId ? "正在读取文章…" : "选择一篇文章查看原文"}</div>`;
    return;
  }
  const source = article.content || "";
  const raw = source.split(/\r?\n/).map((line, index) => `
    <div class="article-source-line"><span>${index + 1}</span><span>${escapeHtml(line) || "&nbsp;"}</span></div>`).join("");
  target.innerHTML = `
    <div class="article-detail-head">
      <div><span class="article-kicker">${escapeHtml(article.author)} · ${escapeHtml(article.published_at || "未标日期")}</span><h2>${escapeHtml(article.title)}</h2></div>
      <div class="article-detail-actions">
        <button id="article-ask" class="wiki-secondary-button" type="button">在对话中分析</button>
        <div class="article-view-mode" role="group" aria-label="文章视图">
          <button type="button" data-article-mode="reading" class="${state.articleDisplay === "reading" ? "active" : ""}">阅读</button>
          <button type="button" data-article-mode="source" class="${state.articleDisplay === "source" ? "active" : ""}">原文</button>
        </div>
      </div>
    </div>
    ${article.source_url ? `<a class="article-external" href="${escapeHtml(article.source_url)}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a>` : ""}
    <div class="article-body ${state.articleDisplay === "source" ? "article-source" : "message-body"}">${state.articleDisplay === "source" ? raw : formatMessage(source)}</div>`;
  $("article-ask").addEventListener("click", () => {
    state.currentView = "chat";
    setPromptText(`请分析${article.author}的文章《${article.title}》，区分作者观点、事实和待核实的部分。`);
    renderAll();
    $("prompt-input").focus();
  });
  target.querySelectorAll("[data-article-mode]").forEach((button) => button.addEventListener("click", () => {
    state.articleDisplay = button.dataset.articleMode;
    renderArticleDetail();
  }));
}

async function loadArticles(selectedId = "") {
  if (state.companyId !== "finance_observer") return;
  const companyId = state.companyId;
  const epoch = ++state.articleLoadEpoch;
  state.articleBusy = true;
  state.articleError = "";
  if (state.currentView === "articles") renderArticles();
  try {
    const data = await api(`/companies/${companyId}/workbench/finance/articles`);
    if (epoch !== state.articleLoadEpoch || companyId !== state.companyId) return;
    state.articles = data.articles || [];
    state.articleAuthors = data.authors || [];
    state.articleSyncIssues = data.sync?.issues || [];
    state.selectedArticleId = selectedId || state.selectedArticleId;
    if (state.currentView === "articles") renderArticles();
  } catch (error) {
    if (epoch === state.articleLoadEpoch) state.articleError = error.message;
  } finally {
    if (epoch === state.articleLoadEpoch) {
      state.articleBusy = false;
      if (state.currentView === "articles") renderArticles();
    }
  }
}

async function loadArticle(articleId) {
  const companyId = state.companyId;
  try {
    const data = await api(`/companies/${companyId}/workbench/finance/articles/${encodeURIComponent(articleId)}`);
    if (companyId !== state.companyId || articleId !== state.selectedArticleId) return;
    state.articleDetail = data.article;
    renderArticleDetail();
  } catch (error) {
    if (companyId === state.companyId && articleId === state.selectedArticleId) {
      state.articleError = error.message;
      renderArticles();
    }
  }
}

function openArticleDialog() {
  $("article-create-form").reset();
  $("article-create-error").classList.add("hidden");
  $("article-author-options").innerHTML = state.articleAuthors.map((author) => `<option value="${escapeHtml(author)}"></option>`).join("");
  $("article-author").value = state.articleAuthorFilter || "";
  $("article-dialog").showModal();
  $("article-author").focus();
}

async function importArticleFile() {
  const file = $("article-file").files?.[0];
  if (!file) return;
  if (!/\.(md|txt)$/i.test(file.name) || file.size > 500_000) {
    $("article-create-error").textContent = "请选择不超过 500 KB 的 Markdown 或文本文件";
    $("article-create-error").classList.remove("hidden");
    return;
  }
  const content = await file.text();
  $("article-content").value = content;
  const heading = content.match(/^#\s+(.+)$/m);
  if (!$("article-title").value) $("article-title").value = heading?.[1]?.trim() || file.name.replace(/\.(md|txt)$/i, "");
  $("article-create-error").classList.add("hidden");
}

async function submitArticle(event) {
  event.preventDefault();
  if (state.articleBusy) return;
  const payload = {
    author: $("article-author").value.trim(), title: $("article-title").value.trim(),
    published_at: $("article-date").value, source_url: $("article-source-url").value.trim(),
    content: $("article-content").value,
  };
  state.articleBusy = true;
  $("article-create-submit").disabled = true;
  try {
    const result = await api(`/companies/${state.companyId}/workbench/finance/articles`, {
      method: "POST", body: JSON.stringify(payload),
    });
    $("article-dialog").close();
    state.articleAuthorFilter = "";
    state.articleFilter = "";
    state.articleDetail = result.article;
    await loadArticles(result.article.article_id);
    toast("文章已添加并进入检索索引");
  } catch (error) {
    $("article-create-error").textContent = error.message;
    $("article-create-error").classList.remove("hidden");
  } finally {
    state.articleBusy = false;
    $("article-create-submit").disabled = false;
  }
}

function renderWiki() {
  const view = $("wiki-view");
  const wiki = state.wiki || {};
  const finance = currentFinance();
  const savedPages = Array.isArray(wiki.pages) ? wiki.pages : [];
  const pages = finance?.profile
    ? [{ ...(savedPages.find((page) => isFinanceProfilePath(page.path)) || {}), path: FINANCE_PROFILE_PATH, title: "金融观察档案" },
      ...savedPages.filter((page) => !isFinanceProfilePath(page.path))]
    : savedPages;
  const stats = wiki.stats || {};
  if (!pages.length) {
    view.innerHTML = `
      <div class="wiki-empty">
        <h2>${state.companyId ? "暂无 Wiki 页面" : "暂无工作空间"}</h2>
        <p>${escapeHtml(wiki.error || finance?.profileError || (state.companyId ? "当前工作空间尚无页面或事实。" : "尚未创建公司或个人画像。"))}</p>
        <div class="wiki-action-row">
          ${state.companyId && !wiki.error ? '<button id="wiki-new-page" class="wiki-primary-button" type="button"><span aria-hidden="true">+</span> 新建页面</button>' : ""}
          ${state.companyId ? '<button id="wiki-refresh" class="wiki-secondary-button" type="button">重新读取</button>' : ""}
        </div>
      </div>
    `;
    $("wiki-new-page")?.addEventListener("click", openWikiPageDialog);
    $("wiki-new-workspace")?.addEventListener("click", openWorkspaceDialog);
    $("wiki-refresh")?.addEventListener("click", refreshWiki);
    return;
  }
  if (!state.selectedWikiPath || !pages.some((page) => page.path === state.selectedWikiPath)) {
    state.selectedWikiPath = pages[0].path;
  }
  const selected = pages.find((page) => page.path === state.selectedWikiPath) || pages[0];
  const draft = state.wikiDrafts[wikiDraftKey(state.companyId, selected.path)];
  const savedFacts = Array.isArray(selected.facts) ? selected.facts : [];
  const facts = draft?.facts || savedFacts;
  const profile = wiki.profile || {};
  const financeSelected = isFinanceProfilePath(selected.path);
  view.innerHTML = `
    ${finance?.profileError ? `<p class="wiki-form-error finance-load-error" role="alert">${escapeHtml(finance.profileError)}</p>` : ""}
    <div class="wiki-toolbar">
      <form id="wiki-search-form" class="wiki-search-form" role="search">
        <label class="sr-only" for="wiki-search-query">搜索当前工作空间的事实</label>
        <input id="wiki-search-query" type="search" name="query" required maxlength="2000" value="${escapeHtml(state.wikiSearchQuery)}" placeholder="搜索事实" />
        <button class="wiki-secondary-button" type="submit">搜索</button>
        <button id="wiki-search-clear" class="wiki-secondary-button" type="button">清除</button>
      </form>
      <div class="wiki-action-row">
        <button id="wiki-new-page" class="wiki-secondary-button" type="button" ${state.wikiAction ? "disabled" : ""}><span aria-hidden="true">+</span> 新建页面</button>
      </div>
    </div>
    <section id="wiki-search-results" class="wiki-search-results" aria-label="事实检索结果" aria-live="polite"></section>
    <div class="wiki-shell">
      <section class="wiki-index" aria-label="Wiki 目录">
        <div class="wiki-index-head">
          <span>工作空间 Wiki</span>
          <strong>${escapeHtml(String(finance?.profile ? pages.length : stats.page_count || pages.length))} 页</strong>
        </div>
        <div class="wiki-company">
          <strong>${escapeHtml(finance?.profile?.display_name || profile.company_name || profile.short_name || wiki.company_id || state.companyId)}</strong>
          <span>${finance?.profile ? "金融观察" : escapeHtml([profile.stock_code, profile.industry].filter(Boolean).join(" · ") || (profile.profile_type === "personal" ? "个人画像" : "公司画像"))}</span>
        </div>
        <div class="wiki-page-list">
          ${pages
            .map((page) => {
              const active = page.path === selected.path ? "active" : "";
              const label = page.parts?.length ? page.parts.join(" / ") : page.path;
              return `
                <button class="wiki-page-item ${active}" data-wiki-path="${escapeHtml(page.path)}">
                  <span>${escapeHtml(page.title || label)}</span>
                  <small>${escapeHtml(label)} · ${escapeHtml(String(page.fact_count || 0))} 条事实</small>
                </button>
              `;
            })
            .join("")}
        </div>
      </section>
      <section class="wiki-detail${financeSelected ? " finance-detail" : ""}" aria-label="Wiki 页面详情">
        ${financeSelected ? renderFinanceWiki() : `
        <div class="wiki-detail-head">
          <div>
            <div class="wiki-kicker">${escapeHtml(selected.path)}</div>
            <h2>${escapeHtml(selected.title || selected.path)}</h2>
          </div>
          <div class="wiki-head-actions">
            <div class="wiki-stat-row">
              <span title="本页已保存的事实">本页 ${savedFacts.length} 条事实</span>
              <span title="本页已保存且重要性不低于 4 的事实">本页 ${savedFacts.filter((fact) => Number(fact.importance) >= 4).length} 条重点</span>
              <span id="wiki-save-status">${draft ? "未保存" : "已保存"}</span>
            </div>
            <div class="wiki-action-row">
              <button id="wiki-refresh" class="wiki-secondary-button" type="button" ${state.wikiAction ? "disabled" : ""}>${state.wikiAction === "refresh" ? "读取中" : "重新读取"}</button>
              <button id="wiki-save" class="wiki-primary-button" type="submit" form="wiki-editor-form" ${state.wikiAction ? "disabled" : ""}>
                ${state.wikiAction === "save" ? "保存中" : "保存页面"}
              </button>
            </div>
          </div>
        </div>
        <div class="wiki-meta-grid">
          ${renderWikiMeta("领域", selected.domain)}
          ${renderWikiMeta("负责人", selected.owner)}
          ${renderWikiMeta("新鲜度", selected.freshness)}
          ${renderWikiMeta("更新", selected.last_updated)}
          ${renderWikiMeta("来源", selected.source)}
        </div>
        <form id="wiki-editor-form" class="wiki-structured-editor">
          <div class="wiki-editor-head">
            <div>
              <div class="wiki-section-title">页面说明</div>
            </div>
          </div>
          <textarea id="wiki-intro-editor" class="wiki-intro-editor" aria-label="页面说明" spellcheck="false" ${state.wikiAction ? "disabled" : ""}>${escapeHtml(draft ? draft.intro : extractWikiIntro(selected.body || "", selected.title || selected.path))}</textarea>
          <div class="wiki-editor-head">
            <div>
              <div class="wiki-section-title">结构化事实</div>
            </div>
            <button id="wiki-add-fact" class="wiki-secondary-button" type="button" ${state.wikiAction ? "disabled" : ""}><span aria-hidden="true">+</span> 添加事实</button>
          </div>
          <div id="wiki-fact-form-list" class="wiki-fact-form-list">
            ${facts.length
              ? facts.map((fact, index) => renderWikiFactEditor(fact, index)).join("")
              : '<p class="wiki-no-facts">暂无事实</p>'}
          </div>
          <datalist id="wiki-source-options">
            <option value="user_confirmed" label="用户确认"></option>
            <option value="internal_wiki" label="内部资料"></option>
            <option value="project_design" label="项目设计"></option>
            <option value="analysis_inference_from_disclosed_business" label="分析推断"></option>
          </datalist>
          <details class="wiki-raw-markdown">
            <summary>原始文本</summary>
            <pre>${escapeHtml(selected.body || "")}</pre>
          </details>
        </form>`}
      </section>
    </div>
  `;
  view.querySelectorAll("[data-wiki-path]").forEach((button) => {
    button.addEventListener("click", () => {
      if (state.wikiAction) return;
      captureWikiDraft();
      state.selectedWikiPath = button.dataset.wikiPath || "";
      renderWiki();
    });
  });
  $("wiki-editor-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    saveSelectedWikiPage();
  });
  $("wiki-editor-form")?.addEventListener("input", captureWikiDraft);
  $("wiki-editor-form")?.addEventListener("change", captureWikiDraft);
  if ($("wiki-editor-form")) $("wiki-editor-form").dataset.savedSnapshot = draft?.baseline ?? JSON.stringify({
    intro: $("wiki-intro-editor").value,
    facts: readWikiFactFields($("wiki-editor-form")),
  });
  $("wiki-refresh")?.addEventListener("click", refreshWiki);
  $("wiki-add-fact")?.addEventListener("click", addWikiFactCard);
  $("wiki-new-page")?.addEventListener("click", openWikiPageDialog);
  $("wiki-new-workspace")?.addEventListener("click", openWorkspaceDialog);
  $("wiki-search-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    searchWikiFacts();
  });
  $("wiki-search-query")?.addEventListener("input", (event) => { state.wikiSearchQuery = event.target.value; });
  $("wiki-search-clear")?.addEventListener("click", clearWikiSearch);
  renderWikiSearchResults();
  bindWikiFactEditorActions();
  if (financeSelected) bindFinanceWiki();
}

const FINANCE_PROFILE_PATH = "wiki/finance_profile.md";
const FINANCE_SYMBOLS = {
  xauusd: "国际现货黄金", xagusd: "国际现货白银", spx: "标普500", ixic: "纳斯达克综合指数", dji: "道琼斯工业平均指数",
  sh000001: "上证指数", sh000300: "沪深300", sz399006: "创业板指", sz399001: "深证成指", sh000016: "上证50", sh000688: "科创50",
};

function isFinanceProfilePath(path) {
  return String(path || "").replace(/\\/g, "/").replace(/^wiki\//, "").toLowerCase() === "finance_profile.md";
}

function currentFinance() {
  return state.finance?.companyId === state.companyId ? state.finance : null;
}

async function readFinanceStatus(finance) {
  try {
    const data = await api(`/companies/${encodeURIComponent(finance.companyId)}/workbench/finance/status`);
    if (!data.status || typeof data.status !== "object") throw new Error("状态响应无效");
    finance.status = data.status;
    finance.statusError = "";
  } catch (error) {
    finance.status = null;
    finance.statusError = `采集状态读取失败：${error.message}`;
  }
}

async function loadFinanceWiki(companyId) {
  const finance = { companyId, profile: null, status: null, profileError: "", statusError: "", saveError: "", actionMessage: "" };
  try {
    const data = await api(`/companies/${encodeURIComponent(companyId)}/workbench/finance/profile`);
    if (data.profile && typeof data.profile === "object" && !Array.isArray(data.profile)) finance.profile = data.profile;
  } catch (error) {
    finance.profileError = `金融档案读取失败：${error.message}`;
  }
  if (finance.profile) await readFinanceStatus(finance);
  return finance;
}

function financeFields(profile) {
  return {
    display_name: profile.display_name || "", purpose: profile.purpose || "",
    status: profile.status || "draft", report_days: String(profile.report_days ?? 7),
    markets: (profile.markets || []).join("\n"), symbols: profile.symbols || [],
    focus_topics: (profile.focus_topics || []).join("\n"), constraints: (profile.constraints || []).join("\n"),
  };
}

function readFinanceFields() {
  const fields = {};
  for (const key of ["display_name", "purpose", "status", "report_days", "markets", "focus_topics", "constraints"]) {
    fields[key] = $(`finance-${key}`).value;
  }
  fields.symbols = Object.keys(FINANCE_SYMBOLS).filter((symbol) => $(`finance-${symbol}`).checked);
  return fields;
}

function captureFinanceDraft() {
  const finance = currentFinance();
  if (!finance?.profile || !$("finance-profile-form") || state.wikiAction) return;
  const previous = state.financeDrafts[state.companyId];
  const fields = readFinanceFields();
  const baseProfile = previous?.baseProfile || finance.profile;
  if (JSON.stringify(fields) === JSON.stringify(financeFields(finance.profile))) delete state.financeDrafts[state.companyId];
  else state.financeDrafts[state.companyId] = { fields, baseProfile, expectedVersion: baseProfile.version };
  $("finance-save-state").textContent = state.financeDrafts[state.companyId] ? "未保存" : "已保存";
}

function financeProfilePayload(base, fields) {
  const list = (value) => [...new Set(value.split(/[\n,，、;；]+/).map((item) => item.trim()).filter(Boolean))];
  const profile = { ...base, ...fields, domain: "finance", timezone: "Asia/Shanghai", holdings: base.holdings ?? null,
    report_days: Number(fields.report_days), markets: list(fields.markets),
    focus_topics: list(fields.focus_topics), constraints: list(fields.constraints),
    display_name: fields.display_name.trim(), purpose: fields.purpose.trim() };
  if (!Number.isInteger(profile.version) || profile.version < 0) throw new Error("档案版本无效，请重新读取");
  if (!["draft", "confirmed"].includes(profile.status)) throw new Error("请选择草稿或已确认");
  if (!profile.display_name || !profile.purpose) throw new Error("请填写档案名称和观察目的");
  if (!Number.isInteger(profile.report_days) || profile.report_days < 1 || profile.report_days > 365) throw new Error("报告天数须为 1 至 365 的整数");
  if (!profile.markets.length) throw new Error("请填写关注市场");
  if (!profile.symbols.length || profile.symbols.some((symbol) => !Object.hasOwn(FINANCE_SYMBOLS, symbol))) throw new Error("请至少选择一个支持的行情标的");
  if (profile.symbols.length > 8) throw new Error("行情标的最多选择 8 项");
  return profile;
}

function financeStatusLabel(value) {
  return ({ idle: "尚无定时记录", running: "采集中", paused: "已暂停", interrupted: "已中断", ok: "成功",
    partial: "部分数据", empty: "无数据", error: "失败", disabled: "已停用", busy: "已有采集任务",
    retry_wait: "等待重试", deduplicated: "已复用本轮采集" })[value] || value || "未记录";
}

function renderFinanceSourceDetail(detail) {
  if (!detail) return "";
  let issueCount = null;
  try {
    const parsed = JSON.parse(detail);
    if (Array.isArray(parsed?.issues)) issueCount = parsed.issues.length;
  } catch { /* Plain text and truncated projections have no reliable issue count. */ }
  return `${issueCount === null ? "" : `<p class="wiki-form-status">${issueCount} 项问题</p>`}<details class="finance-source-detail"><summary>技术详情</summary><pre>${escapeHtml(detail)}</pre></details>`;
}

function renderFinanceStatus(finance) {
  const status = finance.status || {};
  const sources = Array.isArray(status.source_statuses) ? status.source_statuses : [];
  const warnings = Array.isArray(status.warnings) ? status.warnings : [];
  const disabled = state.wikiAction ? "disabled" : "";
  const value = (label, text) => `<div><dt>${label}</dt><dd>${escapeHtml(String(text ?? "未记录"))}</dd></div>`;
  return `<section class="finance-collection" aria-label="只读资料采集">
    <div class="finance-section-head"><h3>资料采集 <small>只读 · 非 LLM</small></h3>
      <div class="wiki-action-row">
        <label class="finance-toggle"><input id="finance-enabled" type="checkbox" ${status.enabled === true ? "checked" : ""} ${disabled || (typeof status.enabled !== "boolean" ? "disabled" : "")} />定时采集</label>
        <button id="finance-collect" class="wiki-secondary-button" type="button" ${disabled}>${state.wikiAction === "finance-collect" ? "采集中" : "手动刷新"}</button>
        <button id="finance-status-refresh" class="wiki-secondary-button" type="button" ${disabled}>更新状态</button>
      </div>
    </div>
    <p class="wiki-form-error" role="alert">${escapeHtml(finance.statusError)}</p>
    <p class="wiki-form-status" role="status">${escapeHtml(finance.actionMessage)}</p>
    <dl class="finance-status-grid">
      ${value("采集开关", status.enabled === true ? "已开启" : status.enabled === false ? "已关闭" : "未知")}
      ${value("Worker 进程", status.worker_running === true ? "运行中" : status.worker_running === false ? "未运行" : "未知")}
      ${value("定时任务状态", financeStatusLabel(status.status))}
      ${value("正在采集", status.collecting === true ? "是" : status.collecting === false ? "否" : "未知")}
      ${value("快照采集时间", status.collected_at)}
      ${value("采集间隔", status.interval_minutes == null ? null : `${status.interval_minutes} 分钟`)}
      ${value("上次开始", status.last_started_at)}${value("上次结束", status.last_finished_at)}
      ${value("上次定时结果", financeStatusLabel(status.last_status))}${value("上次成功", status.last_success_at)}
      ${value("下次计划时间", status.next_run_at)}${value("连续失败", status.consecutive_failures)}
    </dl>
    ${status.last_error ? `<p class="wiki-form-error">${escapeHtml(status.last_error)}</p>` : ""}
    <div class="finance-section-head"><h3>数据源状态</h3></div>
    ${sources.length ? `<ul class="finance-source-list">${sources.map((source) => `<li>
      <div class="finance-source-head"><strong>${escapeHtml(source.source_id)}</strong><span>${escapeHtml(financeStatusLabel(source.status))}</span><span>${escapeHtml(String(source.count ?? "未记录"))} 条</span></div>
      <dl class="finance-source-meta">${value("最新日期", source.latest_date || null)}${value("耗时", source.elapsed_ms == null ? null : `${source.elapsed_ms} ms`)}</dl>
      ${renderFinanceSourceDetail(source.detail)}
    </li>`).join("")}</ul>` : '<p class="wiki-form-status">暂无数据源记录</p>'}
    ${warnings.length ? `<ul class="finance-warnings" aria-label="采集提示">${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
    ${status.snapshot_projection_truncated ? '<p class="wiki-form-status">状态摘要已截断</p>' : ""}
  </section>`;
}

function renderFinanceWiki() {
  const finance = currentFinance();
  if (!finance?.profile) return `<div class="wiki-detail-head"><h2>金融观察档案</h2><button id="wiki-refresh" class="wiki-secondary-button" type="button">重新读取</button></div><p class="finance-unavailable">${escapeHtml(finance?.profileError || "当前工作空间无金融档案，此页面只读。")}</p>`;
  const profile = finance.profile;
  const draft = state.financeDrafts[state.companyId];
  const fields = draft?.fields || financeFields(profile);
  const holdingsLabel = Array.isArray(profile.holdings)
    ? `已记录 ${profile.holdings.length} 项；有效性以原记录为准`
    : profile.holdings_history_path ? "当前未确认；历史记录见「历史持仓」" : "未提供";
  const disabled = state.wikiAction ? "disabled" : "";
  const field = (key, label, multiline = false, required = false) => `<label for="finance-${key}">${label}${multiline
    ? `<textarea id="finance-${key}" name="${key}" rows="2" ${required ? "required" : ""}>${escapeHtml(fields[key])}</textarea>`
    : `<input id="finance-${key}" name="${key}" value="${escapeHtml(fields[key])}" maxlength="2000" ${required ? "required" : ""} />`}</label>`;
  return `<div class="wiki-detail-head"><div><h2>金融观察档案</h2>
    <div class="finance-profile-meta"><span class="finance-badge ${profile.status === "confirmed" ? "confirmed" : "draft"}">${profile.status === "confirmed" ? "已确认" : "草稿 · 待确认"}</span><span>版本 ${escapeHtml(String(profile.version))}</span><span id="finance-save-state" role="status">${draft ? "未保存" : "已保存"}</span></div>
    </div><button id="finance-profile-reload" class="wiki-secondary-button" type="button" ${disabled}>重新读取</button></div>
    <form id="finance-profile-form" class="finance-profile-form">
      <fieldset class="finance-fields" ${disabled}>
        <legend class="sr-only">金融档案</legend>
        ${field("display_name", "档案名称", false, true)}
        <label for="finance-status">保存状态<select id="finance-status" name="status"><option value="draft" ${fields.status !== "confirmed" ? "selected" : ""}>草稿（待确认）</option><option value="confirmed" ${fields.status === "confirmed" ? "selected" : ""}>已确认</option></select></label>
        ${field("purpose", "观察目的", false, true)}
        <label for="finance-report_days">报告天数<input id="finance-report_days" name="report_days" type="number" min="1" max="365" step="1" required value="${escapeHtml(fields.report_days)}" /></label>
        ${field("markets", "关注市场（每行一项）", true, true)}${field("focus_topics", "关注主题（每行一项）", true)}
        <fieldset class="finance-symbols"><legend>行情观察标的（贵金属采用国际现货参考基准） · 最多 8 项</legend>${Object.entries(FINANCE_SYMBOLS).map(([symbol, name]) => `<label for="finance-${symbol}"><input id="finance-${symbol}" type="checkbox" name="symbols" value="${symbol}" ${fields.symbols.includes(symbol) ? "checked" : ""} /><span>${name}<small>${symbol} · ${["xauusd", "xagusd"].includes(symbol) ? "USD/troy oz（美元/金衡盎司）" : "点 · 价格指数"}</small></span></label>`).join("")}</fieldset>
        ${field("constraints", "观察约束（每行一项）", true)}
        <label for="finance-timezone">时区<input id="finance-timezone" value="Asia/Shanghai" readonly /></label>
        <label for="finance-holdings">持仓<input id="finance-holdings" value="${escapeHtml(holdingsLabel)}" readonly title="${escapeHtml(holdingsLabel)}" /></label>
      </fieldset>
      <p id="finance-save-error" class="wiki-form-error" role="alert">${escapeHtml(finance.saveError)}</p>
      <div class="wiki-action-row"><button id="finance-save" class="wiki-primary-button" type="submit" ${disabled}>${state.wikiAction === "finance-save" ? "保存中" : "保存档案"}</button></div>
    </form>${renderFinanceStatus(finance)}`;
}

function bindFinanceWiki() {
  $("finance-profile-form")?.addEventListener("input", captureFinanceDraft);
  $("finance-profile-form")?.addEventListener("change", captureFinanceDraft);
  $("finance-profile-form")?.addEventListener("submit", (event) => { event.preventDefault(); saveFinanceProfile(); });
  $("finance-profile-reload")?.addEventListener("click", reloadFinanceProfile);
  $("finance-collect")?.addEventListener("click", () => runFinanceAction("collect"));
  $("finance-status-refresh")?.addEventListener("click", () => runFinanceAction("status"));
  $("finance-enabled")?.addEventListener("change", (event) => runFinanceAction("schedule", event.target.checked));
}

async function saveFinanceProfile() {
  const finance = currentFinance();
  const form = $("finance-profile-form");
  if (!finance?.profile || state.wikiAction || !form?.reportValidity()) return;
  captureFinanceDraft();
  const draft = state.financeDrafts[state.companyId];
  let profile;
  try {
    profile = financeProfilePayload(draft?.baseProfile || finance.profile, readFinanceFields());
  } catch (error) {
    $("finance-save-error").textContent = error.message;
    return;
  }
  state.wikiAction = "finance-save";
  finance.saveError = "";
  renderWiki();
  renderCompanies();
  try {
    const data = await api(`/companies/${encodeURIComponent(finance.companyId)}/workbench/finance/profile`, {
      method: "POST", body: JSON.stringify({ profile, expected_version: draft?.expectedVersion ?? finance.profile.version }),
    });
    if (!data.profile || !Number.isInteger(data.profile.version)) throw new Error("保存响应无效，请重新读取以核对版本");
    finance.profile = data.profile;
    delete state.financeDrafts[finance.companyId];
    if (currentFinance() === finance) clearWikiSearch();
    toast("金融档案已保存");
  } catch (error) {
    finance.saveError = `保存失败，修改已保留：${error.message}`;
  } finally {
    state.wikiAction = "";
    if (currentFinance() === finance) renderWiki();
    renderCompanies();
  }
}

async function reloadFinanceProfile() {
  if (state.wikiAction || !currentFinance()?.profile) return;
  captureFinanceDraft();
  const companyId = state.companyId;
  if (state.financeDrafts[companyId] && !window.confirm("放弃金融档案未保存的修改并重新读取？")) return;
  state.wikiAction = "finance-reload";
  renderWiki();
  renderCompanies();
  try {
    await loadWiki();
    if (companyId === state.companyId && !currentFinance()?.profileError) delete state.financeDrafts[companyId];
  } finally {
    state.wikiAction = "";
    renderWiki();
    renderCompanies();
  }
}

async function runFinanceAction(action, enabled) {
  const finance = currentFinance();
  if (!finance?.profile || state.wikiAction || !["status", "collect", "schedule"].includes(action)) return;
  if (action === "schedule" && typeof enabled !== "boolean") return;
  captureFinanceDraft();
  state.wikiAction = `finance-${action}`;
  finance.actionMessage = "";
  renderWiki();
  renderCompanies();
  try {
    if (action !== "status") {
      const data = await api(`/companies/${encodeURIComponent(finance.companyId)}/workbench/finance/${action}`, {
        method: "POST", body: JSON.stringify(action === "schedule" ? { enabled } : {}),
      });
      finance.actionMessage = action === "schedule" ? "采集开关已更新"
        : `采集请求已返回：${financeStatusLabel(data.status)}${data.collection_status ? `（${financeStatusLabel(data.collection_status)}）` : ""}${data.error ? `：${data.error}` : ""}`;
    }
  } catch (error) {
    finance.actionMessage = `操作失败：${error.message}`;
  } finally {
    await readFinanceStatus(finance);
    state.wikiAction = "";
    if (currentFinance() === finance) renderWiki();
    renderCompanies();
  }
}

function clearWikiSearch() {
  state.wikiSearchEpoch += 1;
  state.wikiSearchQuery = "";
  state.wikiSearchResults = null;
  state.wikiSearchError = "";
  state.wikiSearchBusy = false;
  if ($("wiki-search-query")) $("wiki-search-query").value = "";
  renderWikiSearchResults();
}

async function searchWikiFacts() {
  const query = ($("wiki-search-query")?.value ?? state.wikiSearchQuery).trim();
  if (!query || !state.companyId) return;
  const companyId = state.companyId;
  const epoch = ++state.wikiSearchEpoch;
  state.wikiSearchQuery = query;
  state.wikiSearchBusy = true;
  state.wikiSearchError = "";
  state.wikiSearchResults = null;
  renderWikiSearchResults();
  try {
    const data = await api(`/companies/${encodeURIComponent(companyId)}/workbench/wiki/search?${new URLSearchParams({ query, top_k: "20" })}`);
    if (epoch !== state.wikiSearchEpoch || companyId !== state.companyId) return;
    state.wikiSearchResults = Array.isArray(data.results) ? data.results : [];
  } catch (error) {
    if (epoch !== state.wikiSearchEpoch || companyId !== state.companyId) return;
    state.wikiSearchError = `检索失败：${error.message}`;
  } finally {
    if (epoch === state.wikiSearchEpoch && companyId === state.companyId) {
      state.wikiSearchBusy = false;
      renderWikiSearchResults();
    }
  }
}

function wikiSourceKindLabel(kind) {
  return ({ public_disclosure: "公开披露", user_confirmed: "用户确认", project_design: "项目设计", analysis_inference: "分析推断", other: "其他来源" })[kind] || kind || "未记录来源";
}

function renderWikiSearchResults() {
  const container = $("wiki-search-results");
  if (!container) return;
  container.setAttribute("aria-busy", String(state.wikiSearchBusy));
  if (state.wikiSearchBusy) container.innerHTML = '<p class="wiki-search-status">正在检索…</p>';
  else if (state.wikiSearchError) container.innerHTML = `<p class="wiki-form-error" role="alert">${escapeHtml(state.wikiSearchError)}</p>`;
  else if (state.wikiSearchResults === null) container.innerHTML = "";
  else if (!state.wikiSearchResults.length) container.innerHTML = '<p class="wiki-search-status">未找到匹配事实</p>';
  else {
    container.innerHTML = `<p class="wiki-search-status">返回 ${state.wikiSearchResults.length} 条事实</p><ol class="wiki-result-list">${state.wikiSearchResults.map((result) => {
      const fact = result.fact || {};
      return `<li class="wiki-result">
        <div class="wiki-result-head"><strong>${escapeHtml(fact.fact_id || "")}</strong><button class="wiki-secondary-button" type="button" data-wiki-result-path="${escapeHtml(fact.source_path || "")}" data-wiki-result-fact="${escapeHtml(fact.fact_id || "")}">打开页面 <span aria-hidden="true">→</span></button></div>
        <p>${escapeHtml(String(fact.value ?? fact.text ?? ""))}</p>
        <dl class="wiki-result-meta">
          <div><dt>来源类型</dt><dd>${escapeHtml(wikiSourceKindLabel(fact.source_kind))}</dd></div>
          <div><dt>置信度</dt><dd>${escapeHtml(String(fact.confidence ?? "未记录"))}</dd></div>
          <div><dt>检索分</dt><dd>${escapeHtml(String(result.retrieval_score ?? "未记录"))}</dd></div>
          <div><dt>来源</dt><dd>${escapeHtml(String(fact.source || "未记录"))}</dd></div>
          <div><dt>页面</dt><dd>${escapeHtml(fact.source_path || "未记录")}</dd></div>
        </dl>
      </li>`;
    }).join("")}</ol>`;
    container.querySelectorAll("[data-wiki-result-path]").forEach((button) => {
      button.addEventListener("click", () => openWikiSearchResult(button.dataset.wikiResultPath, button.dataset.wikiResultFact));
    });
  }
}

async function openWikiSearchResult(path, factId) {
  if (state.wikiAction) return;
  const companyId = state.companyId;
  captureWikiDraft();
  if (!(state.wiki?.pages || []).some((page) => page.path === path)) await loadWiki();
  if (companyId !== state.companyId) return;
  if (!(state.wiki?.pages || []).some((page) => page.path === path)) {
    toast("页面已变更，请重新检索");
    return;
  }
  state.selectedWikiPath = path;
  renderWiki();
  const input = [...$("wiki-view").querySelectorAll(".wiki-fact-id-input")].find((item) => item.value === factId);
  (input?.closest(".wiki-fact-editor") || $("wiki-editor-form"))?.scrollIntoView({ block: "nearest" });
  input?.focus({ preventScroll: true });
}

function showWikiFormError(id, message = "") {
  const element = $(id);
  element.textContent = message;
  element.classList.toggle("hidden", !message);
}

function setWikiFormBusy(form, busy) {
  form.setAttribute("aria-busy", String(busy));
  form.querySelectorAll("input, select, textarea, button").forEach((element) => { element.disabled = busy; });
}

function openWorkspaceDialog() {
  if (state.sending || state.wikiAction || state.workspaceBusy) {
    toast("当前操作完成后可创建工作空间");
    return;
  }
  captureWikiDraft();
  if (!state.workspaceCreation) {
    $("workspace-create-form").reset();
    $("workspace-identity-fields").disabled = false;
    $("workspace-id").value = `ws_${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`;
    $("workspace-create-status").textContent = "";
    $("workspace-create-submit").textContent = "创建空间";
    showWikiFormError("workspace-create-error");
  }
  $("workspace-dialog").showModal();
  $(state.workspaceCreation ? "workspace-create-submit" : "workspace-name").focus();
}

async function submitWorkspaceCreation(event) {
  event.preventDefault();
  if (state.workspaceBusy || state.sending || state.wikiAction) return;
  const form = $("workspace-create-form");
  if (!form.reportValidity()) return;
  const profile = {
    company_id: $("workspace-id").value.trim(),
    name: $("workspace-name").value.trim(),
    profile_type: $("workspace-profile-type").value,
  };
  const sections = [
    { key: "background", title: "背景", filename: "00_background.md" },
    { key: "goals", title: "目标", filename: "01_goals.md" },
    { key: "constraints", title: "约束", filename: "02_constraints.md" },
  ].map((section) => ({ ...section, value: $(`workspace-${section.key}`).value.trim() }));
  if (!profile.name) { showWikiFormError("workspace-create-error", "请输入空间名称"); return; }
  state.workspaceBusy = true;
  setWikiFormBusy(form, true);
  renderCompanies();
  showWikiFormError("workspace-create-error");
  try {
    if (!state.workspaceCreation) {
      $("workspace-create-status").textContent = "正在创建工作空间…";
      const created = await api("/companies", { method: "POST", body: JSON.stringify(profile) });
      state.workspaceCreation = { companyId: created.company_id, wiki: created.wiki, saved: new Set() };
    }
    const creation = state.workspaceCreation;
    await refreshWorkspaceOptions();
    for (const section of sections) {
      if (!section.value || creation.saved.has(section.key)) continue;
      $("workspace-create-status").textContent = `空间已创建，正在保存${section.title}…`;
      const page = (creation.wiki?.pages || []).find((item) => item.title === section.title || item.path?.endsWith(`/${section.filename}`));
      if (!page) throw new Error(`未找到${section.title}页面，请重新读取工作空间`);
      const fact = { fact_id: `profile.${section.key}`, value: section.value, source: "user_confirmed", importance: 3, confidence: 1, policy_relevance: [], note: "" };
      const body = assembleStructuredWikiBody(`# ${section.title}`, [fact], page.path, creation.wiki);
      const updated = await api(`/companies/${encodeURIComponent(creation.companyId)}/workbench/wiki/pages`, { method: "POST", body: JSON.stringify({ path: page.path, body }) });
      creation.wiki = updated.wiki;
      creation.saved.add(section.key);
    }
    await refreshWorkspaceOptions();
    await switchWorkspace(creation.companyId, "wiki");
    state.workspaceCreation = null;
    $("workspace-dialog").close();
    toast("工作空间已创建");
  } catch (error) {
    const prefix = state.workspaceCreation ? "空间已创建，后续步骤未完成，可重试：" : "创建失败：";
    showWikiFormError("workspace-create-error", `${prefix}${error.message}`);
    $("workspace-create-status").textContent = state.workspaceCreation ? `空间标识：${state.workspaceCreation.companyId}` : "";
  } finally {
    state.workspaceBusy = false;
    setWikiFormBusy(form, false);
    $("workspace-identity-fields").disabled = Boolean(state.workspaceCreation);
    for (const section of sections) $(`workspace-${section.key}`).disabled = Boolean(state.workspaceCreation?.saved.has(section.key));
    $("workspace-create-submit").textContent = state.workspaceCreation ? "重试未完成步骤" : "创建空间";
    renderCompanies();
  }
}

function wikiPagePathFromTitle(title) {
  const name = title.trim();
  if (!name || name.length > 80 || /[<>:"/\\|?*\x00-\x1f]/.test(name) || /[. ]$/.test(name) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)) {
    throw new Error("页面名称不能为空，且不能包含路径分隔符、保留名称或特殊文件名字符");
  }
  return `wiki/${name}.md`;
}

function openWikiPageDialog() {
  if (!state.companyId || state.wikiAction || state.workspaceBusy) return;
  captureWikiDraft();
  state.pageCreationCompanyId = state.companyId;
  $("wiki-page-create-form").reset();
  $("wiki-page-facts").innerHTML = '<p class="wiki-no-facts">暂无事实</p>';
  $("wiki-page-workspace").textContent = `${currentCompany().company_name} · ${state.companyId}`;
  showWikiFormError("wiki-page-create-error");
  $("wiki-page-dialog").showModal();
  $("wiki-page-title").focus();
}

function addNewPageFact() {
  if (state.wikiAction) return;
  let path;
  try {
    path = wikiPagePathFromTitle($("wiki-page-title").value);
  } catch (error) {
    showWikiFormError("wiki-page-create-error", error.message);
    $("wiki-page-title").focus();
    return;
  }
  showWikiFormError("wiki-page-create-error");
  const list = $("wiki-page-facts");
  list.querySelector(".wiki-no-facts")?.remove();
  const index = list.querySelectorAll(".wiki-fact-editor").length;
  list.insertAdjacentHTML("beforeend", renderWikiFactEditor(defaultWikiFact({ path }), index));
  bindWikiFactEditorActions();
  list.lastElementChild?.querySelector(".wiki-fact-value-input")?.focus();
}

async function submitWikiPageCreation(event) {
  event.preventDefault();
  if (state.wikiAction || state.workspaceBusy) return;
  const form = $("wiki-page-create-form");
  if (!form.reportValidity()) return;
  const companyId = state.pageCreationCompanyId;
  let path, body;
  try {
    if (!companyId || companyId !== state.companyId) throw new Error("工作空间已变更，请重新打开新建页面");
    path = wikiPagePathFromTitle($("wiki-page-title").value);
    if ((state.wiki?.pages || []).some((page) => page.path.toLowerCase() === path.toLowerCase())) throw new Error("同名页面已存在");
    const description = $("wiki-page-description").value.trim();
    body = assembleStructuredWikiBody([`# ${$("wiki-page-title").value.trim()}`, description].filter(Boolean).join("\n\n"), readWikiFactFields(form), path);
  } catch (error) {
    showWikiFormError("wiki-page-create-error", error.message);
    return;
  }
  state.wikiAction = "create";
  setWikiFormBusy(form, true);
  $("wiki-page-create-submit").textContent = "创建中…";
  showWikiFormError("wiki-page-create-error");
  renderCompanies();
  try {
    const data = await api(`/companies/${encodeURIComponent(companyId)}/workbench/wiki/pages`, { method: "POST", body: JSON.stringify({ path, body, create: true }) });
    state.wiki = data.wiki;
    state.selectedWikiPath = data.page.path;
    clearWikiSearch();
    $("wiki-page-dialog").close();
    toast("页面已创建");
    refreshWorkspaceOptions().then(renderCompanies).catch((error) => toast(`页面已创建，空间列表更新失败：${error.message}`));
  } catch (error) {
    showWikiFormError("wiki-page-create-error", `创建失败：${error.message}`);
  } finally {
    state.wikiAction = "";
    setWikiFormBusy(form, false);
    $("wiki-page-create-submit").textContent = "创建页面";
    renderCompanies();
    renderWiki();
  }
}

function renderWikiMeta(label, value) {
  return `
    <div class="wiki-meta-cell">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "未记录")}</strong>
    </div>
  `;
}

function wikiPageGuidance(page) {
  const domain = String(page?.domain || page?.path || "").toLowerCase();
  const guidance = [
    {
      match: ["profile", "00_profile"],
      text: "维护企业身份、地域、行业和报告默认关注方向。这些事实决定系统默认报告会把哪些外部变化判断为相关。",
    },
    {
      match: ["business", "01_business"],
      text: "维护核心产品、客户类型、收入来源和近期目标。这些事实决定报告里的影响链条和行动建议是否贴近业务。",
    },
    {
      match: ["operations", "02_operations"],
      text: "维护人员、研发、数据、供应链和上线约束。这些事实用于判断政策/技术变化是否会影响交付成本和合规边界。",
    },
    {
      match: ["qualification", "03_qualifications"],
      text: "维护资质、证书、专利、软著和有效期。这些事实用于判断政策适用性和申报机会。",
    },
    {
      match: ["policy_history", "04_policy_history"],
      text: "维护历史申报、获批、失败原因和主管部门沟通记录。这些事实用于避免重复建议和复用历史决策。",
    },
    {
      match: ["preference", "08_preferences"],
      text: "维护报告风格、风险偏好、人工确认边界和固定口径。这些事实用于控制报告输出方式。",
    },
  ];
  const hit = guidance.find((item) => item.match.some((term) => domain.includes(term)));
  return hit?.text || "维护会长期影响报告判断的稳定事实。临时观点和一次性讨论建议放在对话里，不要写进 Wiki。";
}

function extractWikiIntro(body, fallbackTitle = "") {
  const text = String(body || "");
  const index = text.search(/^## FACT:/m);
  const intro = (index >= 0 ? text.slice(0, index) : text).trim();
  if (intro) return intro;
  const title = String(fallbackTitle || "").trim();
  return title ? `# ${title}\n\n请补充这页的稳定背景。` : "请补充这页的稳定背景。";
}

function wikiFactIdPrefix(page) {
  const path = String(page?.path || page?.domain || "fact").replaceAll("\\", "/").normalize("NFC").toLowerCase();
  const slug = path
    .replace(/^wiki\//, "")
    .replace(/\.md$/, "")
    .replace(/[^a-z0-9_.-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 36);
  let hash = 2166136261;
  for (const character of path) hash = Math.imul(hash ^ character.codePointAt(0), 16777619) >>> 0;
  return `${slug || "page"}_${hash.toString(16).padStart(8, "0")}`;
}

function defaultWikiFact(page) {
  const prefix = wikiFactIdPrefix(page);
  const used = new Set([
    ...(state.wiki?.facts || []).map((fact) => fact.fact_id),
    ...(state.wiki?.pages || []).flatMap((item) => (item.facts || []).map((fact) => fact.fact_id)),
  ]);
  document.querySelectorAll(".wiki-fact-id-input").forEach((input) => used.add(input.value.trim()));
  let index = 1;
  while (used.has(`${prefix}.fact_${index}`)) index += 1;
  return {
    fact_id: `${prefix}.fact_${index}`,
    value: "",
    importance: Number(page?.importance || 3),
    confidence: 0.7,
    source: "user_confirmed",
    policy_relevance: [],
    text: "",
  };
}

function wikiFactNote(fact) {
  if (fact.note !== undefined) return fact.note;
  const text = String(fact.text || "").trim();
  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  if (text === String(fact.value ?? "") || lines.every((line) => /^- [^:]+:/.test(line))) return "";
  return text;
}

function renderWikiFactEditor(fact, index) {
  const relevance = Array.isArray(fact.policy_relevance)
    ? fact.policy_relevance.join(", ")
    : String(fact.policy_relevance || "");
  const factId = fact.fact_id ?? "";
  return `
    <article class="wiki-fact-editor" data-fact-index="${escapeHtml(String(index))}">
      <div class="wiki-fact-editor-top">
        <label>
          <span>事实 ID</span>
          <input class="wiki-fact-id-input" value="${escapeHtml(factId)}" required pattern="([A-Za-z0-9_.]|-)+" spellcheck="false" />
        </label>
        <label>
          <span>重要性</span>
          <select class="wiki-fact-importance-input">
            ${[5, 4, 3, 2, 1]
              .map((value) => `<option value="${value}" ${Number(fact.importance ?? 3) === value ? "selected" : ""}>${value}</option>`)
              .join("")}
          </select>
        </label>
        <label>
          <span>置信度</span>
          <input class="wiki-fact-confidence-input" type="number" min="0" max="1" step="any" required value="${escapeHtml(String(fact.confidence ?? 0.7))}" />
        </label>
      </div>
      <label class="wiki-field-block">
        <span>事实内容</span>
        <textarea class="wiki-fact-value-input" rows="2" required>${escapeHtml(fact.value ?? fact.text ?? "")}</textarea>
      </label>
      <div class="wiki-fact-editor-grid">
        <label>
          <span>来源</span>
          <input class="wiki-fact-source-input" value="${escapeHtml(fact.source || "user_confirmed")}" list="wiki-source-options" placeholder="user_confirmed" spellcheck="false" />
        </label>
        <label>
          <span>关联标签</span>
          <input class="wiki-fact-relevance-input" value="${escapeHtml(relevance)}" placeholder="agent, finance_ai, compliance" spellcheck="false" />
        </label>
      </div>
      <label class="wiki-field-block">
        <span>补充说明</span>
        <textarea class="wiki-fact-note-input" rows="2">${escapeHtml(wikiFactNote(fact))}</textarea>
      </label>
      <div class="wiki-fact-editor-actions">
        <button class="wiki-remove-fact" type="button">删除事实</button>
      </div>
    </article>
  `;
}

function addWikiFactCard() {
  if (state.wikiAction) return;
  const list = $("wiki-fact-form-list");
  if (!list) return;
  const selected = (state.wiki?.pages || []).find((page) => page.path === state.selectedWikiPath) || {};
  const index = list.querySelectorAll(".wiki-fact-editor").length;
  list.querySelector(".wiki-no-facts")?.remove();
  list.insertAdjacentHTML("beforeend", renderWikiFactEditor(defaultWikiFact(selected), index));
  const card = list.lastElementChild;
  card?.querySelector(".wiki-fact-id-input")?.focus();
  bindWikiFactEditorActions();
  captureWikiDraft();
}

function bindWikiFactEditorActions() {
  document.querySelectorAll(".wiki-remove-fact").forEach((button) => {
    button.onclick = () => {
      if (state.wikiAction || state.workspaceBusy) return;
      const list = button.closest(".wiki-fact-form-list");
      button.closest(".wiki-fact-editor")?.remove();
      if (list && !list.querySelector(".wiki-fact-editor")) list.innerHTML = '<p class="wiki-no-facts">暂无事实</p>';
      if (list?.id === "wiki-fact-form-list") captureWikiDraft();
    };
  });
}

function parseWikiTags(value) {
  return String(value || "")
    .split(/[,\n，、]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function wikiInlineValue(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function renderFactMarkdown(fact) {
  const tags = fact.policy_relevance.length
    ? `[${fact.policy_relevance.map((item) => JSON.stringify(wikiInlineValue(item))).join(", ")}]`
    : "[]";
  const note = fact.note ? `\n\n${fact.note}` : "";
  return [
    `## FACT: ${fact.fact_id}`,
    `- value: ${JSON.stringify(String(fact.value).trim())}`,
    `- importance: ${fact.importance}`,
    `- confidence: ${fact.confidence}`,
    `- source: ${JSON.stringify(String(fact.source).trim())}`,
    `- policy_relevance: ${tags}`,
    note,
  ].join("\n");
}

function readWikiFactFields(container) {
  return Array.from(container.querySelectorAll(".wiki-fact-editor")).map((card) => ({
    fact_id: card.querySelector(".wiki-fact-id-input")?.value.trim() || "",
    value: card.querySelector(".wiki-fact-value-input")?.value.trim() || "",
    importance: card.querySelector(".wiki-fact-importance-input")?.value ?? "3",
    confidence: card.querySelector(".wiki-fact-confidence-input")?.value ?? "0.7",
    source: card.querySelector(".wiki-fact-source-input")?.value.trim() || "user_confirmed",
    policy_relevance: parseWikiTags(card.querySelector(".wiki-fact-relevance-input")?.value || ""),
    note: card.querySelector(".wiki-fact-note-input")?.value.trim() || "",
  }));
}

function assembleStructuredWikiBody(intro, inputFacts, pagePath, wiki = state.wiki) {
  if (/^\s*## FACT:/m.test(intro) || /^---/.test(intro.trim())) {
    throw new Error("页面说明不能包含事实块或元数据，请在事实表单中填写");
  }
  const facts = inputFacts.map((fact) => ({ ...fact }));
  const seen = new Set((wiki?.pages || [])
    .filter((page) => page.path !== pagePath)
    .flatMap((page) => (page.facts || []).map((fact) => fact.fact_id)));
  for (const fact of facts) {
    if (!fact.fact_id) throw new Error("存在空的事实 ID");
    if (!/^[A-Za-z0-9_.-]+$/.test(fact.fact_id)) {
      throw new Error(`事实 ID 只能包含字母、数字、点、下划线和短横线：${fact.fact_id}`);
    }
    if (seen.has(fact.fact_id)) throw new Error(`当前工作空间内事实 ID 重复：${fact.fact_id}`);
    seen.add(fact.fact_id);
    if (!fact.value) throw new Error(`事实 ${fact.fact_id} 缺少事实内容`);
    fact.importance = Number(fact.importance);
    if (!Number.isInteger(fact.importance) || fact.importance < 1 || fact.importance > 5) throw new Error("重要性须为 1 至 5 的整数");
    if (fact.confidence === "" || !Number.isFinite(Number(fact.confidence)) || Number(fact.confidence) < 0 || Number(fact.confidence) > 1) throw new Error("置信度须为 0 至 1 的数值");
    fact.confidence = Number(fact.confidence);
    if (/^\s*## FACT:/m.test(fact.note || "")) throw new Error("补充说明不能包含其他事实块");
    if (/^\s*-\s+(value|source|importance|confidence|policy_relevance):/m.test(fact.note || "")) throw new Error("补充说明不能包含事实元数据，请使用对应字段");
  }
  return [intro, ...facts.map(renderFactMarkdown)].filter(Boolean).join("\n\n");
}

function buildStructuredWikiBody() {
  return assembleStructuredWikiBody(
    ($("wiki-intro-editor")?.value || "").trim(),
    readWikiFactFields($("wiki-editor-form")),
    state.selectedWikiPath,
  );
}

function wikiDraftKey(companyId, path) {
  return `${companyId}:${path}`;
}

function captureWikiDraft() {
  captureFinanceDraft();
  const form = $("wiki-editor-form");
  if (!form || state.wikiAction) return;
  const draft = { intro: $("wiki-intro-editor").value, facts: readWikiFactFields(form) };
  const key = wikiDraftKey(state.companyId, state.selectedWikiPath);
  const baseline = state.wikiDrafts[key]?.baseline ?? form.dataset.savedSnapshot;
  if (JSON.stringify(draft) === baseline) delete state.wikiDrafts[key];
  else state.wikiDrafts[key] = { ...draft, baseline };
  $("wiki-save-status").textContent = state.wikiDrafts[key] ? "未保存" : "已保存";
}

function renderWikiFact(fact) {
  const relevance = Array.isArray(fact.policy_relevance) ? fact.policy_relevance.join(" / ") : "";
  return `
    <article class="wiki-fact">
      <div class="wiki-fact-top">
        <strong>${escapeHtml(fact.fact_id || "fact")}</strong>
        <span>${escapeHtml(fact.source_kind || "source")}</span>
      </div>
      <p>${escapeHtml(fact.value || fact.text || "未记录事实内容")}</p>
      <div class="wiki-fact-meta">
        <span>importance ${escapeHtml(String(fact.importance ?? "n/a"))}</span>
        <span>confidence ${escapeHtml(String(fact.confidence ?? "n/a"))}</span>
        ${relevance ? `<span>${escapeHtml(relevance)}</span>` : ""}
      </div>
    </article>
  `;
}

function renderErrorNotice() {
  if (!state.lastError) return "";
  return `
    <div class="error-notice">
      <strong>本轮未执行</strong>
      <span>${escapeHtml(state.lastError)}</span>
    </div>
  `;
}

function formatMessage(content, artifacts = {}) {
  if (isBrokenText(content)) {
    return '<div class="message-line muted">历史消息编码异常，原文不可恢复。</div>';
  }
  const hasArtifacts = Object.values(artifacts || {}).some(Boolean);
  const artifactLine = /^(Markdown 报告|HTML 报告|RunArtifact|运行产物|结构化产物)[：:]/i;
  const visibleContent = redactLocalPaths(
    hasArtifacts
      ? String(content || "")
        .split(/\r?\n/)
        .filter((line) => !artifactLine.test(line.trim()))
        .join("\n")
        .replace(/\n{3,}/g, "\n\n")
        .trim()
      : content,
  );
  const lines = String(visibleContent || "").split(/\r?\n/);
  const blocks = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();
    const fence = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (fence) {
      const code = [];
      while (++index < lines.length) {
        const closing = lines[index].match(/^ {0,3}(`{3,}|~{3,})[ \t]*$/);
        if (closing && closing[1][0] === fence[1][0] && closing[1].length >= fence[1].length) break;
        code.push(lines[index]);
      }
      blocks.push(`<pre class="message-code"><code>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }
    const heading = line.match(/^ {0,3}(#{1,6})(?:[ \t]+(.*)|$)/);
    if (heading) {
      const level = heading[1].length;
      blocks.push(`<h${level} class="message-heading">${formatInlineMarkdown((heading[2] || "").replace(/[ \t]+#+[ \t]*$/, ""))}</h${level}>`);
      continue;
    }
    const header = splitMarkdownTableRow(line);
    const separator = splitMarkdownTableRow(lines[index + 1] || "");
    if (header?.length && separator?.length === header.length && separator.every((cell) => /^:?-{3,}:?$/.test(cell))) {
      const cellHtml = (cells, tag) => `<tr>${header.map((_, column) => {
        const align = separator[column].endsWith(":") ? (separator[column].startsWith(":") ? "center" : "right") : "left";
        return `<${tag}${tag === "th" ? ' scope="col"' : ""} class="message-cell-${align}">${formatInlineMarkdown(cells[column] || "")}</${tag}>`;
      }).join("")}</tr>`;
      const rows = [];
      index += 1;
      let cells;
      while (index + 1 < lines.length && (cells = splitMarkdownTableRow(lines[index + 1]))) {
        rows.push(cellHtml(cells, "td"));
        index += 1;
      }
      blocks.push(`<div class="message-table" role="region" aria-label="报告表格" tabindex="0"><table><thead>${cellHtml(header, "th")}</thead><tbody>${rows.join("")}</tbody></table></div>`);
      continue;
    }
    if (!trimmed) { blocks.push('<div class="message-spacer"></div>'); continue; }
    const inline = formatInlineMarkdown(trimmed.replace(/^[-*+]\s+/, "").replace(/^\d+\.\s+/, ""));
    const numbered = trimmed.match(/^(\d+)\.\s+/);
    if (/^[-*+]\s+/.test(trimmed)) blocks.push(`<div class="message-list-item"><span>•</span><div>${inline}</div></div>`);
    else if (numbered) blocks.push(`<div class="message-list-item"><span>${escapeHtml(numbered[1])}.</span><div>${inline}</div></div>`);
    else blocks.push(`<div class="message-line">${inline}</div>`);
  }
  return blocks.join("");
}

function splitMarkdownTableRow(line) {
  let text = line.trim();
  if (!text.includes("|")) return null;
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|") && !text.endsWith("\\|")) text = text.slice(0, -1);
  const cells = [""];
  let codeTicks = 0;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === "\\" && text[index + 1] === "|") { cells[cells.length - 1] += "|"; index += 1; }
    else if (char === "`") {
      const ticks = text.slice(index).match(/^`+/)[0];
      codeTicks = codeTicks === ticks.length ? 0 : codeTicks || ticks.length;
      cells[cells.length - 1] += ticks;
      index += ticks.length - 1;
    } else if (char === "|" && !codeTicks) cells.push("");
    else cells[cells.length - 1] += char;
  }
  return cells.map((cell) => cell.trim());
}

function safeMarkdownHref(value) {
  if (!/^https?:\/\//i.test(value) || /[\u0000-\u0020\u007f<>"'`\\]/.test(value)) return "";
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && url.hostname ? value : "";
  } catch { return ""; }
}

function trimMarkdownUrl(value) {
  let url = value.replace(/[.,!?;:]+$/, "");
  const openers = { ")": "(", "]": "[", "}": "{" };
  while (Object.hasOwn(openers, url.at(-1))) {
    const close = url.at(-1);
    if (url.split(close).length <= url.split(openers[close]).length) break;
    url = url.slice(0, -1).replace(/[.,!?;:]+$/, "");
  }
  return url;
}

function formatInlineMarkdown(value, allowLinks = true, depth = 0) {
  const text = String(value ?? "");
  if (depth > 12) return escapeHtml(text);
  // Tokenize raw text once; generated HTML must never be fed back into link matching.
  const tokens = /(`+)([\s\S]*?)\1(?!`)|\[([^\]\n]+)\]\((<[^>\n]+>|(?:[^()\\]|\\[\\()]|\([^()\n]*\))+)\)|\*\*([^\n]+?)\*\*|<(https?:\/\/[^<>\s]+)>|(https?:\/\/[^\s<>"'`|，。；！？、（）【】]+)/gi;
  let html = "";
  let offset = 0;
  for (const match of text.matchAll(tokens)) {
    html += escapeHtml(text.slice(offset, match.index));
    if (match[1]) html += `<code>${escapeHtml(match[2])}</code>`;
    else if (match[5]) html += `<strong>${formatInlineMarkdown(match[5], allowLinks, depth + 1)}</strong>`;
    else {
      const rawUrl = match[4] ? match[4].replace(/^<|>$/g, "") : match[6] || match[7];
      const url = match[7] ? trimMarkdownUrl(rawUrl) : rawUrl;
      const href = allowLinks ? safeMarkdownHref(url) : "";
      const label = match[3] ? formatInlineMarkdown(match[3], false, depth + 1) : escapeHtml(url);
      html += href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${label}</a>${escapeHtml(rawUrl.slice(url.length))}` : escapeHtml(match[0]);
    }
    offset = match.index + match[0].length;
  }
  return html + escapeHtml(text.slice(offset));
}

function renderMessageArtifacts(artifacts) {
  const entries = Object.entries(artifacts || {}).filter(([, value]) => value);
  if (!entries.length) return "";
  return `
    <div class="message-artifacts">
      ${entries
        .map(
          ([key, value]) => `
            <a class="artifact-card" href="${escapeHtml(artifactHref(value))}" target="_blank" rel="noopener" title="${escapeHtml(artifactDisplayValue(value))}">
              <span>${escapeHtml(artifactLabel(key))}</span>
              <strong>${escapeHtml(artifactActionLabel(key))}</strong>
            </a>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderThinking(trace) {
  const steps = trace?.steps || [];
  const summary = trace.trace_summary || {};
  const completed = summary.completed_steps ?? steps.filter((step) => step.status === "done").length;
  const toolCalls = steps.flatMap((step) => step.tool_calls || []);
  const gates = steps.filter((step) => step.gate_result && Object.keys(step.gate_result).length);
  const artifacts = trace.artifacts || [];
  const phases = ["think", "act", "observe", "answer"].map((phase) => ({
    phase,
    count: summary.loop_phases?.[phase] ?? steps.filter((step) => step.metadata?.loop_phase === phase).length,
  }));
  const toolCallCount = summary.tool_call_count ?? toolCalls.length;
  const gateCount = summary.gate_count ?? gates.length;
  const artifactCount = summary.artifact_count ?? artifacts.length;
  const selectedModel = summary.selected_model_id || summary.model_id || trace.model_id || DEFAULT_MODEL_ID;
  const modelActivity = summarizeTraceModels(trace);
  const executionLabel = !modelActivity.callCount && !modelActivity.unclassifiedCount
    ? `${selectedModel} 已选择 · 未记录模型请求${modelActivity.runtimeActionCount ? ` · ${modelActivity.runtimeActionCount} 次运行时动作` : ""}`
    : selectedModel;
  const qualityBlocked = trace.metadata?.stop_reason === "quality_blocked";
  const runStatus = qualityBlocked ? "质量校验未通过 / 未发布报告" : `流程${statusLabel(trace.status)}`;
  const openAttribute = !qualityBlocked && ["completed", "done", "success", "ok"].includes(trace.status) ? "" : " open";
  return `
    <details class="thinking-card" data-run-id="${escapeHtml(trace.run_id || "")}"${openAttribute}>
      <summary class="thinking-head">
        <div>
          <div class="thinking-title">运行过程</div>
          <div class="thinking-subtitle">Run ${escapeHtml(shortId(trace.run_id))} · ${escapeHtml(skillLabel(trace.skill_id))} · ${escapeHtml(executionLabel)}</div>
        </div>
        <div class="thinking-status">
          <span class="trace-chip">${escapeHtml(runStatus)}</span>
          <span class="trace-chip">${completed}/${steps.length} 步已记录</span>
        </div>
      </summary>
      <div class="thinking-body">
        <div class="trace-metrics">
          <div class="trace-metric"><span>步骤</span><strong>${steps.length}</strong></div>
          <div class="trace-metric"><span>工具</span><strong>${toolCallCount}</strong></div>
          <div class="trace-metric"><span>门控</span><strong>${gateCount}</strong></div>
          <div class="trace-metric"><span>输出产物</span><strong>${artifactCount}</strong></div>
        </div>
        ${renderTraceEvidence(trace)}
        ${renderSubagentPanel(trace)}
        <div class="loop-phase-row">
          ${phases
            .map(
              (item) => `
                <span class="loop-phase ${item.count ? "active" : ""}">
                  ${escapeHtml(loopPhaseLabel(item.phase))}
                  <strong>${item.count}</strong>
                </span>
              `,
            )
            .join("")}
        </div>
        <div class="step-list">
          ${steps.length ? steps.map((step) => renderStep(step)).join("") : "<p>本轮没有记录到执行步骤。</p>"}
        </div>
      </div>
    </details>
  `;
}

function renderPendingTrace(runId, error = "") {
  return `
    <details class="thinking-card trace-pending" data-run-id="${escapeHtml(runId)}">
      <summary class="thinking-head">
        <div>
          <div class="thinking-title">运行过程</div>
          <div class="thinking-subtitle">Run ${escapeHtml(shortId(runId))}</div>
        </div>
        <div class="thinking-status"><span class="trace-chip">${error ? "读取失败" : "读取中"}</span></div>
      </summary>
      <div class="thinking-body">
        ${error ? `<p>${escapeHtml(error)}</p><button type="button" data-run-trace="${escapeHtml(runId)}">重试读取</button>` : "<p>正在读取这一轮的运行记录...</p>"}
      </div>
    </details>
  `;
}

function renderStreamingThinking(streaming) {
  const status = streaming?.status || "模型与工具链路运行中";
  const runId = streaming?.runId || "";
  const started = streaming?.startedAt ? formatTime(streaming.startedAt) : "";
  return `
    <details class="thinking-card streaming-thinking" open>
      <summary class="thinking-head">
        <div>
          <div class="thinking-title">运行过程</div>
          <div class="thinking-subtitle">${escapeHtml(runId ? `Run ${shortId(runId)}` : "等待 Runtime 分配 Run")} · ${escapeHtml(started || "刚刚")}</div>
        </div>
        <div class="thinking-status">
          <span class="trace-chip">SSE 流式输出</span>
          <span class="trace-chip">${escapeHtml(status)}</span>
        </div>
      </summary>
      <div class="thinking-body">
        <div class="stream-progress">
          <span></span>
        </div>
        <div class="stream-hint">完整 Trace 会在本轮结束后自动展开，包括 Context、Memory、Tool、Gate、Subagent 和 Artifact。</div>
      </div>
    </details>
  `;
}

function subagentFieldLabel(key) {
  const fields = {
    claims: "结论", claim_ref: "结论引用", statement: "判断", confidence: "置信度",
    counterexamples: "质疑", issue: "问题", notes: "补充说明",
    evidence: "证据", evidence_refs: "证据引用", source_ref: "来源引用",
    source_url: "来源地址", summary: "摘要", verdicts: "核验结果",
    decision: "核验判定", reason: "依据", answer: "回答", verified_claim_refs: "已核验结论引用",
  };
  return Object.hasOwn(fields, key) ? fields[key] : key;
}

function summarizeSubagentValue(value, key = "") {
  if (value === null || value === undefined) return "未提供";
  if (Array.isArray(value)) return value.length ? value.map((item) => summarizeSubagentValue(item)).join(" / ") : "无";
  if (typeof value === "object") {
    return Object.entries(value).map(([field, item]) => `${subagentFieldLabel(field)}：${summarizeSubagentValue(item, field)}`).join("；");
  }
  if (key === "decision" && ["pass", "fail", "unverified"].includes(value)) {
    return { pass: "证据支持（pass）", fail: "直接反证（fail）", unverified: "未核实（unverified）" }[value];
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

function flattenSubagentOutput(output) {
  if (!output || typeof output !== "object") return [];
  const rows = [];
  Object.entries(output).forEach(([key, value]) => {
    if (Array.isArray(value)) {
      value.forEach((item, index) => {
        rows.push({
          label: `${subagentFieldLabel(key)} ${index + 1}`,
          value: summarizeSubagentValue(item, key),
        });
      });
    } else if (value !== undefined && value !== null && value !== "") {
      rows.push({ label: subagentFieldLabel(key), value: summarizeSubagentValue(value, key) });
    }
  });
  return rows;
}

function renderSubagentPanel(trace) {
  const summary = trace?.subagent_summary || {};
  const tasks = Array.isArray(summary.tasks) ? summary.tasks : [];
  const results = Array.isArray(summary.results) ? summary.results : [];
  if (!tasks.length && !results.length) return "";
  const taskByRole = new Map(tasks.map((task) => [task.role, task]));
  const cards = results.length
    ? results.map((result) => ({ ...(taskByRole.get(result.role) || {}), ...result }))
    : tasks;
  return `
    <section class="subagent-panel" aria-label="子智能体委派">
      <div class="subagent-panel-head">
        <span>动态子智能体</span>
        <strong>${escapeHtml(String(summary.delegate_count || results.length || 0))} 次委派</strong>
      </div>
      <div class="subagent-card-grid">
        ${cards
          .map((item) => {
            const outputRows = flattenSubagentOutput(item.output || {});
            const contextStep = (trace.steps || []).find((step) => step.step_type === "subagent_context"
              && ((item.task_id && step.input_payload?.task_id === item.task_id)
                || (item.context_manifest_id && (step.output_payload?.context_manifest?.record_id
                  || step.output_payload?.context_manifest?.metadata?.manifest_id
                  || step.output_payload?.context_manifest?.manifest_id) === item.context_manifest_id)));
            return `
              <article class="subagent-card">
                <div class="subagent-card-top">
                  <strong>${escapeHtml(item.role || "subagent")}</strong>
                  <span>${escapeHtml(item.status || item.trigger || "planned")}</span>
                </div>
                <div class="subagent-reason">${escapeHtml(item.reason || "由主 Agent 根据本轮问题动态委派")}</div>
                ${item.trigger ? `<div class="subagent-trigger">触发信号：${escapeHtml(item.trigger)}</div>` : ""}
                ${item.output_schema ? `<div class="subagent-schema">输出契约：${escapeHtml(item.output_schema)}</div>` : ""}
                ${contextStep ? `<div class="subagent-schema">委派上下文：${escapeHtml(contextSummary(contextStep))}</div>` : ""}
                ${outputRows.length
                  ? `<div class="subagent-output">
                      ${outputRows.slice(0, 8)
                        .map(
                          (row) => `
                            <div class="subagent-output-row">
                              <span>${escapeHtml(row.label)}</span>
                              <p>${escapeHtml(row.value)}</p>
                            </div>
                          `,
                        )
                        .join("")}
                    </div>`
                  : `<div class="subagent-empty">本角色只完成规划或未产生可展示输出。</div>`}
                ${outputRows.length > 8 ? `<div class="subagent-schema">输出摘要：8 / ${outputRows.length} 项</div>` : ""}
                <details class="raw-details">
                  <summary>子智能体结果原始 JSON</summary>
                  <pre class="json-box">${escapeHtml(JSON.stringify(redactTraceValue(item), null, 2))}</pre>
                </details>
              </article>
            `;
          })
          .join("")}
      </div>
    </section>
  `;
}

function sourceSummary(trace) {
  const source = trace.source_summary || {};
  const selected = Array.isArray(source.selected_source_types) ? source.selected_source_types : [];
  const route = source.route_label || source.route || "";
  const count = (value) => Number.isInteger(value) && value >= 0;
  const enabled = (value) => count(value) ? value : "未记录";
  const snapshotInfo = [
    count(source.series_count) ? `指数序列 ${source.series_count} 组` : "",
    count(source.document_count) ? `资料 ${source.document_count} 篇` : "",
    count(source.snapshot_item_count) ? `共享来源快照 ${source.snapshot_item_count} 条` : "",
    count(source.snapshot_news_count) ? `新闻快照 ${source.snapshot_news_count}` : "",
    count(source.snapshot_policy_count) ? `政策快照 ${source.snapshot_policy_count}` : "",
  ]
    .filter(Boolean)
    .join(" / ");
  const counts = [
    count(source.source_count) ? `来源 ${source.source_count}（成功 ${enabled(source.successful_source_count)}${count(source.partial_source_count) ? ` / 部分 ${source.partial_source_count}` : ""} / 失败 ${enabled(source.failed_source_count)}）` : "",
    count(source.news_source_count) ? `新闻 ${enabled(source.enabled_news_source_count)}/${source.news_source_count}` : "",
    count(source.policy_source_count) ? `政策 ${enabled(source.enabled_policy_source_count)}/${source.policy_source_count}` : "",
    count(source.fallback_source_count) ? `兜底 ${source.fallback_source_count}` : "",
  ]
    .filter(Boolean)
    .join(" / ");
  if (!route && !selected.length && !counts && !snapshotInfo) return "";
  return {
    value: route || selected.slice(0, 3).join(" / ") || "已自动选择",
    detail: [counts, snapshotInfo].filter(Boolean).join(" · ") || "来源采集统计未记录",
  };
}

function modelStepMetadata(step) {
  return { ...(step.output_payload?.metadata || {}), ...(step.metadata || {}) };
}

function modelStepKind(step) {
  if (step.step_type !== "model_call") return "";
  const metadata = modelStepMetadata(step);
  // Runtime actions share model_call storage with provider requests, including in older traces.
  if (step.metadata?.deterministic_action === true || step.output_payload?.metadata?.deterministic_action === true
    || String(step.output_payload?.response_id || "").startsWith("runtime_")) return "runtime";
  if (metadata.provider || metadata.adapter === "AnthropicCompatibleModelAdapter") return "provider";
  return "unclassified";
}

function modelStepLabel(step) {
  const kind = modelStepKind(step);
  if (kind === "runtime") return "运行时动作";
  if (kind === "provider") return "模型请求";
  if (kind === "unclassified") return "模型记录（来源未确认）";
  return stepTypeLabel(step.step_type);
}

function summarizeTraceModels(trace) {
  const steps = (trace.steps || []).filter((step) => step.step_type === "model_call");
  const requests = steps.filter((step) => modelStepKind(step) === "provider");
  const metadata = requests.map(modelStepMetadata);
  const attempts = metadata.map((item) => item.attempts).filter((value) => Number.isInteger(value) && value >= 0);
  const latencies = metadata.map((item) => item.total_latency_ms ?? item.latency_ms)
    .filter((value) => typeof value === "number" && Number.isFinite(value) && value >= 0);
  const missingRecords = Math.max(0, (trace.model_summary?.call_count ?? trace.trace_summary?.model_call_count ?? 0) - steps.length);
  return {
    callCount: requests.length,
    runtimeActionCount: steps.filter((step) => modelStepKind(step) === "runtime").length,
    unclassifiedCount: steps.filter((step) => modelStepKind(step) === "unclassified").length + missingRecords,
    totalAttempts: attempts.reduce((total, value) => total + value, 0),
    attemptsComplete: attempts.length === requests.length,
    retriedCallCount: attempts.filter((value) => value > 1).length,
    totalLatencyMs: latencies.reduce((total, value) => total + value, 0),
    latencyCount: latencies.length,
    modelIds: [...new Set(requests.map((step) => modelStepMetadata(step).model_id || step.input_payload?.model_id).filter(Boolean))],
    contractIds: [...new Set(requests.map((step) => step.input_payload?.prompt_contract?.contract_id).filter(Boolean))],
  };
}

function renderTraceEvidence(trace) {
  const context = trace.context_summary || {};
  const memory = trace.memory_summary || {};
  const model = summarizeTraceModels(trace);
  const source = trace.source_summary || {};
  const subagents = trace.subagent_summary || {};
  const tools = trace.tool_summary || {};
  const gates = trace.gate_summary || {};
  const artifacts = trace.artifact_summary || {};
  const structuredEvents = trace.structured_events || [];
  const sourceCell = sourceSummary(trace);
  const budgetUtilization = Number(context.budget_utilization || 0);
  const budgetDetail = context.token_estimate_total
    ? `估算 ${context.token_estimate_total} tokens${budgetUtilization ? ` · 已用预算 ${Math.round(budgetUtilization * 100)}%` : ""}`
    : context.stage_name || "";
  const cells = [
    {
      label: "上下文",
      value: context.manifest_id ? `${context.visible_key_count || 0} 可见块 / ${context.hidden_field_count || 0} 隐藏块` : "未生成",
      detail: budgetDetail,
    },
    {
      label: "记忆",
      value: `${memory.read_steps || 0} 读 / ${memory.actual_write_count || 0} 写`,
      detail: `${memory.total_memory_hits || 0} 命中，过滤 ${memory.total_filtered_out || 0}${memory.deduplicated_write_count ? `，去重 ${memory.deduplicated_write_count}` : ""}`,
    },
    {
      label: "模型",
      value: `${model.callCount} 次模型请求${model.unclassifiedCount ? "（已确认）" : ""}`,
      detail: [
        `${model.runtimeActionCount} 次运行时动作（确定性）`,
        model.unclassifiedCount ? `${model.unclassifiedCount} 条模型记录来源未确认` : "",
        model.modelIds.join(" / "),
        model.contractIds.length ? `contract ${model.contractIds.join(", ")}` : "",
        model.callCount ? `${model.totalAttempts} 次已记录 provider 尝试${model.attemptsComplete ? "" : "（部分次数未记录）"}` : "",
        model.retriedCallCount ? `${model.retriedCallCount} 个请求发生重试` : "",
        model.latencyCount ? `${Math.round(model.totalLatencyMs)} ms 累计耗时${model.latencyCount < model.callCount ? "（部分未记录）" : ""}` : "",
      ].filter(Boolean).join(" · "),
    },
    {
      label: "子智能体",
      value: `${subagents.delegate_count || 0} 次委派`,
      detail: (subagents.roles || []).join(" / ") || "本轮未触发动态委派",
    },
    {
      label: "数据源",
      value: sourceCell?.value || "未记录",
      detail: sourceCell?.detail || [source.route, source.route_label].filter(Boolean).join(" / ") || "未进入外部变化分析路线",
    },
    {
      label: "工具",
      value: `${tools.total_calls || 0} 次调用`,
      detail: (tools.unique_tools || []).slice(0, 3).join(" / ") || "无工具调用",
    },
    {
      label: "门控",
      value: `${gates.total_gates || 0} 个门控`,
      detail: gates.decisions ? Object.entries(gates.decisions).map(([key, value]) => `${key}:${value}`).join(" / ") : "",
    },
    {
      label: "输出产物",
      value: `${artifacts.total_output_artifacts || artifacts.total_artifacts || 0} 个产物`,
      detail: artifacts.by_type ? Object.keys(artifacts.by_type).slice(0, 3).join(" / ") : "",
    },
    {
      label: "可观测",
      value: `${structuredEvents.length || 0} events`,
      detail: trace.observability_schema_version || "workbench.trace",
    },
  ];
  return `
    <div class="trace-evidence-grid">
      ${cells
        .map(
          (cell) => `
            <div class="trace-evidence-cell">
              <span>${escapeHtml(cell.label)}</span>
              <strong>${escapeHtml(cell.value)}</strong>
              <small>${escapeHtml(cell.detail || "")}</small>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function contextSummary(step) {
  const record = step.output_payload?.context_manifest || step.input_payload?.context_manifest || {};
  // Main contexts are flat; subagent ContextManifestRecord stores fields under metadata.
  const manifest = { ...(record.metadata || {}), ...record };
  const visibleKeys = manifest.visible_keys;
  const estimates = manifest.token_estimates || {};
  const budget = manifest.token_budget || step.metadata?.token_budget || {};
  const content = manifest.visible_content || {};
  const brief = content.task_brief || {};
  const refs = content.input_artifact_refs ?? brief.input_artifact_refs ?? step.input_payload?.input_artifact_refs
    ?? (step.input_payload?.artifact_ref ? [step.input_payload.artifact_ref] : undefined);
  if (!Object.keys(manifest).length && !Object.keys(budget).length && !Array.isArray(refs)) return "";
  const estimateTotal = sumNumericValues(estimates);
  const budgetTotal = sumNumericValues(budget);
  return [
    Array.isArray(visibleKeys) ? `${visibleKeys.length} 个可见块` : "可见块未记录",
    Array.isArray(refs) ? `委派引用 ${refs.length} 个${refs.length ? `：${refs.map((ref) => redactLocalPaths(ref)).join(" / ")}` : ""}` : "",
    estimateTotal ? `估算 ${estimateTotal} tokens` : "",
    budgetTotal ? `Token 预算 ${budgetTotal}` : "",
    brief.max_steps != null ? `步骤上限 ${brief.max_steps}` : "",
    brief.max_model_calls != null ? `调用预算 ${brief.max_model_calls}（含运行时动作）` : "",
    brief.timeout_seconds != null ? `超时 ${brief.timeout_seconds} 秒` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function toolSummary(step) {
  const calls = step.tool_calls || [];
  if (!calls.length) return "";
  const allowed = calls.filter((call) => call.allowed !== false).length;
  const successfulStatuses = new Set(["ok", "success", "done", "allowed"]);
  const failed = calls.filter((call) => call.status && !successfulStatuses.has(String(call.status).toLowerCase())).length;
  const names = uniqueNames(calls, "tool_name");
  return `${calls.length} 次工具 · ${allowed} 允许${failed ? ` · ${failed} 异常` : ""}${names ? ` · ${names}` : ""}`;
}

function gateSummary(step) {
  const gate = step.gate_result || {};
  const gateCount = step.output_payload?.gate_count || gate.stage_gate_count || 0;
  if (gate.decision) return `${gate.decision}${gateCount ? ` · ${gateCount} 个 gate` : ""}`;
  if (gateCount) return `${gateCount} 个 gate`;
  return "";
}

function artifactSummary(step) {
  const artifacts = step.output_payload?.artifacts || {};
  const artifactKeys = step.output_payload?.artifact_keys || Object.keys(artifacts);
  if (!artifactKeys?.length) return "";
  return artifactKeys.map((key) => artifactLabel(key)).join(" / ");
}

function memorySummary(step) {
  if (step.step_type !== "memory_read" && step.step_type !== "memory_write") return "";
  const output = step.output_payload || {};
  if (step.step_type === "memory_read") {
    const count = Number(output.memory_count ?? output.count ?? 0);
    const raw = Number(output.raw_memory_count ?? 0);
    const filtered = Number(output.filtered_out ?? 0);
    return [`读取 ${count} 条`, raw ? `候选 ${raw} 条` : "", filtered ? `过滤 ${filtered} 条` : ""].filter(Boolean).join(" · ");
  }
  if (output.deduplicated) return `已存在 ${shortId(output.memory_id)} · 未重复写入`;
  return output.memory_id ? `写入 ${shortId(output.memory_id)}` : "写入长期记忆";
}

function modelCallSummary(step) {
  if (step.step_type !== "model_call") return "";
  const input = step.input_payload || {};
  const output = step.output_payload || {};
  const kind = modelStepKind(step);
  const metadata = modelStepMetadata(step);
  return [
    kind === "runtime" ? "确定性动作，未请求模型" : kind === "provider" ? `模型请求${metadata.provider ? ` · ${metadata.provider}` : ""}` : "模型记录，provider 来源未确认",
    input.schema_name ? `schema ${input.schema_name}` : "",
    output.response_id ? `${kind === "runtime" ? "运行记录" : "response"} ${shortId(output.response_id)}` : "",
    input.prompt_contract?.contract_id ? `contract ${input.prompt_contract.contract_id}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function routeSummary(step) {
  if (step.step_type === "intent_classification") {
    const output = step.output_payload || {};
    return output.resolved_skill_id || output.route || output.mode || "";
  }
  if (step.step_type === "skill_selection") {
    const output = step.output_payload || {};
    return output.selected_skill_id || output.skill_id || output.skill?.id || "";
  }
  if (step.step_type === "final_answer") {
    const output = step.output_payload || {};
    const artifacts = output.artifact_keys || [];
    return artifacts.length ? `最终回答 · ${artifacts.length} 个产物` : "最终回答已保存";
  }
  return "";
}

function observationSummary(step) {
  const candidates = [
    routeSummary(step) ? { label: "路由", value: routeSummary(step) } : null,
    contextSummary(step) ? { label: "上下文", value: contextSummary(step) } : null,
    memorySummary(step) ? { label: "记忆", value: memorySummary(step) } : null,
    modelCallSummary(step) ? { label: modelStepKind(step) === "runtime" ? "运行时" : "模型", value: modelCallSummary(step) } : null,
    toolSummary(step) ? { label: "工具", value: toolSummary(step) } : null,
    gateSummary(step) ? { label: "门控", value: gateSummary(step) } : null,
    artifactSummary(step) ? { label: "产物", value: artifactSummary(step) } : null,
  ].filter(Boolean);
  if (candidates.length) return candidates;
  return [{ label: "控制", value: "确定性运行步骤，无额外工具或门控" }];
}

function renderStep(step) {
  const phase = step.metadata?.loop_phase || "";
  const payload = {
    input: step.input_payload || {},
    output: step.output_payload || {},
    tool_calls: step.tool_calls || [],
    gate_result: step.gate_result || {},
    metadata: step.metadata || {},
  };
  return `
    <div class="step-item ${escapeHtml(step.status || "")}">
      <div class="step-dot">${escapeHtml(step.step_index)}</div>
      <div class="step-main">
        <div class="step-topline">
          <div>
            <div class="step-title">${escapeHtml(step.title || modelStepLabel(step))}</div>
            <div class="step-meta">${escapeHtml(statusLabel(step.status))} · ${escapeHtml(formatTime(step.created_at))}</div>
          </div>
          <div class="step-tags">
            <span class="step-type">${escapeHtml(modelStepLabel(step))}</span>
            ${phase ? `<span class="phase-pill">${escapeHtml(loopPhaseLabel(phase))}</span>` : ""}
          </div>
        </div>
        <div class="step-summary-row">
          ${observationSummary(step)
            .map((item) => `<span class="step-summary"><b>${escapeHtml(item.label)}</b>${escapeHtml(item.value)}</span>`)
            .join("")}
        </div>
        <details class="step-details">
          <summary>展开运行证据</summary>
          <div class="detail-grid">
            <div class="detail-panel">
              <div class="detail-title">输入</div>
              ${renderCompactDict(step.input_payload || {}, "无输入字段")}
            </div>
            <div class="detail-panel">
              <div class="detail-title">输出</div>
              ${renderCompactDict(step.output_payload || {}, "无输出字段")}
            </div>
            <div class="detail-panel">
              <div class="detail-title">工具调用</div>
              ${renderCompactDict(
                {
                  count: (step.tool_calls || []).length,
                  tools: uniqueNames(step.tool_calls || [], "tool_name", 6),
                  status: uniqueNames(step.tool_calls || [], "status", 6),
                },
                "无工具调用",
              )}
            </div>
            <div class="detail-panel">
              <div class="detail-title">门控</div>
              ${renderCompactDict(step.gate_result || {}, "无门控结果")}
            </div>
          </div>
          <details class="raw-details">
            <summary>原始 JSON</summary>
            <pre class="json-box">${escapeHtml(JSON.stringify(redactTraceValue(payload), null, 2))}</pre>
          </details>
        </details>
      </div>
    </div>
  `;
}

function renderSlashPalette() {
  const input = $("prompt-input");
  const raw = getPromptText();
  const range = slashQueryRange(raw, getCaretOffset(input));
  const query = range?.query || "";
  if (!range || !query.startsWith("/")) {
    $("slash-palette").classList.add("hidden");
    $("slash-palette").innerHTML = "";
    state.slashOpen = false;
    state.slashMatches = [];
    state.slashIndex = 0;
    state.slashQuery = "";
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    return;
  }
  const matches = slashCommands.filter((item) => item.command.startsWith(query || "/"));
  if (query !== state.slashQuery) state.slashIndex = 0;
  state.slashQuery = query;
  state.slashMatches = matches;
  state.slashIndex = Math.max(0, Math.min(state.slashIndex, Math.max(0, matches.length - 1)));
  $("slash-palette").innerHTML = matches
    .map(
      (item, index) => `
        <button id="slash-option-${index}" class="slash-option ${index === state.slashIndex ? "active" : ""}" data-command="${escapeHtml(item.command)}" role="option" aria-selected="${index === state.slashIndex}" tabindex="-1">
          <span class="slash-key">${escapeHtml(item.command)}</span>
          <span>
            <span class="slash-title">${escapeHtml(item.name)}</span>
            <span class="slash-desc">${escapeHtml(item.description)}</span>
          </span>
          <span class="small-muted">${escapeHtml(skillLabel(item.skillId))}</span>
        </button>
      `,
    )
    .join("");
  $("slash-palette").classList.toggle("hidden", !matches.length);
  state.slashOpen = Boolean(matches.length);
  input.setAttribute("aria-expanded", String(state.slashOpen));
  if (state.slashOpen) input.setAttribute("aria-activedescendant", `slash-option-${state.slashIndex}`);
  else input.removeAttribute("aria-activedescendant");
  document.querySelectorAll(".slash-option").forEach((button) => {
    button.addEventListener("click", () => applySlashCommand(button.dataset.command));
  });
}

function moveSlashSelection(delta) {
  if (!state.slashOpen || !state.slashMatches.length) return;
  state.slashIndex = (state.slashIndex + delta + state.slashMatches.length) % state.slashMatches.length;
  renderSlashPalette();
  document.getElementById(`slash-option-${state.slashIndex}`)?.scrollIntoView({ block: "nearest" });
}

function applySlashCommand(command) {
  const input = $("prompt-input");
  const raw = getPromptText();
  const caret = getCaretOffset(input);
  const range = slashQueryRange(raw, caret) || { start: caret, end: caret };
  const suffix = raw.slice(range.end).replace(/^\s+/, "");
  const insertion = `${command} `;
  const next = `${raw.slice(0, range.start)}${insertion}${suffix}`;
  setPromptText(next, range.start + insertion.length);
  closeSlashPalette();
  input.focus();
}

function closeSlashPalette() {
  $("slash-palette").classList.add("hidden");
  state.slashOpen = false;
  state.slashMatches = [];
  state.slashIndex = 0;
  $("prompt-input").setAttribute("aria-expanded", "false");
  $("prompt-input").removeAttribute("aria-activedescendant");
}

function parseSlashCommand(raw) {
  const parsed = extractInlineSlashCommand(raw);
  const command = parsed.command;
  if (!command) {
    return { message: parsed.message, skillId: null, command: null };
  }
  return { message: parsed.message, skillId: command.skillId, command };
}

function renderAll() {
  renderCompanies();
  renderSessions();
  renderCommands();
  renderTools();
  renderModelPicker();
  renderHeader();
  renderWorkspace();
  renderPromptSyntax();
  renderSlashPalette();
}

async function loadBootstrap() {
  await refreshWorkspaceOptions();
  if (!state.companies.length) {
    state.companyId = "";
    state.currentSessionId = "";
    state.currentView = "wiki";
    state.wiki = null;
    renderAll();
    return;
  }
  const nextCompanyId = preferredWorkspaceId();
  if (nextCompanyId && nextCompanyId !== state.companyId) {
    state.companyId = nextCompanyId;
  }
  if (!state.companies.some((company) => company.company_id === state.companyId)) {
    state.companyId = state.companies[0].company_id;
  }
  rememberWorkspace(state.companyId);
  await Promise.all([loadSkills(), loadModelStatus(), loadSessions(), loadWiki()]);
  if (!state.currentSessionId) {
    await createSession();
  } else {
    await loadSessionData();
  }
  renderAll();
}

async function refreshWorkspaceOptions() {
  const data = await api("/companies");
  state.companies = Array.isArray(data.companies) ? data.companies.map(normalizeCompany) : [];
}

async function switchWorkspace(companyId, view = state.currentView) {
  if (state.sending) throw new Error("当前对话执行完成后可切换工作空间");
  if (!state.companies.some((company) => company.company_id === companyId)) throw new Error("工作空间不存在，请刷新列表");
  captureWikiDraft();
  const previousBusy = state.workspaceBusy;
  state.workspaceBusy = true;
  state.sessionLoadEpoch += 1;
  state.companyId = companyId;
  rememberWorkspace(companyId);
  state.currentView = view === "articles" && companyId !== "finance_observer" ? "chat" : view;
  state.currentSessionId = "";
  state.sessions = [];
  state.messages = [];
  state.trace = null;
  state.tracesByRun = {};
  state.traceSessionId = "";
  state.traceLoadErrors = {};
  state.wiki = null;
  state.articles = [];
  state.articleAuthors = [];
  state.articleSyncIssues = [];
  state.articleDetail = null;
  state.selectedArticleId = "";
  state.articleAuthorFilter = "";
  state.articleFilter = "";
  state.articleError = "";
  state.articleLoadEpoch += 1;
  state.selectedWikiPath = "";
  state.lastError = "";
  clearWikiSearch();
  renderCompanies();
  try {
    await Promise.all([loadSkills(), loadModelStatus(), loadSessions(), loadWiki()]);
    if (state.currentView === "articles") await loadArticles();
    if (!state.currentSessionId) await createSession();
    else await loadSessionData();
  } finally {
    state.workspaceBusy = previousBusy;
    renderAll();
  }
}

async function loadModelStatus() {
  const data = await api(`/companies/${state.companyId}/workbench/models`);
  state.models = data.models || [];
  const model = state.models.find((item) => item.metadata?.configured_model === item.model_id) || state.models[0] || null;
  state.modelStatus = {
    available: Boolean(model?.metadata?.available),
    missing: model?.metadata?.missing || [],
    authConfigured: Boolean(model?.metadata?.auth_configured),
    configuredModel: model?.metadata?.configured_model || model?.model_id || DEFAULT_MODEL_ID,
  };
}

async function loadSkills() {
  const data = await api(`/companies/${state.companyId}/workbench/skills`);
  state.skills = data.skills || [];
}

async function loadWiki() {
  const companyId = state.companyId;
  const epoch = ++state.wikiLoadEpoch;
  if (!companyId) { state.wiki = null; state.finance = null; return; }
  const financeRequest = loadFinanceWiki(companyId);
  try {
    const wiki = await api(`/companies/${encodeURIComponent(companyId)}/workbench/wiki`);
    if (companyId === state.companyId && epoch === state.wikiLoadEpoch) state.wiki = wiki;
  } catch (error) {
    if (companyId !== state.companyId || epoch !== state.wikiLoadEpoch) return;
    state.wiki = {
      schema_version: "workbench.wiki_tree.v1",
      company_id: state.companyId,
      profile: {},
      pages: [],
      facts: [],
      stats: {},
      error: error.message,
    };
  } finally {
    const finance = await financeRequest;
    if (companyId === state.companyId && epoch === state.wikiLoadEpoch) state.finance = finance;
  }
}

async function refreshWiki() {
  if (state.wikiAction || !state.companyId) return;
  const key = wikiDraftKey(state.companyId, state.selectedWikiPath);
  if (state.wikiDrafts[key] && !window.confirm("放弃本页未保存的修改并重新读取？")) return;
  state.wikiAction = "refresh";
  renderWiki();
  try {
    await loadWiki();
    if (state.wiki?.error) throw new Error(state.wiki.error);
    delete state.wikiDrafts[key];
    clearWikiSearch();
    await refreshWorkspaceOptions();
    toast("Wiki 已重新读取");
  } catch (error) {
    state.lastError = error.message;
    toast(`Wiki 读取失败：${error.message}`);
  } finally {
    state.wikiAction = "";
    renderAll();
  }
}

async function saveSelectedWikiPage() {
  if (!state.selectedWikiPath || state.wikiAction) return;
  if (isFinanceProfilePath(state.selectedWikiPath)) return;
  let nextBody = "";
  try {
    nextBody = buildStructuredWikiBody();
  } catch (error) {
    toast(`Wiki 表单未通过校验：${error.message}`);
    return;
  }
  captureWikiDraft();
  const companyId = state.companyId;
  const pagePath = state.selectedWikiPath;
  state.wikiAction = "save";
  setWikiFormBusy($("wiki-editor-form"), true);
  $("wiki-save").disabled = true;
  $("wiki-save").textContent = "保存中";
  renderCompanies();
  try {
    const data = await api(`/companies/${encodeURIComponent(companyId)}/workbench/wiki/pages`, {
      method: "POST",
      body: JSON.stringify({
        path: pagePath,
        body: nextBody,
      }),
    });
    state.wiki = data.wiki || state.wiki;
    state.selectedWikiPath = data.page?.path || state.selectedWikiPath;
    delete state.wikiDrafts[wikiDraftKey(companyId, pagePath)];
    clearWikiSearch();
    toast("Wiki 已保存并重新索引");
    refreshWorkspaceOptions().then(renderCompanies).catch((error) => toast(`页面已保存，空间列表更新失败：${error.message}`));
  } catch (error) {
    state.lastError = error.message;
    toast(`Wiki 保存失败：${error.message}`);
  } finally {
    state.wikiAction = "";
    renderAll();
  }
}

async function loadSessions() {
  const companyId = state.companyId;
  const data = await api(`/companies/${companyId}/workbench/sessions`);
  if (companyId !== state.companyId) return;
  state.sessions = data.sessions || [];
  const current = state.sessions.find((item) => item.session_id === state.currentSessionId);
  // An explicitly selected empty conversation is valid, especially just after creation.
  if (!state.currentSessionId || !current) {
    state.currentSessionId =
      state.sessions.find((item) => Number(item.message_count || 0) > 0 && !isTestSession(item))?.session_id ||
      state.sessions.find((item) => Number(item.message_count || 0) > 0)?.session_id ||
      state.sessions[0]?.session_id ||
      "";
  }
}

async function loadSessionData(sessionId = state.currentSessionId) {
  if (!sessionId) return;
  const epoch = ++state.sessionLoadEpoch;
  const companyId = state.companyId;
  const [messages, trace] = await Promise.all([
    api(`/companies/${companyId}/workbench/sessions/${sessionId}/messages`),
    api(`/companies/${companyId}/workbench/sessions/${sessionId}/trace`),
  ]);
  if (epoch !== state.sessionLoadEpoch || state.currentSessionId !== sessionId || state.companyId !== companyId) return;
  const cached = state.traceSessionId === sessionId ? state.tracesByRun : {};
  state.messages = messages.messages || [];
  state.trace = trace || null;
  state.tracesByRun = { ...cached, ...(trace?.run_id ? { [trace.run_id]: trace } : {}) };
  state.traceSessionId = sessionId;
  state.traceLoadErrors = {};
  hydrateSessionTraces(companyId, sessionId, epoch);
}

function hydrateSessionTraces(companyId, sessionId, epoch) {
  const pending = [...new Set(state.messages
    .filter((message) => message.role === "assistant")
    .map((message) => message.metadata?.run_id)
    .filter((runId) => runId && !state.tracesByRun[runId]))];
  if (!pending.length) return;
  const current = () => epoch === state.sessionLoadEpoch
    && state.currentSessionId === sessionId && state.companyId === companyId;
  const worker = async () => {
    while (current() && pending.length) {
      const runId = pending.shift();
      try {
        const trace = await api(`/companies/${companyId}/workbench/sessions/${sessionId}/runs/${runId}/trace`);
        if (!current()) return;
        state.tracesByRun[runId] = trace;
        delete state.traceLoadErrors[runId];
      } catch (error) {
        if (!current()) return;
        state.traceLoadErrors[runId] = error.message;
      }
      renderChat({ preserveScroll: true });
    }
  };
  for (let index = 0; index < Math.min(3, pending.length); index += 1) void worker();
}

async function loadHistoricalTrace(runId, button) {
  if (!runId || !state.currentSessionId || state.tracesByRun[runId]) return;
  if (button) {
    button.disabled = true;
    button.textContent = "读取中";
  }
  try {
    const trace = await api(
      `/companies/${state.companyId}/workbench/sessions/${state.currentSessionId}/runs/${runId}/trace`,
    );
    state.tracesByRun[runId] = trace;
    delete state.traceLoadErrors[runId];
    renderChat({ scrollRunId: runId });
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "重试运行记录";
    }
    state.traceLoadErrors[runId] = error.message;
    toast(`运行记录读取失败：${error.message}`);
  }
}

async function createSession() {
  if (state.sending || state.sessionCreating || !state.companyId) return;
  const companyId = state.companyId;
  const previousSessionId = state.currentSessionId;
  state.sessionCreating = true;
  renderHeader();
  try {
    const session = await api(`/companies/${companyId}/workbench/sessions`, {
      method: "POST",
      body: JSON.stringify({
        title: "新对话",
        mode: "auto",
        model_id: activeModelId(),
        active_skill_id: "",
      }),
    });
    if (!session.session_id) throw new Error("服务未返回会话编号");
    // A late creation response must not override a subsequent navigation.
    if (state.companyId !== companyId || state.currentSessionId !== previousSessionId) return;
    state.sessionLoadEpoch += 1;
    state.sessions.unshift(session);
    state.currentSessionId = session.session_id;
    state.currentView = "chat";
    state.messages = [];
    state.trace = null;
    state.tracesByRun = {};
    state.traceSessionId = "";
    state.traceLoadErrors = {};
    state.streaming = null;
    state.lastError = "";
    $("session-search").value = "";
    setPromptText("");
    closeSlashPalette();
    closeMobileSidebar();
    renderAll();
    $("prompt-input").focus();
  } catch (error) {
    toast(`新建对话失败：${error.message}`);
  } finally {
    state.sessionCreating = false;
    renderHeader();
  }
}

async function selectSession(sessionId) {
  if (state.sending || !sessionId || sessionId === state.currentSessionId) return;
  state.currentSessionId = sessionId;
  state.currentView = "chat";
  state.lastError = "";
  await loadSessionData();
  closeMobileSidebar();
  renderAll();
}

async function updateSessionSettings(partial, options = {}) {
  const session = currentSession();
  if (!session) return null;
  const next = {
    title: session.title,
    mode: session.mode,
    model_id: activeModelId(),
    active_skill_id: session.active_skill_id,
    ...partial,
  };
  if (Object.prototype.hasOwnProperty.call(partial, "active_skill_id")) {
    next.mode = partial.active_skill_id ? "skill" : "auto";
  }
  await api(`/companies/${state.companyId}/workbench/sessions/${session.session_id}/settings`, {
    method: "POST",
    body: JSON.stringify(next),
  });
  await loadSessions();
  await loadSessionData();
  if (options.render !== false) {
    renderAll();
  }
  return currentSession();
}

async function sendMessage() {
  const input = $("prompt-input");
  const rawText = getPromptText().trim();
  const raw = rawText;
  if (!raw || state.sending || state.sessionCreating || !state.currentSessionId) return;
  const targetSessionId = state.currentSessionId;
  state.sending = true;
  state.lastError = "";
  setPromptDisabled(true);
  renderHeader();
  renderSessions();
  renderSlashPalette();
  try {
    await loadModelStatus();
    if (!modelAvailable()) {
      throw new Error("当前没有可用模型，无法执行本轮任务");
    }
    if (currentSession()?.model_id !== activeModelId()) {
      await updateSessionSettings({ model_id: activeModelId() }, { render: false });
    }
    state.streaming = {
      sessionId: targetSessionId,
      userMessage: raw,
      content: "",
      runId: "",
      artifacts: {},
      status: "Runtime 已接收请求",
      startedAt: new Date().toISOString(),
    };
    setPromptText("");
    renderWorkspace();
    await streamApi(`/companies/${state.companyId}/workbench/sessions/${targetSessionId}/messages/stream`, { message: raw }, async (type, payload) => {
      if (!state.streaming || state.streaming.sessionId !== targetSessionId) return;
      if (type === "start") {
        state.streaming.status = "构建 Context / Memory / Tool Policy";
      } else if (type === "progress") {
        state.streaming.runId = payload.run_id || state.streaming.runId;
        state.trace = payload;
        if (payload?.run_id) state.tracesByRun[payload.run_id] = payload;
        const lastStep = (payload.steps || []).at(-1);
        state.streaming.status = lastStep ? `执行中：${lastStep.title}` : "正在准备本轮执行";
      } else if (type === "heartbeat") {
        return;
      } else if (type === "result") {
        state.streaming.runId = payload.run_id || state.streaming.runId;
        state.streaming.artifacts = payload.artifacts || {};
        state.streaming.status = payload.selected_skill_id === "external_impact_report"
          ? "报告工作流已完成，正在流式展示"
          : "主 Agent 已完成，正在流式展示";
      } else if (type === "chunk") {
        state.streaming.content += payload.content || "";
      } else if (type === "trace") {
        state.trace = payload || null;
        if (payload?.run_id) state.tracesByRun[payload.run_id] = payload;
        state.streaming.status = "Trace 已回传";
      } else if (type === "done") {
        state.streaming.runId = payload.run_id || state.streaming.runId;
        state.streaming.status = "完成";
      } else if (type === "error") {
        throw new Error(payload.detail || "流式执行失败");
      }
      renderWorkspace({ scrollRunId: state.streaming.runId });
    });
    await loadSessions();
    if (state.currentSessionId === targetSessionId) await loadSessionData(targetSessionId);
    await loadModelStatus();
    state.streaming = null;
    renderAll();
  } catch (error) {
    state.streaming = null;
    state.lastError = error.message;
    setPromptText(rawText);
    toast(`发送失败：${error.message}`);
    await loadSessions();
    if (state.currentSessionId === targetSessionId) await loadSessionData(targetSessionId);
    await loadModelStatus().catch(() => null);
    renderAll();
  } finally {
    state.sending = false;
    setPromptDisabled(false);
    renderCompanies();
    renderHeader();
    renderSessions();
    input.focus();
  }
}

function focusSlashCommand() {
  const input = $("prompt-input");
  input.focus();
  const raw = getPromptText();
  const caret = getCaretOffset(input);
  const needsLeadingSpace = caret > 0 && !/\s/.test(raw[caret - 1] || "") ? " " : "";
  replacePromptRange(caret, caret, `${needsLeadingSpace}/`);
  renderSlashPalette();
}

function autoGrow(editor) {
  editor.style.height = "auto";
  const contentHeight = editor.scrollHeight;
  editor.style.height = `${Math.min(contentHeight, 160)}px`;
  editor.style.overflowY = contentHeight > 160 ? "auto" : "hidden";
}

function toggleSidebar() {
  const app = $("app");
  if (window.matchMedia("(max-width: 900px)").matches) {
    app.classList.remove("sidebar-collapsed", "mobile-sidebar-open");
    syncSidebarControlState();
    return;
  }
  app.classList.toggle("sidebar-collapsed");
  syncSidebarControlState();
}

function toggleMobileSidebar() {
  $("app").classList.remove("sidebar-collapsed");
  $("app").classList.toggle("mobile-sidebar-open");
  syncSidebarControlState();
}

function closeMobileSidebar() {
  $("app").classList.remove("sidebar-collapsed", "mobile-sidebar-open");
  syncSidebarControlState();
}

function syncSidebarControlState() {
  const app = $("app");
  const mobile = window.matchMedia("(max-width: 900px)").matches;
  const label = mobile
    ? "关闭侧边栏"
    : app.classList.contains("sidebar-collapsed")
      ? "展开侧边栏"
      : "收起侧边栏";
  $("sidebar-toggle").title = label;
  $("sidebar-toggle").setAttribute("aria-label", label);
}

function normalizeSidebarForViewport() {
  const app = $("app");
  if (window.matchMedia("(max-width: 900px)").matches) {
    app.classList.remove("sidebar-collapsed");
  } else {
    app.classList.remove("mobile-sidebar-open");
  }
  syncSidebarControlState();
  autoGrow($("prompt-input"));
}

function bindEvents() {
  $("new-session").addEventListener("click", () => state.companyId ? createSession() : openWorkspaceDialog());
  $("create-workspace").addEventListener("click", openWorkspaceDialog);
  $("workspace-create-form").addEventListener("submit", submitWorkspaceCreation);
  $("wiki-page-create-form").addEventListener("submit", submitWikiPageCreation);
  $("article-create-form").addEventListener("submit", submitArticle);
  $("article-file").addEventListener("change", () => importArticleFile().catch((error) => {
    $("article-create-error").textContent = error.message;
    $("article-create-error").classList.remove("hidden");
  }));
  $("wiki-page-add-fact").addEventListener("click", addNewPageFact);
  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!state.workspaceBusy && !state.wikiAction && !state.articleBusy) $(button.dataset.closeDialog).close();
    });
  });
  ["workspace-dialog", "wiki-page-dialog", "article-dialog"].forEach((id) => $(id).addEventListener("cancel", (event) => {
    if (state.workspaceBusy || state.wikiAction || state.articleBusy) event.preventDefault();
  }));
  $("sidebar-toggle").addEventListener("click", toggleSidebar);
  $("mobile-sidebar-toggle").addEventListener("click", toggleMobileSidebar);
  $("sidebar-backdrop").addEventListener("click", closeMobileSidebar);
  window.addEventListener("resize", normalizeSidebarForViewport);
  $("focus-slash-command").addEventListener("click", focusSlashCommand);
  $("company-select").addEventListener("change", async (event) => {
    if (state.sending || state.workspaceBusy || state.wikiAction) { renderCompanies(); return; }
    try { await switchWorkspace(event.target.value); }
    catch (error) { toast(`切换失败：${error.message}`); renderCompanies(); }
  });
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", async () => {
      const nextView = button.dataset.view || "chat";
      if (state.currentView === nextView) return;
      if (!state.companyId && nextView === "chat") { openWorkspaceDialog(); return; }
      if (nextView === "articles" && state.companyId !== "finance_observer") return;
      state.currentView = nextView;
      if (nextView === "wiki" && !state.wiki) await loadWiki();
      if (nextView === "articles" && !state.articles.length) await loadArticles();
      renderAll();
    });
  });
  $("session-search").addEventListener("input", renderSessions);
  $("toggle-test-sessions").addEventListener("click", () => {
    state.showTestSessions = !state.showTestSessions;
    if (!state.showTestSessions && isTestCompany(currentCompany())) {
      const nextCompanyId = preferredWorkspaceId();
      if (nextCompanyId && nextCompanyId !== state.companyId) {
        switchWorkspace(nextCompanyId).catch((error) => toast(`切换失败：${error.message}`));
        return;
      }
    }
    renderAll();
  });
  $("model-picker").addEventListener("change", (event) => {
    if (event.target.value !== activeModelId()) {
      event.target.value = activeModelId();
      toast(`当前只配置了 ${activeModelId()}`);
    }
  });
  $("send-message").addEventListener("click", sendMessage);
  $("prompt-input").addEventListener("compositionstart", () => {
    state.composing = true;
  });
  $("prompt-input").addEventListener("compositionend", (event) => {
    state.composing = false;
    renderPromptSyntax(getCaretOffset(event.target));
    renderSlashPalette();
  });
  $("prompt-input").addEventListener("paste", (event) => {
    event.preventDefault();
    const text = event.clipboardData?.getData("text/plain") || "";
    document.execCommand("insertText", false, text);
  });
  $("prompt-input").addEventListener("input", (event) => {
    if (!state.composing) {
      renderPromptSyntax(getCaretOffset(event.target));
      renderSlashPalette();
    } else {
      autoGrow(event.target);
    }
  });
  $("prompt-input").addEventListener("click", renderSlashPalette);
  $("prompt-input").addEventListener("keyup", (event) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) renderSlashPalette();
  });
  $("prompt-input").addEventListener("keydown", (event) => {
    if (state.slashOpen && event.key === "ArrowDown") {
      event.preventDefault();
      moveSlashSelection(1);
      return;
    }
    if (state.slashOpen && event.key === "ArrowUp") {
      event.preventDefault();
      moveSlashSelection(-1);
      return;
    }
    if (event.key === "Escape" && state.slashOpen) {
      event.preventDefault();
      closeSlashPalette();
      return;
    }
    if (state.slashOpen && (event.key === "Enter" || event.key === "Tab")) {
      event.preventDefault();
      const selected = state.slashMatches[state.slashIndex];
      if (selected) applySlashCommand(selected.command);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
}

async function init() {
  bindEvents();
  normalizeSidebarForViewport();
  try {
    await loadBootstrap();
  } catch (error) {
    state.lastError = error.message;
    toast(`初始化失败：${error.message}`);
    $("chat-stream").innerHTML = `
      <div class="empty-state">
        <div>
          <h2>初始化失败</h2>
          <p>${escapeHtml(error.message)}</p>
        </div>
      </div>
    `;
  }
}

init();
