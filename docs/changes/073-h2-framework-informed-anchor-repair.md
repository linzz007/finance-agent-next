# H2 技术问题的框架借鉴与最小锚点补正

后续 `087-h2-framework-informed-json-escape-repair.md` 已在本文件原有的
终态严格解析边界内，补充字符串非法反斜线的有界语法修复；本文件关于
锚点补正、结构性 JSON 拒绝、流式片段不可执行和不伪造证据的约束继续有效。

日期：2026-10-20。设计依据：用户本轮授权常规技术问题借鉴成熟实现后解决，复杂问题采用 MVP。
范围：仅显式 H2 的研究子任务终稿补正；先记录本设计，再实现和验证。其余 072 仍为草案。

## 1. 已核对的一手实现

直接从官方 Git 仓库获取源码，固定以下 commit；没有安装或执行外部项目。
网页读取工具故障不作为源码结论依据。源码机制存在，不等于本项目或这些项目的生产成功率证明。

| 项目与固定源码 | 实际机制 | 本项目采用与不采用的部分 |
| --- | --- | --- |
| [pi-mono JSON parser](https://github.com/badlogic/pi-mono/blob/fbc456d827d7a4a951c50e7145af066226e2f8e9/packages/ai/src/utils/json-parse.ts) | 完整参数先严格解析，可修复字符串内部控制字符和非法转义；流式预览可解析局部，但终态参数不能执行截断前缀 | 借鉴区分预览与可执行动作。不复制自动补括号、截取首个 JSON；当前没有证据表明字符串转义是首要故障，不新增宽松解析器 |
| [pi-mono agent loop](https://github.com/badlogic/pi-mono/blob/fbc456d827d7a4a951c50e7145af066226e2f8e9/packages/agent/src/agent-loop.ts) | 调用前查工具并校验参数；错误返回 isError 的工具结果 | 借鉴错误定位和执行前校验；我们已有参数错误反馈，不另造一套。它的部分参数转换策略不直接移植到严格金融契约 |
| [DeepSeek Harness tool calls](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/core/agent-loop/src/tool-calls.ts) 与 [工具 Schema](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/core/tools/src/schema.ts) | 非法 JSON 参数保留为字符串，不伪装成有效对象；执行前校验，错误成为可记录结果；测试明确验证不合成默认值 | 借鉴解析、校验、执行的分层；不是“所有 JSON 都自动修好”。保持我们的严格额外字段和权限约束 |

DeepSeek 官方仓库 README 明确标记 developer preview 和可能破坏兼容的更新，不能仅因官方/热门就称其商业成熟。
两者主要是模型工具调用路径；本项目继续使用已选择的文本 AgentAction 协议，不切 native function calling，不引入框架依赖。
“冻结分析后只修锚点”是针对本项目 ResearchResult 的适配设计，不声称是上面任一框架的现成能力。

## 2. 技术归因与处理边界

| 当前问题 | 类型与处理 |
| --- | --- |
| 非法 JSON、多个动作、工具参数不符 | 常规协议技术；沿用严格解析、按需工具契约、有界诊断与一次补正，记录首发/补正成功率 |
| RC12：补正缩写正文并新增金融错误 | 恢复机制技术缺口；本次优先锁正文，只修锚点 |
| RC07：深度二把溯源 ID 当作可用引用 | 具体上下文/权限视图缺陷；另做最小可用句柄修复，不靠扩大权限掩盖 |
| 传输错误 | 分层记录未知结果和真实请求；保持同模型、有界重发与实际记账，不把请求失败当成金融观点错误 |
| 虚假矛盾、跨日期归因、过度概括 | 语义质量，不能靠 JSON 合法证明解决；保留原文正反例，做定向核对 |
| 收尾阶段切入、缺少业务适配、真实 Redis 与负载 | 小范围未完成设计、工程范围或环境证据；不因为架构主体可用就声称均已解决 |

总策略：先复现，查一手实现；已有契约的技术缺陷直接修；复杂问题只解决当前阻塞；改变金融判断或权限语义仍先讨论。

## 3. 本次 MVP 的执行契约

1. 只有首响应已是合法 final 动作、每个 EvidenceLink 字段有效、去掉失配锚点后其余 ResearchResult
   全部通过严格校验时才进入此分支。JSON/字段类型/重复 disposition 等其他错误仍走 065 隔离补正。
2. 保存原始候选与正文 hash；content、candidate_answer、来源 refs、quotes、限制、缺口、材料处理和
   原动作状态增量全部冻结。有效锚点保持不变，不补造证据。
3. 对 065 增加唯一窄例外：补正可见已通过字段校验的 content/candidate_answer 只读视图，以及
   失配 link 的序号和原 refs。标记为 untrusted draft，不回喂完整被拒响应、原错误锚点或工具/XML 外壳。
   原始可信消息保持不变；候选不能授予权限、作为新观测或证明自身正确。代码限制输出与执行能力，
   不声称靠 untrusted 标签就消除了注入。
4. 同一模型通过原 agent_action.v1 返回以下唯一补正形态；不执行工具，不接受 state_update 或正文：

```json
{"type":"final","output":{"schema_version":"finance.h2.anchor_patch.v1","anchors":[{"link_index":0,"text":"The author keeps the view conditional."}]}}
```

5. anchors 可为空。只允许定位原失配序号一次；text 必须逐字存在于冻结的 content 或 candidate_answer。
   Runtime 只替换对应 text，refs/quotes 原样保留；合成后重跑 ResearchResult 及原来源/权限/摘录校验。
   结构绑定成功不代表文本被来源语义支持，仍不能叫“金融事实已验证”。
6. 未能定位的锚点剔除并明确记录序号和 partial/incomplete_metadata，原正文不丢失。
   补正再次出现契约错误也只剔除原失配绑定，保留原本合法的绑定与全文，不另重建或再补正。
   输入容量不足时同样不发补正请求，保留全文并披露绑定缺口；不强行扩上下文。
   真实传输/额度/预算/取消/租约/存储故障仍按原边界报告，原候选保存在审计，不伪装成已补正。
7. 一次补正、首次补正传输错误原样重发一次、最多三次物理请求的既有上限不变；每次仍走真实账本。
   非 eligible 的普通动作/Root 补正保持 065 原行为。新分支无新工具、DDL、依赖或模型选择。
8. 审计关联原候选、原请求/输出、补正请求/输出、合成结果、正文 hash、原失配/实际修复/剔除序号。
   原始模型输出不被覆盖；合成产物不能冒充模型原始输出。后续正常语义修订仍可产出新版本。

## 4. 验证

离线先覆盖：真实故障形态、候选正文及可选 candidate_answer 不变、原来源及摘录不变、有效绑定不变、
无效/重复/越界 patch、非法正文/工具/状态增量注入、空 patch/二次契约错误的完整 partial 交付、
上下文不足不发请求、传输重发完全相同、取消及预算故障不吞掉、其他字段错误仍隔离恢复。
然后运行受影响 H2 回归、默认金融基线、配置隔离与静态检查。

本轮不盲目追加付费批次，不以离线绑定通过声称金融错误已解决。下一次真实模型检查需单列新批次、
冻结输入版本，分别报告正文保留、绑定、语义、费用与耗时；不改写 R20。

## 5. 实现与实际验证结果

已实现：`dispatch/finance_anchor_repair.py` 限定补正视图、patch 和不可变合成；
H2 研究终稿按错误类型选用该分支，原有 Root/其他错误仍隔离补正。角色 Prompt 增加了
Runtime 补正专用的 Schema 例外，避免原 ResearchResult 指令与临时 patch 互相冲突。
全文、候选答案、原 refs/quotes 保留；未修复绑定通过终态 warnings 与 partial 状态披露。

- 新增 38 项离线用例。最终四个相关模块 **340 passed**（116.23 秒）。
- 最终 H2/state/compatibility/live-driver：**954 passed / 7 skipped**（340.98 秒）。
- 默认金融/shared-runtime：**1638 passed / 30 skipped**，52 模块（114.91 秒）。
- 配置/项目隔离 **10/10**；受影响六个 Python 文件完整 Ruff、tracked diff 检查通过；
  四份相关 Markdown 的 46 个本地文件链接目标存在。
- 初次广域回归有 4 项失败：一处可选 metadata 的兼容读取已修复；三处旧测试桩/旧协议预期已调整。
  非 anchor-only 的重复错误仍验证原终态失败，不借测试更新扩大普通补正。上述最终结果为修复后重跑。
- 使用 R20 已保存的首次原始终稿做无模型离线回放，确认为 eligible：28/28 锚点失配，
  空 patch 路径原样保留 5,613 字符 content 与 candidate_answer，排除全部失配绑定并记录 28 项缺口。
  这只验证保留与披露，不宣称修好了这 28 项引用，更不宣称原分析正确。

所有执行会话已结束；没有新增付费模型调用、真实数据库迁移、默认入口切换、Redis/Docker
操作或 Git 提交/推送。测试仍有既有 Starlette/httpx 弃用警告。跳过项不算通过；没有执行
外部框架自身测试，不宣称其兼容本项目。R20 及旧报告保持原样。

RC12 的正文重写漏洞已有代码约束与离线证据；真实模型的 patch 合规率、语义质量、RC07 两层
引用句柄、收尾切入、缺少业务适配和真实 Redis/负载仍未由本次验证解决。
