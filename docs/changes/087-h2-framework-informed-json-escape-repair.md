# H2 JSON 字符串转义的有界修复

日期：2026-10-21  
状态：用户确认采用常规技术问题的 MVP 修复；先更新设计，再实现和离线验证。  
基线：`H2-CONTRACTS-2026-10-18-v1`、`H2-DISPATCH-2026-10-18-v1`

## 问题归因

H2 已经对完整模型响应执行严格 JSON 解析、动作 Schema 校验、参数校验和一次同模型补正。此前只把 JSON 字符串中的裸控制字符编码为标准转义；字符串中的非法转义（例如 `\q` 或不完整的 Unicode 转义）仍会在语法层失败。这是模型输出的常规 JSON 语法问题，不是金融语义、文章取材或调度设计问题。

## 参考实现与取舍

- [Pi Agent 固定源码](https://github.com/badlogic/pi-mono/blob/fbc456d827d7a4a951c50e7145af066226e2f8e9/packages/ai/src/utils/json-parse.ts)的 `repairJson` 处理字符串字面量中的裸控制字符和非法转义，`parseToolCallArguments` 对终态仍调用标准 JSON 解析，不以流式部分解析结果代替终态。
- DeepSeek Harness 的[参数解析](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/core/agent-loop/src/tool-calls.ts)和 [Schema 校验](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/packages/core/tools/src/schema.ts)与执行分层；非法参数不会被伪造成成功，而是保留为可反馈的结构化失败。

本项目只借鉴终态解析的边界，不引入 provider-native Function Calling，不引入局部 JSON 执行，也不把任意自然语言中的 JSON 猜成动作。

## MVP 契约

1. 仅对完整的 H2 `agent_action.v1` 公共 JSON 候选执行字符串字面量修复。
2. 字符串内的合法 JSON 转义和 Unicode 转义保持原样；裸控制字符继续按既有规则转义。
3. 字符串内的非法反斜线只把反斜线自身编码为 `\\`，以保留模型表达的字面反斜线；不补对象、数组、引号或字段。
4. 字符串外的非法语法、缺失括号、截断首对象、自然语言前缀和非法字段不修复。首对象之外的处理继续遵守 [083](083-h2-bounded-action-json-recovery.md)：无围栏时只选择首个完整对象，尾部丢弃；唯一外层围栏内部必须恰好一个完整对象，多对象或伪 transcript 仍拒绝；不能跳过非法首对象选择第二个。
5. 修复后仍必须经过完整 JSON、`AgentAction`、工具参数、权限和金融结果校验；解析成功不等于动作成功。
6. 原始模型输出、修复计数、usage、错误和 Trace 全部保留。只向公开元数据暴露有界整数计数：
   `json_string_controls` 和 `json_string_invalid_escapes`。
7. 修复不消耗模型调用，不增加预算，不切换模型；仍遵守已有一次结构/锚点补正及其一次传输重发边界，不授权语义改写。

## 验收边界

必须覆盖：合法 JSON 逐字不变、裸控制字符、合法转义、非法反斜线、非法 Unicode 转义、字符串外反斜线、缺括号、多个对象、动作 Schema 错误、原文保留和元数据脱敏。该修复只证明协议稳定性，不证明金融答案正确性。

后续 [088](088-h2-action-repair-diagnostic-boundary.md)仅修复计数与原始尾部长度的诊断边界，不改变 083 的对象选择或 087 的字符串修复范围。

