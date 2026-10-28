# 105 子 Agent 调用与配置替换验收

日期：2026-10-27  
状态：按用户要求补充测试；不改变 104 的执行协议和预算。

新增独立测试目录 `tests/fixtures/h2_subagent_acceptance.json`，不修改已冻结的 30 条业务 Query。
覆盖通用动态盲审、无来源的通用解释、Analyst 不核验/自主跳过/核验后修订再核验、
独立 Verifier 和 Skeptic。每题记录任务指令、验收条件、输入角色和预期返回合同。

使用离线模型替身驱动真实 H2 Worker、SQLite 和 FakeRedis，检查实际发送的模型消息，
而不只检查配置能否加载。分别核对任务指令、目标、验收条件、材料范围、角色 Prompt、
工具调用、检查点恢复和返回父任务的完整正文。独立追加以下边界检查：

- 任一当前角色在深度 1 可以委派通用任务并整合，深度 2 不再继续派发。
- 在临时配置中替换角色 Prompt 和工具白名单，执行器使用替换后的定义；被移除的工具不可执行。
- 新增专家配置复用通用返回合同，可以通过同一 Worker 完成任务，无须新增 Python 角色分支。
- 父任务读过的其他文章和原始工具历史不自动进入子任务；显式背景引用才能进入。

通用子任务的临时工作指令由父 Agent 填写 `task_instructions`，基础系统规则和角色默认 Prompt
由 Harness 从冻结配置加载；两者在运行时组合。专业角色也使用相同的 Spawn 输入，
区别在角色默认指令、允许的返回合同，以及 Analyst v2 的可选核验控制。
外层输出合同和权限需要由 Runtime 支持，不能只用任务指令任意新增执行能力。

## 验证结果

- `final-suite.xml`：新增 16 个用例与 `test_dispatch_general_profiles.py` 的既有回归合计 45 通过、0 失败、0 跳过，控制台耗时 104.18 秒（JUnit 103.51 秒）。
- 覆盖 general 两种任务、Analyst 的 off/auto/required 及两轮修订核验、独立 Verifier/Skeptic、四种角色各自委派 general、父背景显式授权、Prompt/工具白名单替换和新增专家注册。
- 真实 H2 Worker、SQLite、FakeRedis、模型适配器和任务恢复/交付链路均执行；模型响应由离线确定性替身生成。无外部模型请求。
- `uv run --locked ruff check tests/test_dispatch_subagent_acceptance.py` 与 `git diff --check` 通过。
- 首次完整测试报告 `initial-suite.xml`/`initial-suite.log` 保留了 13 通过、1 失败：测试错误地假设权限字段直接位于 TaskBrief，而实际上它保存在独立的 permission snapshot。修正测试断言后全量通过；早期交互调试发现的回执视图和测试动作构造问题也只修正了测试代码，没有改 Runtime。

日志和 JUnit 位于项目同级 `runtime-records/h2-subagent-acceptance-20261027`。
离线通过证明装配、路由、约束与回传机制，不证明真实模型会自主选择最优角色或达到论文/金融研究的内容质量。
