# 015 Harness 2.0 模型执行与 Worker 能力边界

日期：2026-10-18。状态：第 1 步已实现并离线验证，第 2/3 步未完成。依据：用户同意继续实现已冻结的调度契约。本记录细化[接口第 8 节](../harness-v2/interface-contracts.md#8-内部接口repositorydispatcherworkeradapter)，不改变金融研究方法、收费授权或默认 1.0。

## 实现顺序与边界

1. 完成单次物理模型请求、调度账本与可信执行能力的接通，用临时 SQLite、FakeRedis、脚本响应和 HTTP 仿真验证。
2. 后续接通金融主 Agent 的持久执行位置，以及计划、证据、能力加载、候选稿和审核状态的保存/恢复。根与子任务均使用 h2 身份，不保留一个不可恢复的父 Python 栈。
3. 后续接业务入口和服务循环，真实 Redis 及付费金融对照单独验收。

本轮代码首先完成第 1 步，不把通用执行能力误称为第 2 步已经完成。不改现有 AgentLoop、默认模型、HTTP API 或前端；没有隐式初始化、迁移和网络请求。文档和代码里的“执行能力”是提供给可信处理器的对象，不是放进模型 JSON 的租约或数据库句柄。

## 一次模型调用

流程：Worker claim/start -> 绑定 Attempt 的不可变模型快照 -> 验证参数与实际输入容量 -> 解析凭证引用 -> 预约一次调用 -> 发送前再次校验执行权 -> 记 started -> 单次 HTTP 请求 -> 检查完整终态 -> 公共 JSON/现有动作契约校验 -> 写调用结果 -> 交回处理器。

- 单次请求使用现有 OpenAI Chat/Responses 的请求构造和终态解析，不调用带内部重试的旧 `complete()`。HTTP 层无重试、不跟随重定向、不自动选择另一 endpoint/profile。格式修复若后续执行，也必须是独立、有账本的模型请求。
- 参数来自可信快照。上下文沿用最高 1M、输出/安全预留和较小显式窗口；使用现有 token 估算器，不称为供应方容量认证。未知配置拒绝，不静默忽略。
- `profiles_from_registry(profile_ids, settings=...)` 只导出显式选择的已有命名配置和参数；不解析密钥、不修改进程默认模型、不自动授权注册表中的全部模型。新层默认参数来自现有研究容量/输出和传输边界：1M 输入窗口、32768 输出、4096 安全预留、90 秒连接/idle、600 秒单次总时长；均进入新 Attempt 快照，不改旧模型配置。
- 使用真正可取消的异步 HTTP 连接；连接、读取 idle、请求总时长、可选 Attempt 截止、Task/Run 截止取更早边界。未另设 Attempt 时限时沿用 Task 剩余时间，不暗中减少已确认研究预算。
- SSE 使用现有终态校验。此阶段公开正文缓冲至完整终态才交付事件；不承诺实时逐 token UI。无终态、截断、原生 tool_calls 或损坏流不得产生 action_candidate，不拼接两次响应。隐藏推理不进入事件、TaskBrief 或父结果。
- started/text_delta/usage/action_candidate/completed/failed 事件绑定 call_id，调用内 seq 单调。text_delta 在此阶段是经过终态校验的完整公共文本；候选通过现有 JSON 动作契约后才交给执行者，Adapter 本身不执行工具。
- 实际 HTTP 成功而 JSON 不合规，调用账本仍记录物理请求已完成，另记录 contract_error；不能假装没有付费请求。未发送且可确认时才退款；超时/取消/断流等在途不明保守记 unknown，不退款。
- 额度、鉴权、协议、格式错误不自动重试。可重试传输错误交给现有 Repository 创建有上限的同模型新 Attempt，Adapter 不在内部重连。取消只保证本地停止接收及不再批准新增调用，不保证供应方撤回或免计费。
- 异常只公开分类、脱敏诊断和供应方请求标识；凭证在发送时从 auth_env 解析，不写入快照、事件、日志或产物。存储失败必须向上冒泡，不作为普通模型失败后 ACK。

## Worker 的可信能力

保留旧 handler(work) 兼容入口；新增显式 executor(work, execution) 入口，二者只能选一个。execution 私有绑定本次 lease，处理器可调用：

| 能力 | 用途与限制 |
| --- | --- |
| `complete(messages, schema_name, final=False)` | 单次模型请求；final 是可信处理器的阶段选择，不接受模型自行声明绕过预留 |
| `tool(name, arguments)` | 仅已授权的 agent.spawn/status/send/cancel/resume、result.get；复用 Repository 的权限/幂等/作用域规则，不暴露 run.model_policy.set |
| `store_context` / `load_context` / `checkpoint` | 保存合法公共执行状态、读取当前任务已授权产物、持久检查点；不保存私有推理或密钥 |
| `wait(arguments, checkpoint)` | 返回 Suspend，Worker 原子保存并释放执行容量；恢复仍为同 Task 的新 Attempt |
| `mailbox` / `validate` | 下一安全边界取消息、检查取消/lease；不抢断远端推理 |

工具详细 Schema 按 `await describe(name)` 获取。调用参数用已有严格命令模型；status/result.get 也验证未知字段和类型。未授权工具不能通过 describe 泄漏完整契约。缺稳定 key 返回参数错误，不替模型随机生成新 key。主 Agent 执行位置保存后才能安全重用原 key；本层不宣称替金融 Agent 完成了动作 journal。

WorkInput 不增加租约 token。只读能力仍检查当前 lease；金融业务工具不在此处重新实现。金融最终交付/审核仍归既有金融策略，不允许把调度 Submission 当成“金融结论已验证”。

## 验收与记录

- [x] Chat/Responses JSON 与 SSE、完整/截断/断流、无原生工具、隐藏推理隔离。
- [x] 输入/凭证/配置错误发送前拒绝；每次实际 HTTP 恰有一条调用账；无自动 fallback。
- [x] 额度/鉴权/传输/超时/取消分类，时限取早边界，取消停止本地连接；unknown 不退款。连接故障使用 HTTPX 异常注入，idle/持续流总时长另有真实 loopback 联测；不宣称远端 TLS/网络故障已覆盖。
- [x] DB 失败不 ACK、不继续发请求；取消/模型撤销后的边界检查。
- [x] 新 Worker 能力实际驱动 root -> spawn -> wait -> 新 Attempt -> 结果 envelope -> 完整结果，单名额仍可前进，重复 20 轮。
- [x] 默认入口无改动，既有调度和 1.0 离线回归。

### 代码与验证记录

| 文件 | 实际职责 |
| --- | --- |
| `dispatch/model_execution.py` | 快照参数、命名配置导出、ModelRequest/ModelEvent、异步 HTTP、现有网关请求/终态复用；单次物理请求 |
| `dispatch/execution.py` | 私有绑定 lease 的可信能力、模型账本、公共输出保存、检查点/作用域引用、严格调度工具 |
| `dispatch/worker.py` | opt-in executor 装配、结束时撤销能力并清理在途请求；基础设施异常继续向上冒泡，不提前 ACK |
| `dispatch/repository.py` | 授权输入读取；模型完成的错误分类、usage 和公共输出引用在同一事务记录 |
| `tests/test_dispatch_execution.py` | 临时 DB/FakeRedis、HTTPX 仿真与临时 loopback HTTP 服务；20 轮有限并发父子交接 |

命令：`uv run --locked python -m pytest tests/test_dispatch_execution.py -q`，**78 passed / 32.39 秒**。包含重复参数用例，不是 78 个金融业务 Query。

定稿后合并命令：`uv run --locked python -m pytest tests/test_dispatch_foundation.py tests/test_dispatch_execution.py -q`，**173 passed / 2 skipped / 63.65 秒**；跳过的仍是没有设置 `H2_TEST_REDIS_URL` 的两个真实 Redis 用例。修改范围的 Ruff 与 `git diff --check` 通过，七份入口/契约记录的 118 个本地文件链接目标存在（未验证 heading 锚点）。临时 loopback 服务随测试结束关闭，不是部署服务。

兼容命令：`uv run --locked python scripts/test_finance_baseline.py`，**1589 passed / 30 skipped / 128.19 秒**，51 模块，一项既有 Starlette/httpx 弃用警告。脚本清除凭证并隔离工作区；没有调用付费模型。

新增测试确认：HTTP 403 的明确 `insufficient_user_quota` 才归为额度错误，普通 403 归供应方拒绝；Responses 失败终态和 Chat SSE error 均不误触发传输重试。格式不合规但有完整公共响应时，保留脱敏全文，物理调用记 succeeded、error_code 记 contract_error；它不代表研究成功。传输重试由 Repository 新建同模型 Attempt，每次重新记账，不在 Adapter 内重连。

### 后续未完成项

金融主循环仍为旧同步实现。下一步须先细化并实现其业务状态快照：执行位置/稳定动作 key、研究计划、真实 EvidenceLedger/来源版本、已加载能力、文章理解与综合处置、审核目标/缓存和完整候选稿。不能通过重新读取工具或重跑旧闭包来假装恢复，不能只恢复聊天消息而丢掉证据与审核状态。新 Adapter 尚未组织金融层的有界格式修复；旧 1.0 的恢复逻辑保持不变，后续修复请求也须走本层账本。

本轮没有业务入口/前端切换、真实 Redis、付费金融对照、公开 API、常驻服务或负载验收。HTTP 仿真、loopback 与脚本模型不证明真实金融质量、供应方可用性、远端取消或商业化性能。环境 9/10 及既有工作区目录问题沿用 [014](014-harness-v2-foundation.md) 的原记录，本轮没有删除目录或改断言；也没有重跑全库继承测试以覆盖其原有失败。
