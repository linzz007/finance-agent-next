# 金融文章导入

## 推荐链路

1. 在微信文章页面复制公开链接，或使用公众号文章导出工具批量得到 Markdown/HTML。
2. 将导出结果按作者放入一个临时目录，例如 `D:\金融导出\TT`。
3. 运行本项目的导入脚本。脚本会识别标题、日期和来源 URL，写入 `D:\AAAcode\金融\文章数据\TT\articles`，按来源 URL/正文哈希去重，并刷新 Runtime 索引。

```powershell
py -3 scripts/import_wechat_articles.py `
  --input 'D:\金融导出\TT' `
  --author TT `
  --report 'work\wechat-import-tt.json'
```

也可以直接尝试处理公开文章链接清单：

```powershell
py -3 scripts/import_wechat_articles.py `
  --urls-file 'D:\金融导出\tt-links.txt' `
  --author TT `
  --report 'work\wechat-import-tt.json'
```

直接请求可能受到公众号页面限制；失败时保留链接和失败原因，不绕过登录、验证码或反爬。更稳定的做法是先用浏览器导出工具得到 Markdown/HTML，再使用本脚本归档。

## 可选外部工具

- [wechat-article-exporter](https://github.com/yizecoder/wechat-article-exporter)：MIT，支持公开文章 URL 列表、TXT/CSV/XLSX、Markdown 输出、图片目录和 manifest 断点记录。适合本项目的“外部采集，内部归档”边界。
- [wechat2md](https://github.com/luoziyan100/wechat2md)：MIT，浏览器扩展，适合打开一篇文章后一键导出 Markdown。
- [wechat-download-api](https://github.com/tmwgsicp/wechat-download-api)：AGPL-3.0，功能覆盖公众号文章批量获取、RSS/API/MCP 和多种导出格式。它需要单独评估部署、数据合规和 AGPL 义务，不直接嵌入本项目 Runtime。

本项目不把第三方爬虫作为核心依赖。第三方工具只负责获取公开文章，Runtime 只消费本地作者目录中的 Markdown，并通过 `ArticleCorpus` 建立文章版本和证据索引。
