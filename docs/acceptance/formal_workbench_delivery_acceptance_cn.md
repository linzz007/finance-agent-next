# 正式 Workbench 验收记录

- 验收时间：2026-09-10 17:01:59
- API：http://127.0.0.1:8503
- 覆盖空间：同花顺企业空间、Agent 自进化个人空间
- 用例数：10，通过：10/10
- 中位耗时：8.87s

## 总体判断

本轮结构验收通过：企业/个人报告、报告追问、动态 Subagent、来源盘点和边界拒绝均有可查 trace 与产物证据。

## 企业报告应有形态

企业报告应围绕同花顺 Wiki 中的金融数据、问财/iFinD、智能投研、数据安全和合规边界展开；外部来源只是输入，最终要输出“对业务/产品/合规的条件性影响、证据链、待确认问题和行动建议”，不能编造内部采用、收益提升或生产指标。

## 个人报告应有形态

个人报告应围绕 Agent 自进化学习目标展开，关注 skill、memory、workflow、Eval、Trace、Subagent Harness 等资料；最终建议要落到两小时内可执行的小实验或阅读路径，不能套用同花顺金融产品语境。

## 用例明细

| 用例 | 空间 | Skill | Subagent | 报告 | 通过 | 证据目录 |
|---|---|---|---|---|---|---|
| H01 | 企业空间：同花顺 | `external_impact_report` | - | [run_artifact](http://127.0.0.1:8503/companies/company_001/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Ccompany_001%5Cruns%5C97d66b2ccefe4ca2a0f26651d8ed328f%5C2026-09-10-news-run_artifact.json)<br>[report](http://127.0.0.1:8503/companies/company_001/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Ccompany_001%5Creports%5C2026-09-10-recent-news-impact-97d66b2c.md)<br>[html_report](http://127.0.0.1:8503/companies/company_001/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Ccompany_001%5Creports%5C2026-09-10-recent-news-impact-97d66b2c.html) | OK | `D:\AAAcode\code-code\agent+\harness\policy_impact\data\acceptance\formal_20260910_clean_delivery_final\cases\H01` |
| H02 | 企业空间：同花顺 | `general_chat` | - | - | OK | `D:\AAAcode\code-code\agent+\harness\policy_impact\data\acceptance\formal_20260910_clean_delivery_final\cases\H02` |
| H03 | 企业空间：同花顺 | `general_chat` | skeptic, verifier | - | OK | `D:\AAAcode\code-code\agent+\harness\policy_impact\data\acceptance\formal_20260910_clean_delivery_final\cases\H03` |
| H04 | 企业空间：同花顺 | `external_impact_report` | - | [run_artifact](http://127.0.0.1:8503/companies/company_001/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Ccompany_001%5Cruns%5Ce667f4a2b171498997e0010caab5b28b%5C2026-09-10-news-run_artifact.json)<br>[report](http://127.0.0.1:8503/companies/company_001/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Ccompany_001%5Creports%5C2026-09-10-recent-news-impact-e667f4a2.md)<br>[html_report](http://127.0.0.1:8503/companies/company_001/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Ccompany_001%5Creports%5C2026-09-10-recent-news-impact-e667f4a2.html) | OK | `D:\AAAcode\code-code\agent+\harness\policy_impact\data\acceptance\formal_20260910_clean_delivery_final\cases\H04` |
| H05 | 企业空间：同花顺 | `external_impact_report` | - | [run_artifact](http://127.0.0.1:8503/companies/company_001/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Ccompany_001%5Cruns%5C844d5f7df94c4542b94750588bee5cbe%5C2026-09-10-source-inventory.json) | OK | `D:\AAAcode\code-code\agent+\harness\policy_impact\data\acceptance\formal_20260910_clean_delivery_final\cases\H05` |
| P01 | 个人空间：Agent 自进化 | `external_impact_report` | - | [run_artifact](http://127.0.0.1:8503/companies/agent_self_evolution/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Cagent_self_evolution%5Cruns%5C6c1152f5225a45699973b86faa8b9fb3%5C2026-09-10-news-run_artifact.json)<br>[report](http://127.0.0.1:8503/companies/agent_self_evolution/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Cagent_self_evolution%5Creports%5C2026-09-10-recent-news-impact-6c1152f5.md)<br>[html_report](http://127.0.0.1:8503/companies/agent_self_evolution/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Cagent_self_evolution%5Creports%5C2026-09-10-recent-news-impact-6c1152f5.html) | OK | `D:\AAAcode\code-code\agent+\harness\policy_impact\data\acceptance\formal_20260910_clean_delivery_final\cases\P01` |
| P02 | 个人空间：Agent 自进化 | `general_chat` | - | - | OK | `D:\AAAcode\code-code\agent+\harness\policy_impact\data\acceptance\formal_20260910_clean_delivery_final\cases\P02` |
| P03 | 个人空间：Agent 自进化 | `general_chat` | skeptic, analyst, verifier | - | OK | `D:\AAAcode\code-code\agent+\harness\policy_impact\data\acceptance\formal_20260910_clean_delivery_final\cases\P03` |
| P04 | 个人空间：Agent 自进化 | `external_impact_report` | - | [run_artifact](http://127.0.0.1:8503/companies/agent_self_evolution/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Cagent_self_evolution%5Cruns%5C0609f48e34cc4db8b8bca02c1aac162e%5C2026-09-10-news-run_artifact.json)<br>[report](http://127.0.0.1:8503/companies/agent_self_evolution/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Cagent_self_evolution%5Creports%5C2026-09-10-recent-news-impact-0609f48e.md)<br>[html_report](http://127.0.0.1:8503/companies/agent_self_evolution/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Cagent_self_evolution%5Creports%5C2026-09-10-recent-news-impact-0609f48e.html) | OK | `D:\AAAcode\code-code\agent+\harness\policy_impact\data\acceptance\formal_20260910_clean_delivery_final\cases\P04` |
| P05 | 个人空间：Agent 自进化 | `external_impact_report` | - | [run_artifact](http://127.0.0.1:8503/companies/agent_self_evolution/workbench/artifact?path=D%3A%5CAAAcode%5Ccode-code%5Cagent%2B%5Charness%5Cpolicy_impact%5Cdata%5Ccompanies%5Cagent_self_evolution%5Cruns%5Cfb71260971cf4a03a99c288c7b6a8b0f%5C2026-09-10-news-run_artifact.json) | OK | `D:\AAAcode\code-code\agent+\harness\policy_impact\data\acceptance\formal_20260910_clean_delivery_final\cases\P05` |

## 失败检查项

