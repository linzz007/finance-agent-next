# 核验放行与调度约束定向复验

日期：2026-10-28。对应设计：[111](../changes/111-review-verdict-and-explicit-dispatch-policy.md)、
[112](../changes/112-h2-required-review-time-window.md)。

## 结论

两项核心机制已实现并有真实成功样本：required 必须取得绑定最终完整草稿的 passed 报告，
off 必须逐次显式传递且不能被后续派发覆盖。不能把这一事实扩大为“所有运行稳定成功”。
最新 SCH-02 因模型额度错误中断，没有交付；此前成功样本仍有过期流程说明的文字瑕疵。
本报告保留所有结果，不以 Root 的 succeeded 代替专业核验通过。

## 已实现的约束

- 新 v5 Run 的每次 Analyst 2.0 派发必须填写 profile_options.review_mode。
  可选的可信 Run dispatch_constraints 在创建时冻结，并向子/孙任务传递；冲突或遗漏在创建前拒绝。
  工作台可选择“必须通过核验”或“关闭核验”。自然语言仍由模型理解，Runtime 不靠关键词推断用户意图。
- Verifier 的 ReviewReport v2 必填 verdict、verdict_reason、blocking_finding_ids。
  报告格式合法与观点核验通过分开记录；重大不利 finding、未完成的本次检查不能标记 passed。
- required 的最终成功出口同时要求：有效 passed 报告、准确草稿版本、完整结果与 review_targets
  的哈希一致、累计发现项处置完整且无 unresolved。修改结果后重新核验；最多五轮，触顶 partial。
- 通过记录最多保留五条。A→B→A 只在完整内容和范围相同的情况下复用原报告，仍展示原报告绑定版本。
  同一哈希/范围收到新的有效不利报告时撤销旧通过记录。
- 可纠正的领域引用/编号合同错误保存完整候选与有界失败观察，在原 Task 中最多五次补正。
  领域补正、动作格式补正、核验轮数分别计数，全部物理调用仍受原预算控制。
- required Analyst 的默认窗口为 45 分钟；普通子任务默认 20 分钟，默认分配低于十分钟时明确拒绝。
  不再把一个完整核验任务静默压缩到只有几秒。显式较短时间仍按原合同处理。
- 上述新语义随 Run 冻结；历史 v1-v4 快照继续原合同。原历史结果没有改写。

## 实测记录

所有批次采用 DeepSeek、SQLite 和显式标注的 FakeRedis；没有自动切换模型。
每题 Root 自身 120 次、每个子/孙自身 60 次、整轮 600 次、60 分钟，包含补正和传输请求。
沿用冻结的 2026-10-19 材料快照及研究时钟；这次验证不覆盖实时宏观事实正确性。

记录位于项目同级 `../runtime-records/`。以下每个实际批次运行期间的源码和输入快照均未变化。
不同批次之间进行了修复，不能把这些试验汇成单一版本的成功率。

| 批次 | 用例 | 物理请求 | 结果与实际发现 |
| --- | --- | ---: | --- |
| r6 | SCH-02 | 60 | Root 有交付，Analyst 失败；第二位 Verifier 分到不足的剩余时间后超时，严格核验不通过 |
| r6 | SCH-03 | 24 | 两个 Analyst off 场景完成；后续 r7e 为最终复核样本 |
| r7b | SCH-02 | 108 | Analyst partial，未通过审查；Root 给出有缺口结果，不算 required 成功 |
| r7d | SCH-03 | 0 | 启动阶段未完成，无模型调用；保留原目录 |
| r7e | SCH-03 | 23 | 两个同级 Analyst 均成功、off/0 轮、Root 完整读取后综合，有真实请求重叠 |
| r7f | SCH-02 | 109 | Verifier 有 passed 报告，但 Analyst 未成功提交；暴露通过稿复用/停止条件问题，严格场景不通过 |
| r7h | SCH-02 | 82 | 核验四轮，Analyst 最终 succeeded，最终 revision 4 精确匹配 passed 报告，Root 完整读取并交付 |
| r7i | SCH-02 | 95 | 前三轮均 revision_required 并继续修订；第四轮运行中 Root 收到 quota_error，整轮失败，无交付 |

r6 至 r7i 已记录的物理请求共 501 次，不等同于 501 次计费成功：未知响应仍可能计费，未擅自估算费用。
r7、r7c、r7g 等启动目录也保留；r7g 的快照路径错误发生于模型调用前。

## 成功样本的独立 Trace 核对

### SCH-03：明确 off 的两文章并行

记录：`h2-sched-r7e-20261028/cases/SCH-03`，Run `h2-54c15e73aba7459d94dca0518b9e7c93`。
耗时 239.46 秒，23 次成功响应，三个 Task 全部 succeeded，无失败响应。
Root 的两个 Analyst 子任务各自收到 off，均为 not_requested、0 轮；没有 Verifier。
两个子任务分别完成后，Root 以 Agent 身份完整读取两份结果，再交付对比。
原 trace-facts.json 记录了两对实际重叠的同级模型请求。

该题结构化 off 约束也由题库传给入口。它验证可信字段的完整传导，不能单凭此题宣称
任意自然语言要求都能被模型百分之百翻译正确。

### SCH-02：最终草稿必须通过

记录：`h2-sched-r7h-20261028/cases/SCH-02`，Run `h2-47f783a726ab457ca22aa7114b0c7aca`。
耗时 1,906.45 秒，82 次请求（79 次成功响应、3 次结果未知），Task 共六个。
Root 自身 41 次、Analyst 29 次、四个 Verifier 分别 3/4/4/1 次，均未超限。

实际路径为 Root→Analyst→Verifier。四轮中第 2 轮 Verifier 因 transport_error 失败，
没有报告，也未被当作通过；后续核验继续。第 1/3 轮有非阻塞建议，Analyst 采纳并更改后再次核验。
最终第 4 轮报告 passed，10 个目标完成检查、blocking_finding_ids 为空。

只读 SQLite 与原 Artifact 独立核对结果：

- Analyst result_ref：res_3ff24f0e5b52468c8dd2cc749ed1463f，status=succeeded。
- 最终 draft_ref：art_ec139da2e2fd4930b8c8090d7a46281c，revision=4。
- 最终 report_ref：res_d93c7a604d2a4148af1b2d15f307239d，准确绑定上述引用和版本。
- 完整 result 与 review_targets 的规范化 SHA-256 均为
  `0930b510a3cfd3649b2d3c0b0aa8c2aa64e5194d5c3e74c8511d762675cc1481`。
- 累计三个 finding 均有且仅有一个处置，无 unresolved；Root 实际全文读取了 Analyst 的 21,769 字符结果。
- reused_approval=false：本次没有触发 A→B→A 历史通过缓存复用，该分支依赖离线回归证据。

all_children_succeeded=false 保留了第 2 轮失败的 Verifier。它没有结果可读，不能把它算作
“父任务漏读了一份可用报告”；其余三个 Verifier 报告和最终 Analyst 结果均有父 Agent 的全文读取事件。

## 最新失败样本与未解决边界

r7i Run：`h2-7737153bed88464e9d4345a2ae3c2024`。耗时 2,273.58 秒，95 次物理请求：
87 次成功、7 次结果未知、1 次失败。最后一次为 Root 的 quota_error；未耗尽 600 次/60 分钟。
Root 失败后，未完成的 Analyst 与第四位 Verifier 被取消；后台付费测试已经结束。

前三轮完整报告均为 revision_required，分别指出引文绑定错位及条件语气过强。
Analyst 完整读取每份报告后继续修订，没有在这些未通过版本上成功放行。
这证明阻断和继续分支实际触发，但不能据此认定第四版已经通过或整题完成。

另有两项可见限制：

1. r7h 的模型结果 limitations/warnings 仍含草稿期“待第 4 轮复核”表述，与 Runtime 的 passed 状态矛盾。
   Root 在正文明确披露这一矛盾；该成功样本的“精确版本绑定”成立，文字一致性不算完美。
   因为连 limitations 都属于被核验完整结果，不能在 passed 后静默改字。
   r7i 前已增加“持久限制与审核流程状态分开”Prompt 说明，但 revision 4 仍出现过期措辞；
   它只是一条模型指令，尚未可靠解决此问题。未清洗历史记录，也未把短 Prompt 的断言测试当作行为证明。
2. 长核验期间父任务多次 wait 超时后重新调用模型，成本和时延仍偏高。r7h Root 的 41 次调用
   中包含等待恢复，不能把全部 82 次解释成必要分析。暂未新增更复杂的自动等待策略。

真实 Redis、跨模型稳定性、多用户部署、重复运行成功率，以及独立外部事实核验均不在本次通过范围。
semantic_acceptance=manual_review_required 是测试脚本固定保留的语义人工复核标记，不代表自动语义验收成功。

## 离线与审计证据

最终相关回归覆盖放行/拒绝、改稿重审、A→B→A、旧通过撤销、五次领域补正、off 约束逐层冻结、
旧快照、通用 Agent、Worker 恢复、工作台 API 与 Trace。
完整测试结果及 JUnit 记录在 `../runtime-records/h2-review-policy-audit-20261028/regression.*`。
本次最终离线回归为 450 passed；H2 工作台 Trace UI 测试为 9 passed。静态检查为本次相关 Python 文件 Ruff，
另执行 git diff --check。离线测试有一条 Starlette/httpx 弃用警告，不影响通过结果。

独立审计脚本 `../runtime-records/h2-review-policy-audit-20261028/verify.py` 只读原始数据库和文件，
没有请求模型。evidence.json 保存三个批次的逐 Task 请求数、审核绑定和发现项处置核对结果。
共校验 1,315 份 Artifact 的字节长度和 SHA-256：r7e 174、r7h 560、r7i 581；
对应 Trace 事件分别 306、1,088、1,158。

## 工作台查看

- [SCH-03 off 并行成功记录](http://127.0.0.1:8511/workbench?archive=88bf8af9ebe4931f724e37f3)
- [SCH-02 required 完成记录](http://127.0.0.1:8511/workbench?archive=b1bed7d81a70663b08b4e4ed)
- [SCH-02 最新额度中断记录](http://127.0.0.1:8511/workbench?archive=7b5e42dc5506756528923e3c)

已核对归档 API 的只读状态、Task 数、调用统计和分页事件，以及浏览器中已保存的成功报告。
打开这些记录读取原数据库与文件，不会产生新的模型请求。
