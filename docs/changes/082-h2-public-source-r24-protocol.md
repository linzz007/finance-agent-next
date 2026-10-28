# H2 URL 凭证扫描修复后的单题复验

日期：2026-10-20。依据：[081](081-public-url-secret-scan-mvp.md)。
本批 R24 只复验 R23 暴露的持久化误报，不改变公开来源、金融审核或调度设计。

## 固定范围

- 使用同一验收 Query Q19、同一隔离语料快照和 DeepSeek 模型；显式绑定 `web.search/web.open`。
- FakeRedis；Root 120、子/孙 60、整轮 600 次、60 分钟；不换模型、不自动 fallback、不提高额度。
- 新输出目录 `runtime-records/h2-public-r24b-20261020`，不覆盖 R23。R23 的 23 次失败请求、
  原始账本和完整性记录继续作为独立证据。

## 必须检查

1. 普通网页 query 参数不会导致 `internal_error`；实际 `web.open` 结果能保存为 H2 artifact。
2. 证据仍满足 URL、正文、hash、行范围、抓取时间和引用校验；搜索摘要不变成证据。
3. 若模型交付，人工检查作者观点、事实和外部核查是否分层；交付不等于语义通过。
4. 若仍失败，区分模型/传输、网页抓取、权限/契约、持久化安全扫描和语义原因；不重跑追求成功。
5. 运行结束先保存账本、状态和冻结完整性，再写报告。未触发的分支标记 `not_triggered`。

## 状态

R24b 已结束，完整记录位于项目旁 `runtime-records/h2-public-r24b-20261020`：

- 32 次 DeepSeek 请求，539.51 秒，Root succeeded，交付 2772 字符；FakeRedis，未验证真实 Redis。
- `web.open` 实际成功保存 2 个网页正文来源，`public_sources` 保留 URL、hash、抓取/发表时间、
  行范围和原 reader；未把公开网页混入 `article_states`。R23 的 URL 持久化 `internal_error` 未复现。
- 1 个 analyst Task 因 6 次 `contract_error` 终止，Root 仍交付；因此这不是完整业务/语义通过。
  `metadata_status=incomplete_metadata`，模型对未能核实的内容保留为未核实/观点，仍需人工审查。
- 输入快照/源码未变化，自动语义评分关闭，真实 Redis/并发/商业稳定性未验证。

结论：081 的技术缺陷修复通过本批实际路径验证；剩余 analyst 协议失败另行归因，不扩大本修复范围。
