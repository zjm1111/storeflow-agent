# StoreFlow 使用手册

StoreFlow 为区域采购负责人生成**单门店、单 SKU、单补货周期**的采购建议草案。它不自动下单。

## 启动与入口

```powershell
cd C:\Users\17818\Desktop\codex_learn\agent_program\supplymind
& 'C:\Users\17818\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe' compose up --build -d
```

打开 `http://localhost:5174/`；API 文档为 `http://localhost:5174/api/docs`。使用 `docker compose ps` 检查 `api`、`web`、`worker`、`mysql`、`redis` 和 `qdrant`。

## 固定演示流程

1. 导入 `output/pdf/storeflow_demo_operations_pack.pdf`，或使用控制台“本地知识库”上传门店资料。
2. 创建问题：`本周末暴雨叠加饮料促销，上海浦东门店当前库存只够 1.5 天，应订多少？`
3. 填写 scope：区域“上海浦东”、门店“浦东示范店”、品类“瓶装饮料”、SKU“BEV-500ML”；填写库存、日均销量、促销、中央仓提前期与预算等约束。
4. 在“Agent 运行轨迹”查看白名单工具选择；在“证据中心”查看并行通道、RRF、精排及 Evidence ID。
5. 在“策略比较”查看正常订货、适度加订、高保障加订的成本、服务水平、缺货概率和 CVaR。
6. 由 reviewer/admin 批准、改约束、要求补证或拒绝。只有批准后的候选记忆才能被沉淀并在新任务中召回。

## 角色与降级

- `operator`：创建、查看任务，上传内部资料。
- `reviewer`：另可审核决策、审批记忆。
- `admin`：完整演示权限和评测接口。

未配置 Key 时使用确定性演示链路；百炼、Tavily 失败不会中断任务，会在 trace/errors 中显示降级。JWT 模式下控制台以 fetch stream 携带 Bearer 令牌订阅 SSE。

## 运行可观测性

任务概览中的“模型与成本”卡会显示远程/确定性调用模式、token、远程耗时和成本估算；“检索与降级”卡会显示并行检索耗时、Tavily 返回证据数量与最近的降级原因。成本费率默认是未配置状态，页面会明确显示“费率未配置”，不会把未知费用伪装成零成本。若近期证据与已批准的长期经验冲突，卡片会显示“历史记忆待复核”；旧经验不会自动覆盖当天的天气、促销或配送证据。

## 边界

系统不能连接 ERP、创建采购单或扣减库存。输出必须由企业授权负责人结合真实库存、采购与财务流程复核。
