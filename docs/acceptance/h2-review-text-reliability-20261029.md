# 核验状态与交付文字一致性审查

日期：2026-10-29。范围：针对 111/112 已实现规则的可靠性检查，未修改运行代码、规则资产或历史记录。

## 结论

当前不能将“交付文字始终与核验状态一致”判定为可靠。已确认一个真实交付矛盾，并用当前代码独立复现。
精确版本绑定和 required 放行检查继续有效；这与自由文本中的流程描述一致性是不同的检查。
37 项相关回归通过说明已定义的门控与恢复分支工作正常，不能作为文字一致性通过证据。

## 原始 Trace 证据

只读打开 r7h/r7i 的 SQLite，重新校验全部 1,141 份 Artifact 的长度和 SHA-256。
记录位于项目同级 `../runtime-records/h2-review-text-reliability-20261029/`，包含 audit.py 与 evidence.json。
未请求模型、未改写原数据库或 Artifact。

1. r7h，Run `h2-47f783a726ab457ca22aa7114b0c7aca`：
   最终 Analyst result `res_3ff24f0e5b52468c8dd2cc749ed1463f` 的 review.status=passed，
   draft_ref=`art_ec139da2e2fd4930b8c8090d7a46281c`，revision=4。
   独立核对完整结果/targets 与已通过草稿相同，报告准确绑定该版本，无阻断项、未完成检查或未处置 finding。
   但正文和 limitations[4] 均保留“revision 4 的两处修正尚待第4轮复核”。
   limitations 的这条说明也进入结果 envelope 的 warnings；并非仅有页面缓存或前端文字错误。
   Root 的最终报告主动披露该矛盾，并引用结构化 review 状态；这一次模型识别了问题，不能推断每次都会识别。
2. r7i，Run `h2-7737153bed88464e9d4345a2ae3c2024`：
   冻结 Analyst Prompt 已包含 durable limitations / review_workflow 分离要求，文本与本次检查的当前 Prompt 相同。
   但 revision 4 草稿 `art_e6e42bebd06d4b3487a54b2459c1b6ad` 的 limitations[5] 仍写入
   “上述修改尚待第4轮评审确认”。这证明新增指令没有阻止流程状态进入草稿。
   该轮最终因 quota_error 终止，无 Analyst 最终结果；不能将该草稿算作第二次矛盾交付，也不能当作通过样本。

## 当前代码的四组离线检查

使用现有 ReviewCapabilities 和实际 FinanceH2ResearchDomain 的草稿冻结、报告消费、最终提交路径。
检验报告由测试替身提供；这些检查验证 Runtime 行为，不测试真实模型是否能发现文本问题。

| 输入/操作 | 当前代码实际结果 | 说明 |
| --- | --- | --- |
| limitations 写入“本稿仍等待独立核验”，再收到有效 passed 报告 | succeeded，原句保留并进入 warnings | 没有阻断过期流程文字的检查 |
| 同一句写入正文，再收到有效 passed 报告 | succeeded，正文保留 | 只处理 warnings 无法覆盖问题 |
| limitations 写入“宏观事实未独立核实” | succeeded，限制保留 | 这与来源忠实度核验 passed 可以同时成立，不应统一删掉“未核验”表述 |
| passed 后仅删除上述过期 limitation | continuing，无 submission，重新进入 dispatch_pending | 现有完整草稿哈希检查确实覆盖 limitations；事后直接改字会破坏已通过版本绑定 |

## 原因与实际传播路径

- `prompts/finance/analyst.md` 提醒持久限制与流程状态分开；这是模型指令。
- `ResearchResult` 的 content/limitations 是自由文本；Schema 检查类型与引用锚点，没有检查流程叙述的时效性。
- `FinanceH2ResearchDomain._prepare_review` 冻结全部结果和 targets。送审时“仍待复核”可能为真，报告通过后仍保留原文就会过期。
- `_final_impl` 校验哈希、scope、verdict 和 findings，并在原样提交时保留整个 result；这些检查证明“交付的就是通过稿”，不证明稿内每句话仍然适用于交付时点。
- `_final_impl` 还把 result.limitations 合并到 Submission.warnings。父 Agent 可通过完整结果及 envelope 读取它们。
- Root 读取后生成最终报告；该次 Root 解释了矛盾。`H2Workbench._project_results` 保存 Root 原文和结果 warnings，前端据此显示正文与说明。
  这条展示路径没有一个覆盖各类核验流程文字的确定性校正器。

## 建议的最小改进边界

以下为建议，尚未实施，不改变 111 的完整结果哈希要求。

- 已有结构化 review 字段继续作为当前核验状态的唯一权威；由 Runtime 生成当前状态说明并提供给父任务、页面和导出。
- 明确区分“送审时的不可变分析快照”和“此刻核验状态”；历史草稿里的流程文字保留送审时点含义，避免把它直接展示成当前状态。
- Analyst 的送审正文和 limitations 只保留研究结论、证据缺口与持久限制；Verifier 对发现的临时流程描述要求移除后重新送审。
  这项 Prompt/语义检查可降低发生率，但不能声称覆盖所有自然语言表述。
- 不能用关键词删除“尚未核验”：来源核验通过并不代表作者陈述的宏观事实已被外部验证。
  也不能在 passed 后静默清洗已核验正文或 limitations。

若验收要求是“当前状态展示必定与 Runtime 相同”，结构化展示可提供确定性检查；
若要求“模型所有自由文本永不叙述过期状态”，当前没有此保证，仍需单独的语义质量评估。

## 验证记录

- `python -m pytest tests/test_dispatch_review_verdicts.py tests/test_dispatch_finance_review_loop.py -q`：37 passed，10.65 秒。
- 四组当前代码离线检查均复现上述预期观察；其中两个是暴露缺口的反例，并非业务通过案例。
- 两批历史绑定审计与 1,141 份 Artifact 哈希校验通过。Prompt 比较使用 Run 内原始快照。
- 本次没有新增付费调用，没有用旧 Run 结果推算新模型成功率。

原成功记录可在[工作台](http://127.0.0.1:8511/workbench?archive=b1bed7d81a70663b08b4e4ed)查看。
