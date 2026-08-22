# StoreFlow 求职项目材料

> 表述原则：以下内容只覆盖已实现、已在本机 Docker 环境验收的能力。StoreFlow 使用模拟门店资料与公开信息，不连接真实 ERP，也不宣称线上采购收益、生产 SLA 或多 SKU 联合优化。

## 一、简历可直接使用的版本

### 项目名称

**StoreFlow｜连锁零售补货风险决策 Agent**

### 一句话项目描述

面向区域采购负责人的受限自治 Agent：在促销、天气、中央仓延迟和销量波动下，检索并核验风险证据，比较三种单门店、单 SKU、单周期订货草案，并通过人工审核沉淀可追溯经验。

### 技术栈

Python、FastAPI、LangGraph、Celery、Redis Streams、MySQL、Alembic、Qdrant、百炼（Qwen / embedding / rerank）、Tavily、Monte Carlo、OR-Tools、React、TypeScript、SSE、JWT/RBAC、Prometheus、Docker Compose。

### 推荐简历条目（AI 应用工程师，4 条）

- 独立构建 StoreFlow 连锁零售补货风险决策 Agent：以 LangGraph 实现“选择下一步—白名单工具调用—观察—再规划”的受限 ReAct 循环；设置最多 6 步、最多 2 次外部检索及 JSON/超时确定性降级，禁止下单、写库存和任意 URL 调用。
- 搭建内部 PDF 向量/BM25、已批准长期记忆与 Tavily 风险信息的并行 Fan-out 检索；以 RRF 融合、重排序和 Evidence ID 绑定的上下文压缩完成 Fan-in，支持来源冲突待裁决与原文回溯。
- 将补货建议从 LLM 文本生成拆分为固定种子 Monte Carlo + OR-Tools 的确定性决策层，输出正常订货、适度加订、高保障加订三策略的预期成本、缺货概率、服务水平、CVaR 和约束可行性。
- 实现 Celery + Redis Streams 异步任务、MySQL checkpoint 的可恢复 HITL、仅审批后写入的长期记忆，以及 React 控制台的检索漏斗、调用成本/降级、记忆冲突与审核约束 diff；在 Docker Compose 下完成无 Key 降级、外部检索、worker 恢复和 48 题/96 Evidence 金标回归验收。

### 后端开发岗位的压缩版（3 条）

- 基于 FastAPI、Celery、Redis Streams、MySQL/Alembic 和 Docker Compose 构建可恢复的异步 Agent 服务；任务采用幂等键、checkpoint 与 SSE 事件续传，失败可降级并保留审计轨迹。
- 设计工作记忆、情景快照和审批式长期记忆模型，所有业务数据按 workspace 过滤；采用 JWT/RBAC、SSRF 白名单和 Prometheus 指标建立演示级安全与可观测边界。
- 解决情景记忆查询对大型 JSON 快照排序导致的 MySQL `Out of sort memory`：新增 `(workspace_id, status, updated_at)` 索引，并改为索引取 ID 后按主键读取快照，重新完成无 Key 回归。

### 30 秒自我介绍中的项目说法

“我做了一个叫 StoreFlow 的零售补货风险决策 Agent。它不是让大模型直接说订多少，而是先让受限 Agent 决定需要补哪些证据，再用混合 RAG 形成带 Evidence ID 的证据包，最后由固定随机种子的仿真和优化比较三种订货方案。高影响建议必须人工审核，审核后才能沉淀长期经验。这个项目重点展示了 Agent 控制边界、RAG 可追溯性、异步可恢复服务和决策可复现性。”

## 二、项目结果与可核验事实

| 结果 | 已验证事实 | 不应夸大的部分 |
| --- | --- | --- |
| 全链路 | 可演示“上传资料 → 动态补证 → 风险/策略 → 人审 → 记忆 → 新任务复核” | 不是 ERP 下单系统 |
| Agent | 白名单工具、受限 ReAct、Plan-Execute-Replan，最长 6 步 | 不是无限自主 Agent |
| RAG | 并行候选、RRF、精排、上下文预算和 Evidence ID | 未声称线上 Recall/NDCG 指标 |
| 决策 | 三种策略、Monte Carlo、OR-Tools、CVaR 与约束解释 | 仅单门店/单 SKU/单周期 |
| 验收 | 静态 48 问题 / 96 条 Evidence 标注；关键测试 8 passed | 不代表真实零售收益 |
| 韧性 | 无 Key 确定性降级约 5.9 s；worker 恢复任务约 1.5 s | 外部百炼/Tavily 任务约 92–116 s，仍需优化延迟 |

详细命令、环境和验收日志见 [验收报告](acceptance-report.md)。

## 三、STAR 面试故事

### 1. 核心故事：为什么不是普通 RAG

- **S（情境）**：补货场景中，采购负责人同时面对库存不足、促销、天气和配送延迟。普通问答 RAG 能给摘要，却无法解释“为什么订这个量”、也无法保证模型不会跳过证据直接给结论。
- **T（任务）**：构建一个能自主发现证据缺口、又有明确权限与人工边界的决策 Agent，并把最终的数值决策固定在可复现算法中。
- **A（行动）**：我用 LangGraph 做受限 ReAct 与 Plan-Execute-Replan。模型只输出经过 schema 校验的 `{tool, reason}`；控制器实施最多 6 步、最多 2 次外部检索与只读工具白名单。并行检索后以 RRF/重排收敛到 Evidence ID 证据包；Monte Carlo + OR-Tools 统一比较三种策略；人工审核通过后才产生长期业务记忆。
- **R（结果）**：完成了 Docker 化端到端闭环，并且无 Key 与外部能力失败时仍保留确定性链路和审计。项目清晰地把“LLM 判断下一步”与“代码保证权限、数值与可复现性”分开。

### 2. 工程故事：定位并解决 MySQL 排序内存问题

- **S**：无 Key 验收中，情景记忆检索偶发 MySQL `Out of sort memory`，会破坏 Agent 的降级演示。
- **T**：既不能粗暴增大数据库内存，也不能丢掉任务快照的情景检索能力。
- **A**：检查查询后发现按 `updated_at` 排序会让 MySQL 搬运大型 JSON 任务快照。我为 `(workspace_id, status, updated_at)` 新增复合索引，查询改为先按索引得到任务 ID，再按主键回表读取快照。
- **R**：重新运行无 Key 验收通过；该修复也说明我会从数据模型与查询路径，而不是只从应用层重试，处理 Agent 系统的稳定性问题。

### 3. 取舍故事：为什么不让 Agent 自动下单

- **S**：补货动作直接影响库存、现金和损耗；输入资料可能过期，外部检索也可能失败或冲突。
- **T**：要展示 Agent 能力，同时不能把不可靠模型输出包装为企业自动化。
- **A**：限制 Agent 为只读证据与仿真工具；将下单能力排除在工具集外；在决策后插入持久化 HITL，提供批准、改约束、补证和拒绝四条恢复路径，并把冲突记忆标为待复核。
- **R**：输出是可审核的采购草案，而不是假装生产可用的自动采购系统；这也是我在高影响 AI 应用中最强调的控制边界。

## 四、高频追问与回答要点

| 问题 | 建议回答 |
| --- | --- |
| 这是 workflow 还是 Agent？ | 两者兼有：研究层是受限 ReAct + Plan-Execute-Replan Agent，确定性决策与 HITL 是工作流。这样既保留动态补证，又把高风险动作锁在可审计控制器中。 |
| ReAct 用在哪里？ | 模型在每轮依据已有 Observation 选择下一个白名单工具；代码执行后写 Observation，再让模型决定是否继续。不会存储或展示自由式思维链。 |
| RRF 的价值？ | 不同通道的分数不可直接比较。RRF 根据各通道排序融合，避免某一向量分数尺度压制 BM25、记忆或公开风险；后续再重排。 |
| 为什么还要 Monte Carlo/OR-Tools？ | LLM 擅长理解证据与选择工具，但不适合稳定计算高影响订货量。仿真处理需求/到货不确定性，优化层保证预算、服务水平等约束可解释、可复现。 |
| 长期记忆为什么要审批？ | 网页和模型草稿不等于业务规则。只有审核后，带 Evidence ID、适用 scope、有效期和审核人的经验才能跨任务复用。 |
| 外部模型慢怎么办？ | 已限制模型工具选择最多 2 次、外部检索最多 2 次，默认关闭非必要扩写；进一步可做缓存、并行 I/O、模型分级与异步通知。不能把当前 92–116 秒外部模式延迟说成生产级。 |

## 五、投递前检查清单

- 使用“受限工具调用与动态补证 Agent”，不要写“全自动采购”或“生产级智能体”。
- 简历中保留单门店、单 SKU、单周期边界；不要写多 SKU 优化。
- 面试演示优先使用无 Key 模式，外部 Key 模式仅作为已配置时的增强演示。
- 录屏前运行 [Demo 脚本](demo-recording-script.md) 与 [验收报告](acceptance-report.md) 中的关键命令。
