# 从这里开始验收

> 2026-09-10范围修订：旧H03使用“同花顺AI研发测试画像”，关注点仍是Agent技术；它不能证明同花顺业务与Agent自进化两个领域已区分。旧P10主要证明显式多角度委派，不能作为主模型自主委派的证明。接下来先核定Wiki与数据源，见[双Wiki与报告任务约定](D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_wiki_sources_report_contract_cn.md)。下列记录保留为原测试范围内的证据。

页面：http://127.0.0.1:8501/

这次交付的是32种真实中文用户任务、实际运行证据和修复，不是只列一份理想测试计划。结果中的部分通过仍需你评议，不当作满分。

## 先看三个演示

在左侧下方展开“工作空间”，选择下面的空间ID，再搜索对应会话名。不要选同名的baseline/round2旧空间。

### H03 企业默认报告

- 空间：`accept_20260910_release_ths`。
- 会话名：`验收 ths report`；run_id：`862763a412834a638b49fae6a0a1818f`。
- 用户问题：/report 生成一份报告。
- 评审：通过；从Wiki取得默认7天最多5条，实际窗口09-03至09-10；5项中文分析的连续来源引文和Wiki引用均对应。1GB截断、128K输出上限及缓存观测形成具体只读验证建议；明确采用和收益未知、Eval/检索覆盖不足，不等于全主题完整报告。
- [打开实际HTML报告](http://127.0.0.1:8501/companies/accept_20260910_release_ths/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Caccept_20260910_release_ths%5Creports%5C2026-09-10-recent-news-impact-e5fd61ef.html)

### P01 个人默认研判

- 空间：`accept_20260910_release_personal`。
- 会话名：`验收 personal report`；run_id：`b48d05924d1d496f98c6dd21c2d234ba`。
- 用户问题：/report 生成一份报告。
- 评审：通过；默认报告给出5项有真实来源、连续原文引用和Wiki依据的中文个人学习分析，建议具体且有适用条件；薄证据未进入主要推荐，无公司套话或仅拒编兜底。
- [打开实际HTML报告](http://127.0.0.1:8501/companies/accept_20260910_release_personal/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Caccept_20260910_release_personal%5Creports%5C2026-09-10-recent-news-impact-34eae848.html)

### P10 分析与质疑子任务

- 空间：`accept_20260910_release_personal`。
- 会话名：`验收 personal multi`；run_id：`dd43d694458445419a4d1b7f795dfe25`。
- 用户问题：请从分析和质疑两个角度分别判断：我是否应该在两小时学习时间里优先补Trace与回归用例，而不是立即重写runtime？最后给有条件的建议。
- 评审：通过；skeptic与analyst均有真实子任务产出和本轮引用，主回答结合反例给出有条件的Trace优先建议，并承认缺少代码证据，未将建议写成已验证效果。

## 再看不完美的真实案例

- P04：论文专题有引文校验失败的情况。对照问答与Trace看partial如何披露，不把有HTML等同于内容全部达标。
- H09：政策原文排除条件与企业是否符合分开；附件缺失时只能给有边界的报告。
- P19：未来时间窗没有已发布资料，应改日期，不应建议换数据源来凑结果。

## 你重点验收什么

1. 企业和个人问同一句/report，建议是否真的因Wiki不同而不同。
2. 报告里是否能区分外部事实、用户画像、推断和待确认条件，来源是否能定位。
3. H04/P02/P05追问的是刚才报告，而不是另找一份结果；发布时间和抓取时间是否分开。
4. P06-P10四角色是否有真实上下文和输出；不能只看success标签。
5. H11/H12、P11/P12长期偏好可跨会话；P13/P14临时要求不写长期记忆。
6. P15/P16修改Wiki后，读取和报告是否使用新目标。
7. P17-P20错误命令、权限、未来资料、只解释不执行是否处理清楚。

## 文档顺序

- [结果总表与真实耗时](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_results_cn.md>)
- [32条测试问题及预期](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_query_set_cn.md>)
- [真实问答全文](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_answers_cn.md>)
- [逐条Trace与生命周期](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_trace_report_cn.md>)
- [修复、决策与剩余边界](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_changes_and_limits_cn.md>)
- [前端操作验收](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_ui_acceptance_cn.md>)
- [测试规范与覆盖范围](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_functional_test_plan_cn.md>)
- [自动化回归记录](<D:/AAAcode/code-code/agent+/harness/policy_impact/docs/acceptance/persona_regression_checks_cn.md>)
