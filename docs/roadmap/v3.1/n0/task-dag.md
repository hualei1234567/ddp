# DDP V3.1 N0-04 实施任务 DAG 与里程碑

- 制定日期：2026-11-22
- 事实基线：`origin/main` @ `46864a9`
- 依据：`docs/roadmap/v3.1/n0/gap-matrix.md`、`reuse-map.md`
- 约束：每个任务一个 PR、一个可验收切片；先测试后实现；不通过的 `make check` 不进入下一任务

## 1. 里程碑

| 里程碑 | 目标 | 完成判据 |
|---|---|---|
| M1 证据修复 | 消除“伪贯通”与文档漂移 | 上传→RawAsset 真实断言；真实 MinIO 专项；基线文档同步 |
| M2 上游贯通 | 采集 → 上传 → Episode 自动成链 | 会话完成后素材与 Episode 可回读且自动触发 |
| M3 标注可用 | 媒体标注控制台可用 | 时间轴 / 帧标注 + 审核证据视图 + 浏览器 E2E |
| M4 质检异步 | 提交后自动质检 | 质检运行状态机 + 失败重投 + 结论驱动返工 |
| M5 训练可交付 | 冻结数据可被真实消费者加载 | Manifest 对象化 + Canonical/LeRobot 导出验证 |

## 2. 任务清单

### M1 证据修复（无架构变更，先止血）

| ID | 任务 | 依赖 | 变更面 |
|---|---|---|---|
| V31-001 | 修正 `test_upload_session_carries_capture_session_to_raw_asset`：真实分片上传 → ACK → complete → 查 RawAsset → 查 artifacts | 无 | 测试 |
| V31-002 | CaptureSession 上传专项真实 MinIO 验收（重复 ACK / 重复 complete / 回读复算 SHA256 / URL 过期） | V31-001 | `scripts/e2e`、测试夹具 |
| V31-003 | 同步基线与文档漂移：`risk-list.md:72`、`pipeline.py:7-12` 注释、本目录 | 无 | 文档 |
| V31-004 | 决策记录：`COMPLETED` 是否可进 Dataset Builder；Canonical `1.0.0-draft` vs `1.0.0` 收敛方案 | 无 | ADR |

### M2 上游贯通

| ID | 任务 | 依赖 | 变更面 | 备注 |
|---|---|---|---|---|
| V31-010 | Agent 协议 ADR：注册、心跳、任务 lease、能力上报、失败重试、无控制命令 | V31-003 | ADR + 契约草案 | **必须先批准** |
| V31-011 | `edge_agents` / `agent_heartbeats` / `agent_task_leases` 表与 API | V31-010 | 迁移 + API + 契约 | 新增最小表 |
| V31-012 | Agent 最小运行时：领取任务 → 创建/启动 CaptureSession → 本地 spool → SHA256 → 上传 → 完成会话 | V31-011 | `services/edge-agent` | 只读采集，不发控制命令 |
| V31-013 | Agent 断网 / 崩溃恢复 | V31-012 | 本地状态 + 重试 | 需故障注入测试 |
| V31-014 | 删除人工触发依赖：导入成功 → Outbox → 自动 Episode 物化 worker | 无（可并行） | worker + 迁移 | 保留手工 API 作为重放入口 |
| V31-015 | ProcessingRun 接受 `raw_asset_id` 并校验 `VALID` | V31-004 | API + 领域校验 | 保持 `input_refs` 兼容 |
| V31-016 | `processing_artifacts` 产物目录表 + 领域契约 | V31-004、V31-015 | 迁移 + 领域 + worker | 替换 `output_refs` 堆叠 |
| V31-017 | Canonical 正式版本收敛 + 共享 JSON Schema | V31-004 | `packages/contracts` + `ddp_data` | 需 Single Owner；**已完成（ADR-0040）** |
| V31-018 | Pipeline `EPISODE_SEGMENTATION` 在存在明确边界时创建 Episode | V31-016、V31-017 | 应用层 | 无边界必须 SKIPPED/BLOCKED，禁止伪造。**已完成（2026-11-22）**：单文件 MP4 按 `[0, duration_ms)` 建 Episode；容器格式如实 SKIPPED |

### M3 标注可用

| ID | 任务 | 依赖 | 变更面 | 备注 |
|---|---|---|---|---|
| V31-020 | 媒体访问 API（受权下载 / 签名 URL / 过期语义） | 无 | API + 契约 | ADR-0017 落地 |
| V31-021 | 媒体审看层组件（播放 / 逐帧 / 倍速 / 快捷键 / 七态） | V31-020 | `components/media` | 新建 |
| V31-022 | 时间轴片段标注器（`TIME_RANGE` / `ACTION_SEGMENT`） | V31-021 | `components/annotation` | 复用 `SchemaField` 外壳 |
| V31-023 | 帧 / 对象轨迹标注器（`FRAME` / `OBJECT_TRACK`） | V31-021、坐标系契约 | `components/annotation` | 最高风险，单独 PR |
| V31-024 | 审核 / 仲裁媒体证据视图 | V31-021、V31-022 | `ReviewPanel` / `ArbitrationPanel` | 复用现有面板 |
| V31-025 | 个人领取队列 | 无 | `pages/annotation` | 复用 Assignment API |
| V31-026 | 工作量运营页 | 无 | `pages/people` / `pages/annotation` | 复用 workload API |
| V31-027 | 标注主链浏览器 E2E：Schema → 任务 → 批次 → 指定 → 工作台 → 提交 → 审核 → 返工 → 仲裁 | V31-022、V31-024 | `apps/web/e2e` | 必跑 Playwright |

### M4 质检异步

| ID | 任务 | 依赖 | 变更面 | 备注 |
|---|---|---|---|---|
| V31-030 | 质检运行状态机（`QUEUED/RUNNING/SUCCEEDED/FAILED/RETRYING/CANCELLED`） | V31-016（Artifact 语义） | 领域 + 迁移 | **已完成（2026-11-22）**：生命周期与结论正交拆列（迁移 0070），结论视图只认 `SUCCEEDED`；Worker 接线留给 V31-031 |
| V31-031 | 提交事件 → Outbox → 质检 Worker（幂等 + 重试 + 死信） | V31-030 | worker | **执行链路已完成（2026-11-22）**：enqueue/start/conclude + CAS 仓储、`workers/quality.py`、compose 服务、DISPATCH_EVENTS 登记；HTTP 异步入口与生命周期查询留给后续切片 / V31-032 |
| V31-032 | 结论驱动路由（自动转审核 / 自动返工），规则版本快照 | V31-031 | 应用层 | — |
| V31-033 | 质检批量重跑与差异报告 | V31-032 | API + UI | — |

### M5 训练可交付

| ID | 任务 | 依赖 | 变更面 |
|---|---|---|---|
| V31-040 | 冻结时 Manifest 写对象存储 + 回读校验 + `manifest_key/sha256` 原子落库 | V31-004 | 应用层 + 存储端口 |
| V31-041 | `ExportFormat` 扩展 Canonical（契约 + worker strategy + UI 选项） | V31-017 | 契约 + 领域 + worker + UI |
| V31-042 | LeRobot 导出从“元数据 profile”升级到可被固定版本 Loader 加载 | V31-017、V31-018 | `domain/datasets/exporting.py` + 测试 |
| V31-043 | 审核 → Dataset Builder 前端闭环（加入数据集、条目回链、排除原因跳转） | V31-024 | 前端 |
| V31-044 | 真实数据端到端验收：`HDF5/rosbag → Episode → 指定标注人 → 标注 → 审核 → 冻结 → 导出 → 加载` | V31-012、V31-022、V31-040、V31-042 | `scripts/e2e` |

## 3. 依赖图

```text
V31-001 ──► V31-002
V31-003 ──► V31-010 ──► V31-011 ──► V31-012 ──► V31-013
V31-004 ──┬─► V31-015 ──► V31-016 ──┬─► V31-030 ──► V31-031 ──► V31-032 ──► V31-033
          │                          │
          │                          └─► V31-018 ─┐
          ├─► V31-017 ──────────────────────────────┴─► V31-042 ──┐
          │        └─► V31-041 ──────────────────────────────────┤
          └─► V31-040 ───────────────────────────────────────────┤
                                                                  ▼
V31-014（独立）                                          V31-044
V31-020 ──► V31-021 ──┬─► V31-022 ──┬─► V31-024 ──► V31-043
                      ├─► V31-023   │
                      └─────────────┘
                      └─► V31-027（需 V31-022 / V31-024）
V31-025、V31-026（独立，可并行）
```

## 4. 并行分组

| 组 | 任务 | 说明 |
|---|---|---|
| A | V31-001 → V31-002 → V31-003 | 证据止血，最先做，无架构风险 |
| B | V31-004 | 决策，需用户确认，阻塞 C/D/E |
| C | V31-010 → V31-011 → V31-012 → V31-013 | 端侧 Agent，串行 |
| D | V31-014、V31-015、V31-016、V31-017、V31-018 | 上游贯通，与 C 并行 |
| E | V31-020 → V31-021 → V31-022 / V31-023 → V31-024 → V31-027 | 标注控制台，与 C/D 并行 |
| F | V31-025、V31-026 | 独立小任务，可随时插入 |
| G | V31-030 → V31-031 → V31-032 → V31-033 | 依赖 D 的 Artifact 语义 |
| H | V31-040、V31-041、V31-042 → V31-043 → V31-044 | 收口，必须最后 |

## 5. 需要用户确认的决策点

| 编号 | 决策 | 影响 |
|---|---|---|
| D1 | `COMPLETED` 状态 Episode 是否允许进入 Dataset Builder | 影响 V31-004 与 selection 门禁 |
| D2 | 端侧 Agent 是否允许“拉取任务”而仍禁止任何设备控制命令 | 影响 V31-010 协议边界 |
| D3 | Canonical 正式版本采用 `1.0.0` 还是新版本号，以及是否迁移已有 `1.0.0-draft` 数据 | 影响 V31-017 |
| D4 | 媒体审看是否复用现有 HTTP Range 视频方案，还是引入专用播放组件 | 影响 V31-021 依赖评估 |
| D5 | LeRobot 导出是否必须通过官方 Loader 验证才允许标记可交付 | 影响 V31-042 验收门槛 |

> 以上五项属于“不同选择会产出不同工作”的决策，按 `CLAUDE.md` 任务标准流程需先确认。
>
> **D1 / D3 已裁决（2026-11-22，ADR-0038）**：`COMPLETED` 允许进入 Dataset Builder 与标注抽样（状态机保证其质量结论为 PASS）；Canonical 正式版本为 `ddp.canonical/1.0.0`，导入层改为引用 `SCHEMA_VERSION`，历史 `-draft` 数据不迁移。D2 / D4 / D5 待各自任务开工时裁决。

## 6. 本 DAG 明确不含

- 不重写设备、采集、上传、Episode、标注、Dataset、ModelOps 领域。
- 不新建微服务、Kafka、Celery、Kubernetes。
- 不做机器人运动 / 远程控制。
- 不做多甲方商务与供应商结算。
- 不把 AI 预标注当作最终真值。
- 不把未配置的外部训练平台标为已接通。
- 不在本轮执行 staging / 生产部署、迁移或数据删除。
