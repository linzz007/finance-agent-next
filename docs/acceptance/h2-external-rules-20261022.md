# H2 规则外置与失败上下文验收记录

日期：2026-10-22。设计：[101](../changes/101-h2-external-agent-rules.md)。

## 1. 本次完成什么

主/子指令、角色方法、来源规则、工具用途和补正指令已从 H2 Python 实现文件中外置。角色定义在 config/agents/finance.json，指令在 prompts/finance，Python 保留装配、实际 Schema、授权、预算、调度与持久化。

Root 输入新增来自同源角色定义的 research_roles，包含角色用途、输入要求和输出契约简介，不注入全部子角色长指令。既有动作、子结果格式和金融判断规则不变。

新金融 Run 将规则内容与哈希保存到不可变 Artifact，Root Brief 绑定该引用。Root、子、孙、同一 Run 的恢复和补正均使用该包。重复创建沿用原快照；改文件只影响新 Run。损坏的已声明快照不能静默替换成当前规则。

不新增数据库表、Redis 消息字段、重试额度、自动模型回退或语义输出门控。没有将所有历史复制到子任务；自动背景选择仍是独立待讨论设计。

## 2. 格式补正不是同概率原样再抽一次

首次模型调用用原任务、实际材料、工具目录、已有状态和指令。结构失败后，第二次在这些可信消息后追加纠错反馈，不修改原消息。例如研究子任务缺少正文时，反馈包含以下结构，完整 Schema 实际也会附带：

```json
{
  "input_policy": "finance.h2.isolated_correction.v1",
  "validation_stage": "final.output",
  "validation_errors": [
    {"loc": ["content"], "type": "missing", "msg": "Field required"}
  ],
  "final_output_schema_scope": "final.output"
}
```

补正指令要求修结构、使用原有真实上下文、不新增研究/来源/结论，并在同模型、原预算和截止时间内执行。语法错误另附有界、遮蔽正文值的语法定位。被拒全文保留审计，不当作新事实或整段重放给模型。

第二次成功率是“已知首次失败，且加入纠错信息后”的条件概率，不是默认等于首次成功率。两次请求共享模型、任务和上下文，错误可能高度相关；没有数据时不能把它们视为独立 70% 事件，更不能宣称总成功率为 91%。

第二次仍出普通结构错误，当前就结束该执行并保存故障/原输出，不继续无界尝试。适用的终稿锚点问题走不同分支：冻结正文，只补锚点；未修好绑定可丢弃并警告，完整分析保留。无法解析的任意文本不保证都能作为有效正文交接。

补正请求的同一传输可重发一次；重发输入相同，没有新增研究。首次请求的可重试传输故障还有独立 Attempt 恢复，不能把“原调用、一次补正、补正重发”误说成整个 Task 所有调用最多三次。上述边界本轮没有调整。

## 3. 工具失败观察的实际例子

模型错误动作：

```json
{"type":"tool_call","tool_name":"article.read","arguments":{"id":"article_alpha"}}
```

动作壳可识别，但 article.read 的参数校验不通过。保存的动作观察含 status=failed，payload 中 error.code=invalid_arguments、describe_name=article.read，以及 article_id 缺失等字段定位。此处展示关键字段，不是完整持久化对象：

```json
{
  "tool_name": "article.read",
  "status": "failed",
  "payload": {
    "tool": "article.read",
    "ok": false,
    "error": {
      "code": "invalid_arguments",
      "describe_name": "article.read",
      "issues": [{"path": ["article_id"], "type": "missing"}]
    }
  }
}
```

下一次模型输入的 action_observations 包含这次真实失败。模型选择调用 tool.describe(name=article.read)，Runtime 返回来自程序的准确参数 Schema；该描述作为新的真实成功观察继续进入后续上下文。模型再提交 article_id=article_alpha 的正确读取请求，才产生成功读文记录。

五次模拟决策实际经过持久化队列/Attempt：列文章、错参数读文、取准确契约、正确读文、最终交付。只有一次真实的 fixture 文章读取；每次决策属于不同 Attempt，规则哈希始终相同。这里没有实际供应商调用。

缺少合法 article_id 时，Runtime 不猜测失败归属于哪篇文章，因此相关文章仍为 unread，不是凭空标记 failed 或 complete。若提供有效 ID 但其他参数失败，可以关联到该文章；两种情况不能混淆。

若引用未授权，失败码为 forbidden 等对应错误；Runtime 不会因为重试而自动扩大授权。模型可选已有授权材料，或向父任务说明缺少材料。失败观察不是成功来源，也不进入有效证据。

## 4. 离线证据

记录目录：D:/AAAcode/code-code/agent+/harness/runtime-records/h2-external-rules-20261022。

| 范围 | 实际结果 | 证据 |
| --- | --- | --- |
| 规则、补正、上下文、研究角色、服务定向回归 | 346 passed，23.57 秒 | focused.xml |
| H2/状态/兼容/网页回归 | 1272 passed、7 skipped、2554 deselected，669.65 秒 | h2-regression.xml |
| 金融与共享 Runtime，52 模块 | 1639 passed、30 skipped，238.71 秒 | finance-regression.xml |
| 配置与新旧项目隔离检查 | 10/10，通过，模型调用 0 | check_finance_baseline 输出 |
| 涉及 Python 文件 Ruff | 通过 | 实际命令输出 |
| 涉及已跟踪文件 diff --check | 通过；只有 LF/CRLF 提示 | 实际命令输出 |

三组测试范围有交集，不相加为独立测试总数。当前金融计数相对历史变化不全部归因于本次修改。

迁移前固定/兼容预算下，Root 加三个研究角色共八份系统指令渲染哈希，与外置后完全一致；新增角色目录是显式的输入扩展，单独校验。测试也覆盖规则路径、重复 JSON 键、未知模板变量、规则篡改、增加角色/工具/付费模型字段被拒、快照不可变、跨 Repository 恢复、两层子任务继承，以及补正使用冻结规则而不是当前文件。

中间失败未隐藏：第一组原测试发现上下文容量辅助检查对没有 brief 的测试输入访问错误，已修复；新增用例曾把缺少 article_id 的状态误写成 failed、把规则 Artifact 损坏的收口错误码预期错，以及调用 fixture helper 时重名参数冲突。这三项属于测试预期/夹具错误，纠正后通过。最终结果与中间失败不混写。

## 5. 网页服务与真实验证边界

实际检查 8511 为 harness-v2 / fakeredis-local，只有一轮已成功的历史 Run，没有活动 Run。尝试以原 deepseek 配置安全重启时，执行环境在启动命令前拒绝了进程控制命令，原进程未被修改。

因此：**仓库实现及离线回归完成，但现有 8511 网页进程尚未加载本次代码，需正常重启服务后才对新 Run 生效。** 未另起一个连接相同数据库的旧/新混合 Worker 服务，未触碰 8510。旧进程对应的历史 Run 没有新规则快照，这是历史事实，不回写伪造。

本轮付费模型调用为 0。没有宣称真实补正成功率提高、金融语义质量通过、真实 Redis 或多人并发通过。一次补正仍是 MVP 失败收口；进一步按错误类别增加结构化局部补正或重复失败策略，需基于错误 Trace 和新设计讨论，不以盲目追加次数代替分析。

2026-10-24 的 [102](../changes/102-h2-five-action-corrections.md) 已将新 Run 的格式补正更新为最多五次；本历史记录中的“一次补正”只描述 2026-10-22 的当时行为，不回写历史结果。
