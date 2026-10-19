# 022 模型文本动作兼容层

日期：2026-10-19  
状态：已实现，兼容现行 Harness 1.0/2.0 action 契约  

## 背景

DeepSeek 通过 OpenAI-compatible Chat Completions 返回过文本化 DSML 工具动作。此前 Runtime 只接受一个 JSON action，因此即使模型已经选择了正确工具，仍会在 JSON 解析阶段连续重试并最终以 `model_error/source_only` 收尾。

## 设计边界

- 内部唯一执行协议仍是 `type=tool_call`、`tool_name` 和对象型 `arguments`。
- 不启用供应方原生 function calling，也不把 DSML 暴露给工具、权限、Trace 或业务层。
- 只在模型网关边界识别一个完整的 DSML `calls -> invoke(name=tool_call) -> parameter` 结构。
- 只接受一个工具调用、唯一参数名和合法 JSON 参数；任意额外文本、多个调用、未知 invoke 或非法参数都继续按格式错误处理。
- 归一化后必须继续经过现有 action schema、权限策略、工具参数和执行预算校验。
- 模型失败不自动切换供应方或模型；兼容层只减少协议格式失败，不改变模型选择策略。

## 实现位置

- `src/policy_impact/harness/model_gateway.py`
  - 普通 `complete()` 和研究预算路径统一使用严格 action 解析器。
  - `_parse_dsml_tool_action()` 将 DeepSeek 文本动作转换为内部 JSON action。
- `tests/test_model_gateway.py`
  - 覆盖全角竖线 DSML、工具名和 JSON arguments 的归一化。

## 复验

- 模型网关、Agent Loop、H2 执行回归：`203 passed`。
- Q04 在同一冻结文章快照、DeepSeek 配置下从之前的 `model_error/source_only` 进入真实研究、验证和交付，耗时 469.25 秒，生成 5948 字，`delivery_mode=qualified`。
- Q04 当前仍是 `verification_status=disputed`、`evidence_coverage=partial`、`answer_audit_coverage=partial`。这表示证据与审核存在真实缺口，不表示 DSML 兼容层失败。
- 同批次 Q05 尚未启动：Q04 完成后供应方返回 HTTP 401，批次按“不自动换模型”的策略停止。该鉴权阻断不能计入 Q05 业务失败或成功。

## 后续边界

DSML 兼容只解决动作格式进入 Runtime 的问题，不保证模型会选择正确文章、审核目标或外部证据，也不改变证据覆盖和金融语义验收标准。Q05 需要在供应方鉴权恢复后用新批次单独复验。
