# ADR-0047 Edge Agent 任务获取模式与控制边界（V31-010 / D2 裁决）

- 状态：**ACCEPTED**（用户于 2026-11-23 批准方案 A）
- 日期：2026-11-23
- 决策者：用户（D2 裁决）；起草：DeepSeek Harness
- 关联：ADR-0001、ADR-0008、ADR-0037（CaptureSession 实体）；V3.1 审计报告（2026-11-23）；V4.1 蓝图 04/16（FR-001~008）
- 后续实施：V31-011（表与 API）、V31-012（运行时）、V31-013（恢复）

## 1. 背景

V3.1 审计确认 Edge Capture Agent 是唯一 `MISSING` 的主链环节。V31-010 要求先以
ADR 冻结 Agent 协议边界；其中 **D2（任务获取模式与控制边界）** 为用户裁决点。

D2 选项与影响分析见 `docs/roadmap/v4.1/s0-baseline-and-d2.md §2`：

| 选项 | 摘要 |
|---|---|
| A. 纯拉取 | Agent 出站 HTTPS 轮询/长轮询拉取任务与上传；服务端无入站连接；无控制命令通道 |
| B. 拉取 + 白名单命令通道 | A 之上增加服务端下发采集参数/标定更新的命令语义 |
| C. 服务端推送 | 长连接/MQTT 实时下发 |

## 2. 决策

**采用方案 A：纯拉取。** 用户于 2026-11-23 批准（对话记录「A」）。

## 3. 协议边界（因 A 而确定的具体行为）

### 3.1 网络与部署

- Agent 部署在采集工作站/工控机/边缘计算机，**不进入机器人运动控制器**；
- 仅发起**出站 HTTPS**（443）；**服务端没有任何入站到 Agent 的连接**；
- 不提供任意远程 shell / 命令执行 / 远程控制（与 ADR-0008 bash 策略一致并更严）。

### 3.2 身份与配对（FR-001）

- 管理员在设备页生成**一次性配对凭证**（短 TTL、单次使用）；
- Agent 首次启动用凭证换取 **Agent 专属身份**（agent_id + token），与用户 JWT 完全分离；
- 支持轮换与撤销；撤销后所有在途 Lease 立即失效；凭证与 token 不回显、不写日志、不写 Manifest。

### 3.3 心跳（FR-002）

- 周期上报：agent_id、version、os/arch、adapter versions、disk free、network 可达性、
  current session、spool pending count/bytes、upload queue depth、last error、timestamp；
- 默认心跳 30s、失联阈值 90s（可配置，Pilot 后调整）；失联只是**展示与派发抑制**，
  不自动撤销身份。

### 3.4 任务获取与 Lease（FR-003）

- Agent 主动拉取「分配给本 agent（经设备绑定）的待执行 CaptureSession」；
- 执行前申请 **Lease**（agent × session × 设备集合 × 过期时间）；同一 Session 同时
  只有一个 active lease；
- 心跳携带 lease 续租；**lease 过期不能开始新采集**；已有采集在短断网宽限期
  （默认 2×心跳周期）内可继续写本地 Spool，长断则停止采集、保留 Spool 等恢复；
- 服务端强制回收需记录原因（审计）。

### 3.5 Preflight（FR-004）

逐项 PASS/WARN/BLOCKED：身份有效性、版本、任务/Session 状态、设备/传感器可用、
标定快照、Adapter 可用、磁盘余量、时钟偏移、网络、Spool 可写、上一 Session 残留。
**BLOCKED 不可被「继续」按钮绕过**；WARN 允许继续但记录证据。

### 3.6 数据通路（FR-005~008）

- **Filesystem Export Adapter** 为首批真实能力（目录监听/导出目录扫描）；
- 本地 Spool：临时目录写完 → fsync → SHA256 → Metadata → Manifest → 原子移动 pending
  → uploading → 服务端回执后 uploaded；永久失败进 failed/quarantine；重启从本地状态恢复；
- Manifest（schema version、agent/version、session/task/project、device/sensor/calibration
  IDs、起止、时钟元数据、adapter/version、streams、artifacts（path/MIME/size/SHA256/
  duration/format）、warnings、manifest hash）；**服务端二次校验，不信任 Agent 自报**；
- 上传**复用现有 multipart Upload API**（V31-002 已四要素验收：重复 ACK 幂等、
  篡改 ETag 409、重复 complete 不分叉、TTL 部署口径）；Agent 持久化 UploadSession id /
  part / ETag / 恢复状态；URL 过期重签名不重建 Session。

### 3.7 服务端新增面（V31-011 落地清单）

| 表 | 用途 |
|---|---|
| `edge_agents` | agent_id、project、display_name、status（PAIRED/ACTIVE/REVOKED）、public_key 指纹、last_seen_at |
| `agent_heartbeats` | 心跳时序（最新一条供查询，历史按保留策略） |
| `agent_task_leases` | agent × capture_session × devices × expires_at × released_at/reason |

API 均为**出站拉取语义**：`POST /edge-agents/pair`（凭证换身份）、
`POST /edge-agents/{id}/heartbeat`（含 lease 续租）、`POST /edge-agents/{id}/leases`
（申请/释放）、`GET /edge-agents/{id}/assignments`（拉取可执行任务）。
权限：Agent token 作用域仅限本 agent 的资源；管理面复用既有管理员角色。

## 4. 后果

**正面**：攻击面最小（零入站端口、零命令语义）；协议全部可经现有 API 承载，无消息
代理；断网恢复完全由本地 Spool + 幂等上传协议保证（已被 V31-002 实测）。

**负面/代价**：任务派发非实时（轮询间隔内延迟）；服务端不能即时感知 Agent 本地决策；
命令式运维（如远程重启采集）不可用 —— 由「人工到工作站」或未来独立 ADR 承担。

**中性**：实时性要求由 CaptureSession 的「计划开始时间 + Agent 轮询」满足；Pilot 期
轮询间隔 30s 足够。

## 5. 明确不做

- 不做机器人运动控制、不做远程 shell、不做服务端推送、不做命令下发语义（B/C 的
  核心能力全部排除）；不与 DDP PostgreSQL 主库共享 Agent 本地状态存储。
