### MEM-INC-031 | Edge Agent 分片完成未刷新 UploadSession.version 必撞 409 且 403 预签名过期未重签

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-28`
- **适用范围**：项目 `DDP` | 环节 `Edge Agent 真机上传联调` | 批次/资产 `services/edge-agent/edge_agent/upload/` | 密级 `INTERNAL`
- **核心结论**：真实 DDP 后端每次 `ack_part` 都会递增 `UploadSession.version`，但 `UploadDriver._complete_artifact()` 此前直接持有初始 `version=1` 发 `If-Match`，联调真实后端必然撞 `409 version_conflict` 并将 Spool 永久隔离；同时 `http_gateway.py` 在预签名 URL 过期返回 `400/403` 时误抛 `GatewayTransientError` 而非 `SignedUrlExpiredError`，导致永远不会触发重签。
- **关键依据与细节**：
  - 根因：既有单元测试使用 Fake 网关且未模拟 `ack_part` 递增 `version`（Fake 与真实协议漂移）。
  - 修复：`driver.py` 在 `complete_session` 前先 `get_session()` 刷新最新 `version` 再携带 `If-Match`；`http_gateway.py` 将 `400/403` 映射为 `SignedUrlExpiredError`、将 `429/5xx` 映射为 `GatewayTransientError`，并修复 `verify_tls=False` 未注入 `SSLContext` 的缺陷。
  - 双令牌边界提醒：`/api/v1/edge-agents/*` 使用 `ddp_agt_` Agent 令牌，`/api/v1/projects/{id}/upload-sessions*` 使用项目写权限 JWT，上机手册与 `pilot_edge_chain.py` 已显式拆分。
- **影响与后续动作**：
  - 任何客户端/Worker 调用乐观锁端点前，若中间经过多次状态写操作，必须以服务端最新 `version` 构造 `If-Match`，禁止复用创建时的初始版本号。

#### 工程扩展字段

- **check_ref**：`services/edge-agent/tests/test_http_e2e_upload.py`
- **recurrence**：`1`
- **tags**：`edge-agent,optimistic-lock,if-match,version-conflict,409,presigned-url,403,fake-drift`
