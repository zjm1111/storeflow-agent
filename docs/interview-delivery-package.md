# StoreFlow 演示交付包

## 业务故事

区域采购负责人需要在上午决定门店订货量。暴雨可能延迟中央仓配送，周末饮料促销会抬高需求，而库存仅够 1.5 天：少订会缺货，多订会产生积压、损耗和资金占用。StoreFlow 不替人下单，而是形成可追溯、可审核的建议草案。

## 交付物

- [Demo 录屏脚本](demo-recording-script.md)：5–7 分钟的镜头、台词与失败预案；
- [架构与状态机](architecture.md)：Agent、并行检索和 HITL 的关系；
- [求职项目材料](job-interview-kit.md)：简历条目、STAR 与追问回答；
- [验收报告](acceptance-report.md)：已验证结果、耗时与明确边界；
- 演示资料：`output/pdf/storeflow_demo_operations_pack.pdf`。

## 演示中的边界

- 只演示单门店、单 SKU、单周期，未实现联合多 SKU 优化。
- 使用模拟资料与公开信息，不连接真实 ERP/WMS/TMS。
- 百炼/Tavily 不可用时使用确定性降级，并保留错误与审计记录。
- 静态评测集为 48 题/96 金标证据，属于回归基线，不代表线上业务效果。
