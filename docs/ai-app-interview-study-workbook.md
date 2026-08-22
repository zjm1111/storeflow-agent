# StoreFlow｜14 天 AI 应用工程师面试学习工作簿

> 每天投入 2–3 小时。不要尝试背完整代码；每一天只要能用自己的话说明“输入、处理、输出、边界”即可。完成当天的“提交物”后，把录音、文字或截图发给面试陪练者复盘。

## 使用方式

1. 固定每天一个学习时段：**40 分钟阅读 → 50 分钟动手/画图 → 30 分钟口述 → 20 分钟复盘**。
2. 每次口述都按“结论 → 实现 → 边界”组织；不知道的指标明确说“当前未测”，不补编。
3. 录音无需精致。手机语音备忘录即可；目标是发现自己讲不清的部分。
4. 只在 Day 8 后运行系统。前 7 天先建立因果链，避免只会点界面。

---

## Day 1｜业务定位：先说清为什么做

### 阅读

- `docs/storeflow-positioning.md`
- `docs/project-handbook.md`

### 必须掌握

- **用户**：连锁零售企业的区域采购负责人。
- **触发场景**：促销、天气、中央仓配送延迟、销量波动与库存紧张同时发生。
- **决策**：今天该订多少，而不是“帮我搜索天气”。
- **输出**：带 Evidence ID 的风险事件、正常/适度加订/高保障加订三方案、推荐理由与审核动作。
- **边界**：模拟资料；单区域、单门店、单 SKU、单周期；只生成草案，不接 ERP、不下单、不扣库存。

### 练习

不看资料回答：为什么“少订”和“多订”都不好？为什么用户仍然需要人工审核？

### 2 分钟模板

“StoreFlow 面向____。当____同时发生时，负责人必须在____之前决定____。以前需要在____之间手工判断；StoreFlow 把这些信息整理为____，再输出____。它不____，因为____。首版范围是____。”

### 提交物

- 一段 2 分钟录音；
- 写下用户、输入、输出、边界各一句。

---

## Day 2｜Agent：为什么不只是 RAG

### 阅读

- `docs/architecture.md`
- `app/agent/graph.py`
- `app/agent/state.py`

### 必须掌握

`ResearchState` 是贯穿节点的状态：问题、scope、约束、来源、证据、风险、工作记忆、Agent 动作、错误、checkpoint 和决策都在其中流转。

研究层顺序为：初始化 → Agent 选择动作 → 代码执行白名单工具 → 观察结果 → 再选择 → 计划检索 → 证据处理 → 风险 → 报告。其间证据不足会进入 `replan`。

它是 **受限 Agent + 确定性工作流**：

- Agent 部分：模型根据 Observation 选择下一步工具；
- 工作流部分：RRF、数值仿真、优化和人工审核按固定受控流程执行；
- 边界：最多 6 步、最多 2 次外部检索、模型动作最多 2 次；不能调用下单、写库存或任意命令。

### 练习

画出下面的简图并口述每条箭头的含义：

```text
问题 → Agent 选工具 → 代码执行 → Observation → Agent 再规划
                                      ↓
                            证据/风险/仿真 → 人审
```

### 提交物

- 一张状态机图；
- 回答：“这是 workflow 还是 Agent？”（目标 60 秒）。

---

## Day 3｜混合 RAG：证据如何进入模型

### 阅读

- `app/services/retrieval.py`
- `app/agent/nodes/workflow.py` 中 `retrieve_sources` 与并行检索相关代码
- `docs/senior-interviewer-deep-dive.md`

### 必须掌握

四类候选：内部 PDF 的向量/BM25、已批准记忆、Tavily/公开风险、内部元数据筛选结果。互不依赖的内部知识、近期风险和已批准记忆可并行 Fan-out；随后统一 Fan-in。

不要把不同通道的原始分数直接相比。RRF 按每条候选在各通道中的**排名**融合，让一个分数尺度不会压制其他通道；rerank 负责对融合后的候选按当前问题精排。最终模型只接收预算内、带 Evidence ID 的上下文摘要，原文仍保留可回溯。

### 3 分钟回答骨架

1. 所有检索结果塞进模型的问题：token 溢出、重复、过期、来源单一、无法审计；
2. Fan-out：并行拿到独立证据；
3. RRF：解决跨通道分数不可比；
4. rerank + 多样性 + 时效：保证最终证据包质量；
5. Evidence ID：风险事件只能引用允许的证据，摘要不是新事实。

### 提交物

- 一段 3 分钟口述；
- 用一句话区分 BM25、向量检索、RRF、rerank。

---

## Day 4｜决策层：为什么不是 LLM 算数量

### 阅读

- `app/services/decision.py`
- 控制台的“策略比较”页面

### 必须掌握

`parameters_from_events` 将带证据的风险事件转为可解释参数：需求激增影响需求均值/波动，配送延迟影响延迟概率和提前期，成本风险影响采购成本。用户约束会覆盖默认参数。

`make_decision` 固定随机种子，运行 Monte Carlo 样本评估三种订货量；OR-Tools 为高保障策略寻找满足预算、最大订货量和服务目标的数量。每个策略比较：预期成本、缺货概率、服务水平、CVaR 95%、约束可行性和统一目标分数。

### 练习

解释以下概念，禁止只给定义：

- 缺货概率：需求超过可用库存的仿真比例；
- 服务水平：近似为 `1 - 缺货概率`；
- CVaR：最差 5% 成本情景的平均成本，用来约束尾部风险；
- 为什么预算紧与服务目标高可能同时不可行。

### 提交物

- 90 秒回答：“为什么不用 LLM 直接给订货量？”

---

## Day 5｜记忆与 HITL：什么可以跨任务复用

### 阅读

- `app/agent/review_graph.py`
- `app/services/memory.py`
- 任务 API 中 `/memory`、`/review` 路由

### 必须掌握

| 层次 | 内容 | 是否跨任务 | 能否直接成为业务规则 |
| --- | --- | --- | --- |
| 工作记忆 | 当前计划、查询、缺口、证据包、仿真 | 否 | 否 |
| 情景记忆 | 已完成任务输入—风险—策略—审核反馈快照 | 可检索案例 | 否 |
| 长期记忆 | 审核批准的经验、约束和复盘 | 是 | 仅在 scope/有效期内作为先验 |

长期记忆必须有 Evidence ID、审核人、适用区域/门店/品类、有效期、置信度和替代关系。网页、模型草稿和失败任务不能直接进入长期记忆。近期证据冲突时，UI 标“历史记忆待复核”，Agent 不自动覆盖新事实。

HITL 使用 LangGraph `interrupt` 暂停；审核可批准、改约束重优化、要求补证或拒绝。MySQL checkpointer 使审核恢复不依赖浏览器一直在线。

### 提交物

- 解释“为什么网页内容不能直接写入记忆”；
- 用 4 格图画出“候选记忆 → 审核 → approved → 新任务召回/冲突复核”。

---

## Day 6｜后端：异步、持久化与恢复

### 阅读

- `app/api/tasks.py`
- `app/worker.py`
- `app/repositories/tasks.py`
- `alembic/versions/`

### 必须掌握

创建任务返回 `202 Accepted`。`Idempotency-Key` 保证用户重试不会重复调度；Celery worker 使用 Redis broker 异步执行；任务版本与 checkpoint 用于避免过期 worker 重复执行；Redis Streams 持久化事件，SSE 使用 `Last-Event-ID` 续传；MySQL 保存任务快照、审计、决策和审核状态。

遇到 worker 丢失后，任务不依赖一次 HTTP 请求存活；新的 worker 可按持久化任务和 checkpoint 接续。一次真实修复是：情景记忆按更新时间排序会让 MySQL 排序大型 JSON 快照，触发 `Out of sort memory`；现在先走 `(workspace_id, status, updated_at)` 索引取得 ID，再按主键读取快照。

### 练习

画出时序：浏览器 → API → MySQL → Celery/Redis → worker → Redis Streams/SSE → 审核 → checkpoint 恢复。

### 提交物

- 一张时序图；
- 90 秒讲解 MySQL 问题的 STAR 故事。

---

## Day 7｜安全、降级、评测：可信不是口号

### 阅读

- `docs/acceptance-report.md`
- `docs/failure-recovery.md`
- `docs/evaluation-dataset.md`
- `app/core/auth.py` 与 SSRF 相关服务

### 必须掌握

- JWT/RBAC：operator、reviewer、admin 分离；所有查询按 workspace 限定；
- SSRF：仅 HTTP(S)，拒绝内网/loopback/link-local、限制重定向、端口和响应体；
- 提示注入：网页/PDF/记忆视为不可信数据；模型只接收受控问题、范围、证据包和允许 ID；
- 降级：无 Key、429、超时或模型 JSON 异常不会阻断任务，会留下 trace/errors；
- 评测：48 问题/96 Evidence 标注是静态离线回归基线，不等于真实零售效果；当前已验证关键测试 8 passed。

### Day 7 自测（20 分钟）

脱稿各用 60 秒回答：

1. 业务痛点；2. Agent 与 workflow；3. RRF；4. 审批式记忆；5. 异步恢复；6. 评测边界。

每题按 0–2 分自评：0=说不出，1=能说概念，2=能说项目具体实现。低于 9 分则先补 Day 1–6，不进入下一阶段。

---

## Day 8｜亲手跑完整任务与接口走读

### 操作

1. 启动 Docker Compose，打开 `http://localhost:5174/`；
2. 用固定暴雨+饮料促销问题创建一个无 Key 任务；
3. 打开 `http://localhost:5174/api/docs`，找到任务创建、结果、证据、记忆、决策、审核、事件流接口；
4. 在任务结束后按顺序查看 `GET /tasks/{id}`、`/result`、`/evidence`、`/memory`、`/review`。

### 提交物

写一张状态变化表：`queued → running → completed → awaiting_review → approved/rejected`，并注明每一步由谁触发。

---

## Day 9｜控制台彩排

按 `docs/demo-recording-script.md` 不录制彩排一次，必须展示：

1. 业务问题与边界；2. Agent 工具动作；3. 证据/RRF/Evidence ID；4. 三策略；5. HITL；6. 审批式记忆；7. 模型/Tavily 状态、成本与降级。

### 提交物

录一段屏幕或语音彩排；标注卡顿、说不清、等待任务的地方。

---

## Day 10｜四张源码面试讲解卡

每张卡只有四栏，禁止逐行复述代码：

| 文件 | 输入 | 核心处理 | 输出 | 失败/边界 |
| --- | --- | --- | --- | --- |
| `app/agent/graph.py` | `ResearchState` 和 checkpoint | 编排 Agent、检索、风险、报告节点及条件边 | 下一个状态节点 | 步数/搜索预算耗尽后结束研究 |
| `app/agent/nodes/workflow.py` | 问题、Observation、scope | schema 校验工具选择、并行检索、RRF/rerank | 证据、动作、遥测 | 非法/异常模型输出走确定性策略 |
| `app/services/retrieval.py` | 查询与 scope | 内部/公开/记忆候选召回、过滤、融合 | 来源与排序候选 | 失败记录，不拿猜测补证据 |
| `app/services/decision.py` | RiskEvent 与约束 | 参数化、Monte Carlo、OR-Tools、策略比较 | 三策略与推荐 | 不可行时显式解释 |

### 提交物

完成四张卡，并随机抽一张讲 2 分钟。

---

## Day 11｜简历与三个 STAR

使用 `docs/job-interview-kit.md` 的 AI 应用工程师简历版；不删掉限制条件。

必须练熟的故事：

1. 为什么不是普通 RAG；
2. 如何定位和修复 MySQL `Out of sort memory`；
3. 为什么不让 Agent 自动下单。

### 提交物

- 一份简历项目段落；
- 三段 90 秒 STAR 录音。

---

## Day 12｜15 张问答卡模拟

使用 `docs/interview-flashcards.md`。每题先给 15 秒结论，再给 45 秒实现和边界。不会的问题记到“薄弱点”，不要死背完整答案。

### 提交物

- 完成 15 题；
- 选最低分的 5 题重录一次。

---

## Day 13｜正式录屏

按 `docs/demo-recording-script.md` 录 5–7 分钟。优先无 Key 稳定路径；若已配置外部能力，再展示远程调用和成本卡。录像前关闭敏感环境变量和无关窗口。

### 提交物

- 一版 MP4；
- 复盘：最有说服力的 1 段、最需要重录的 1 段、下次要删的 1 段。

---

## Day 14｜45 分钟终局模拟

| 时长 | 环节 | 验收标准 |
| --- | --- | --- |
| 2 分钟 | 项目介绍 | 用户、痛点、价值、边界完整 |
| 7 分钟 | Demo | 不依赖自由发挥，证据—策略—审核链清楚 |
| 20 分钟 | 技术追问 | Agent、RAG、记忆、异步、决策各至少一题 |
| 10 分钟 | 系统设计 | 能说明并行边界、持久化、降级、权限与扩展方向 |
| 6 分钟 | 反问 | 询问团队 Agent 评测、线上可观测性、业务验证方式 |

### 最终交付物

- 简历项目段落；
- 2 分钟项目介绍；
- 5–7 分钟 Demo 视频；
- 15 张问答卡的个人薄弱点标记；
- 一页“下一步改进”：缓存/并发、真实历史回放、影子评估、多 SKU 是未来路线，不能伪装为当前能力。
