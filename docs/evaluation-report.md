# StoreFlow 离线评测基线

当前回归数据集含 48 个静态 StoreFlow 问题：配送到货、门店库存、促销需求、成本风险各 12 个；每题两条金标准证据，共 96 条。详见 [评测集说明](evaluation-dataset.md)。

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_evaluation.py -q
```

结果用于校验数据完整性、风险事件类型、引用结构和决策复现性。它不是企业真实数据上的 Recall、NDCG 或线上效果声明。
