# V61_FR_Status_Matrix — V6.1 功能需求状态矩阵

> 依据蓝图 24 号矩阵 + S0 实测重定级。VERIFIED 需代码+测试+适用真实证据。
>
> **本表已按 2026-11-29 通宵建设结果刷新**（原文为 S0 定级，保留在 git 历史）。
> 最新状态与证据见 `../OVERNIGHT-REPORT-2026-11-29.md`。

| ID | 功能 | 蓝图预估 | **S0 实测定级** | 依据/差距 |
|---|---|---|---|---|
| V61-001 | 仓库/库/契约基线 | P0 待做 | **VERIFIED** | `V61_Current_Baseline.md`：main=c5f78be、单 head 0086、契约 1.40.0、CI success |
| V61-002 | 继承矩阵/重复建设审计 | P0 待做 | **VERIFIED** | `V61_V51_V52_Reuse_Map.md`，12 类禁重建清单核验通过 |
| V61-003 | 图像/视频 Golden Sample 准入 | P0 缺 | **PARTIAL** | 样本已在本机（lerobot/pusht MP4 6.6MB + Parquet）；缺准入登记（sample_manifest/许可/Hash 门禁）流程 |
| V61-004 | 视频 Adapter 六级实证 | P0 缺 | **PARTIAL** | 六级机制 VERIFIED；mp4=INSPECT 级，缺 PAYLOAD_PARSE→ROUND_TRIP 真实证据 |
| V61-005 | Video Decode / Frame Index | P0 缺 | **VERIFIED** | PR #177：`FfprobeVideoDecoder` + `RawAssetVideoResolver` 生产接线；真实装配集成测试（3 帧解码落库 + 归一化回填） |
| V61-006 | 图像 Observation Canonical 映射 | P0 缺 | **PARTIAL** | PR #183：`ddp_data/canonical/image_observation.py` 帧→Observation 投影（15 项测试）；**未接入导出链路** |
| V61-007 | 视频+状态/动作多流同步 | P0 缺 | **NOT_STARTED** | 领域层有 sync_analysis（声明值+合成 fixture）；无 PTS 实测链 |
| V61-008 | LeRobot Video Loader | P0 缺 | **VERIFIED** | 导出器支持 video feature + GATE-A 证据（官方 Loader 真实解码 100 帧） |
| V61-009 | 全量样本 Pipeline 基准 | P0 缺 | **PARTIAL** | 5 demos/280 帧 E1 已有；写路径压测基线已有（26.46 TPS）；缺全量 200 demos 基准 |
| V61-010 | Worker 并发/背压/恢复 | P0 部分 | **PARTIAL** | 9 worker 幂等/租约/重试 VERIFIED；缺并发压测+故障注入报告 |
| V61-011 | 目标训练脚本 Smoke | P0 缺 | **PLAN 可行(GO)** | lerobot 0.3.3 自带 `lerobot-train` 入口+MPS 原生支持（S0-C 实证 wheel 解包）；待装 venv 执行 |
| V61-012 | TrainingRun 证据关联 | P0 部分 | **VERIFIED** | 迁移 0088 `training_run_evidence` + 3 个项目隔离端点 + 10 段闭环 E2E `PASSED` |
| V61-013 | EvaluationRun 与 Badcase 归因 | P1 | **VERIFIED**（后端） | evaluations 表+failure_cases+attribute_gap_evidence 全通；前端页缺 |
| V61-014 | DataRequest 回流闭环 | P1 | **VERIFIED**（后端） | data_requirements 9 态状态机 + link-retest 闭环 + close 门禁；E2E 真实数据待跑 |
| V61-015 | Cleaning TRANSFORM | P1 | **VERIFIED** | PR #180：`cleaning_actions.py` 有界限幅 + `transformed-NNN.ndjson` 衍生产物；8 项测试 |
| V61-016 | QUARANTINE/恢复 | P1 | **PARTIAL** | PR #180：清洗侧隔离已实现（`QuarantineDecision`）；RawAsset 侧恢复动作既有；Episode 级恢复流程未做 |
| V61-017 | Judge 校准集/漂移 | P1 | **VERIFIED**（领域层） | PR #181：准确率 + Cohen's kappa + 漂移判定（14 项测试）；**未接 API/前端** |
| V61-018 | 标注运营质量看板 | P1 | **PARTIAL** | 后端指标有（approved_1h 等）；看板页部分 |
| V61-019 | Token 预算/告警 | P1 | **PARTIAL** | PR #181：5 维度判定 + `GET /ops/token-budget`（13 项测试 + API 23 项）；**告警投递未接** |
| V61-020 | 真实硬件 Pilot | P1 | **BLOCKED** | 解除条件 3 条（SO-101 单臂+1 相机+SDK 授权 / Staging 全链 / 证据归档） |
| V61-021 | Staging/生产门禁 | P1 | **PARTIAL** | compose+监控+备份恢复已有；无独立 staging（用户已定全 prod 策略） |
| V61-022 | 多模态 Lineage | P1 | **PARTIAL** | data_lineage_edges VERIFIED（含 TRAINED_ON）；TrainingRun 端点缺 |
| V61-023 | 许可/隐私/媒体治理 | P1 | **PARTIAL** | PR #184：导入前许可门禁（GATED / 非宽松 / 许可缺失必须显式确认并留痕）；上传入口未纳入，脱敏审计仍缺 |
| V61-024 | 额外格式/机型 | P2 | NOT_IN_SCOPE(M0) | 排 M6 |
| V61-025 | GPU 真实推理 | P2 | **BLOCKED** | 无 CUDA；MPS 可做 CPU/MPS 级替代，GPU 性能宣称除外 |
| V61-026 | 外部训练平台 Connector | P2 | NOT_IN_SCOPE | API 未提供 |

## 汇总

| 定级 | 数量（S0） | 说明（2026-11-29 刷新后见上表逐项） |
|---|---|---|
| VERIFIED | 6 | 基线/继承/归因/闭环（含 4 项超蓝图预期）；本轮新增 V61-005/008/012/015 与 V61-017（领域层） |
| PARTIAL | 11 | 多数「后端有、实测/前端缺」；本轮 V61-006/016/019/023 有实质推进但仍未全闭 |
| NOT_STARTED | 5 | S0 时全部集中在视频像素链；本轮已全部离开 NOT_STARTED |
| PLAN/GO | 1 | V61-011（本机可执行） |
| BLOCKED | 2 | 硬件 / GPU |
| NOT_IN_SCOPE | 2 | M6 扩展 |

**关键修正（相对蓝图）**：训练反馈闭环（V61-012/013/014）后端已基本建成，V6.1 真正的新建量在**视频像素链**与**TrainingRun 证据表**两处。
