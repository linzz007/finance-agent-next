# Workbench API And UI Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留当前原生 Web Workbench 视觉基础的前提下，接入异步 Run/SSE、Lazy Trace、Tool Approval、Memory、Artifact 和 Replay，使完整 Harness 能力可以真实使用和验收。

**Architecture:** 现有同步 Message API 保持兼容；新增本地进程内 RunTaskManager 和 SSE 事件流。前端仍使用 `index.html + styles.css + app.js`，主界面保持左侧会话、中间对话、Composer 内模型标识，复杂管理能力使用抽屉或模态层，不恢复占空间的常驻右侧栏。

**Tech Stack:** FastAPI、Server-Sent Events、原生 HTML/CSS/JavaScript、SQLite、现有 CDP UI 检查脚本、pytest。

## Global Constraints

- 不更换前端技术栈，不增加 Streamlit 主界面。
- 模型只显示真实 ID `deepseek-v4-flash`；没有可用模型时发送按钮禁用并明确提示。
- Skills 继续通过 Slash Palette 选择，不恢复大面积 Skill 卡片。
- 执行过程位于对应回答上方；不添加常驻右侧 Trace 面板。
- Trace 三级展示：摘要、时间线、按需脱敏详情。
- 不展示隐藏思维链、完整 Prompt、凭证、环境变量或隐藏 Context 字段。
- 页面使用固定应用高度，不产生主文档空白滚动；每个区域独立滚动。
- 桌面和移动端文本、按钮、时间线、Composer 不得重叠或溢出。

---

### Task 1: Add Local Run Lifecycle And SSE API

**Files:**
- Create: `src/policy_impact/app/run_task_manager.py`
- Create: `src/policy_impact/app/sse.py`
- Modify: `src/policy_impact/app/chat_workbench_service.py`
- Modify: `src/policy_impact/app/api.py`
- Test: `tests/test_run_sse_api.py`

**Interfaces:**
- Consumes: TurnCoordinator, EventStore, RunRecovery, shared `RunStatus`, and `require_run_transition()`.
- Produces: create/status/cancel/SSE endpoints whose status fields serialize only shared `RunStatus` values while preserving sync Message API.

- [ ] **Step 1: Write failing API lifecycle tests**

```python
def test_create_turn_returns_run_identity_before_completion(client, session):
    response = client.post(
        f"/companies/company_001/workbench/sessions/{session.id}/turns",
        json={"message": "/research 分析 Agent Workbench"},
    )
    assert response.status_code == 202
    assert response.json()["run_id"]
    assert response.json()["status"] in {"created", "running"}


def test_sse_resumes_after_last_event_id(client, seeded_events):
    response = client.get(
        seeded_events.stream_url,
        headers={"Last-Event-ID": str(seeded_events[1].sequence_no)},
    )
    assert seeded_events[0].event_id not in response.text
    assert seeded_events[2].event_id in response.text


def test_run_api_never_returns_a_module_local_status(client, existing_run):
    body = client.get(f"/companies/company_001/workbench/runs/{existing_run.run_id}").json()
    assert body["status"] in {item.value for item in RunStatus}
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_run_sse_api.py -q`

Expected: turn/SSE endpoints do not exist.

- [ ] **Step 3: Implement RunTaskManager**

Use one `ThreadPoolExecutor(max_workers=2)` owned by the FastAPI app and a thread-safe map of local futures. `submit()` calls `TurnCoordinator.prepare_turn()` synchronously, returns its `run_id`, then submits `TurnCoordinator.execute_prepared_turn(prepared)` to the pool; it must not call `run_turn()` and create a duplicate Run. `status()` always reads SQLite, not the future, so completed/interrupted state survives process-local map loss. Cancellation marks `cancel_requested`; Runtime checks it between actions and stops with `cancelled`.

Define `RunStatusResponse.status: RunStatus`; cancellation must call `require_run_transition(current, RunStatus.CANCELLED)` rather than assigning a string. Unknown persisted status is a server integrity error and is not passed through to the UI.

- [ ] **Step 4: Implement SSE serialization**

SSE event format:

```text
id: <sequence_no>
event: <event_type>
data: {"event_id":"evt_01","run_id":"run_01","status":"done","public_summary":"已选择调研技能","attributes":{"skill_id":"research_report"}}
```

Poll SQLite at 250 ms while Run is active and emit a heartbeat every 15 seconds. Close the current stream after terminal `RunStopped`; also close after non-terminal `RunInterrupted` once its event is delivered, allowing the client to present an explicit Resume action and open a new stream after `RunResumed`. Apply redaction before serialization.

- [ ] **Step 5: Add endpoints and preserve sync compatibility**

```text
POST /companies/{company_id}/workbench/sessions/{session_id}/turns
GET  /companies/{company_id}/workbench/runs/{run_id}
POST /companies/{company_id}/workbench/runs/{run_id}/cancel
GET  /companies/{company_id}/workbench/runs/{run_id}/events
```

Current `POST /companies/{company_id}/workbench/sessions/{session_id}/messages` continues to call the synchronous path for scripts and old tests.

- [ ] **Step 6: Verify and commit**

Run: `py -3 -m pytest tests/test_run_sse_api.py tests/test_chat_workbench_service.py tests/test_app_service.py -q`

Expected: all selected tests pass.

```powershell
git add src/policy_impact/app/run_task_manager.py src/policy_impact/app/sse.py src/policy_impact/app/chat_workbench_service.py src/policy_impact/app/api.py tests/test_run_sse_api.py
git commit -m "feat: stream local agent run events"
```

### Task 2: Integrate Streaming Run State Into The Conversation

**Files:**
- Modify: `src/policy_impact/app/static/workbench/index.html`
- Modify: `src/policy_impact/app/static/workbench/app.js`
- Modify: `src/policy_impact/app/static/workbench/styles.css`
- Modify: `scripts/run_workbench_ui_cdp_check.mjs`

**Interfaces:**
- Consumes: create Run and SSE APIs.
- Produces: one live execution card in the message stream, cancellation control, reconnect and final assistant message refresh.

- [ ] **Step 1: Extend UI smoke assertions before implementation**

Add CDP assertions for:

```javascript
assertElement("#chat-stream");
assertElement("#prompt-input");
assertElement("#model-picker");
assertNoDocumentOverflow();
assertComposerVisible();
```

Add a mocked API mode that delays `RunStopped`; assert `.run-live-card` appears above the eventual assistant bubble.

- [ ] **Step 2: Run UI check and verify RED**

Run: `node scripts/run_workbench_ui_cdp_check.mjs`

Expected: live-run selector assertion fails.

- [ ] **Step 3: Implement frontend run state**

Extend `state` with:

```javascript
activeRun: null,
eventSource: null,
liveEvents: [],
runConnectionState: "idle",
```

`sendMessage()` posts to `/turns`, renders the user message immediately, opens EventSource, appends redacted events, then reloads messages and final Trace after `RunStopped`. On disconnect, reconnect with last sequence ID and show a compact “正在重新连接” status; do not duplicate events.

- [ ] **Step 4: Render a stable live execution card**

The card appears after the user bubble and before the assistant bubble. It has fixed header height, one status line, elapsed time, latest public event, event count and a symbol-only cancel button with tooltip. It does not show raw JSON or animated decorative backgrounds.

- [ ] **Step 5: Keep Composer behavior stable**

The fixed model select stays inside `.composer-toolbar`; with only one available model it displays `deepseek-v4-flash` and is disabled, not removed. Slash Palette continues to populate the prompt. Sending is disabled when model unavailable, Run active, or prompt empty.

- [ ] **Step 6: Verify and commit**

Run:

```powershell
node --check src\policy_impact\app\static\workbench\app.js
node scripts\run_workbench_ui_cdp_check.mjs
```

Expected: syntax and UI assertions pass.

```powershell
git add src/policy_impact/app/static/workbench scripts/run_workbench_ui_cdp_check.mjs
git commit -m "feat: show live agent execution in chat"
```

### Task 3: Render Lazy Span-Aware Trace

**Files:**
- Modify: `src/policy_impact/app/static/workbench/app.js`
- Modify: `src/policy_impact/app/static/workbench/styles.css`
- Modify: `scripts/run_workbench_ui_cdp_check.mjs`

**Interfaces:**
- Consumes: Trace v3 summary/events and event-detail endpoint.
- Produces: summary metrics, parent/child timeline and on-demand detail drawer.

- [ ] **Step 1: Add failing UI assertions**

Assert that a seeded Subagent run renders:

```text
.thinking-card[data-run-id]
.trace-event[data-depth="0"]
.trace-event[data-depth="1"]
.trace-detail-trigger
```

Before clicking detail, assert the browser made no event-detail request. After clicking, assert exactly one request and one `.trace-detail-drawer`.

- [ ] **Step 2: Run UI check and verify RED**

Run: `node scripts/run_workbench_ui_cdp_check.mjs`

Expected: depth/detail assertions fail against the current step list.

- [ ] **Step 3: Replace raw step rendering with event timeline**

Render one row per public AgentEvent. Compute depth by following `parent_span_id`; cap visible indentation at 3 levels and display deeper levels with a textual parent label. Event rows show familiar type icon, public summary, status and duration. Subagent child spans are grouped under a collapsible parent.

- [ ] **Step 4: Load redacted detail on demand**

`loadEventDetail(eventId)` calls the detail API only after user action, caches by event ID, renders allowlisted key/value fields, and caps visible long text with “展开”。Never render HTML from payload values; use `textContent` or escaped strings.

- [ ] **Step 5: Preserve trace placement and responsive behavior**

The Trace card remains immediately above its assistant answer. Remove use of legacy `trace.steps` after compatibility window. On widths below 720 px, metrics become a two-column grid and child indentation narrows without horizontal page scrolling.

- [ ] **Step 6: Verify and commit**

Run:

```powershell
node --check src\policy_impact\app\static\workbench\app.js
node scripts\run_workbench_ui_cdp_check.mjs
```

Expected: lazy request, span depth and no-overflow assertions pass.

```powershell
git add src/policy_impact/app/static/workbench/app.js src/policy_impact/app/static/workbench/styles.css scripts/run_workbench_ui_cdp_check.mjs
git commit -m "feat: render lazy span-aware trace"
```

### Task 4: Add Tool Approval Cards

**Files:**
- Modify: `src/policy_impact/app/static/workbench/index.html`
- Modify: `src/policy_impact/app/static/workbench/app.js`
- Modify: `src/policy_impact/app/static/workbench/styles.css`
- Modify: `scripts/run_workbench_ui_cdp_check.mjs`

**Interfaces:**
- Consumes: pending Approval and decision/resume APIs.
- Produces: accessible approval card inside the paused Run, with approve/deny and expiry state.

- [ ] **Step 1: Add failing approval UI assertions**

Seed a paused Run and assert `.approval-card` contains tool name/version, reason, risk summary, normalized argument summary, expiry and two buttons. Assert no API key/header values appear.

- [ ] **Step 2: Run UI check and verify RED**

Run: `node scripts/run_workbench_ui_cdp_check.mjs`

Expected: approval card is absent.

- [ ] **Step 3: Implement approval rendering and actions**

When SSE emits `ApprovalRequested`, fetch the approval and render one card under the corresponding event. Approve and deny require one click; approve sends decision then resume. Disable both buttons while submitting and after terminal status. Expired state is read-only and offers “重新规划” by sending a new user turn, not by mutating the old Run.

- [ ] **Step 4: Make risk readable without exposing secrets**

Show argument keys and safe values only. URL displays host/path without query secrets; file path displays controlled relative path; long text displays checksum and size. The card uses warning accent sparingly and no nested cards.

- [ ] **Step 5: Verify and commit**

Run:

```powershell
node --check src\policy_impact\app\static\workbench\app.js
node scripts\run_workbench_ui_cdp_check.mjs
py -3 -m pytest tests/test_approval_api.py -q
```

Expected: all pass.

```powershell
git add src/policy_impact/app/static/workbench scripts/run_workbench_ui_cdp_check.mjs
git commit -m "feat: approve paused tools from workbench"
```

### Task 5: Add Memory And Proposal Management Sheet

**Files:**
- Modify: `src/policy_impact/app/static/workbench/index.html`
- Modify: `src/policy_impact/app/static/workbench/app.js`
- Modify: `src/policy_impact/app/static/workbench/styles.css`
- Modify: `scripts/run_workbench_ui_cdp_check.mjs`

**Interfaces:**
- Consumes: Memory list/history/forget, Proposal decision and Profile Snapshot APIs.
- Produces: an on-demand sheet; no permanent right sidebar.

- [ ] **Step 1: Add a Memory entry point and failing assertions**

Add a compact `记忆` command in the sidebar footer and a `/memory` local UI command. Assert opening it creates `#memory-sheet`, focus moves into the sheet, Escape closes it, and focus returns to the trigger.

- [ ] **Step 2: Run UI check and verify RED**

Run: `node scripts/run_workbench_ui_cdp_check.mjs`

Expected: memory sheet assertions fail.

- [ ] **Step 3: Implement sheet views**

Use tabs `已确认`、`待确认`、`历史版本`. Filter by namespace and memory type. Each active record shows subject, short content, namespace, confidence, source and version. Proposal rows have approve/reject commands. History shows superseded/tombstone records but never allows direct editing.

- [ ] **Step 4: Implement Forget and Snapshot actions**

Forget opens a confirmation dialog naming one subject and creates a tombstone. “生成画像快照” calls the snapshot API and shows returned JSON/Markdown Artifacts. No inline free-form editing is added; corrections go through a new proposal generated by chat.

- [ ] **Step 5: Verify and commit**

Run:

```powershell
node --check src\policy_impact\app\static\workbench\app.js
node scripts\run_workbench_ui_cdp_check.mjs
py -3 -m pytest tests/test_memory_api.py -q
```

Expected: all pass.

```powershell
git add src/policy_impact/app/static/workbench scripts/run_workbench_ui_cdp_check.mjs
git commit -m "feat: manage versioned memory in workbench"
```

### Task 6: Add Artifact Preview And Replay Controls

**Files:**
- Modify: `src/policy_impact/app/api.py`
- Modify: `src/policy_impact/app/static/workbench/app.js`
- Modify: `src/policy_impact/app/static/workbench/styles.css`
- Modify: `scripts/run_workbench_ui_cdp_check.mjs`
- Test: `tests/test_artifact_api.py`
- Test: `tests/test_report_catalog_api.py`

**Interfaces:**
- Consumes: ArtifactStore, Eval/Benchmark Artifact records, and Inspect/Replay APIs.
- Produces: safe Artifact content endpoint, read-only experiment catalog, preview sheet, checkpoint inspect, Replay launch and Diff preview.

- [ ] **Step 1: Write failing Artifact API tests**

```python
def test_artifact_content_is_scoped_to_company_and_record(client, artifact):
    response = client.get(f"/companies/company_001/workbench/artifacts/{artifact.artifact_id}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(artifact.media_type)


def test_unknown_or_cross_company_artifact_is_not_disclosed(client, other_company_artifact):
    response = client.get(
        f"/companies/company_001/workbench/artifacts/{other_company_artifact.artifact_id}"
    )
    assert response.status_code == 404


def test_eval_report_catalog_returns_only_persisted_artifact_metrics(client, eval_report):
    response = client.get("/companies/company_001/workbench/eval-reports")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["artifact_id"] == eval_report.artifact_id
    assert item["dataset_version"]
    assert item["config_hash"]


def test_report_catalog_does_not_scan_unregistered_files(client, unregistered_report_file):
    response = client.get("/companies/company_001/workbench/benchmark-reports")
    assert str(unregistered_report_file) not in response.text
```

- [ ] **Step 2: Run tests and verify RED**

Run: `py -3 -m pytest tests/test_artifact_api.py tests/test_report_catalog_api.py -q`

Expected: Artifact content and report catalog endpoints are missing.

- [ ] **Step 3: Add Artifact metadata/content API**

Add `GET /companies/{company_id}/workbench/artifacts/{artifact_id}`. Resolve metadata from SQLite, verify company ownership through Run, re-check path root and content hash, then return FileResponse with safe media type and filename.

Add read-only `GET /companies/{company_id}/workbench/eval-reports` and `GET /companies/{company_id}/workbench/benchmark-reports`. They query registered ArtifactRecord metadata by company and artifact type; each item exposes dataset/config/runtime hashes, model ID, measured-run count, status, hard violations and public metrics from the validated report Schema. Never scan arbitrary directories, infer metrics from filenames or start a live model experiment from these endpoints.

- [ ] **Step 4: Implement preview and Replay UI**

Artifact chips open an on-demand sheet. Markdown renders as escaped structured text, JSON as a collapsible tree, HTML as source/download only unless served in a sandboxed iframe without same-origin privileges. A read-only “评测证据” tab lists Eval/Benchmark versions and links every visible metric to its Artifact ID and hashes. Trace offers `检查 Checkpoint`、`Recorded Replay`、`Fork Replay`、`Fresh Replay`; Fresh requires a confirmation dialog explaining external model/tool calls. Display ReplayDiff sections and lineage.

- [ ] **Step 5: Verify and commit**

Run:

```powershell
py -3 -m pytest tests/test_artifact_api.py tests/test_report_catalog_api.py tests/test_replay_api.py -q
node --check src\policy_impact\app\static\workbench\app.js
node scripts\run_workbench_ui_cdp_check.mjs
```

Expected: all pass.

```powershell
git add src/policy_impact/app/api.py src/policy_impact/app/static/workbench scripts/run_workbench_ui_cdp_check.mjs tests/test_artifact_api.py tests/test_report_catalog_api.py
git commit -m "feat: inspect artifacts and replay runs"
```

### Task 7: Apply Stable Visual Tokens And Responsive Constraints

**Files:**
- Modify: `src/policy_impact/app/static/workbench/styles.css`
- Modify: `src/policy_impact/app/static/workbench/index.html`
- Modify: `scripts/run_workbench_ui_cdp_check.mjs`

**Interfaces:**
- Consumes: all Workbench components.
- Produces: coherent desktop/mobile visual system and automated layout checks.

- [ ] **Step 1: Lock visual tokens**

Use this restrained palette and geometry, adjusting existing selectors rather than adding a theme framework:

```css
:root {
  --bg: #f7f8fa;
  --surface: #ffffff;
  --surface-subtle: #f1f3f5;
  --sidebar: #111827;
  --text: #182033;
  --muted: #657084;
  --border: #dce1e8;
  --accent: #0f766e;
  --accent-strong: #0b5f59;
  --danger: #b42318;
  --warning: #9a6700;
  --radius: 8px;
  --sidebar-width: 320px;
  --composer-max: 920px;
}
```

No gradients, decorative orbs, negative letter spacing, viewport-scaled font sizes or radii above 8 px.

- [ ] **Step 2: Fix application layout ownership**

```css
html, body, #app { height: 100%; overflow: hidden; }
.app-shell { display: grid; grid-template-columns: var(--sidebar-width) minmax(0, 1fr); }
.sidebar { min-height: 0; overflow: hidden; }
.session-list { min-height: 0; overflow-y: auto; }
.workspace { min-width: 0; min-height: 0; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; }
.chat-stream { min-height: 0; overflow-y: auto; }
```

Collapsed sidebar uses a fixed icon rail; mobile sidebar overlays with backdrop. Composer never participates in document scroll.

- [ ] **Step 3: Add viewport and text-overflow checks**

CDP script checks `1440x900`, `1024x768`, `768x1024`, and `390x844`. For each viewport assert:

```text
document scrollWidth == viewport width
document scrollHeight == viewport height
Composer fully visible
session list independently scrollable
long Chinese and English tokens fit or wrap
Trace/Approval/Memory/Replay layers stay inside viewport
no two visible interactive element rectangles overlap unexpectedly
```

- [ ] **Step 4: Add keyboard and accessibility checks**

Verify Tab order reaches sidebar toggle, new session, session list, prompt, model and send. Escape closes palette/sheet/dialog. Buttons have accessible names; status updates use `aria-live`; dialogs trap focus while open. Color is never the only status indicator.

- [ ] **Step 5: Run visual verification and commit**

Run:

```powershell
node --check src\policy_impact\app\static\workbench\app.js
node scripts\run_workbench_ui_cdp_check.mjs
```

Expected: all four viewports pass layout and accessibility assertions. Save screenshots under `data/acceptance/ui/<commit>/`; these are generated evidence and remain ignored by Git.

```powershell
git add src/policy_impact/app/static/workbench scripts/run_workbench_ui_cdp_check.mjs
git commit -m "style: stabilize workbench interaction layout"
```

### Task 8: Full Workbench Acceptance Gate

**Files:**
- Modify: `readme.md`
- Create: `docs/acceptance/2026-07-17-workbench-api-ui-acceptance.md`
- Modify: `scripts/run_workbench_acceptance.py`
- Modify: `scripts/run_workbench_ui_cdp_check.mjs`

**Interfaces:**
- Consumes: all eight implementation plans.
- Produces: final functional, visual and evidence-backed acceptance report.

- [ ] **Step 1: Run the dialogue matrix**

Exercise simple chat, `/chat`, `/policy`, `/news`, `/research`, `/wiki`, multi-turn context, cross-session Memory, approval pause/resume, Subagent disagreement, Artifact preview and Recorded Replay. Record Run IDs and Artifact refs, not only answer text.

- [ ] **Step 2: Run complete automated verification**

Run:

```powershell
py -3 -m compileall -q src scripts tests
py -3 scripts\harness_linter.py
py -3 -m pytest -q
py -3 scripts\run_workbench_acceptance.py
node --check src\policy_impact\app\static\workbench\app.js
node scripts\run_workbench_ui_cdp_check.mjs
git diff --check
```

Expected: compile/linter/tests/JavaScript/UI commands exit `0`; acceptance script reports actual scenario failures instead of swallowing them.

- [ ] **Step 3: Inspect fresh browser evidence manually**

Use the in-app browser at desktop and mobile viewport to inspect session list, empty state, long answer, running state, failed state, approval card, Memory sheet, Artifact preview, parent/child Trace and ReplayDiff. Check browser console and failed network requests. Log every defect and fix it with a failing automated assertion before acceptance.

- [ ] **Step 4: Update truthful product documentation**

README “已实现” must match fresh tests and acceptance Artifacts. Include exact startup URL, fixed model requirement, supported Slash commands, inspect-vs-Replay semantics, Memory authorization behavior and where Eval/Benchmark reports live. Do not copy expected plan values into results.

- [ ] **Step 5: Commit final acceptance**

```powershell
git add readme.md docs/acceptance/2026-07-17-workbench-api-ui-acceptance.md scripts/run_workbench_acceptance.py scripts/run_workbench_ui_cdp_check.mjs
git commit -m "docs: accept complete agent workbench harness"
```
