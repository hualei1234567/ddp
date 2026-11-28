# 2026-11-28 A 级真机硬件 Pilot 软件接入就绪与 Edge Agent 联调证据

> **诚实边界声明**：
> - 本次验证运行于**无物理机器人硬件**环境，当前真机采集状态仍严格保持 **`HARDWARE_CAPTURE_NOT_TESTED`**。
> - 本目录记录的是**软件侧最后一公里联调基线**（`SOFTWARE_INTEGRATION_BASELINE`），证明 Edge Agent 端到端协议、断点续传、版本乐观锁与后端真实 API 路由已彻底打通；**不冒充 A 级真机 E1 硬件证据**。
> - 真机到位后，直接按 [hardware-pilot-runbook.md](../../../ops/hardware-pilot-runbook.md) 使用 `scripts/e2e/pilot_edge_chain.py --live-url ...` 实跑并新建 `hardware-pilot-e1` 证据目录。

---

## 1. 本次审计发现并修复的真实缺陷（Edge Agent）

在将 `services/edge-agent` 与真实 FastAPI 后端做端到端联调时，定位并修复了 4 处真实缺陷与缺口（均已补充回归测试）：

| # | 位置 | 缺陷 / 缺口 | 实际影响 | 修复措施 |
|---|---|---|---|---|
| 1 | `services/edge-agent/edge_agent/upload/driver.py` | `_complete_artifact()` 直接使用建会话时的 `cursor.session_version`（`1`）发送 `If-Match`，未在 `ack_part` 后刷新最新 `session.version` | **真实后端 100% 失败**：真实 DDP 每次 `ack_part` 都会递增 `UploadSession.version`，导致 `complete` 必然撞 `409 version_conflict` 并把 Spool 条目永久隔离（此前所有 mock/fake 测试均未递增 `version`，掩盖了此缺陷） | 在 `_complete_artifact()` 调用 `complete_session` 前先 `get_session()` 刷新最新 `version`；并在 `tests/test_http_e2e_upload.py` 强制模拟 `ack` 递增 `version` 与 `If-Match` 校验 |
| 2 | `services/edge-agent/edge_agent/upload/http_gateway.py` | `upload_part()` 收到预签名 PUT `400/403` 时抛 `GatewayTransientError` 而非 `SignedUrlExpiredError`；收到 `429/5xx` 时抛 `GatewayPermanentError` | 传输途中签名过期时 `_call_with_retry` 盲目重试同一过期 URL，永不触发 `_put_part_with_resign` 重签；对象存储短暂 503 会直接把条目打成 `FAILED` | `400/403` 改抛 `SignedUrlExpiredError`；`429/5xx` 改抛 `GatewayTransientError`；补充真实 403 途中重签 E2E 回归测试 |
| 3 | `services/edge-agent/edge_agent/upload/http_gateway.py` | `HttpGatewayConfig.verify_tls=False` 未传递给 `urllib.request.HTTPSHandler` | 内网自签证书环境下关闭 TLS 校验的配置被静默忽略 | 新增 `_build_opener(verify_tls=...)` 实际装配 `SSLContext` 并补充单测 |
| 4 | `services/edge-agent/edge_agent/control_plane.py` & `pyproject.toml` | 缺少 V31-011 Edge Agent 控制面客户端（`pair`/`heartbeat`/`leases`/`assignments`）；`pyproject.toml` 缺 `pythonpath = ["."]` 导致直接运行 `pytest` 报 `ModuleNotFoundError` | 硬件工作站无法通过 `edge_agent` 完成配对、心跳和 Lease 管理 | 新增零外部依赖标准库客户端 `edge_agent/control_plane.py` 及 8 项协议/错误映射单测；补齐 `pyproject.toml` |

---

## 2. 架构边界说明（供硬件上机与后续迭代参考）

实测确认当前后端存在明确的双令牌鉴权边界：
- **控制面**（`/api/v1/edge-agents/{agent_id}/heartbeat|leases|assignments`）：仅接受配对签发的 `ddp_agt_...` Agent Token（`backend/app/api/v1/edge_agent_dependencies.py`）。
- **上传数据面**（`/api/v1/projects/{project_id}/upload-sessions*`）：当前使用 `WriteScope`（项目写权限 JWT），尚未开放 `ddp_agt_...` 直连。

`scripts/e2e/pilot_edge_chain.py` 与 `docs/ops/hardware-pilot-runbook.md` 已明确支持并文档化双凭据运行方式，真机到位即可无阻塞上机；如需统一为单 `ddp_agt_` 令牌直传，需单独走 System Architect 契约与安全评审。

---

## 3. 实测验证结果

| 验证项 | 命令 | 结果 | 证据文件 |
|---|---|---|---|
| Edge Agent 全量单测（含新增控制面、403 途中重签、版本推进回归） | `cd services/edge-agent && pytest -v` | **76 passed** | 终端实测 |
| 端到端 Pilot 联调脚本测试（CLI 校验 + TestClient 全链路） | `pytest -v scripts/tests/test_pilot_edge_chain.py` | **4 passed** | 终端实测 |
| 端到端软件联调基线输出（配对→心跳→Lease→Spool→3 分片上传→RawAsset→会话关联→释放 Lease） | `python scripts/e2e/pilot_edge_chain.py --output docs/testing/evidence/2026-11-28-pilot-edge-readiness/software-baseline-result.json` | **PASSED** (`parts_uploaded=3`, `linked_to_capture_session=true`, `hardware_capture_status=HARDWARE_CAPTURE_NOT_TESTED`) | [software-baseline-result.json](./software-baseline-result.json) |
| 代码风格与安全检查 | `ruff check` / `ruff format --check` / `python3 scripts/check_secrets.py` | **0 errors** | 终端实测 |

---

## 4. 下一步（真机到位即执行）

1. 准备一台物理单臂机器人（推荐 SO-101）+ 1 路 RGB 相机 + 工作站；
2. 按 [hardware-pilot-runbook.md](../../../ops/hardware-pilot-runbook.md) 签发 Pairing Token 并运行 `scripts/e2e/pilot_edge_chain.py --live-url ...`；
3. 完成 5 项上机验收清单并将真机证据归档至 `docs/testing/evidence/<日期>-hardware-pilot-e1/`。
