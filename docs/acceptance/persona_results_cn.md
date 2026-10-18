# 双画像功能验收结果

批次：`20260910_delivery`；模型：`deepseek-v4-flash`。Wiki 和聊天全部通过公开 HTTP API 录入。

## 口径

结构通过不等于业务语义通过；部分完成、安全拒绝、无数据诊断不充作有用报告成功。没有等条件多次测量，不宣称性能提升比例。

逐例最新证据合并：persona_20260910_release（32条）、persona_20260910_followup（1条）、persona_20260910_semanticfix（6条）。不是同一版本的一次完整运行；原始批次全部保留。

- 实际完成：32/32；结构及硬约束通过：31/32。
- 端到端中位耗时：7.232 秒。
- 本表所选证据的 Trace 记录 input tokens：185051；output tokens：21437。不是本次所有修复复跑的总用量，也不等同账单；重试、缓存和金额以供应方记录为准。
- 独立语义评审：{'通过': 28, '部分通过': 4}。

| Case | 硬约束 | 语义结论 | 秒 | 真实回答与证据 |
| --- | --- | --- | ---: | --- |
| H01 | 通过 | 通过 | 1.741 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H01/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H01/trace.json>) |
| H02 | 通过 | 通过 | 4.449 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H02/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H02/trace.json>) |
| H03 | 通过 | 通过 | 37.039 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H03/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H03/trace.json>) |
| P01 | 通过 | 通过 | 30.066 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P01/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P01/trace.json>) |
| H04 | 通过 | 通过 | 7.446 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H04/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H04/trace.json>) |
| H05 | 通过 | 通过 | 9.283 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H05/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H05/trace.json>) |
| H06 | 通过 | 通过 | 7.189 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H06/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H06/trace.json>) |
| H07 | 通过 | 通过 | 18.633 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H07/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H07/trace.json>) |
| H08 | 通过 | 通过 | 7.823 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H08/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H08/trace.json>) |
| H09 | 通过 | 部分通过 | 19.311 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_semanticfix/cases/H09/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_semanticfix/cases/H09/trace.json>) |
| H10 | 通过 | 通过 | 3.167 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H10/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H10/trace.json>) |
| H11 | 通过 | 通过 | 1.888 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H11/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H11/trace.json>) |
| H12 | 通过 | 通过 | 2.831 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H12/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/H12/trace.json>) |
| P02 | 通过 | 通过 | 5.77 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P02/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P02/trace.json>) |
| P03 | 通过 | 通过 | 20.66 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P03/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P03/trace.json>) |
| P04 | 未通过 | 部分通过 | 19.454 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_semanticfix/cases/P04/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_semanticfix/cases/P04/trace.json>) |
| P05 | 通过 | 通过 | 7.593 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_semanticfix/cases/P05/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_semanticfix/cases/P05/trace.json>) |
| P06 | 通过 | 通过 | 7.276 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P06/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P06/trace.json>) |
| P07 | 通过 | 部分通过 | 8.773 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_semanticfix/cases/P07/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_semanticfix/cases/P07/trace.json>) |
| P08 | 通过 | 通过 | 10.551 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P08/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P08/trace.json>) |
| P09 | 通过 | 通过 | 6.788 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P09/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P09/trace.json>) |
| P10 | 通过 | 通过 | 15.629 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P10/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P10/trace.json>) |
| P11 | 通过 | 通过 | 1.875 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P11/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P11/trace.json>) |
| P12 | 通过 | 通过 | 2.398 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P12/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P12/trace.json>) |
| P13 | 通过 | 通过 | 1.778 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P13/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P13/trace.json>) |
| P14 | 通过 | 通过 | 3.319 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P14/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P14/trace.json>) |
| P15 | 通过 | 通过 | 4.981 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_semanticfix/cases/P15/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_semanticfix/cases/P15/trace.json>) |
| P16 | 通过 | 部分通过 | 13.133 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_semanticfix/cases/P16/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_semanticfix/cases/P16/trace.json>) |
| P17 | 通过 | 通过 | 0.868 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P17/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P17/trace.json>) |
| P18 | 通过 | 通过 | 2.413 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P18/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P18/trace.json>) |
| P19 | 通过 | 通过 | 33.476 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_followup/cases/P19/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_followup/cases/P19/trace.json>) |
| P20 | 通过 | 通过 | 2.476 | [回答](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P20/answer.md>) / [Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/data/acceptance/persona_20260910_release/cases/P20/trace.json>) |

## 尚未完全通过的用例

- **H09 部分通过**：已核对本轮349014a4678845a78884ad38e288a3d6的实际answer/report/trace。原文关于知识问答等服务且不涉及持续情感互动的排除条件，在结构化适用性、影响评估、Markdown和HTML中均保留；企业是否满足排除条件明确待核实，未将测试画像推定为生产事实或直接不适用。skeptic模型前实际预读tool_da14bf0aaa354ea4b71d4efe4001d773，条文进入模型输入且校验和、audit ref对应，该修复点通过。工信部仍仅有610字通知页、缺少办法附件全文，答复和报告已披露部分完成；整体仍为部分通过，不计完整政策报告通过。
- **P04 部分通过**：3项均为窗口内真实arXiv论文，保存原文与Wiki引用可对应，训练复现前置和摘要核验边界明确；但ReCite在引用校验及一次修复后仍无有效中文分析，实际回答为兜底摘要与通用影响句。其余2项有中文分析，非整份纯兜底；useful_report_not_only_fallback硬检查false保留，不因诚实披露判全通过。
- **P07 部分通过**：analyst确实执行，Trace优先的判断符合Wiki两小时及不重写约束；迁移框架能力收益已改为可能且待验证，不再承诺一步到位。但主回答仍无具体框架、代码与估算依据就断言Trace成本低风险小、迁移成本高风险大，取舍的确定性仍偏强。
- **P16 部分通过**：候选eval=true与最终eval=false已分开，3项实际均为Trace且有中文分析，明确披露Eval有候选但排序后未入选；更新Wiki与memory排除生效。仍将运行超1GB输出工具的方案称为只读小实验，未符合个人轻量只读偏好；“当前来源未覆盖eval”也不如后文最终未入选准确。属于建议适配与表述不足，非覆盖字段修复失败或实际越权执行。

## 耗时分类

以下均为该用例一次执行，不是压测SLO，也不是多次采样p95。源缓存、模型重试和输入不同，不可直接据此声称优化比例。

| 类型 | 数量 | 中位秒 | 最小-最大秒 |
| --- | ---: | ---: | --- |
| 普通对话/追问/记忆 | 16 | 2.654 | 0.868-7.593 |
| 报告/资料不足诊断 | 9 | 19.454 | 7.823-37.039 |
| 显式子任务 | 7 | 8.773 | 6.788-15.629 |

## 本地复现

```powershell
py -3 scripts/run_persona_acceptance.py --label 20260910_delivery_reproduce
```

这会创建新的隔离测试空间，不覆盖本轮证据。日志仅保存业务请求，模型密钥不写入报告。

相关文档：
- [验收设计方案](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_functional_test_plan_cn.md>)
- [32条测试集](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_query_set_cn.md>)
- [完整问答](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_answers_cn.md>)
- [逐轮Trace](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_trace_report_cn.md>)
- [修复与已知边界](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_changes_and_limits_cn.md>)
