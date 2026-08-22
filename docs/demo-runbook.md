# StoreFlow 端到端 Demo

1. 打开 `http://localhost:5174`，上传 `output/pdf/storeflow_demo_operations_pack.pdf`（或等价的门店库存日报、销量、促销计划、中央仓配送通知）。
2. 创建问题：`本周末暴雨叠加饮料促销，上海浦东门店当前库存只够 1.5 天，应订多少？`
3. 等待 Agent 的只读工具轨迹完成；展示内部资料检索、近期风险补证、Evidence、RiskEvent、Coverage 与降级记录。
4. 展示“正常订货、适度加订、高保障加订”三种方案的成本、缺货概率、服务水平与 CVaR；系统不创建采购单。
5. 在待审状态输入审核意见并选择“修改约束并重优化”，例如 `{"budget":1500}`；确认审计记录与更新后的 KPI。
6. 选择“批准”，展示最终报告、完整 audit trail 与候选长期记忆；再批准候选记忆后，创建相同门店/SKU 的新任务确认它被召回且仍需新证据校验。
7. 打开 `http://localhost:5174/api/tasks/evaluations/run`，展示静态离线评测基线。

演示限制：不要把模型建议直接用于真实采购或库存操作；该 Demo 的所有决策均为单门店、单 SKU、单周期的可重复模拟结果。

## 一键验收

在 Compose 服务健康后，执行：

```powershell
python scripts/v2_demo_smoke.py
```

脚本依次验证：内部 PDF 上传、混合检索与上下文证据包、RiskEvent、三策略决策、LangGraph 人审暂停/批准、批准后长期记忆写入、后续同 scope 任务召回记忆。配置 JWT 时传入 `--token $env:STOREFLOW_TOKEN`。它不会展示 Key、原始网页正文或数据库密码。

默认直接使用 Compose 当前发布的 API 端口 `8001`；若浏览器网关没有被其他本机开发服务占用，也可显式传入 `--base-url http://127.0.0.1:5173/api` 验证 Nginx 路径。
