# StoreFlow 静态评测集（v1）

评测数据位于 `sample_data/evaluation_cases.json`，是仓库内直接可审阅、可版本管理的 JSON 标注集，不在程序运行时生成。

- 48 个问题：配送到货、门店库存、促销需求、成本风险各 12 个；
- 96 条独立金标准证据标注：每题两条，包含 `evidence_id`、预期来源和可核验 claim；
- 每题给出预期风险类型、离线基线预测类型与引用有效性标记；加载时会确定性派生 96 条金标 passage 与 48 条无关干扰资料，形成 144 文档的冻结模拟语料；
- 范围固定为单区域 / 单门店 / 单 SKU / 单补货周期的模拟资料，不能被表述为真实零售企业数据。

加载器会校验 48/96 的数量、四类各 12 题、Evidence ID 唯一性。修改标签时应同步运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_evaluation.py -q
```

`GET /api/tasks/evaluations/run` 会在该冻结模拟语料上比较本地 BM25、哈希向量、RRF + 本地 rerank，输出宏平均 Recall@8、MRR、NDCG@8、Precision@8 以及四个风险维度明细。它不调用百炼、Tavily 或真实企业资料，结果只能表述为“冻结模拟语料上的离线确定性评测”，不能推断线上企业效果。
