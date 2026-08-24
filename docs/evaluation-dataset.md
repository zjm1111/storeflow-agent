# StoreFlow 静态评测集（v1）

评测数据位于 `sample_data/evaluation_cases.json`，是仓库内直接可审阅、可版本管理的 JSON 标注集，不在程序运行时生成。

- 48 个问题：配送到货、门店库存、促销需求、成本风险各 12 个；
- 96 条独立金标准证据标注：每题两条，包含 `evidence_id`、预期来源和可核验 claim；
- 每题给出预期风险类型、离线基线预测类型与引用有效性标记；另有 12 条同义问法、12 条跨维度干扰资料与 12 条带时间/版本说明的冲突资料；加载时会确定性派生 96 条金标 passage、48 条无关干扰和 24 条 challenge 文档，形成 168 文档的冻结模拟语料；
- 范围固定为单区域 / 单门店 / 单 SKU / 单补货周期的模拟资料，不能被表述为真实零售企业数据。

加载器会校验 48/96 的数量、四类各 12 题、Evidence ID 唯一性。修改标签时应同步运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_evaluation.py -q
```

`GET /api/tasks/evaluations/run` 会在该冻结模拟语料上比较本地 BM25、哈希向量、RRF + 本地 rerank，输出主问题和同义问法上的宏平均 Recall@8、MRR、NDCG@8、Precision@8 以及四个风险维度明细。它不调用百炼、Tavily 或真实企业资料，结果只能表述为“冻结模拟语料上的离线确定性评测”，不能推断线上企业效果。

若明确配置百炼并接受外部调用成本，可主动运行：

```powershell
.\.venv\Scripts\python.exe scripts\run_evaluation.py --bailian --max-queries 12
```

这会使用 `text-embedding-v4`，并在配置 `BAILIAN_RERANK_BASE_URL` 时增加 `qwen3-rerank` 比较。它默认只跑 12 个主问题；设为 `--max-queries 0` 才跑完整 48 题。远程结果仍只是该冻结模拟语料的离线结果，不能写成企业线上 Recall 或业务收益。
