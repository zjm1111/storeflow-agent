# StoreFlow 项目手册

## 一句话

StoreFlow 是面向连锁零售区域采购负责人的供应链异常调查与补货决策 Agent：先调查和核验证据，再用可复现仿真生成可审核的订货建议。

## 输入、输出与边界

| 输入 | 输出 |
| --- | --- |
| 区域、门店、品类/SKU、时间窗口 | RiskEvent 与 Evidence ID |
| 当前库存、日均销量、促销、中央仓提前期 | 三个订货策略与统一 KPI |
| 预算、最大订货量、服务目标、缺货/持有成本 | 推荐理由、不可行原因和审核动作 |

范围是单区域、单门店、单 SKU、单周期。不会自动执行采购。

## 系统结构

1. LangGraph 单 Manager 受限 Agent 以 `{tool, focus, reason}` 在最多 6 步内动态选择取证、运营分析、调查评估、定向补证、决策与人审；决策只能在调查状态 ready/degraded 后运行。
2. 内部资料、近期公开风险与已批准记忆并行采集；RRF 融合、重排序、Evidence-ID 绑定压缩随后执行。
3. 风险事件输入固定种子 Monte Carlo 与 OR-Tools，比较正常订货、适度加订、高保障加订。
4. LangGraph interrupt + MySQL checkpoint 暂停等待审核；审核通过后才可沉淀长期记忆。

## 主要服务

FastAPI、React、Celery、Redis Streams、MySQL、Qdrant、百炼（可选）、Tavily（可选）由 Docker Compose 启动。历史内部命名仅用于兼容已有数据，不代表产品名称。

## 验证材料

- [架构图](architecture.md)
- [评测静态金标](evaluation-dataset.md)
- [Demo 脚本](demo-runbook.md)
- [故障恢复](failure-recovery.md)

所有结论均以模拟资料和公开风险信息演示，不能解释为企业生产能力或效果指标。
