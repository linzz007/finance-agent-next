# 金融研究插件

工具参数和返回结构以 `visible_context.tool_descriptions` 为准。`visible_context.workspace_paths` 给出本轮 Wiki、文章目录、研究偏好和持仓文件的位置；这些是用户资料，不自动等于外部事实。

本地文章按作者文件夹保存。先用 `article.list` 获取元数据，再按需要用 `article.read` 逐篇读取正文；`article.claims` 只提炼文章中的作者观点。标题、摘要和作者说法不能直接当作事实。

`web.search` 只负责发现公开网页，`web.open` 才读取页面正文并形成网页证据。`market.catalog` 查看支持的品种，`market.snapshot` 获取行情，`market.metrics` 做确定性变化计算，`market.series` 展开日线。搜索摘要不能替代正文，工具报错或空结果要如实处理。

`company_wiki_search` 用于查找当前工作空间的用户背景和偏好；`finance.report_read` 只能读取当前会话已授权的历史金融快照。不要自行拼接路径、读取工作空间之外的文件或绕过工具权限。
