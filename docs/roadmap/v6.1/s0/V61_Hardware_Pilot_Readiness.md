# V61_Hardware_Pilot_Readiness — 硬件 Pilot 就绪度（S0-C 实测）

## 1. 判定：BLOCKED（外部硬件未提供）

全仓 26 处 `HARDWARE_CAPTURE_NOT_TESTED`（均在 docs/README，生产代码无硬编码）——状态诚实，无需修正代码。

## 2. 软件侧就绪度（高）

| 能力 | 状态 | 证据 |
|---|---|---|
| Edge Agent（配对/心跳/Lease/Spool/上传/断点续传） | ✅ 34 测试全绿 | `services/edge-agent/` |
| 服务端四表（edge_agents / pairing_tokens / heartbeats / task_leases） | ✅ 已建 | 迁移 0071 |
| 设备/机器人/传感器/标定域 | ✅ 完整（devices/robots/sensors/calibration + Capture Session + Preflight） | V4.1 起持续建设 |
| 采集→上传→解析→Episode→QC→Dataset→Loader 全链 | ✅ 除首环外全部 VERIFIED | 各域审计 |
| **唯一空白** | ❌ 真实硬件现场数据 | `HARDWARE_CAPTURE_NOT_TESTED` |

## 3. Pilot 最小资源需求（解除 BLOCKED 的 3 个条件）

### 条件 1：硬件与授权到位
- **机器人**：单臂 follower 一台。推荐 **SO-101**（lerobot 原生支持 so101/so100/koch 系列，采集脚本现成）；其他 Mitsubishi/NXT 亦可但需适配验证
- **传感器**：1 个 RGB 相机 + 关节总线读取（minimal sensor set，蓝图 M5 口径）
- **授权**：SDK/串口权限、标定快照可获取

### 条件 2：Staging Gate（工作站）
- 工作站 + 出站网络 + 磁盘就绪
- 全链真实栈演练一次：配对 → 心跳 → Lease → Manifest → 断网续传上传 → Parser → Episode → QC → Dataset → Loader

### 条件 3：验收证据归档
- 按 V5.1 §17/§6 清单落 `docs/testing/evidence/`（设备/固件/样本 Hash/Manifest/Session/Episode/Loader 输出）
- 状态从 `HARDWARE_CAPTURE_NOT_TESTED` 改为实测标注

## 4. 与主计划的关系

- Pilot 属 **M5**（P1），**不阻塞 M2/M3/M4**（DAG：M0→M5 设备到位后可与 M2/M3 并行）
- 公开数据集（pusht）只做软件集成验证，**不能替代** Pilot 验收（蓝图红线 10）
- 需用户决策项：①是否有 SO-101 或同系单臂可调 ②现场网络条件 ③Pilot 时间窗口
