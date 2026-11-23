# DDP V4.1 S0 基线复核与 D2 决策报告

- 复核日期：2026-11-23
- 基线：`origin/main` @ `b4f965b`（V3.1 审计报告合并后）
- 依据：`docs/roadmap/v4.1/`（V4.1 蓝图包 18 文件，SHA256 校验 0 差异）
- 蓝图要求：S0 只读复核后暂停；D2 必须列原文选项并经用户批准

---

## 1. 基线复核（全部 E1 实跑）

| 项 | 值 |
|---|---|
| main | `b4f965b`（与 V3.1 审计基线 `3d45344` 之间只隔审计报告 PR #84） |
| OpenAPI | 1.32.0 / 244 路径 |
| Alembic head | 0070（64 个迁移） |
| 契约 parity strict | exit=0（62 项已接受差异） |
| 质量门禁 | `check.sh` 9 步通过（2026-11-23 实跑两次：#83 合并前 + 审计前） |
| V3.1 审计 | `docs/roadmap/v3.1/n0/audit-report-2026-11-23.md`（八域矩阵 + 七条不得宣称） |

### FR 状态复核（对照蓝图 13）

蓝图 45 项 FR 中，与当前 HEAD 有出入的判定已在 V3.1 审计报告中修正，本报告确认以下关键口径：

- FR-023（AI 建议人工决策）`VERIFIED` ✓；FR-024（提交自动 QC）`VERIFIED` ✓；
- FR-029/031/032/033（Dataset Gate/Manifest/Canonical/LeRobot Writer）`VERIFIED` ✓；
- FR-016/017（L2⊆L1 / L3⊆L2 服务端校验）确认为 **UNVERIFIED**：`validate_record_labels`
  只按 label_type 校验单值形状，无跨标签层级包含校验 —— S1-A 落点；
- FR-018（OUTCOME_SPAN 提交校验）：Schema 发布期校验已前移（V31-047），但
  `submission.py::_require_type` **尚无 OUTCOME_SPAN 分支**，提交时值形状不校验 —— S1-A 落点；
- FR-026/027/028（QC 批量重跑 UI）：后端 API 已有（`POST /quality-runs/batch`、
  `GET /quality-runs/{baseline}/diff/{candidate}`），前端无入口 —— S1-B 落点；
- FR-034/035（Loader 实测）：`BLOCKED` 于隔离环境 —— S1-C 尝试解除（pip 可达即做）。

## 2. D2 决策（蓝图 16 §3：列原文选项 + 影响 + 推荐，等用户批准）

V31-010 ADR 尚未撰写（蓝图 S0 确认）。基于蓝图 04 功能设计与 V31-010 任务定义，
D2 的决策点是 **「Agent 任务获取模式与控制边界」**，原文语境下的选项：

| 选项 | 说明 | 影响 |
|---|---|---|
| **A. 纯拉取（推荐）** | Agent 只经出站 HTTPS 轮询/长轮询拉取任务与上传；服务端永不主动连接 Agent；无任何控制命令通道 | 部署：Agent 在边缘工作站，出站单向，NAT/防火墙友好；身份：Agent 本地密钥配对；网络：只需出站 443；Lease：服务端发放、Agent 心跳续租；缓存：本地 Spool + Manifest；安全：攻击面最小（无入站端口、无命令执行）；维护：升级由工作站侧推送 |
| B. 拉取 + 受限命令通道 | 在 A 之上增加服务端下发「采集参数/标定更新」等白名单命令 | 便利但引入命令语义与授权面；需命令审计 + 版本兼容；与「不提供任意远程命令」的蓝图红线仅一步之遥 |
| C. 服务端推送（长连接/MQTT） | 实时性最好 | 引入入站连接/消息代理，违反「不引入 Kafka/Celery/微服务」边界 |

**推荐：A**（蓝图 04 §6 与 16 §4 的默认方案一致）；反对 B/C 的理由：B 的命令通道是
攻击面与合规负担，C 违反架构边界。**等待用户批准后，S2 才开工 V31-010 ADR 落地。**

## 3. S1 并行分工（本次启动）

| 线 | FR | 落点 | 执行者 |
|---|---|---|---|
| A | FR-016/017/018 | `submission.py::validate_record_labels` 增加跨标签层级包含 + OUTCOME_SPAN 值校验 | subagent backend |
| B | FR-026/027/028 | `pages/annotation` QC 批量重跑 UI（选择/预览/确认/执行 + 差异报告 + 失败项重试） | subagent frontend |
| C | FR-034/035 | 隔离 venv 固定版本 LeRobot Loader 实测；环境不可达则出具 BLOCKED 报告与解除条件 | subagent 验收 |

## 4. 暂停点

按蓝图 14 §S0：本报告落盘后 S0 结束；S1 三线与 D2 批准互不阻塞（Agent 持久化结构在 D2 批准前不动）。
