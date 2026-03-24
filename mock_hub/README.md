# Coterm Mock Hub

最小可联调的 Hub mock，提供：

1. `ws://127.0.0.1:8765/ws/cli`
2. `ws://127.0.0.1:8765/ws/client`
3. `http://127.0.0.1:8080/api/v1/...`

启动：

```bash
cd mock_hub
python -m coterm_mock_hub.main
```

典型联调步骤：

1. `POST /api/v1/sessions` 创建 session
2. 用返回的 `session_id` 启动 CLI，并连接 `/ws/cli`
3. `POST /api/v1/sessions/{session_id}/messages` 下发用户消息
4. 如有审批，调用 `POST /api/v1/permissions/{request_id}/decision`
