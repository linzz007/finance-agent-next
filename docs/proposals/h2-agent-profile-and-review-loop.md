# Harness 2.0 Agent Profile 与分析-核验协作方案

日期：2026-10-26

2026-10-27 更新：[104 通用子 Agent 与可组合专家配置](../changes/104-h2-general-agent-and-portable-profiles.md)
补充通用动态任务、可移植专家文件、全角色委派和默认五轮核验；涉及这些部分时，以 104 为准。

状态：**用户已批准，设计契约已冻结；实现进度以 [103 变更记录](../changes/103-h2-agent-profile-and-review-loop.md) 为准。** 本文保留方案推导；发生措辞冲突时，以 103 的可执行字段、校验和状态迁移为准。本文不改写 013 Thread/Task/Attempt 基线。

## 1. 目标

在现有 Root -> Analyst -> Verifier 两层委派能力上，建立可复用、可动态适配的文章研究流程：

1. Root 负责研究总体问题、文章范围、必要约束和最终综合。
2. Analyst 负责单篇文章研究，并根据任务策略决定是否委派 Verifier。
3. Verifier 对指定版本的分析草稿和授权原文做独立检查。
4. Analyst 处理核验意见，带着结果状态、证据、限制和未解决事项交回 Root。

静态专业规则与每次具体任务分开维护。新增任务类型时优先组合已有 Profile 与动态任务简报，不为每个问题新建一套固定 Agent 或固定流水线。

## 2. 术语与职责

| 名称 | 定义 | 例子 |
|---|---|---|
| Agent Profile | Runtime 可加载的静态执行配置，决定角色说明、规则文件、工具权限、可委派角色、I/O 契约和资源上限 | `analyst`、`verifier` |
| Prompt 规则 | Profile 引用的稳定角色方法与行为要求 | Analyst 如何保留作者的判断、逻辑、条件与不确定性 |
| 动态任务简报 | 父 Agent 针对本次工作生成的目标、范围、材料、验收要求和任务特有说明 | 分析某篇文章关于黄金的论证，辨别原文支持与研究者推断 |
| Task | 某次具体工作及其预算、权限、状态和最终结果 | 分析文章 A；核验草稿 v2 |
| Skill | 可按需加载的方法、工具用法或知识模块；它可以供 Agent 使用，但本身不是一个独立运行的 Task | 文章比较方法、行情字段说明 |

Agent Profile **不等于一段预先写死、适用于所有任务的 Prompt**。它提供角色稳定边界；动态任务简报提供本次任务的具体问题。最终模型输入由 Runtime 将可信规则、任务简报、授权材料、可用工具和当前执行状态组合而成。

Profile 的适用范围也不是无限的：同一 Analyst 可以处理文章理解、论证分析、指定范围内的比较等任务，但不能因为 Prompt 灵活就把持仓操作、数据库维护等无关职责塞给它。超出角色范围时应选择另一个已注册 Profile，或设计安全的通用 Profile；不得靠临时改写角色权限来勉强适配。Root 默认只需看到短用途目录，不必在每个问题中浏览所有角色全文。

## 3. 设计依据：Codex 与 Claude Code

本文借鉴的是“静态角色配置 + 父 Agent 动态任务消息 + Runtime 组装执行环境”的分层，不假设框架会自动推导出完美的子任务上下文。

### 3.1 Codex

本地 Codex 源码快照为 `970111f6cb62cc02fe36a82ea7d71b37a2889c97`（2026-10-23）。MultiAgentV2 的 `spawn_agent` 接收父 Agent 生成的 `message`、任务名和可选角色/模型/推理配置；Runtime 从父的有效配置构建子配置，再应用所选角色的配置。也就是说，父 Agent 动态编写本次任务消息，系统保留基础指令、角色配置和运行权限的管理边界。

上下文模式可以是新上下文或 fork 父当前模型可见的历史；它是明确的调度选项，不是对父全部磁盘历史进行语义筛选。源码：[spawn 参数](https://github.com/openai/codex/blob/970111f6cb62cc02fe36a82ea7d71b37a2889c97/codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs)、[子 Agent 配置构造](https://github.com/openai/codex/blob/970111f6cb62cc02fe36a82ea7d71b37a2889c97/codex-rs/core/src/agent/child_config.rs)、[角色配置](https://github.com/openai/codex/blob/970111f6cb62cc02fe36a82ea7d71b37a2889c97/codex-rs/core/src/agent/role.rs)。

### 3.2 Claude Code

截至 2026-10-26 的官方文档说明，自定义 Subagent 定义可以包含 `name`、`description`、`tools`、`model` 和 system prompt；父 Agent 还会生成本次委派消息。普通 Subagent 从定义和任务消息建立独立上下文。官方也提供 fork 模式，让子 Agent 继承父会话上下文；因此不能把“子 Agent 永远是空白上下文”当作 Claude Code 的统一规则。[Claude Code Subagents](https://code.claude.com/docs/en/sub-agents)

本方案采用普通的受控 Profile 加显式材料引用，不默认复制 Root 历史。需要背景就由父任务简报或授权 Artifact 引用明确传递，以便控制上下文、权限和证据来源。

### 3.3 对本项目的取舍

借鉴二者的配置与任务分层，保留本项目的领域契约和 Runtime 权限验证。模型可动态写任务说明，但不能动态授权工具、提高预算、改变结果 Schema 或改写不可变的安全规则。

## 4. Profile 静态配置

仓库已有 `config/agents/finance.json` 作为角色目录，Prompt 已外置在 `prompts/finance/`。本方案建议继续使用该目录作为角色配置入口；不要求把所有长 Prompt 塞进单个配置文件。Profile 的用途、白名单和 Prompt 引用集中登记，长方法规则保存在独立 Markdown 并由 Profile 引用。

拟纳入 Profile 的字段：

| 字段 | 用途 | 执行边界 |
|---|---|---|
| `profile_id`, `profile_version` | 标识稳定角色定义 | 每个 Run 冻结配置版本与内容哈希，恢复时沿用快照 |
| `description`, `use_when`, `avoid_when` | 供父 Agent 做紧凑能力发现 | 主上下文显示短目录，不默认注入所有角色全文 |
| `system_prompt_ref` | 指向固定角色规则 | Runtime 装配，不由父模型覆盖 |
| `allowed_tools` | 角色允许看到和调用的工具集合 | 实际权限还须与父权限、Run 授权取交集 |
| `allowed_child_profiles` | 限制该角色可以创建哪些下级角色 | Analyst 可申请 Verifier；Verifier 为 `[]`；Runtime 强制检查 |
| `input_contract`, `output_contract` | 定义注册的输入和输出 Schema | 模型只能选择已注册契约，不能临时发明程序 Schema |
| `context_policy` | 定义必需上下文、按需上下文和可展开引用策略 | Runtime 按模型窗口、任务材料和安全/输出余量确定本次可装配量 |
| `resource_ceiling` | 声明角色可用本地上限 | 不得改变固定预算策略；当前 Root Task 上限 120、每个子/孙 Task 上限 60，另受整轮共享 600 上限约束 |
| `skills` | 可选的按需方法模块 | 仅加载与本次任务有关的模块，不把所有 Skill 正文塞给每个 Agent |

Profile 提供的是能力和权限上限，不等于每次启动都必须调用其全部工具、加载全部 Skills 或执行所有检查。

### 4.1 Analyst Profile

- 用途：文章级理解、观点提炼、论证重建、跨问题的任务导向分析。
- 固定 Prompt：规定通用研究方法和来源边界，不绑定某个作者、资产或固定答案模板。
- 工具：只包含完成文章研究所需的授权工具和调度工具。
- 下级 Profile：允许 `verifier`；不允许任意创建新角色。
- 输出：版本化研究结果，可附本次核验状态与核验 Artifact 引用。

### 4.2 Verifier Profile

- 用途：核对给定草稿的原文支持、归因、逻辑条件与指定外部事实。
- 固定 Prompt：检查目标论断，指出支持、不支持、条件遗漏、矛盾或无法核实；不得为了显得独立而强行反驳。
- 工具：默认只读。外部核验工具只有在任务明确要求且 Run 已授权时才可用。
- 下级 Profile：空；不可继续派 Agent。
- 输出：结构化 ReviewReport，而不是泛泛的“通过/不通过”。

## 5. 动态任务简报

当前 `Spawn` 已有 `role/role_version`、`objective`、`success_criteria`、`input_refs`、`parent_context_refs` 和 `output_contract`。本方案保留这些边界，建议增加有界的 `task_instructions` 与核验策略字段。下面是概念示例，不是现有 Schema：

```json
{
  "role": "analyst",
  "role_version": "2.x",
  "objective": "分析指定文章关于黄金的核心观点和论证链，并指出重要缺口。",
  "task_instructions": "保留作者的条件与不确定性；区分作者原意和分析者推断。",
  "success_criteria": [
    "主要结论能回指授权原文。",
    "呈现支撑结论的推理步骤、条件和缺失证据。"
  ],
  "input_refs": ["authorized_article_ref"],
  "review": {
    "policy": "auto",
    "scope": ["source_fidelity", "reasoning_conditions"],
    "max_rounds": 3
  },
  "output_contract": {"name": "finance.research_result", "version": "2"}
}
```

字段职责：

- `objective`：要解决的研究问题，不写一整套通用角色规则。
- `task_instructions`：本次额外关注点、范围或表达要求；视作任务数据，不获得修改工具权限或 System 规则的能力。
- `success_criteria`：可观察的完成条件；分析深度由模型判断，Runtime 校验结构和证据边界。
- `input_refs`：明确授权的文章、数据和背景材料。Task Receipt 或历史摘要不能冒充原始证据。
- `review.policy`：`required | auto | off`。Root 可对用户明确要求的核验设为 `required`；`auto` 时 Analyst 在授权范围内决定是否委派；`off` 只用于用户要求或任务确实不需要独立核验的场景，并说明原因。`required` 不能被 Analyst 降级为 `off`；`off` 不能被 Analyst 擅自升级并启动额外付费任务。
- `review.scope`：Root 指定允许的检查范围，如 `source_fidelity`、`reasoning_conditions`、`external_facts`。Analyst 可在该范围内聚焦，不能扩大来源权限；外部事实核验还必须有独立数据源授权。
- `review.max_rounds`：Root 设定本 Task 的轮数上限（1–5）；Analyst 可以少用，不能增加；Runtime 另强制硬上限 5。
- `output_contract`：选择注册版本；不能由模型定义新 Schema。

父模型可以为主任务动态生成任务简报；在嵌套委派时，Analyst 可以动态生成 Verifier 的目标、检查范围和成功条件。Runtime 负责把这些字段放入正确的子上下文，并按 Profile 限制权限。

## 6. 逐篇分析与核验流程

```text
Root
  -> 为每篇目标文章创建一个 Analyst Task
  -> 提供文章授权引用、总体问题、验收条件和 review policy

Analyst
  -> 阅读文章并形成 draft v1
  -> required: 必须创建核验任务
     auto: 按当前任务风险决定是否创建，并记录理由
     off: 不创建，标明未独立核验
  -> 如需核验，保存不可变 draft Artifact
  -> 创建 Verifier Task，给它文章原文、该版 draft、核验范围和验收条件
  -> 等待并读取 ReviewReport
  -> 逐项采纳、说明拒绝理由或披露无法处理
  -> 必要时产生新版本，再请求下一轮核验
  -> 返回最终分析、证据和核验状态

Root
  -> 等待 Analyst 最终结果
  -> 对作者/文章间关系做综合并完成总体答案审核
```

Root 不需要生成内部 Verifier 的逐条提示词；它只给文章研究目标和高层核验策略。Analyst 依据 Profile 与这次任务简报，动态组织具体核验任务。这样保留专业规则的充分性，也让任务焦点随问题变化。

## 7. 共享上下文与版本边界

“共享同一份信息”定义为双方读取同一组经过授权、带版本的 Artifact，而不是把两个 Agent 的完整对话合并成一个不断增长的上下文。

| 材料 | Analyst | Verifier | 说明 |
|---|---|---|---|
| 原始文章/数据 | 读取授权材料 | 读取同一授权版本 | 两边各自建立阅读上下文；真实来源由 Runtime 绑定 |
| Root 任务目标/用户约束 | 必须接收 | 通过 ReviewRequest 中必要部分接收 | 避免 Verifier 不知道本次要回答的问题 |
| Analyst 草稿 | 自己产生和修订 | 读取指定版本 | 必须保存为有版本的只读 Artifact |
| 既有核验意见 | 在后续轮次读取 | 只提供完成本轮所需的旧意见 | 不自动复制全部工具历史 |
| 工具和模型轨迹 | 保留在各自 Task | 保留在各自 Task | 不把整段轨迹送给 Root 或对方 |

每份 ReviewReport 必须声明 `reviewed_draft_ref` 和 `reviewed_revision`。核验 v1 的意见不能自动代表 v2 已通过。Artifact 保持不可变；新草稿生成新 revision 并引用前一版。

材料引用沿用当前授权模型：Analyst 通过 `agent.spawn` 只能把自己有权访问的原文和草稿引用传给 Verifier；Runtime 校验后才形成子任务输入。Verifier 的意见、文章引用或模型文本都不能自行创造新的来源权限。

## 8. 核验交接契约

以下是待新增的概念 Schema，须在实现前转为严格、带版本的代码契约。

### 8.1 Analyst 草稿中的可核验目标

保留自由格式 `content`，另提供少量可定位的关键论断：

```json
{
  "draft_ref": "Runtime-generated-artifact-ref",
  "revision": 1,
  "content": "作者认为，若实际利率下降，黄金可能受益。原文并未说明该条件已经发生。",
  "review_targets": [
    {
      "target_id": "claim-1",
      "kind": "author_view",
      "statement": "作者把实际利率下降作为黄金受益的条件之一。",
      "evidence_refs": ["authorized_article_ref"],
      "conditions": ["实际利率下降"]
    }
  ]
}
```

`review_targets` 是核验索引，不是强制把正文切成固定章节，也不是对正文所有句子的机械逐句审查。Verifier 可以指出未被索引但重要的归因或条件问题。

### 8.2 Verifier ReviewReport

每项发现至少包含：

| 字段 | 说明 |
|---|---|
| `finding_id` | 本报告内稳定、唯一的发现标识 |
| `target_id` | 对应草稿目标；无法归入目标时允许为空并提供定位锚点 |
| `category` | `unsupported`、`misattribution`、`condition_loss`、`contradiction`、`reasoning_gap`、`external_fact_unverified` 等已注册类型 |
| `severity` | `material` 或 `minor`；不使用无定义的笼统评分 |
| `assessment` | `supported`、`partly_supported`、`unsupported`、`contradicted`、`inconclusive` |
| `explanation` | 说明为什么构成问题，或为什么未发现该类问题 |
| `evidence_refs` / `quotes` | 仅引用实际读取且授权的来源；quote 必须可在来源原文中定位 |
| `suggested_change` | 可选修订方向，不能要求无依据地加强结论 |
| `unchecked_scope` / `limitations` | 明确哪些内容未检查及原因 |

整份报告另带 `schema_version`、`reviewed_draft_ref`、`reviewed_revision` 和 `checked_scope`。Verdict 可以表示证据评估，但不向用户宣称模型已证明金融事实。

### 8.3 Analyst 对意见的逐项处置

最终研究结果增加 `review` 元数据，至少记录策略、实际轮数、状态、报告引用，以及每条重要 `finding_id` 的处置：

- `accepted`：采纳并指明新草稿中的对应观点或修改。
- `rejected_with_reason`：认为意见不成立并解释依据；不能静默丢弃。
- `unresolved`：材料或预算不足，披露限制。

结果状态用来描述流程，而不是给答案贴“真/假”标签。建议枚举：`not_requested`、`skipped_with_reason`、`reviewed_with_resolutions`、`issues_open`、`review_failed`、`round_limit_reached`。`reviewed_with_resolutions` 仅表示意见均有处置，不意味着 Verifier 与 Analyst 的观点必然正确。

## 9. 核验轮数与停止规则

建议默认最多 3 轮，单任务可申请 1–5 轮，Runtime 的硬上限为 5。每轮定义为一次针对具体 draft revision 的 Verifier Task；Verifier 内的动作调用、格式修复和底层传输重发不增加核验轮次，但都计入原 Task/Run 的模型调用和时间预算。

一次 Verifier Task 被创建即消耗一轮，失败也计数，避免用失败重派绕过上限。达到上限后：

1. Analyst 可以读取最后一份报告，并在原预算剩余范围内完成一次结果整理。
2. 不再创建第六个 Verifier Task。
3. 返回完整可用分析，并将状态标为 `round_limit_reached`；仍未解决的发现逐项列出。
4. `required` 任务即使达到上限仍未完成核验，也只能以核验不完整/partial 交付，不能声称已通过。

核验轮数、模型动作 JSON 修复次数、Attempt 传输恢复次数是三种不同计数，不可相互替代或共享。当前固定预算基线是 Root Task 最多 120 次、每个子/孙 Task 各自最多 60 次、整轮所有 Task 共用 600 次及 60 分钟截止；子任务的物理请求扣自己的 Task 额度和 Run 共享额度，不扣父 Task 的个人调用上限，也不为每个核验轮次预占整份 60 次额度。资源仍按实际物理请求记账。详见[独立 Task 调用上限](../changes/067-h2-independent-call-limits.md)。预算耗尽、截止时间到达、核验工具不可用、Verifier 输出结构无效时，按真实原因保留已有正文和记录，并标记不完整；不自动切换模型、不扩大预算、不把缺失报告算作通过。

## 10. 工具白名单与安全约束

提示词告诉模型“应该如何做”；Runtime 决定模型“实际上能做什么”。

- Analyst 的工具为其文章研究与显式允许的调度能力。它只能委派 Profile 中 `allowed_child_profiles` 声明的角色。
- Verifier 默认只读原文、草稿和指定证据，不得创建子 Agent、修改 Analyst 草稿或直接发布 Root 答案。
- 外部查证工具按 Run 实际配置与授权提供。仅仅在 Profile 中列出 web/market 能力，不代表本次 Run 已授权或工具可用。
- 任何子任务权限均受父级和 Run 授权的交集限制；Prompt、动态任务简报、Skill 不能提高权限和预算。
- Runtime 强校验角色注册、深度、工具白名单、材料引用、契约 Schema、版本对应、预算和核验轮数。

## 11. 返回 Root 的内容与上下文隔离

Analyst 返回 Root 的直接结果包含：自由格式文章分析、来源 EvidenceRefs、主要观点与条件、材料处理、核验策略、核验状态、重要意见处置、未解决问题及可按需展开的完整结果/报告 Artifact 引用。

Root 默认不接收 Analyst 与 Verifier 的完整对话和每次工具观察，只收到 Analyst 最终结果及必要状态摘要。必要时可按授权展开完整 ReviewReport。当前 H2 自动等待和后代查询的访问边界尚未专门设计成“Root 不可读取任何孙任务详情”；如要求强隔离，应在实现阶段单独收紧 descendant result/status 可见性，而不能只靠 Prompt 不提及。

Root 仍负责跨文章综合、处理不同文章间的关系和最终答复审核。文章级 Verifier 不替代 Root 的总体覆盖判断，也不代替全局答案的核验目标选择。

## 12. 与已确认基线的关系

本方案沿用已确认的 Thread/Task/Attempt、异步派发/等待/恢复、SQLite 权威状态、Artifact 引用和两层委派上限，不替换 [013 调度基线](../changes/013-harness-v2-dispatch-baseline.md)。

本方案新增“Analyst 可在 `auto` 下决定内部核验委派”的业务规则。现有 [架构文档第 6 节](../architecture.md#6-验证目标结果与修订)及仓库开发不变量把总体审核目标选择归于 Root。后续需在文档中区分：Root 负责用户层/最终答案的核验目标与强制要求；Analyst 负责文章级内部核验的具体执行及 `auto` 决策。该权限划分经用户确认后再更新现行架构/接口契约；本文本身不覆盖冻结基线。

## 13. 验收计划

### 13.1 离线工程验收

- Profile 正确加载、同一 Run 配置快照固定，恢复不中途更换 Prompt/白名单。
- 角色白名单生效：Analyst 可派 Verifier；Verifier 的 spawn 在 Runtime 被拒绝。
- 动态任务指令可以改变目标和重点，但不能修改系统规则、工具权限、预算、Schema 或父级限制。
- `required` 无真实 Verifier 结果不得报告核验完成；`auto` 跳过必须有理由；`off` 不误启动 Verifier。
- Verifier 输入绑定精确文章版本、草稿版本、范围和授权证据；不得将旧版 ReviewReport 当成新版核验。
- 引文、EvidenceRefs、目标 ID、发现 ID、意见处置和结果引用执行严格结构/权限校验。
- 单任务 1–5 轮边界、失败轮计数、达到上限后的完整 partial 交付、预算与截止时间边界均覆盖。
- 主 Agent 等待恢复、重复事件、部分结果、Artifact 缺失、模型/工具失败和服务重启可恢复且 Trace 可审计。
- Root 默认上下文不重复注入全部 Verifier Transcript；访问控制测试明确后代结果可见范围。

### 13.2 真实模型质量验收

冻结同一批单篇文章、问题、授权材料、模型与预算，对比：

1. 现行 Root -> Analyst 路径。
2. Analyst -> Verifier -> Analyst 路径，覆盖 `required` 和 `auto` 的代表性案例。

分别评估作者归因、条件保留、证据支持、无依据推断、误报/过度质疑、未解决问题披露、Root 输入长度、总模型调用、费用和完成时间。结构通过、调用成功或一次答案好看都不能独立证明研究质量提高；保留失败和 partial 样本，不只挑成功题。

## 14. 与当前实现的差异

当前已存在：Prompt/角色文件化、Profile 简要目录、Spawn 的目标/验收/引用/结果契约、异步两层委派、Agent 工具目录、金融 ResearchResult 结构与证据引用校验。

本方案仍未实现：角色级工具白名单及 allowed-child-role 强校验、动态 `task_instructions`/`review_policy` Schema、专用 ReviewReport 契约、草稿版本到核验报告的版本绑定、Review finding 逐项处置状态、五轮核验专属计数及 Root 的后代结果隔离边界。

当前 analyst/verifier Prompt 简短，不能宣称已经实现了本文的专业分析和核验闭环。不得通过修改 Prompt 文本把尚无 Runtime/契约保证的状态描述成已保证。

## 15. 待用户确认

建议的待确认默认值：

1. 采用静态 Agent Profile + 动态任务简报；动态部分写本次工作，不覆盖 Profile System Prompt 或程序权限。
2. 核验策略为 `required | auto | off`，默认 `auto`；显式用户核验要求优先为 `required`。
3. 每篇文章的 Analyst 可派 Verifier；Verifier 不得继续派 Agent。
4. 默认核验最多 3 轮，Hard cap 5；到上限仍交付可用正文，但标记 partial/未完成核验并披露遗留问题。
5. 分析与核验共享授权材料和版本化草稿 Artifact，不共享完整私有对话历史。
6. 文章级内部核验和 Root 最终答案审核分开定义，避免责任冲突。

确认以上方案后，先更新现行架构与接口契约并做一致性审查，再拆解实现和离线测试；在文档确认前不修改代码或调用付费模型。
