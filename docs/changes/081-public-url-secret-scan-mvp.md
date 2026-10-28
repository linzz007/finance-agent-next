# 公开网页 URL 的凭证扫描误报修复

日期：2026-10-20。类型：已确认的常规工程缺陷，MVP 修复。
依据：R23 Q19 的真实失败记录；不改变金融研究、证据或调度设计。

## 1. 问题

Runtime 为防止凭证进入 artifact/context，会递归扫描可持久化 JSON。
原规则把所有带 query 的 `http(s)` URL 都判为 credential-bearing endpoint。
公开网页正常的分页、筛选和文章导航链接也会带 query，例如 `?page=2`，导致网页正文已经
成功读取后，保存 `web.open` 结果时抛出 `internal_error`，而不是产生公开来源证据。

R23 的实际首发链路是：模型和 `web.search` 成功，`web.open` 已获得正文，但
`capabilities.store_context` 触发通用扫描器失败；主任务因此没有得到有效外部证据。
这不是模型格式错误、预算耗尽、网页不可访问或研究结论错误。

## 2. 修复边界

1. URL 查询参数只有在参数名属于认证/凭证集合（`api_key`、`access_token`、`authorization`、
   `password`、`secret`、`token` 等）或值显式匹配密钥/`Bearer` 格式时才拒绝。
2. 普通公开参数（如 `page`、`section`、`date`）允许保留；URL 用户名、密码、控制字符、
   JSON 中名为凭证的字段和已知密钥格式仍拒绝。
3. 只改变误报判定，不删除 `_no_secrets`，不把任意模型输出或认证端点写入 artifact。
   这是通用持久化安全检查，不是给 `web.open` 绕过检查的特例。
4. 先以单元和真实 R23 原路径回归；修复后若继续运行只使用同一冻结 Query/模型/预算，
   不更换模型、不提高额度。原 R23 失败结果保留，不覆盖。

## 3. 验收

- 普通 query URL 可持久化；认证参数、Bearer、API key 和带用户密码 URL 仍被拒绝。
- Q19 的 `web.open` 结果能保存为 artifact，正文证据可继续进入研究校验。
- H2/state 和默认金融回归不回退；安全检查、预算、权限和默认 1.0 不变。

## 4. 状态

已实现并完成 R23 原因复现、离线回归和 R24b 新批次复验。R23 原始账本不修改。

- R23：23 次模型请求；模型和搜索成功，`web.open` 已取得正文，随后普通网页导航 URL 的
  `?page=2` 被通用扫描器误判，三个任务最终失败；无模型/传输错误。
- 修复后新增 URL 安全回归：普通分页/筛选/cursor 参数通过；认证参数、用户密码、Bearer/API key
  仍拒绝。执行层 191 项通过。
- R24b：同一 Q19、DeepSeek、FakeRedis、Root120/child60/Run600/60 分钟；32 次模型请求，
  主任务 succeeded，交付 2772 字符；2 个公开网页正文保存为 artifact，原 reader、hash、抓取时间和行范围保留。
  没有再次出现 `credential-bearing endpoint`/`internal_error`。R24b 仍有 1 个 analyst 因
  `contract_error` 失败，主任务交付 metadata 为 incomplete；这是模型协议/收敛问题，不是 URL 修复失败。
- R24b 输入快照和源码冻结检查通过，真实 Redis 未验证，语义质量仍需人工审核；R24b 记录位于项目旁
  `runtime-records/h2-public-r24b-20261020`。此前错误的 runtime-records 快照路径预检零调用失败，
  不计入模型批次。
