# StoreFlow 验收报告

> 验收日期：2026-08-22（本机 Docker Compose）  
> 范围：作品集单机演示，不构成生产 SLA、真实企业数据接入或业务效果声明。

## 环境与可用性

- Compose 服务：API、Web、Celery worker、MySQL、Redis、Qdrant；
- Web 健康检查：`GET http://127.0.0.1:5174/health` 返回 `{"status":"ok","service":"web"}`；
- Alembic：`20260822_02 (head)`；
- 控制台/API 网关：`http://localhost:5174/`、`http://localhost:5174/api/docs`。

## 验收结果

| 场景 | 方法 | 结果与耗时 | 结论 |
| --- | --- | --- | --- |
| 无 Key 模式 | 隔离 `compose run`，显式清空百炼与 Tavily Key，直接运行 LangGraph | 容器命令约 5.9 s；任务完成、3 个 RiskEvent、`deterministic-fallback` | 通过 |
| 百炼 + Tavily 闭环 | 上传门店 PDF → 创建风险任务 → 三策略 → 批准并记忆 → 相似任务复核 | 两个 Celery 任务分别 115.6 s、92.5 s；首任务 approved，后续任务 completed；均有远程百炼调用，首任务命中 2 条 Tavily 来源 | 通过；延迟偏高 |
| worker 恢复 | 停止常规 worker 后创建任务，确认 `queued`；启动临时无 Key worker 消费队列，再恢复常规 worker | 临时 worker 处理约 1.5 s；任务最终 `completed`，2 个 RiskEvent | 通过 |
| 异常降级 | 受控检索超时、PDF 解析错误、来源冲突、提示注入和不可行预算的回归 | `pytest tests/test_evaluation.py::test_fault_injections_degrade_safely_and_leave_a_trace tests/test_storeflow_agent.py -q`：7 passed，0.65 s | 通过 |
| 静态评测数据 | 加载 48 题/96 Evidence ID 金标并验证四类均衡和唯一性 | `pytest tests/test_evaluation.py tests/test_storeflow_agent.py -q`：8 passed，0.49 s | 通过 |

## 已发现并修复的问题

本次无 Key 首次验收发现：情景记忆检索按更新时间排序时，MySQL 会将大型任务 JSON 快照放入排序缓冲，触发 `Out of sort memory`。

修复：新增迁移 `20260822_02` 的复合索引 `tasks(workspace_id, status, updated_at)`；情景记忆查询改为先按索引排序取得任务 ID，再逐条按主键读取 JSON 快照。修复后无 Key 验收重新通过。

## 性能与边界

- 外部模式端到端任务明显慢于无 Key 模式。当前约束为 Agent 最多 6 步、最多 2 次外部检索、最多 2 次模型动作选择；可选的计划/报告扩写默认关闭，以避免增加关键路径延迟。
- Tavily 只在结果满足相关性、安全性、去重和来源质量校验后进入证据；未命中时不会阻塞任务。
- 本报告证明本机可重复演示，并不证明吞吐量、P95、并发容量、线上召回率或企业采购收益。

## 可复现命令

```powershell
# 静态回归与故障降级
.\.venv\Scripts\python.exe -m pytest tests\test_evaluation.py tests\test_storeflow_agent.py -q

# 配置百炼/Tavily 后的完整闭环
.\.venv\Scripts\python.exe scripts\v2_demo_smoke.py --base-url http://127.0.0.1:5174/api --timeout 180

# 服务与迁移状态
docker compose ps
docker compose exec -T api alembic current
```
