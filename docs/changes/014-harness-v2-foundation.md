# 014 Harness 2.0 调度底座实现

日期：2026-10-18。状态：独立调度底座已实现并完成离线验证；金融入口接入尚未实现。

后续增量见 [015 模型执行与可信 Worker 能力](015-harness-v2-model-execution.md)：单次异步 ModelAdapter 与请求账本已接通，金融主循环的状态恢复仍未完成。本文件下方“剩余接入边界”保留 014 交付时点的事实，不回写为当时已完成。

后续调度底座补充见 [020 调度压力与 Stream 保留](020-dispatch-pressure-and-stream-retention.md)：它记录 014 交付后新增的服务循环、运行记录、队列水位/老化和安全清理实现及验证，不改变 014 的历史交付时点。

## 确认与权威

用户已明确确认 R1（suspended 与持久等待）、R2（同 Thread 新 follow_up Task）、R3（失败依赖 blocked），并同意继续实现。013 原冻结内容保持历史不改；[接口](../harness-v2/interface-contracts.md)、[存储](../harness-v2/sqlite-schema.md)、[投递](../harness-v2/redis-protocol.md)共同冻结为 `H2-CONTRACTS-2026-10-18-v1`，是经批准补充。

## 实现顺序

- [x] 确认记录和契约冻结。
- [x] 独立 h2 调度模块、显式迁移、类型契约与不可变产物。
- [x] SQLite Repository：创建、派生、租约、预算、事件与生命周期。
- [x] Outbox、Redis Transport、消费与恢复；FakeRedis 和脚本执行器故障验证。
- [x] 离线测试、差异审查、记录实际覆盖与剩余工作。
- [ ] 真实 Redis 的独立 namespace 集成验证；测试已提供，本机 Docker 引擎未运行。
- [ ] 后续将金融 AgentLoop、模型流和业务工具接入可恢复执行。
- [ ] 后续注册金融工具与 HTTP 入口；默认 1.0 不变。
- [ ] 经另行确认的固定批次真实模型与金融兼容性复验。

本轮先构建并验证调度底座；不迁移用户真实数据库、不启动旧项目 Worker、不改金融证据与审核、不自动切模型、不调用付费模型。新 Redis 依赖可选安装，不要求现有 1.0 用户启动 Redis。临时测试产物不写入语料或生产运行目录。

## 实现映射

| 代码 | 已实现的职责 |
| --- | --- |
| `src/policy_impact/dispatch/contracts.py` | 严格命令模型、可信 CallerContext/Lease、配置；拒绝模型伪造内部身份/租约和未知参数 |
| `src/policy_impact/dispatch/schema.sql`、`storage.py` | 与 SQLite 契约一致的 18 张业务表；显式事务迁移及独立版本校验表；WAL/FK/FULL；run 内不可变文件、SHA-256 与路径校验；构造对象不迁移数据库 |
| `src/policy_impact/dispatch/repository.py` | 根工作、spawn、send/新 follow_up Task、Checkpoint、持久 wait/唤醒、状态/游标、取消、恢复、租约 fencing、调用账本、结果版本/渐进读取；Outbox 发布锁/有限重试/显式 redrive；有界分页对账；可信用户模型策略 CAS |
| `src/policy_impact/dispatch/transport.py` | 可注入 Redis client，XADD/XREADGROUP/XAUTOCLAIM/XACK；4 KiB 元数据协议；Dispatcher 不在 DB 事务内联网；DB 确认失败向上传递 |
| `src/policy_impact/dispatch/worker.py` | 异步处理器壳、心跳与执行权丢失时停止、commit 后 ACK、一次元数据补交、恢复输入与真实读取记录；没有默认模型调用或金融处理器 |
| `tests/test_dispatch_foundation.py` | 临时文件 DB/FakeRedis、并发/故障/20 轮脚本父子任务；另有 2 项显式环境变量开启的真实 Redis 测试 |

`WorkInput.brief` 合并不可变任务材料与可信 Repository 生成的运行视图，包含身份/depth、权限快照、允许工具、输出契约、预算和 deadline；不会把父完整会话或兄弟未授权正文复制给子 Agent。`observations` 包含持久 wait 恢复后的全部目标状态、已接受结果 envelope 与 result_ref。它证明脚本处理器获得了这些输入，**不证明真实金融模型已经收到或理解**。

`context_accepts` 是后续可信模型执行器提供的“完整输入是否适配实际上下文”的判断，不交给模型 JSON 自报。缺省仍给完整摘要 envelope 与引用，但不自动注入全文；提供判断且短结果不超 16 KiB 时才尝试附全文。长文完整读取通过 result.get 的字符分页，不依赖截断。

Redis client 放在可选 extra `dispatch`，FakeRedis 为开发依赖；默认金融入口不加载 Redis，也不自动建表。生产产物根目录由后续装配显式指向 `D:/AAAcode/code-code/agent+/harness/runtime-records`；本轮所有新调度测试仅用临时目录，未在生产根写入 Query 或运行记录。

## 验收记录

本节仅记录本轮运行验证，不将此前 16 项内存 DDL 检查算作运行调度通过。

- `uv run --locked python -m pytest tests/test_dispatch_foundation.py -q`：**95 passed / 2 skipped，34.26 秒**。跳过的是未提供 `H2_TEST_REDIS_URL` 的两项真实 Redis 测试；95 项包含 20 轮重复脚本，不是 95 个不同金融 Query。
- 新增 dispatch 代码及定向测试的 Ruff 检查通过；本轮没有修改默认金融 Prompt/工具/AgentLoop 或原项目。
- 7 份入口/契约文档的 119 个本地文件链接目标存在；未把文件存在检查说成 heading 锚点或全部设计语义验证。

### 关键故障覆盖

- 相同命令重放与冲突、并发同 key 创建、两 Worker 抢同一 claim、并发预算分配；失败不产生半个 Thread/账户。
- 根/子/孙 depth=0/1/2；兄弟和跨 Run 管理拒绝；结果/section/Checkpoint 的引用权限；显式授予结果不扩大为兄弟任务管理权。
- 单执行名额中，根挂起释放容量、子任务完成、根换 Attempt 恢复；子任务失败也唤醒父任务并传回真实错误。
- all 等待、超时不取消目标、失联租约接管、旧 token 拒绝成功提交、取消后迟到全文仅审计；取消子树先整体 fencing，不能因取消子任务而意外重新唤醒父任务。
- XADD 成功但 DB 未确认、发布锁失效、重复投递、有限失败进入 dead_letter、人工 redrive 保留历史；Redis 数据丢失重建原 Attempt 通知。
- 结果 commit 后 ACK 失败：恢复只确认、不重复处理器；结果 DB 事务失败或心跳存储失败：不提前 ACK、不伪报完成。
- 缺失/损坏恢复文件不伪造成功，不调用处理器；消息必须先 delivered 才能随 Checkpoint 标 consumed，游标不回退。
- 一次元数据补交报错/换正文/仍不完整/类型不合规时保留原完整正文并标 partial；补交成功复用同一个正文 Artifact，保留初稿版本。
- 结果按字符分页，连续覆盖前不标 full_viewed；等待恢复始终提供 summary envelope，短全文受实际容量判断约束，长全文不自动注入。
- 调用 charge/refund 幂等；未确认在途调用保守记 unknown，终态预算逐层返还；父恢复保留子孙消费；同模型传输重试有上限，额度错误不自动重试/换模型。
- 可信用户显式更新模型策略：CAS/回执幂等、已有 Attempt 快照不变、新工作受新策略约束；撤销后不能批准旧模型的新请求，Agent 不能扩大允许模型。
- 固定脚本父 + 两子任务重复 20 轮，校验父恢复确实拿到两个 envelope，最终无 queued/leased/running 孤儿 Attempt、外键检查无错误。该项是状态/交接验证，不是金融语义评分。

### 兼容与环境检查

- `uv run --locked python scripts/test_finance_baseline.py`：**1589 passed / 30 skipped，51 模块，128.07 秒**；一项既有 Starlette/httpx 弃用警告。凭证清空、测试工作区隔离；不调用真实模型。
- `uv run --locked python scripts/check_finance_baseline.py`：此前检查把测试运行生成的 SQLite 目录误识别为额外工作区。检查现按正式工作区的 `company.yaml` 标识判定，因此保留 `company_001`、`company_test`、`evidence_ledger_test`、`evidence_range_test` 等测试数据库，不删除数据，也不放宽金融工作区隔离要求。
- Docker CLI 存在，但连接 Docker Desktop Linux Engine 失败（未运行）。本轮没有启动/安装 Redis 服务，没有运行两项真实 Redis 测试；不能把 FakeRedis 等同服务部署、网络断开或断电可靠性证明。
- 本轮未跑付费模型、用户金融 Query 或同步/异步金融兼容对照；没有新的模型成功率或金融质量结论。未重新执行全库继承测试；此前 19 项失败的历史基线不被本轮局部通过覆盖。

### 可重复命令

```powershell
uv sync --locked --group dev --extra dispatch
uv run --locked python -m pytest tests/test_dispatch_foundation.py -q
uv run --locked ruff check src/policy_impact/dispatch tests/test_dispatch_foundation.py
uv run --locked python scripts/test_finance_baseline.py
```

两项真实 Redis 测试仅在显式配置 `H2_TEST_REDIS_URL` 时运行。它们只操作随机独立 Stream，结束后删该 Stream，不执行 FLUSHDB/FLUSHALL；不要将 URL 或密码写进 Git。测试命令不会调用模型。

## 剩余接入边界

1. 尚无统一应用服务/认证后 FastAPI 路由或模型工具注册。Repository API 是内部可信调用，不是可直接暴露给浏览器的接口；HTTP 长轮询和完整错误 envelope 仍需服务层接入。
2. 尚无真实 ModelAdapter/金融 AgentLoop 桥接，也未将根 Agent 从旧 Python 栈迁到 h2 Task。当前默认金融委派仍同步；Prompt/Skill、EvidenceLedger、模型实际上下文预算、流式动作与调用预约需在桥接时接通。
3. 普通故障已用异常注入/FakeRedis 验证，不代表进程 kill、真实网络/磁盘掉电或多进程长负载已验收。连接/idle/调用总时长等五层 timeout、供应方取消和私有推理分离需随 Adapter 实测。
4. 没有真实工具副作用 journal，不能把未知状态的外部写操作自动重放，也不宣称 exactly-once；本轮处理器只用脚本结果，不执行真实写工具。
5. 服务调度循环、畸形消息持久隔离/告警、队列高低水位/老化、Stream 安全清理、孤儿产物 GC、Event/Trace 导出、时钟跳变告警及 p50/p95 等部署观测尚未接通。当前 Worker/Dispatcher 提供单步方法，不伪称已部署常驻服务。
6. 未修改前端、默认模型、付费 fallback 或真实数据库；未因本轮实现自动提交/push Git。

接下来按已确认契约接通“根 Agent + 模型/工具执行边界 + 可恢复金融上下文”，再进行真实 Redis 和经授权的固定批次金融对照。此前不得宣布 Harness 2.0 整体完成或商业化可用。

