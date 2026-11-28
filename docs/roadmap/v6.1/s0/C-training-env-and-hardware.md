# S0 审计 C：训练环境与硬件可行性（GATE-B Smoke / Training Evidence / Edge Agent Pilot）

- 日期：2026-11-28
- 审计代理：Agent C（只读审计；唯一产出为本文件）
- 仓库：`DataPlatform` @ 本机（Apple M5 / 10 核 / 16GB / Metal 4，**无 CUDA，可用 MPS**）
- 口径延续：V4.1/V5.1 的 `HARDWARE_CAPTURE_NOT_TESTED` / `BLOCKED` 附解除条件 / 不以 Mock 冒充验收

---

## ① GATE-B 训练 Smoke 可行性判定

### 判定：**可行（GO，CPU/MPS 路径）**

`lerobot==0.3.3` **自带完整训练入口**，装 lerobot+torch(MPS) 后可在本机跑通最小训练 step，无需 CUDA、无需真实机器人/相机（数据用预录数据集）。

### 结构证据（`pip download lerobot==0.3.3 --no-deps` → wheel 解包，597KB）

| 检查项 | 证据 |
|---|---|
| 训练入口 | `entry_points.txt`: `lerobot-train = lerobot.scripts.train:main`（console script） |
| 训练循环 | `lerobot/scripts/train.py`: optimizer/lr_scheduler/AMP grad_scaler、逐 step `save_checkpoint`/`update_last_checkpoint`、`train_metrics` |
| MPS 支持 | `lerobot/utils/utils.py:50-52`: `torch.backends.mps.is_available()` → `torch.device("mps")`（自动设备选择原生覆盖 MPS） |
| 策略库 | `policies/`: act / diffusion / pi0 / pi0fast / sac / smolvla / tdmpc / vqbet（ACT 最轻，适合 Smoke） |
| 数据集 | `datasets/lerobot_dataset.py`（v2.1/v2 格式）；torchcodec 在 darwin-arm64 **不安装**（env marker 排除，视频走 `av`） |
| 配置 | `configs/train.py` `TrainPipelineConfig`（`steps`/`batch_size`/`device`/`checkpoint_path`/wandb 开关，默认 wandb 不启用） |

### 所需依赖清单与体积（M 系 Mac，CPU/MPS 轮子，无 CUDA blob）

| 依赖 | 版本约束（METADATA） | 用途 |
|---|---|---|
| torch | `>=2.2.1,<2.8.0` | 训练框架（macOS arm64 MPS 轮子，安装后约 0.4–0.6GB） |
| torchvision | `>=0.21.0,<0.23.0` | 视觉策略 |
| datasets | `>=2.19.0,<=3.6.0` | HF 数据集加载（注意 ≤3.6.0 钉死） |
| diffusers / einops / opencv-python-headless / av / imageio[ffmpeg] / gymnasium<1.0 / draccus==0.10.0 / wandb / rerun-sdk / deepdiff / flask / jsonlines / packaging / pynput / pyserial / huggingface-hub | 见 METADATA | 训练/数据/录制周边 |
| ~~torchcodec~~ | darwin-arm64 env marker 排除 | 不需要 |
| transformers | 仅 `transformers-dep` extra（pi0/smolvla 才需要） | Smoke 用 ACT 可不装 |

- **磁盘体积**：lerobot wheel 0.6MB；全量安装约 **1.5–2.5GB**（M 系无 CUDA 权重）。backend venv 现状：**无 torch、无 lerobot**（已核验）——建议独立 venv（如 `~/venvs/lerobot`），不污染 backend。
- **内存**：16GB 统一内存对 ACT/Diffusion 小模型 + `batch_size≤8` 可行；pi0 级大策略不在 Smoke 范围。
- **Python**：要求 ≥3.10（本仓 venv 3.11 兼容）。

### 推荐路径（数据零新增下载，复用已验收样本）

- **首选（仓内闭环）**：复用 `docs/testing/evidence/2026-11-25-lerobot-frame-verify/` 已 PASS 的样本——`amandlek/robomimic` `v1.5/lift/ph/demo_v15.hdf5`（36,699,586 B，SHA256 `68f2a8cf…72fc1618272f37`，59 帧 / 20Hz / state 32 维 / action 7 维，`LOADER_FRAME`）。经 `data-platform/ddp_data/lerobot/frame_writer.py` 转 LeRobotDataset v2.1 → `lerobot-train --steps=2 --batch_size=2 --policy.type=act --device=mps`。
- **备选（官方 Demo）**：`lerobot/pusht`（HF Hub 下载，需网络/HF 端点镜像，体积约数百 MB）。
- **Smoke 通过标准（建议）**：step 递增、loss 有限值下降、`outputs/train/<run>/checkpoints/` 产出 checkpoint 目录、`train_metrics` 可读取。
- **风险**：① `datasets<=3.6.0` 与其他库冲突需独立 venv；② wandb 为 METADATA 硬依赖但可用 `wandb.enable=false` 关闭上报；③ MPS 上 AMP 行为与 CUDA 有差异，Smoke 只判"能跑通"，不比精度。

> 注：`scripts/train/run_training_probe.py` + `ddp_data/llm_export/training_probe.py` 为 numpy 微型循环（SFT loss 73.55→9.36 实测），仅证明进程内数值训练能力，**不构成** GATE-B 目标训练脚本；GATE-B 应以 `lerobot-train` 上述路径为准。

---

## ② Training Evidence 库表缺口结论

### 结论：**无独立 TrainingRun 表是有意设计（ADR-0036），外部引用行即 run 记录；但 checkpoint/seed/逐 step metrics 无任何落库字段——执行证据存在结构缺口**

- grep `checkpoint|training_run` 于 `backend/app/domain/modelops/`：**0 命中**；于 `backend/app/infrastructure/db/models/`：**0 命中**。
- 迁移证据（存在训练语义但不是执行证据表）：
  - `0009_stage2_ingestion_episode_lineage.py`：血缘枚举已有 `LineageNodeType.TRAINING_RUN`（节点类型，非表）。
  - `0059_training_lineage_relation.py`：`data_lineage_edges.relation` CHECK 放开 `TRAINED_ON`，支持 `DatasetVersion --TRAINED_ON--> TrainingRun` 血缘边（ADR-0036 冻结）。
  - `0063_training_run_fields.py`（docstring 明示口径）：**"不新建平行 TrainingRun 表：外部平台仍是训练执行的权威系统，引用行本身就是 DDP 侧的 run 记录（ADR-0036 / backlog N7-002）"**，并在 `external_training_references` 上补三列：`config_summary`（JSON，8KiB 裁剪+脱敏快照）、`code_version`（≤100）、`model_version`（≤100），存量 NULL 不回填。

### `ExternalTrainingReferenceModel`（`training.py:318`）字段清单

adapter_id/adapter_version、training_delivery_id、training_request_revision_id、dataset_version_id、**external_run_id / external_run_url / external_status**、execution_mode、status_stale、submitted_at/last_synced_at、model_ref/evaluation_ref/artifacts（JSON）、dry_run_plan、config_summary/code_version/model_version（0063）、correlation_id/idempotency_key、error_code/error_summary、审计列。

### 缺口清单（若 GATE-B 要求训练证据入库需补）

| 证据项 | 现状 |
|---|---|
| code commit / 配置摘要 / 模型版本 | ✅ 有（0063 三列，但为**提交时快照**，非运行时证据，均可 NULL） |
| 外部 run id / 状态 / 产物引用 | ✅ 有（external_run_*、artifacts JSON） |
| **seed** | ❌ 无字段 |
| **checkpoint 路径/Hash** | ❌ 无字段（`checkpoint` 全库 0 命中） |
| **训练 metrics 历史（loss 曲线等）** | ❌ 无字段、无表 |
| ModelVersion 关联训练 | ✅ `modelops.py:107-108` ModelVersionRow.training_request_id/training_delivery_id（仅 FK，无 run 遥测） |

**建议方向**（本审计不实施）：新增 `training_run_evidence` 扩展表（FK → external_training_references：seed、checkpoint_path、checkpoint_sha256、metrics_summary JSON、final_status），或在 GATE-B 验收时把 checkpoint hash/metrics 作为 Evidence 文档落 `docs/testing/evidence/`，暂不动库。

---

## ③ Edge Agent 能力现状（`services/edge-agent/`）

模块一句话盘点（来源 README + 源码目录）：

| 模块 | 能力（一句话） |
|---|---|
| `config.py` | TOML/映射配置加载与阈值校验 |
| `durable_fs.py` | fsync（文件+目录）、SHA256、原子目录重命名 |
| `redaction.py` | 日志/诊断脱敏（配对凭证/Agent Token/JWT/签名禁泄露） |
| `manifest/` | 确定性 Canonical JSON Manifest 构建、校验与禁止内容红线（FR-006） |
| `spool/` | SQLite(WAL) 状态机、落盘原子迁移、崩溃恢复与原始副本保护（FR-007） |
| `adapters/` | `CaptureAdapter` 协议 + `FilesystemExportAdapter`（明确 `realtime_control=False`、`realtime_capture=False`，FR-005） |
| `preflight/` | 11 项 Preflight 检查 + 不可绕过 `BLOCKED` 启动门禁（FR-004） |
| `upload/` | `ServerGateway` Protocol + 可续传分片上传驱动（断网续传/URL 过期重签/篡改隔离，FR-007/008） |
| 服务端四表 | `edge_agents` / `edge_agent_pairing_tokens` / `agent_heartbeats` / `agent_task_leases`（配对 token 哈希唯一、心跳、活跃 lease 部分唯一约束互斥，`models/edge_agents.py`） |

- 测试：8 个测试文件 **34 个测试函数**（spool 崩溃恢复、HTTP E2E 上传、续传驱动、preflight 门禁、manifest/脱敏），服务端网关经 `ServerGateway` Protocol + `fake_gateway.py` 显式替身验证。
- `HARDWARE_CAPTURE_NOT_TESTED` 全仓痕迹：**约 26 处**，全部在 `docs/`（v3.1/v4.1/v5.1 路线图、验收报告、evidence README）与 `services/edge-agent/README.md:6` 边界声明——**生产代码无该常量硬编码**，状态口径由文档与验收流程承载。
- 边界未变：Agent 不控制机器人运动、零入站连接、不直连 DDP PostgreSQL；真实硬件采集从未执行。

---

## ④ Pilot 最小资源需求与 BLOCKED 解除条件

### Pilot 最小资源（延续 v5.1 §17 前置，叠加 lerobot 原生支持）

| 类别 | 最小配置 |
|---|---|
| 机器人 | 单臂 follower 一台（lerobot 原生支持 `so101_follower`/`so100_follower`/`koch_follower`/`lekiwi`/`stretch3`/`viperx`，建议 SO-101 入门级） |
| 传感器 | 1× 相机（OpenCV 兼容 webcam 即可）+ 关节状态（motors 总线） |
| 网络 | Agent 工作站 → DDP 内网可出站 HTTPS（纯拉取边界，零入站），带宽满足分片上传 |
| 工作站 | 装有 edge-agent 的 Mac/Linux（16GB 内存足够；服务器侧沿用现有 docker-compose 栈） |
| 其他 | 采集任务 SOP、数据许可/隐私审查、故障应急联系人、磁盘余量 |

### BLOCKED 解除条件清单（满足即可从 `HARDWARE_CAPTURE_NOT_TESTED` 升级）

1. **硬件与授权到位**：机器人/传感器型号明确（建议 SO-101 + 1 webcam），厂商 SDK/串口或导出方式获授权，Calibration 快照可获取。
2. **环境过 Staging Gate**：Agent 工作站+网络+磁盘就绪，且真实栈上配对 → 心跳 → Lease → 采集/导出 → Manifest → 续传上传 → RawAsset → Parser → Episode → QC → Dataset → Loader Frame Verify 全链通过。
3. **验收证据归档**：按 v5.1 §17 §6 证据清单（设备型号/固件/传感器列表/样本 Hash/Manifest/Agent 版本/Session ID/ProcessingRun/Episode IDs/Stream 样本/同步报告/清洗报告/Dataset Manifest/Loader 输出/录屏日志）落 `docs/testing/evidence/`，状态从 `HARDWARE_CAPTURE_NOT_TESTED` 改为实测标注。

---

## 证据索引

- lerobot 0.3.3 wheel 解包：`/tmp/lr/lerobot-whl/`（entry_points.txt、scripts/train.py、utils/utils.py、configs/train.py、METADATA）
- 训练语义：`backend/app/infrastructure/db/models/training.py:318`、`backend/app/infrastructure/db/models/modelops.py:107`、`backend/migrations/versions/0059_training_lineage_relation.py`、`backend/migrations/versions/0063_training_run_fields.py`
- Edge Agent：`services/edge-agent/README.md`、`backend/app/infrastructure/db/models/edge_agents.py`
- 前序证据：`docs/testing/evidence/2026-11-25-lerobot-frame-verify/README.md`、`docs/roadmap/v5.1/建设设计/17_Staging与真实硬件Pilot.md`、`docs/roadmap/v4.1/audit-report-2026-11-24.md`
