### MEM-INC-028 | 出站 URL 守卫默认拒绝回环地址——本地联调必须走独立开关，不能削弱守卫

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-27`
- **适用范围**：项目 `DDP` | 环节 `出站安全/本地联调` | 批次/资产 `backend/app/infrastructure/adapter_url_guard.py` | 密级 `INTERNAL`
- **核心结论**：Webhook 投递地址是**用户提供的外部输入**，出站前必须过 `ensure_safe_adapter_url`（拒绝环回/私网/链路本地/云元数据）。这导致**本地联调投不到本机接收服务**。正确解法是新增**独立的本地例外开关**（默认 false），**不是**放宽守卫、也**不是**在守卫里为某个端口开后门。
- **关键依据与细节**：
  - 实测（2026-11-27）：`ensure_safe_adapter_url("http://127.0.0.1:12345/hook")` → `AdapterHttpFailure: 地址 127.0.0.1 解析到环回地址 127.0.0.1，已拒绝`。
  - 修法：新增 `DDP_WEBHOOK_OUTBOUND_ALLOW_PRIVATE`（默认 `false`），传 `allow_private=True` 给守卫；该开关**独立于**训练/模型 Adapter 的同名开关（出站面不同，共用会让「为本地联调放开 Webhook」顺带放开训练网关，违反最小授权）。
  - 演练脚本 `scripts/ops/webhook_outbound_drill.py` 显式置 true 并在文档里标注「生产必须 false」。
  - 同族：`DDP_TRAINING_ADAPTER_ALLOW_PRIVATE` / `DDP_MODEL_ADAPTER_ALLOW_PRIVATE` 是同一模式——**按出站面分别**开关，不共用。
- **影响与后续动作**：
  - 新增任何出站面（下游回调、第三方 webhook、镜像同步）时：默认 fail-closed；需要本地例外时**新增独立开关**，并写明「生产必须 false」。
  - 安全相关的守卫不得为了让测试通过而放宽——测试用本地接收服务时应显式打开例外开关。

#### 工程扩展字段

- **check_ref**：`backend/app/core/config.py`（`webhook_outbound_allow_private` 声明与自检）；守卫实现在 `backend/app/infrastructure/adapter_url_guard.py`
- **recurrence**：`1`
- **tags**：ssrf,url-guard,outbound,local-dev-exception,least-privilege
