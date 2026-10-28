# 109 工作台查看真实测试记录

日期：2026-10-28。用户要求在前端查看实际测试记录，以检查前端展示。

## 接入方式

工作台左侧增加“测试记录”视图，列出指定 runtime-records 根目录下已经导出的批次/题目。
选中后复用研究消息、任务树、Attempt、模型请求和事件组件，展示原始问题、最终答案和真实状态。
子任务结果可按 Artifact 分页读取。保留 failed、partial、cancelled 等状态；Root 成功不代表业务验收通过。
刷新及可分享的 archive 查询参数恢复同一条记录。

历史记录是只读查看：读取每题独立 SQLite 和导出的 scheduling-trace.json，
不导入在线数据库、不启动历史 Worker、不补写全文读取事件、不重新执行模型。
归档视图隐藏发起研究/停止执行操作，回到研究记录或新建研究后恢复在线交互。

## 接口与边界

- `GET /api/h2-workbench/archives`：批次、题目、问题摘要、模型、保存时间、Run 状态。
- `GET /api/h2-workbench/archives/{id}?after_event_id=0`：消息投影与兼容现有渲染器的 Run/Task/Trace/metrics。
- `GET /api/h2-workbench/archives/{id}/artifacts/{ref}?offset=0&limit=16000`：原 Run 内 Artifact 分页。

服务端只发现根目录内 `*/cases/*/record.json`；id 由相对目录摘要生成，不接受浏览器传文件路径。
Run 与 Artifact 均按该记录绑定；禁止目录逃逸，读取 Artifact 校验长度和哈希。
SQLite 使用只读连接，不创建 schema 或进行迁移。Trace 分页使用原事件号，保留已导出的事件和层级。
默认根为项目同级 runtime-records，可通过 FINANCE_H2_ARCHIVES 显式配置。
原始导出缺失或损坏时报告读取失败，不编造成功记录或继续付费执行。

## 验证

验证目录边界、跨 Run Artifact、哈希损坏、分页、Task/Attempt 失败状态、预算和结果完整性。
打开真实 SCH-04/SCH-03/SCH-02，在网页核对题目、任务数、请求数和失败展示，检查刷新、任务结果、Trace 事件。
本变更验证展示与读取能力，不据此更改 108 的模型质量结论。

2026-10-28 实测：Python 定向 8 项通过，前端 Node 3 项通过；Ruff、JS 语法及 diff 检查通过。
浏览器核对 SCH-02 r5 为 37 次请求/3 个 Task/476 个事件，SCH-03 r4 为 36/5/475，SCH-04 r4 为 31/2/467。
SCH-02 两个子任务保留 partial；Analyst 结果从 16000 字符继续读取至 23133 字符。
SCH-03 保留失败 Analyst 及第七个 Attempt 的 contract_error，Trace 显示全部 36 次请求。
SCH-04 刷新恢复同一 archive、原答案和完整 Trace；浏览器未捕获控制台错误。
修复现有 setRun 重建 Trace 列表时丢失容器 id、导致切换后模型请求列表无法渲染的问题。
8511 工作台已正常重启并加载当前代码；原始测试记录没有改写，本轮没有新增付费模型调用。
本地验证记录位于 `../runtime-records/h2-workbench-archive-view-20261028`（相对于项目根目录）。
