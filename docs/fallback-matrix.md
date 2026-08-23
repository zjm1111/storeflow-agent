# StoreFlow Fallback Matrix

所有降级都会写入任务 `errors`、Agent Trace 或依赖可观测字段；系统只交付带边界的建议草案，不执行 ERP、下单或库存写入。

| 组件 | 故障或未配置 | 降级行为 | 可观测字段 |
| --- | --- | --- | --- |
| 百炼 LLM | Key 缺失、超时、JSON 异常 | Manager 使用确定性高层动作；不保存思维链 | `model_execution`、`errors` |
| 向量检索 | Qdrant 不可用 | 保留 BM25/fixture 证据并标记检索降级 | `parallel_retrieval`、`errors` |
| Rerank | 远程模型不可用 | 使用本地确定性 rerank | `hybrid_results`、`errors` |
| Tavily | Key 缺失、429、5xx 或 URL 被 SSRF 策略阻断 | 不阻断；内部资料与记忆继续，外部证据数量为零 | `dependency_execution.tavily`、`errors` |
| 长期记忆 | 无 scope 命中或索引不可用 | 仅无记忆先验，不把网页或草稿写入长期记忆 | `recalled_memories`、`memory_conflicts` |
| Monte Carlo | 计算异常 | 拒绝产生数值建议，进入人工审核并标记失败 | `decision`、`errors` |
| OR-Tools | 求解失败或约束不可行 | 展示最大可达服务水平和补救动作 | `constraint_feasible`、`infeasibility_reason` |
| Celery worker | 进程中断 | MySQL 任务快照/checkpoint 用于幂等恢复 | `checkpoint`、任务版本 |
| SSE | 浏览器断线或 JWT Header 限制 | 前端 fetch stream + 2 秒 polling 刷新状态 | Redis Stream ID、任务状态 |
