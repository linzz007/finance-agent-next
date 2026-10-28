# H2 JSON 字符串内控制字符的有限规范化

后续 `087-h2-framework-informed-json-escape-repair.md` 在本文件的严格终态
解析边界内，新增了字符串内非法反斜线的同类有界修复；本文件关于结构性
JSON 不修复、流式片段不执行、原文保留和后续 Schema 校验的约束继续有效。
以下为 078 时点的历史范围；对象边界已由 [083](083-h2-bounded-action-json-recovery.md)
细化，字符串非法转义已由 [087](087-h2-framework-informed-json-escape-repair.md)扩展，
诊断范围以 [088](088-h2-action-repair-diagnostic-boundary.md)为准，不以历史否定句覆盖后续契约。

日期：2026-10-20。范围：用户已授权常规技术问题借鉴一手实现后修复；不改变金融方法。
先记录本设计，再复现和实现。依据为 R22 的真实裸换行失败，不是为了接受任意模型输出。

## 1. 原因与参考

[R22](../acceptance/harness-v2-live-acceptance-r22-20261020.md) 的孙任务首稿含 2,677 字符分析，
但 JSON 字符串内存在未转义换行，在 column 223 被严格解析拒绝；其一次模型补正仍不合格，
后来依赖父任务 resume 才成功。这是有限字符串转义能够处理的明确语法故障。

参考 [pi-mono 固定源码](https://github.com/badlogic/pi-mono/blob/fbc456d827d7a4a951c50e7145af066226e2f8e9/packages/ai/src/utils/json-parse.ts)
的 repairJson/parseJsonWithRepair：它对字符串内控制字符和非法反斜线作有限处理，终态仍需完整解析。
本文件最初只实现控制字符；非法反斜线的新增范围、计数和验收以 087 为准。

## 2. 最小契约

1. 仅 H2 `agent_action.v1` 的完整公开终态，在已有唯一外层围栏规范化之后，
   将 JSON 双引号字符串内部未经转义的 U+0000..U+001F 编码为标准 JSON 转义。
   使用标准 json 字符串编码产生转义，随后仍由原 JSON 解析器、动作/字段/权限校验决定可执行性。
   仅以 `{` 开始的 JSON 对象候选进入此处理；既有 022 的 DSML 文本兼容路径不规范化、不扩大接受范围。
2. 只改变传输表示，不改解码后的字符串值；原始公开文本不覆盖，仍保存到 model_output artifact。
   已转义的换行、合法反斜线、Unicode、引号、结构空白保持。字符串外的非法控制字符不修。
3. 不给裸补丁加外壳、不补括号/引号、不取首个 JSON、不移除说明/XML/多个对象，
   不修未知转义或反斜线后裸控制字符，不合成默认值。不能把截断流或隐藏推理变成动作。
4. 仍不合规则走原一次同模型补正与原有传输边界；规范化本身没有模型请求，不改变预算、
   截止时间、任务恢复、native calling、默认 1.0 或金融语义。073 的正文冻结仍按原条件进入。
5. Adapter 给终态事件携带有界计数 `public_output_normalization.json_string_controls`。
   Execution 将可信计数与原 output_ref 原子记入 `model.call_finished` Event，成功返回时也放入
   ModelResponse.metadata。只接受该已知计数，不序列化任意 adapter/provider 元数据或文字。
   计数必须为严格整数 1..1,000,000；零、布尔、负值、越界或其他类型忽略，其他字段不复制。
   即使后续动作被拒绝，也保留发生过规范化的事实；规范化不是语义或来源验证。
6. 原模型解析与所有非 agent_action Schema 不变。诊断仍根据保留的原始响应工作，不将整个
   规范化后的被拒输出重新喂给模型。取消、租约与存储故障不吞掉，不发生额外隐式退款或调用。

## 3. 验证要求

- 先失败复现裸换行；覆盖有效字符串不变、所有控制字符、已转义引号/反斜线、结构空白，
  并验证未知转义、缺括号、多个 JSON、外部说明、字符串外控制字符、非法字段仍拒绝。
- Chat/Responses 的 JSON/SSE 公开终态、原输出保留、动作后校验和一次物理请求记账。
- 规范化计数的成功/失败 Event、ModelResponse、未知/伪造字段和凭证不泄漏，存储故障不 ACK。
- 只读回放 R22 原裸换行输出，证明解码后分析未改并进入严格后续校验；不代表金融分析正确，
  不改写 R22，也不自动追加付费重跑。
- 受影响模块、完整 H2、默认金融、配置隔离与静态回归。实际结果完成后追加。

## 4. 实现与实际验证

已实现 `finance_actions.normalize_json_string_controls`，在 H2 完整公开动作的围栏处理后运行。
它只扫描字符串边界和反斜线，不替代 JSON 解析器；合法 JSON 逐字不变，标准转义由 `json.dumps`
生成。Adapter 保留原始公开输出，完整解析后才产生候选；Execution/Repository 两层过滤已知计数，
将计数和原 output_ref 与调用结算原子记入 Event。无 DDL、依赖、额外请求或金融流程变更。

实际离线结果（2026-10-20）：

| 范围 | 结果 |
| --- | --- |
| 合成故障复现 | Chat/Responses × JSON/SSE × 有/无围栏，共 8 项先失败 |
| 初轮两个相关模块 | 222 passed；随后扩展检查曾为 315 passed/1 failed，失败原因是新增测试误调不存在的 scheduler 方法，已改为现有 tool 接口，不修改生产行为迁就测试 |
| 补充兼容边界反例 | DSML 原兼容路径有 2 项先失败；限定 JSON 对象入口后，4 项（含 2 个合法对照）保持原接受范围 |
| 最后受影响筛选 | 98 passed，135 deselected |
| 首轮广域 | H2 主体 1009 passed/7 skipped（452.20 秒），状态另跑 46 passed（1.36 秒）；默认金融 1638/30 skipped（185.63 秒） |
| 最终 H2/状态/兼容/验收入口 | 同一次完整执行，1059 passed、7 skipped，435.02 秒；较 076 净增 89 项 |
| 最终默认金融/共享 Runtime | 52 模块，1638 passed、30 skipped，189.49 秒 |
| 配置隔离 | 10/10，零模型调用 |
| 静态检查 | 完整 dispatch 与本次测试/私有回放脚本 Ruff、diff 检查通过 |

覆盖包括全部 32 种字符串控制字符、幂等性、Unicode/合法转义、字符串外控制字符保持拒绝，
Chat/Responses 的完整 JSON/SSE、保留原文、后续非法 Schema/参数/权限拒绝、锚点补正不重写正文、
成功与失败计数、任意元数据不复制、单次物理扣费、Event 结算回滚和存储失败不 ACK。
两个广域套件只出现已有 Starlette TestClient 弃用警告；本轮不顺带升级依赖。

记录在项目旁 `runtime-records/framework-research-20261020/string-controls-*.xml`。
失败记录与中间测试结果保留，不覆盖为成功；首轮 H2/state 分开记录，最终完整结果为
`string-controls-h2-final.xml`，最终默认金融为 `string-controls-finance-final.xml`。
二次代码审查发现旧 DSML 路径不应被规范化，先加反例复现后修复；这属于本次范围内的
接受边界修正，不改变 022 的兼容策略。最终 257 个本地文档链接目标存在，未审查 heading 锚点。

## 5. R22 原失败稿的只读回放

新脚本 `runtime-records/framework-research-20261020/string_controls_replay.py` 不访问供应商 API，
不读取文章或写旧批次，独立输出 `string-controls-r22-replay.json`。实际发现 31 个裸控制字符：
严格 JSON/公共动作可以解析，2,677 字符正文和所有解码字段与诊断对照相同，原 artifact hash 不变。

**ResearchResult 仍未通过**：13 项分析锚点没有逐字匹配；符合既有 073 的 anchor-only 补正入口。
这证明可移除首个语法阻塞，而不是整篇研究已合格。合成 fixture 另验证同一次原预算补正可继续
冻结正文、仅替换锚点；没有对原稿另行调用模型，也不推算实际省下几次调用、耗时或真实成功率。

本轮没有新付费运行、默认入口切换、真实数据库迁移、Redis/Docker 操作或 Git 上传。
072 的剩余验证/收尾设计、内容语义、缺失业务适配和真实并发仍独立未完成。
