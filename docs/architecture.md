# StoreFlow 架构与状态机

```mermaid
flowchart TD
  A[POST /tasks] --> B[initialize]
  B --> X[LLM 选择白名单工具]
  X --> Y[代码执行工具并写入 Observation]
  Y --> X
  X -->|finish 或预算耗尽| C[plan_research]
  C --> D[并行 Fan-out：内部资料 / 近期公开风险 / 已批准记忆]
  D --> E[Fan-in：四路 RRF 融合与精排]
  E --> F[parse_sources]
  F --> G[score_evidence]
  G --> H{coverage 足够?}
  H -->|否| I[replan]
  I --> D
  H -->|是或预算耗尽| J[extract_events]
  J --> K[generate_report]
  K --> L[completed]
  L --> M[POST decision]
  M --> N[awaiting_review]
  N -->|approve| O[approved + final_report]
  N -->|modify constraints| M
  N -->|need more evidence| C
  N -->|reject| P[rejected]
```

MySQL 持久化任务状态、checkpoint、审计记录与决策结果；Redis 缓存 URL 内容；Qdrant 存储内部 PDF 和向量检索载荷。每个节点写入 trace；失败写入 errors 并以受控降级继续，不生成无证据 RiskEvent。

其中只有互不依赖、只读的三条证据通道会并行：内部资料、近期公开风险和已批准长期记忆。它们完成后再统一执行 RRF 融合、重排序和上下文压缩；仿真、策略选择和人工审核保持顺序执行，确保约束与审计链可复现。
