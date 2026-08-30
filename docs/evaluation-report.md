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

## Agent trajectory 离线评测（2026-08-30）

运行方式：`.venv\\Scripts\\python.exe -c "from app.services.evaluation import run_agent_trajectory_evaluation; print(run_agent_trajectory_evaluation())"`。评测实际执行单 Manager、动作持久化、状态机、工具分发、证据解析/评分、确定性运营分析、调查评估、决策守卫和人审交接；仅将网络/数据库采集替换为 `sample_data/agent_trajectory_cases.json` 的冻结 fixture，不调用远程模型。

24 个具名 case 分为：证据充分 6 个、缺失维度并定向补证 6 个、配送来源冲突并补证 6 个、预算/不可行约束/固定 scope 等受控边界 6 个。崩溃恢复指标实际执行持久化的 `running → unknown` 恢复转换；HITL 指标核验每条完成决策都进入预期审核交接。

| 指标 | 实测结果 |
| --- | ---: |
| 冻结模拟调查 case | 24 |
| Task Success | 100.00% |
| Tool Selection Accuracy | 100.00% |
| Focus Accuracy | 100.00% |
| Evidence Sufficiency | 100.00% |
| Unnecessary Tool Rate | 0.00% |
| 平均步骤数 | 6.00 |
| 平均检索次数 | 1.50 |
| Citation Validity | 95.83% |
| Constraint Pass Rate | 100.00% |
| HITL Resume Success | 100.00% |
| Crash Recovery Success | 100.00% |

Citation Validity 未写成满分：一个预算耗尽的冻结场景只含确定性运营异常，未生成文本 Evidence ID；该指标因此为 23/24。所有指标均为固定模拟数据、无远程模型的离线工程行为，不代表企业生产收益、线上模型效果或真实业务准确率。逐 case 的预期动作、focus、预算和断言在 `sample_data/agent_trajectory_cases.json`，运行接口/函数会返回实际轨迹。
