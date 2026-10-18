# 全球金融公开来源实测

实测时间：2026-09-23T12:09:34.601320+00:00

数据来自本次实时请求，未读取旧报告、密钥或用户持仓。

|品种|来源|实际参考/交易日|期前基准日|末值|单位|完整性核验|
|---|---|---|---|---:|---|---|
|spx|Sina Finance US|2026-09-22|2026-09-21|7764.64|点|True|
|ixic|Sina Finance US|2026-09-22|2026-09-21|27244.28|点|True|
|dji|Sina Finance US|2026-09-22|2026-09-21|51863.69|点|True|
|xauusd|BullionVault wholesale spot reference|2026-09-22|2026-09-21|4364.232404|USD/troy_oz|True|
|xagusd|BullionVault wholesale spot reference|2026-09-22|2026-09-21|67.401856|USD/troy_oz|True|

未取得品种：无

金银仅为UTC完整采样日末参考价。六个00/04/08/12/16/20起始的4小时桶合并HLC；开盘价未知保持null。
20点是最后桶起点，不是末值确切成交时间；无精确午夜报价或官方收盘承诺。
session_verified只核验完整参考日采样，不代表交易所认证。静止报价可能沿用休市前价格，不算新交易日；缺失日不补洞。
原始name/securityId/valuationSecurityId/interval/batch、epoch时间戳、USD/kg到USD/troy_oz换算、哈希与全文见同名JSON。
金银只有约30天滚动样本；更长区间如实标缺失。指数来源为第三方公开接口，日历核验不等于交易所价格认证。
Yahoo缺失close不以regularMarketPrice填充，整段回退Sina；任何备用源不拼接。CFD、期货、ETF及LBMA定盘不替代spot。
Fed RSS仅有界样本，正文保留但不抓投影PDF/图片附件；宏观发布时间不证明价格因果。

机器可读审计：global_finance_source_probe.json
