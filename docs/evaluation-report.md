# 冻结模拟语料离线评测报告

运行版本：StoreFlow v2 frozen simulated corpus。运行方式：`GET /api/tasks/evaluations/run`。该报告只描述仓库内 48 个模拟问题、96 条金标 Evidence、12 条同义问法、48 条无关干扰资料，以及 12 条跨维度干扰和 12 条冲突资料（共 168 文档）上的确定性离线结果；不代表真实企业数据、线上流量或模型效果。

| 检索策略 | Recall@8 | MRR | NDCG@8 | Precision@8 |
| --- | ---: | ---: | ---: | ---: |
| BM25 | 0.7812 | 0.8693 | 0.7246 | 0.1953 |
| 哈希向量 | 0.4688 | 0.4393 | 0.3664 | 0.1172 |
| RRF + 本地 rerank | 0.6875 | 0.8177 | 0.6504 | 0.1719 |

| 检索策略 | 同义问法 Recall@8 | 同义问法 MRR | 同义问法 NDCG@8 | 同义问法 Precision@8 |
| --- | ---: | ---: | ---: | ---: |
| BM25 | 0.6667 | 0.7639 | 0.6156 | 0.1667 |
| 哈希向量 | 0.2500 | 0.0799 | 0.1197 | 0.0625 |
| RRF + 本地 rerank | 0.6250 | 0.5417 | 0.4922 | 0.1562 |

在这个更难的冻结模拟语料上，BM25 是当前最佳基线；RRF + 本地 rerank 并未全面优于它。项目保留这一结果而不选择性宣传“融合必然提升”。可在明确配置百炼 Key 后运行 `scripts/run_evaluation.py --bailian`，将真实 embedding/rerank 的**离线模拟**结果附加到 JSON 报告；未运行前不对其效果作数值声明。真实资料评测仍需固定历史快照、模型版本和人工相关性标注。

四维明细与可复跑 JSON 由评测接口返回；实现见 `app/services/evaluation.py`。

## Agent trajectory 离线评测（2026-08-29）

运行方式：`run_agent_trajectory_evaluation()`。评测实际执行受限 Manager、动作持久化、工具分发、证据解析/评分、调查评估与决策前置条件；为保证可重复且不产生外部调用，证据采集通道固定为仓库内 `SAMPLE_SOURCES`，不调用远程模型、Tavily、Qdrant 或 MySQL。

| 指标 | 实测结果 |
| --- | ---: |
| 冻结模拟调查 case | 2 |
| Task Success | 100.0% |
| Tool Selection Accuracy | 80.0% |
| Unnecessary Tool Rate | 20.0% |
| 平均步骤数 | 5.0 |
| 平均检索次数 | 1.0 |
| Citation Validity | 100.0% |
| Constraint Pass Rate | 100.0% |

该结果刻意保留非满分的工具选择准确率和额外工具率：固定 Demo scope 之外的 case 仍完成受控调查与审核，但多执行了决策/审核动作。这是当前实现的真实离线结果，不代表真实企业流量或线上模型效果。
