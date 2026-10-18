# 金融独立验收

## 当前结论

- 审核日期：2026-09-23（Asia/Shanghai）；最后一轮对象为 `finance-accepted`，不将旧 label 的成功替代本轮结果。
- 结论：**限定通过。预定三轮真实报告与追问、只读 API 和69项独立检查通过，既定范围内无未关闭的阻断项。** 限于本轮保存样本及本文覆盖范围，不表示任意金融问题、全网资料或长期采集已获保证。
- 本人实际执行 `FINANCE_REVIEW_ACCEPTANCE=finance-accepted FINANCE_REVIEW_LIVE=1`，独立测试 **69 passed in 1.15s**。未将实现者报告的全量测试计入此数字。
- 三个 case 均返回回答；主报告 `analysis_status=done`、`analysis_verification={supported:true,issues:[]}`，但 `data_status=partial`。人工另核对全部回答、报告四节正文、原文引文与重点数值，没有只依赖 `supported` 标记。
- API 当前返回 `enabled=true`、`worker_running=true`、`last_status=partial`；本轮未暂停、停止或重启 worker，未修改档案。
- `finance-ready` 的统计期 P2 已由 `finance-accepted` 的真实同题追问复验关闭；原失败输出继续保留。

## 验收标准

| 标准 | 判定依据 |
| --- | --- |
| 1. 真实来源与日期 | 指数/官方宏观/具名机构原文可回溯；逐指标区分发布日期、统计期与抓取时间，不补造数据 |
| 2. 可读报告 | 中文结论、档案关联、明确数值与口径、实际引用同时存在，不能只有来源目录 |
| 3. 可复算 | 点位、百分比、排名、每日方向与回撤可由原快照独立重算 |
| 4. 快照追问 | 使用授权原报告，明确非实时；区分未展开、未保存、来源失败 |
| 5. 隔离与未知项 | 三 workspace 不串读；draft 画像、未知持仓不被补造 |
| 6. 真实执行 | AgentLoop、工具权限、门控、Trace、artifact 可见，工具调用不冒充模型选择 |
| 7. 采集状态 | enabled、worker 存活、采集状态分别可辨；暂停/恢复行为可核对 |
| 8. 失败诚实性 | 空数据、超范围、源失败、校验失败不被标成完整分析成功 |

## 已关闭 P2（保留历史）

### P2：追问把混合统计期概括为累计口径，独立复核未识别

- 输入：`基于刚才报告，我接下来最值得关注哪两件事？请分别讲事实依据、可能的另一种解释和还缺什么证据，不要给我买卖建议。`
- 输出位置：`data/acceptance/finance-ready.json` 的 `cases[2].response.answer`，第二点“另一种解释”：`宏观数据为 1—8月累计口径、发布于 9月中旬`。
- 同一回答上一段包含“8月规模以上工业增加值增长5.2%”。保存原文 `finance_doc_2cf97d1fad701f99ed04` 第一段分别给出 **8月当月同比5.2%**、**1—8月累计同比5.3%**；固定资产投资、房地产开发投资使用1—8月累计。不能将所列全部宏观指标概括成同一累计期。
- 主报告正文正确写了“同期发布的当月与累计数据”，错误是在追问归纳时新引入。此问题不是发布日期误当统计期，而是不同指标统计期被合并，仍违反口径要求。
- 代码定位：`src/policy_impact/domains/finance/answer_verifier.py:65` 起的复核指令、`:73` 的四类检查、`:82` 的汇总放行；`src/policy_impact/domains/finance/verification.py:26` 根据各项 verdict 汇总。复核 `sources` 与 `causality` 都判 supported，没有逐指标检查统计期。
- 证据 run：`519b331e7c89408396c6a65fc1dbee8e`。复核用一次模型调用通过，第二次修复机会未触发。
- 建议：事实复核逐指标核对“统计期、同比/环比、单位、发布日期”，不得仅检查类别、数值和来源名。应改为“工业增加值为8月当月同比；投资与地产为1—8月累计”，保留发布时间与行情区间不一致的说明。修复后须复验真实追问；仅修改 prompt 不足以关闭此项。
- 关闭依据：增加逐指标口径提示与标题统计期提示后，`finance-accepted` case3 明确“当月口径”“均为1—8月累计口径”“不能整批统称累计”。本人对照保存原文复核，不以新 prompt 本身作为关闭依据。新 run：`503f544277594f5eb7d90eae783d6710`。

## 本轮证据

| 对象 | ID / 状态 |
| --- | --- |
| 会话 | `744a2be92af047eba6e02e5a6b35dd3d` |
| 报告 | `fin_0599bca0754146a7bcf04c8bba2ace5a` |
| case1 `/report` | `e1964f9578424eaf97c3d6a86a53136e`，done |
| case2 公式追问 | `403414ac9e3449c78b7596799170bfd1`，done |
| case3 证据缺口追问 | `503f544277594f5eb7d90eae783d6710`，done |
| 证据时间 | `2026-09-22T16:39:43.341299Z`，已保存快照，非实时 |
| 请求区间 | 2026-09-01 至 2026-09-22 |
| 实际收益基准 | 2026-08-31 收盘，包含期内首日变动 |

本人经 GET 回读报告、三个 run 的 trace、HTML/Markdown/JSON artifact，并人工读三个回答及四段报告分析。HTML 含数值表、口径、来源、原文引文及档案关联；Markdown 是较短的正文与证据 ID 版本，不等同于完整 HTML。此轮未作浏览器像素或移动端排版验收。

### 原文逐项核对

| case3 数值 | 原文支持 | 口径 |
| --- | --- | --- |
| 房地产开发投资 -19.9% | `finance_doc_1524fe764b1566a86b61` 第一节首段及表1 | 1—8月累计同比，按可比口径 |
| 房地产开发企业到位资金 -21.0% | 同文第三节及表1 | 1—8月累计同比 |
| 固定资产投资 -7.2% | `finance_doc_f65b60cac6aea7822f7a` 首段及数据表 | 1—8月累计同比，不含农户，按可比口径 |
| 民间固定资产投资 -10.1% | 同文“1—8月份，民间固定资产投资”段及数据表 | 1—8月累计同比 |
| 设备工器具购置 +9.3% | 同文“按构成分”表中对应行 | 1—8月累计同比；表注未扣除价格因素 |
| 知识产权产品投资 +9.2% | 同文首段 | 1—8月累计同比 |
| 工业增加值 +5.2% | `finance_doc_2cf97d1fad701f99ed04` 首段 | 8月当月实际同比，已扣除价格因素；不能当作累计5.3% |

两份投资原文均在9月15日发布。回答用“可能”描述基数/可比口径影响，未将解释当作已证实因果，并列出附注和后续数据为待核查项。原文数字、统计期和发布日期分别核对，没有将其中一项替代另一项。HTML 中四条引文经转义后均可定位，且均为对应保存原文的实际子串。

### 数值复算

| 指数 | 期前收盘 | 期末收盘 | 区间变化 | 区间内最大回撤 |
| --- | ---: | ---: | ---: | ---: |
| 上证指数 | 3986.30 | 3952.13 | -0.857186% | 2.904854% |
| 沪深300 | 4625.09 | 4544.59 | -1.740507% | 3.499991% |
| 创业板指 | 3438.68 | 3399.93 | -1.126886% | 4.435840% |

- 独立测试以 Decimal 重算所有保存点位相对基准的变化，公式为 `(期末 / 期前基准 - 1) * 100`。case2 展示的沪深300 `-1.74%` 与此一致。
- 最大回撤采用枚举全部前后收盘配对的独立算法复核，不复用实现的运行峰值算法；排除期前基准。另核对峰谷日期、每日方向、涨跌天数、期末连续上涨天数和精确比值排名。
- 三者期末均连续上涨3个已观测交易日，起点9月18日；9月17日为下跌，不能将9月16日并入连续上涨。
- 沪深300相对期前基准最低跌幅约-3.78%，不等于区间最大回撤3.50%。移除9月7日中间观测不能改变固定首尾收益，独立测试覆盖此不变量。

### 工作流、隔离与只读权限

- `/report` trace 存在真实模型调用、主 AgentLoop 的 `report_workflow.run`、金融 collect/metrics/publish 工具记录及三个 artifact；不是仅挂名 workflow。
- case3 首次 final 因缺少 sources/market_series 被拒，随后模型实际选择两次 `finance.report_read`，再提交 final；独立复核一次。不是 runtime 伪造模型工具选择，也未耗尽三次工具预算。
- case3 使用已保存的沪深300日线和来源目录/原文摘录；要求未来时段日线、未统一单位的量能口径和更细原文附注，没有断言保存的三指数日线不存在，也未声称本次重新联网。
- 独立读工具测试拒绝越权 report_id、伪造 company_id、错误 stage、调用采集工具和非法分页参数。真实 GET 验证另外两个 workspace 无金融 profile，不能读取本报告，artifact 返回403。
- `FinanceAnswerVerifier` 只消费本轮授权报告和已产生 observation，不采集最新行情，不修改回答、档案或交易状态。缺失报告、损坏快照在模型调用前受控拒绝；独立测试核对没有花费 review 预算。
- 保持 `draft`、`holdings=null`。最终报告明确个人持仓、经验、资金与风险偏好未知，未据此编造个性化持仓结论。

## 失败基线与复验

以下 artifact 保留原始失败，不应覆盖或重写为通过。

| 基线 | 观察到的问题 | 修复与最新状态 |
| --- | --- | --- |
| `financial-first` | 投影未展开被当作没有日线/原文，文章事实与引用绑定不足 | 增加保存数量元数据、授权分页读工具、引文子串验证及语义复核；最新报告有真实原文引文，追问区分未读与未保存 |
| `financial-final` | 合法期间/日期被数值 guard 拒绝；追问混淆中间缺日与首尾收益 | 保留失败；日期规则与固定首尾不变量有回归，最新公式正确 |
| `financial-release` | 无中文分析；连续上涨、排名及回撤解释错误，即使读取过工具仍被输出 | 增加确定性排名、日方向、MDD 和追问独立复核；最新复算通过，不据此推断任意问题均正确 |
| `financial-verified` | 复核 issues 中出现“无实质错误”仍否决；case3 8次 final，0次金融读工具，502 | 改逐项 reason/verdict 并由 Runtime 汇总；保留失败 |
| `financial-checks` | 主报告分析完成，但 case3 sources、metrics 后6次 final 均未读 market_series，502 | 非工具3次额度耗尽，实际只用2次；发现 system gate 反馈经 Anthropic 转换前置，形成末尾 assistant 候选 |
| `financial-delivery` | case1 被真实样本“16个交易日”拦截；case3含“连续4天但中间下跌”和中间反弹改变固定起点错误 | 有证据计数白名单与复核数学约束；独立 artifact 回归保留。该轮增强测试实跑5 failed / 63 passed，含下述两个当时未修边界 |
| `financial-handoff` | 同月日期“9月1日至22日”尾部仍触发数字 guard，报告 analysis 为空 | 同月尾部规则修复；该轮复验1 failed / 68 passed，仍不通过；最新 `finance-ready` 报告分析已生成 |
| verifier 缺失/损坏证据边界 | 查不到报告被跳过后可空证据放行；快照异常发生在 try 之外 | 证据装载受控拒绝，检查 workspace，saved_data 按实际内容判断；最新两项独立回归通过 |
| `finance-ready` | 自动69 passed，但 case3 将含8月工业增加值的材料统称1—8月累计 | 逐指标口径提示及标题提示接入；`finance-accepted` 同题明确区分当月/累计，人工原文复核后关闭 |
| `finance-accepted` | 三个真实 case 均完成，报告分析完成，数据覆盖仍为 partial | 独立 live 69 passed；人工核对报告、公式、gap追问及六项投资数据后限定通过 |

时序修复的独立测试实际调用 Anthropic `_split_messages`，确认被拒 assistant 候选后仍是 user Runtime observation，再可选择 market_series。离线路径 sources → metrics → 被拒 final → market_series → final 用5个模型动作、3次只读工具；无真实模型请求。

## 实施差异与限制

- 数值输出责任分开：正文主要定性解释；点位、百分比、排名、回撤由确定性模块提供；引文允许保留经原文子串校验的数字；样本天数只按证据字段放行。该设计不等于自动核验所有中文事实。
- 结构化 checks 解决全局 bool 与解释自相矛盾的汇总问题，但四个大类仍可能漏掉同一回答中的子主张，历史 `finance-ready` 统计期问题就是实证。最新关闭该具体问题，不宣称模型复核数学完备或永不漏检。
- 来源是腾讯财经第三方价格指数日线、统计局中英文发布、Fed 讲话、BIS 收录的具名央行讲话；BIS 收录不等于 BIS 背书，中英文翻译不是独立交叉验证。早期已对样本原始接口响应进行外部比对；本轮优先复用保存原文和 hash，未重新遍历互联网。
- 本轮保留15份文章，统计局中英文各4份、Fed 4份、BIS 3份；采集入口和数量均有边界。`partial` 反映限量/部分来源覆盖，不得宣传为全网搜索或完整宏观数据库。报告披露 IMF 403、未提取图片/PDF/附件、交易所日历未核验、成交量单位未统一等限制。
- worker 的真实存活与 enabled 配置分别展示。GET 观察到上次采集从 `2026-09-22T16:37:01.237872+00:00` 至 `16:37:21.960922+00:00`，约20.7秒，结果 partial。不是把 enabled 当成 worker 正在运行。
- 早期已实际 POST 暂停、在暂停时调用 collect 得到 paused，再恢复 enabled=true，并核对档案未变；本次运行 worker 后本人仅 GET 读取。父任务另报告完成存活 worker 下暂停/collect paused/恢复试验，此动作不是本人执行；本人最后 GET 确认 enabled=true、worker_running=true、collecting=false，profile 与验收保存值完全一致。尚未独立观察跨重启恢复、下一周期成功采集或长时间稳定性，不能据此宣称全天候稳定。
- 空快照、来源失败、无效期间/不支持指数、越权工具等由隔离内存测试覆盖；未对生产档案或数据库注入坏记录。默认只读 API 不代表测试过所有外部源失败组合。
- 审核者未创建 agent，未创建新付费聊天或调用真实模型；本轮使用父任务生成的保存结果与 trace。只写独立测试与本审核文档，未修改实现。

## 复现

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:FINANCE_REVIEW_ACCEPTANCE='finance-accepted'
$env:FINANCE_REVIEW_LIVE='1'
& 'C:\Users\78230\AppData\Local\Programs\Python\Python310\python.exe' -B -m pytest tests/test_finance_review.py -q -p no:cacheprovider --tb=short
```

结果：`69 passed in 1.15s`。先前 `finance-ready` 的 `69 passed in 0.98s` 不覆盖其人工发现的口径错误；直到最新真实输出逐句复核后才关闭 P2。历史失败与运行记录全部保留。

## Trace 来源摘要小修复复验

- 日期：2026-09-23；范围仅限金融 Trace 顶部来源摘要，不重新生成报告，不新增付费模型调用。
- 结论：**该小修复复验通过，维持前述限定通过结论。** 未发现新的阻断或回归；来源 partial 等原有限制不变。
- 代码审查：`src/policy_impact/app/chat_workbench_service.py:524` 的 `_summarize_sources` 仅对 `metadata.workflow=financial_report` 使用金融阶段输出，从 acquisition 取真实来源状态与快照标识，从 validation 取序列/资料数；未取得状态不填虚构零，显式空列表与未记录区分。非金融分支保留原逻辑。
- 前端审查：`src/policy_impact/app/static/workbench/app.js:2218` 的 `sourceSummary` 分别展示成功、部分、失败计数与金融序列/资料数，不将 partial 并入成功，也不伪造新闻分类数量。

实际 GET 路径：

```text
/companies/finance_observer/workbench/sessions/744a2be92af047eba6e02e5a6b35dd3d/runs/e1964f9578424eaf97c3d6a86a53136e/trace
```

返回 `recorded=true`、`route=finance`、`trace_summary.has_source_summary=true`。与报告保存快照逐项核对：

| 字段 | 实测值 |
| --- | --- |
| source_count | 7，按记录的来源状态条目计数，不是7个不同供应商 |
| successful_source_count | 3，对应三条腾讯指数来源 |
| partial_source_count | 4，对应统计局中文、英文、Fed、BIS |
| failed_source_count | 0 |
| series_count / document_count | 3 / 15 |
| snapshot_id | `finance_b532b9408f6f53a55a8a5025` |
| snapshot_time | `2026-09-22T16:39:43.341299Z` |

另 GET 服务实际提供的 `/static/workbench/app.js?v=20260910-visible-demo-sessions-1`，在隔离 JS VM 中将真实 trace 交给 `sourceSummary`，得到：

```text
金融观察
来源 7（成功 3 / 部分 4 / 失败 0） · 指数序列 3 组 / 资料 15 篇
```

这验证了服务所提供 JS 的摘要输出，不冒称完成浏览器像素/排版检查。GET 回读完整 report 与 `finance-accepted.json` 中保存对象完全相等；trace 原始 steps、model_summary 亦完全相等，未改变报告正文、原快照或模型执行记录。

实际执行结果：

```text
FINANCE_REVIEW_ACCEPTANCE=finance-accepted FINANCE_REVIEW_LIVE=1
python -B -m pytest tests/test_finance_review.py -q -p no:cacheprovider --tb=short
69 passed in 0.97s

python -B -m pytest tests/test_finance_trace_summary.py tests/test_trace_summary_accounting.py -q -p no:cacheprovider --tb=short
15 passed in 0.66s

node --test tests/test_trace_source_summary_ui.mjs
4 passed, 0 failed
```

Python 使用上文相同的 Python310 绝对路径。未将实现者报告的19项/前端79项记为本人执行；本人此轮仅追加本文，未修改业务实现、金融档案或数据库。
