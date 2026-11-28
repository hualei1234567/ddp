# 硬件 Pilot 上机操作手册（Hardware Pilot Runbook）

> **适用对象**：机器人硬件工程师、现场采集工程师、平台运维人员
> **目标**：将一台物理机器人工作站（推荐 SO-101 / SO-100 / Koch 单臂 + 1 路 RGB 相机 + 关节总线读取）接入 DDP 平台，通过 Edge Agent 完成「配对 → 心跳上线 → 采集会话 Lease → 本地 Spool 落盘 → 断网续传分片上传 → RawAsset 校验」，产出可归档的 **A 级 E1 真机证据**。
> **诚实边界**：在真实物理设备完成现场采集并归档证据前，平台真机采集状态保持 `HARDWARE_CAPTURE_NOT_TESTED`（严禁用公开数据集或合成样本替代）。

---

## 1. 硬件与工作站前置要求

| 项目 | 最小基线（M5 口径） | 备注 |
|---|---|---|
| 机器人本体 | 单臂 follower 一台（推荐 **SO-101**，lerobot 原生支持） | 需提前完成零点与关节限位标定 |
| 传感器 | 1 路 RGB 相机（USB/UVC 或 CSI）+ 关节总线读取 | 串口权限需加入 `dialout` 组 |
| 采集工作站 | Linux（Ubuntu 22.04/24.04）或 macOS，Python ≥ 3.11 | 建议独立 NVMe/SSD 供本地 Spool |
| 本地磁盘余量 | ≥ 20 GB 可用空间（Spool 目录） | Preflight 门禁低于阈值将 `BLOCKED` |
| 系统时钟 | 启用 NTP / Chrony（偏差 < 500 ms） | 防止预签名 URL 提前 403 过期 |

---

## 2. 真机网络配置（纯出站模型，ADR-0047）

Edge Agent 采用 **纯出站拉取（Outbound-Only）** 架构，工作站**无需开放任何入站端口**，也不直连 DDP PostgreSQL：

| 目标服务 | 方向 | 协议/端口 | 用途 |
|---|---|---|---|
| DDP API 网关（`DDP_BASE_URL`） | 工作站 → 平台 | HTTPS 443（内网 Pilot 可选 8000/8010） | 配对、心跳、任务拉取、Lease、UploadSession |
| 对象存储（MinIO / S3 预签名地址） | 工作站 → 存储 | HTTPS 443 / HTTP 9000 | 分片预签名 `PUT` 直传（不经 API 网关中转流量） |
| NTP 服务器 | 工作站 → NTP | UDP 123 | 时钟同步 |

**网络连通性预检命令**（在采集工作站执行）：

```bash
# 1. 验证 DDP API 可达
curl -fsS "${DDP_BASE_URL}/healthz"

# 2. 验证本地时钟同步（Linux）
timedatectl status | grep -E "synchronized|NTP"

# 3. 验证 Spool 目录可写且磁盘余量充足
mkdir -p /var/lib/ddp-edge-agent/spool
df -h /var/lib/ddp-edge-agent/spool
```

> 若内网 Pilot 使用自签 TLS 证书，联调脚本支持 `--no-verify-tls`（仅限受控内网，禁止在公网环境关闭证书校验）。

---

## 3. 配对码获取与凭据边界

### 3.1 凭据分层说明（重要）

| 通道 | 端点前缀 | 所需凭据 | 说明 |
|---|---|---|---|
| **控制面** | `/api/v1/edge-agents/{agent_id}/heartbeat\|leases\|assignments` | `ddp_agt_...`（Agent Token） | 由一次性配对码调用 `/pair` 换取，与用户 JWT 彻底隔离 |
| **数据面** | `/api/v1/projects/{project_id}/upload-sessions*` | 项目写权限 Bearer JWT（`DDP_UPLOAD_TOKEN`） | 当前后端 `/upload-sessions` 校验项目写权限作用域，建议使用专设采集操作员/服务账号签发的短期项目 JWT |

### 3.2 平台侧准备（项目管理员执行）

1. 在 DDP 平台确认已创建目标 **Project**、**Robot**（记录 `ROBOT_ID`）与 **Device**（相机/传感套件，记录 `DEVICE_ID`）。
2. 为本次上机创建一个 **Capture Session**（状态 `SCHEDULED`，记录 `CAPTURE_SESSION_ID`）：

```bash
curl -fsS -X POST "${DDP_BASE_URL}/api/v1/projects/${PROJECT_ID}/capture-sessions" \
  -H "Authorization: Bearer ${OPERATOR_JWT}" \
  -H "Content-Type: application/json" \
  -d "{\"code\":\"CAP-PILOT-$(date +%Y%m%d-%H%M)\",\"robot_id\":\"${ROBOT_ID}\"}"
```

3. 签发一次性 **Pairing Token**（默认有效期 600 秒，仅可消费一次）：

```bash
curl -fsS -X POST "${DDP_BASE_URL}/api/v1/projects/${PROJECT_ID}/edge-agents/pairing-tokens" \
  -H "Authorization: Bearer ${OPERATOR_JWT}" \
  -H "Content-Type: application/json" \
  -d "{
    \"robot_id\": \"${ROBOT_ID}\",
    \"display_name\": \"pilot-ws-01\",
    \"device_ids\": [\"${DEVICE_ID}\"],
    \"ttl_seconds\": 600
  }"
```

从响应中提取 `pairing_token`（**切勿写入 Git 仓库或粘贴到公共日志**）。

---

## 4. Edge Agent 安装与上机启动

### 4.1 工作站环境准备

```bash
# 在工作站拉取仓库或拷贝 services/edge-agent + scripts/e2e/pilot_edge_chain.py
cd services/edge-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 先跑本地软件自检（不连外部网络）
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

### 4.2 先跑软件联调基线（验证本地栈无回归）

在无硬件或上机前，可在任意开发机运行进程内 FastAPI `TestClient` 软件基线验证：

```bash
python3 scripts/e2e/pilot_edge_chain.py --output /tmp/pilot-software-baseline.json
```

### 4.3 真机上机实跑（`--live-url`）

完成物理机器人一次短采集并导出原始文件（例如 `/data/captures/so101_ep001.mcap`）后，执行全链路实跑：

```bash
export DDP_PROJECT_ID="<PROJECT_UUID>"
export DDP_PAIRING_TOKEN="<一次性配对码>"
export DDP_UPLOAD_TOKEN="<项目写权限 JWT>"

python3 scripts/e2e/pilot_edge_chain.py \
  --live-url "https://ddp.lab.local" \
  --capture-session-id "<CAPTURE_SESSION_UUID>" \
  --file "/data/captures/so101_ep001.mcap" \
  --format-hint MCAP \
  --mime-type application/x-mcap \
  --spool-dir "/var/lib/ddp-edge-agent/spool" \
  --output "/var/lib/ddp-edge-agent/pilot-e1-evidence.json"
```

若 Agent 已配对完成、需复用既有凭据继续上传后续会话，可将 `--pairing-token` 替换为 `--agent-id` 与 `--agent-token`（或环境变量 `DDP_AGENT_ID` / `DDP_AGENT_TOKEN`）。

---

## 5. 上机验收 5 项清单（全部通过方可归档 A 级 E1 证据）

| # | 验收项 | 判定标准 | 证据来源 |
|---|---|---|---|
| **1** | **设备配对与在线心跳** | `POST /edge-agents/pair` 返回 201 且签发 `ddp_agt_` 令牌；管理面 `GET /edge-agents/{agent_id}` 显示 `status=ACTIVE`、`connectivity=ONLINE` | `pilot-e1-evidence.json` + 平台管理页截图 |
| **2** | **采集会话 Lease 互斥与续租** | Agent 拉取到目标 `CAPTURE_SESSION_ID`，成功 `acquire` 租约并在心跳回执中看到 `renewed_leases` 包含该 `lease_id` | `steps.lease_acquired` + 服务端心跳日志 |
| **3** | **本地 Spool 落盘与断网续传** | 采集文件落盘 Spool 并生成确定性 `manifest_hash`；人为断网或中断进程后重启续传，已 ACK 分片**不重传**、UploadSession **不重建** | 本地 Spool SQLite 状态 + 上传日志 |
| **4** | **RawAsset 完整性与会话关联** | `GET /raw-assets/{asset_id}` 返回的 `sha256`、`size_bytes` 与工作站 `sha256sum` 完全一致；`GET /capture-sessions/{id}/artifacts` 包含该 `asset_id` | `steps.upload_completed` + 平台 API 回执 |
| **5** | **下游解析与 E1 证据归档** | 原始文件触发 Parser → Episode → QC → Dataset → Loader 读出真实帧；按 V5.1 §17 清单将设备型号、固件版本、样本 SHA256、Manifest、Session/Episode/Loader 输出归档至 `docs/testing/evidence/<日期>-hardware-pilot-e1/` | 完整证据目录（此时方可更新 `HARDWARE_CAPTURE_NOT_TESTED`） |

---

## 6. 常见故障排查

| 现象 / 错误码 | 常见原因 | 排查与处置 |
|---|---|---|
| `AuthRejectedError (401/403)`（控制面） | Pairing Token 已过期/已被消费，或 Agent 已被撤销/令牌已轮换 | 管理员重新签发 Pairing Token，或调用 `rotate-token` 后更新工作站凭据 |
| `AuthRejectedError (401/403)`（上传数据面） | 误将 `ddp_agt_` 当作 `DDP_UPLOAD_TOKEN` 传入，或项目 JWT 过期/无写权限 | 确认 `DDP_UPLOAD_TOKEN` 为具备项目 `EDITOR`/`OWNER` 权限的有效 JWT |
| `SignedUrlExpiredError (403)`（分片 PUT） | 工作站时钟偏差过大，或弱网下单个分片上传耗时超过 `upload_url_ttl_seconds` | `UploadDriver` 会自动重签分片 URL（不重建会话）；若持续失败请校准 NTP 并适当调小 `part_size_hint_bytes` |
| `ChecksumMismatchError (409)` / `QUARANTINED` | 本地文件在入 Spool 后被外部进程修改，或网络传输中分片校验和不一致 | 检查 `spool` 隔离原因，严禁强行覆盖；核对源文件 `sha256sum` 后重新导出入库 |
| `SSL: CERTIFICATE_VERIFY_FAILED` | 内网自签 TLS 证书未被系统信任 | 将内网根 CA 安装到系统信任库，或仅在内网 Pilot 联调时加 `--no-verify-tls` |
| `PreflightBlockedError` | 磁盘余量不足、Agent 版本低于最低要求或必要目录不可写 | 按报错 `blocked_check_ids` 逐项清理磁盘或升级 Agent，**不可绕过** |
