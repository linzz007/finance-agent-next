# finance-agent-next

独立的金融研究 Agent：阅读博主文章、比较观点与时间变化、获取公开资料、维护证据与 Trace。保留自研 Python Harness，以主 Agent 自主规划和按需调用工具 / Subagent 为基础。

这是原项目实际工作目录的副本，不修改原项目。同花顺与其他非金融产品不在新入口中开放。内部包名 `policy_impact` 暂时保留以兼容既有实现。

## 开发入口

从 [设计总入口](docs/README.md) 开始：按产品、架构、接口、验收、研究选型和开发流程分工。当前先共同确认文本，不继续进行业务或调度重构。

用户已确认以仓库 Markdown 为设计权威。飞书 revision 181 的全文和流程图已归档，并完成本地文本差异整理，见[迁移审查](docs/changes/002-design-authority-migration.md)。未来开发不依赖飞书；历史文档不是新产品的第二套设计权威。[开发说明](docs/development.md)提供代码地图、System Prompt / Skills 位置、变更流程和已知边界。

## 启动与验证

```powershell
uv sync --locked --group dev
uv run --locked python scripts/check_finance_baseline.py
uv run --locked python scripts/test_finance_baseline.py
uv run --locked python run_finance.py --port 8510
```

默认地址：<http://127.0.0.1:8510/>。端口被占用时换一个端口，不停止原服务。

模型凭证配置于 `.env.local`，不提交到 Git。新副本保留了原本的本地模型配置，没有在本次分离中调用付费模型。

## 独立数据

- 文章：`data/finance/articles/<作者>/articles/*.md`。
- 持仓、研究偏好：文章根的 `current_holdings.md`、`research_policy.md`。
- Wiki：`data/companies/finance_observer/wiki/`。
- 数据库、会话和 Trace：首次使用时在新项目生成，不迁移旧数据库和历史。

这些业务数据及运行产物均不提交到 Git。金融启动入口拒绝项目外文章根，避免误写原项目。旧 `run_policy_api.py` 是继承代码，不作为当前产品启动入口。

本次建立的是隔离副本和 AI-native 开发基线，不代表多用户队列、崩溃恢复、金融回答质量或商业化稳定性已经验收。具体改动与验证见 [分离记录](docs/changes/001-finance-research-fork.md)。
