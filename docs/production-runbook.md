# 生产运行手册

## 必要配置

复制 `.env.example` 为部署环境专用的 `.env`，并替换所有数据库密码。必须设置：

- `ENVIRONMENT=production`
- 强随机 `MYSQL_PASSWORD`、`MYSQL_ROOT_PASSWORD`
- 公开站点的精确 `FRONTEND_ORIGINS` 与 `TRUSTED_HOSTS`
- 高熵 `API_KEY`；调用受保护 API 时传递 `X-API-Key`
- `RATE_LIMIT_PER_MINUTE`（默认 120；设为 `0` 可在受控测试环境禁用）

不要提交 `.env`、API Key 或数据库密码。将密钥放在宿主机的秘密管理系统、Docker secrets 或云平台的密钥服务中。

## 启动与检查

```powershell
docker compose up --build -d
Invoke-RestMethod http://127.0.0.1:5173/health
Invoke-RestMethod http://127.0.0.1:5173/api/ready
python scripts/nginx_smoke.py
```

Nginx 是唯一的宿主机入口：它提供 SPA 静态文件，反向代理 `/api`，并处理压缩、缓存、基础安全响应头与上传上限。MySQL、Redis、Qdrant 与 API 都不向宿主机暴露端口；当前本机作品集方案不启用 TLS。

## 运维基线

- 监控 `/health`（进程存活）与 `/ready`（任务持久化可用）。
- 以 `X-Request-ID` 关联访问日志与故障排查。
- 定期备份 MySQL 卷；恢复演练应验证任务、审核审计与决策记录。
- 为 API 关键路径配置 5xx、延迟、任务失败率和数据库不可用告警。
- 在发布前运行 `pytest -q` 和 `GET /tasks/evaluations/run`。
