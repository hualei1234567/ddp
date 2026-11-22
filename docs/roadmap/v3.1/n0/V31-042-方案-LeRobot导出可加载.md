# V31-042 方案：LeRobot 导出从「元数据 profile」升级到可被 Loader 加载

- 状态：`PLANNED`（方案已定，未实施）
- 起草日期：2026-11-22
- 事实基线：分支 `feat/v31-041-canonical-export-format` @ `2b1a6a8`（V31-041 交付后）
- 关联：ADR-0046（Canonical 格式与 LeRobot 语义分离）、V31-017、V31-018、V31-044
- 约束：不新增数据库表/迁移；新增依赖必须单独评估（`CLAUDE.md` §三）

> **读法**：本文件是**方案**，不表示能力已具备。文中「现状」均有代码证据；
> 「目标」属于 `PLANNED`，不得在验收材料里当成已上线能力。

## 1. 现状（已验证）

`backend/app/domain/datasets/exporting.py` 的模块文档已明说产物是元数据 profile：

```
未实现真实 LeRobot 帧级映射、视频/observation/action 对齐与 Loader 兼容性验证；
因此 verification_status 固定 UNVERIFIED，不得宣称已兼容 LeRobot 生态。
```

实际产出文件：

```
meta/info.json
meta/episodes.jsonl
meta/media.jsonl
data/episodes.jsonl      ← DDP 自有训练表（NDJSON），不是 LeRobot 的 parquet
meta/ddp_divisions.json
ddp/sidecar.json
```

**与真实 LeRobot dataset 的差距**（决定 V31-042 的工作量）：

| LeRobot 必需 | 现状 | 差距 |
|---|---|---|
| `meta/info.json` 的 `codebase_version` / `fps` / `features` / `data_path` / `video_path` | 只有 DDP 自有键 | `MISSING` |
| `meta/tasks.jsonl` | 无 | `MISSING` |
| `meta/stats.json` | 无 | `MISSING` |
| `data/chunk-*/episode_*.parquet` | 用 NDJSON 训练表 | `MISSING` |
| `videos/`（observation 视频） | 媒体在对象存储，未按 LeRobot 布局落盘 | `MISSING` |
| 逐帧 `observation.*` / `action` 列 | DDP Episode **没有**逐帧动作存储 | `MISSING` |

结论：当前 `format=LE_ROBOT` 是**命名上的 LeRobot**，不是能被
`LeRobotDataset(...)` 加载的数据集。这正是 ADR-0046 要区分 `CANONICAL` 的原因。

## 2. 可复用能力（已存在，禁止重造）

| 能力 | 位置 | 用途 |
|---|---|---|
| LeRobot v3 `info.json` / `episodes/*.parquet` 结构解析 | `data-platform/ddp_data/lerobot/v3_time_manifest.py` | 写入侧字段名与语义对齐，不凭记忆编写 |
| Human-1M 元数据映射与 Canonical Episode | `data-platform/ddp_data/lerobot/parser.py` | 读取侧既有约定与错误口径 |
| Parquet 读写 | `data-platform/ddp_data/parquet/` | 写 `data/chunk-*/episode_*.parquet` |
| pyarrow 依赖 | `data-platform/requirements.txt`（23.0.1） | **已在 data-platform**，无需在 backend 新增 |
| 确定性 JSON/NDJSON 与 Hash | `backend/app/domain/datasets/canonical.py` | 产物可复算、可回读校验 |
| 导出 Worker 与产物清单 | `backend/app/workers/` + `ExportArtifactSpec` | 只替换产物构建，不动状态机 |

## 3. 关键决策点

### 3.1 产物构建放在哪一层

| 方案 | 做法 | 结论 |
|---|---|---|
| **A** | `data-platform/ddp_data/lerobot/export_writer.py` 纯函数组装 LeRobot 文件（含 parquet 字节），backend `exporting.py` 调用 | **推荐**：pyarrow 已在 data-platform，避免 backend 引入约 40MB wheel；符合「纯数据能力放 data-platform」边界 |
| B | backend 直接引入 pyarrow | 不采纳：违反依赖最小化 |

### 3.2 帧级是否真做

真实训练需要逐帧 `observation.*` / `action`。DDP Episode 目前只有 Episode 级
元数据，`data-platform` 虽有 HDF5 / rosbag / mcap 解析，但未接入 Episode 的
逐帧存储。

| 方案 | 做法 | 代价 | 结论 |
|---|---|---|---|
| **A** | 先做 **v2.1 结构合规 + 元数据可加载**：写出合法 `info.json` / `tasks.jsonl` / `stats.json` / 每 Episode parquet（只含 episode 级列），`features` 显式声明缺失模态；新增 `loadable_scope: METADATA_ONLY` | 仍不能直接训练，但**不谎称** | **推荐** |
| B | 先接逐帧数据链路再做 | 依赖 V31-044 真实数据与存储扩展，范围成倍放大 | 拆到后续 |

**硬约束**：在真实 Loader 实际加载成功之前，`verification_status` 只能是
`UNVERIFIED`，不得写 `VERIFIED_BY_LOADER`。

### 3.3 验证方式

在隔离 venv 里装固定版本 LeRobot，实际执行一次
`LeRobotDataset(repo_id, root=<导出目录>)` 并记录证据，才允许把
`verification_status` 置为 `VERIFIED_BY_LOADER` 并填 `verification_evidence_ref`。
无网络或依赖不可得时标 `BLOCKED`，不得降级成「代码看起来对」。

## 4. 增量切片（每片独立可测）

1. **契约**：`export-job` 增加 `loadable_scope`（`METADATA_ONLY` / `FRAMES`）；
   写清 `verification_status` 由 Loader 实测决定；契约版本递增并登记 changelog。
2. **写入器**：`data-platform/ddp_data/lerobot/export_writer.py` + 单测
   （纯函数，同输入两次产出字节一致）。
3. **接线**：backend `exporting.py` 调用写入器。**保留**既有 NDJSON 训练表与
   `ddp/sidecar.json`（DDP 内部消费者依赖），只新增 LeRobot 必需文件——不删资产。
4. **Worker 端到端**：同一输入两次导出 Hash 一致；产物清单含 LeRobot 必需文件；
   变异验证（去掉一个必需文件后测试必须失败）。
5. **Loader 实测**：隔离环境执行加载；结果写入证据字段。

## 5. 明确不做

- 不修改 `CANONICAL` 语义，也不推翻 V31-041 已交付的 `LE_ROBOT` 标识。
- 不在 backend 引入 pyarrow。
- 不在真实 Loader 跑通前修改 `verification_status`。
- 不做逐帧动作存储的数据库扩展（属后续任务，需单独 ADR）。

## 6. 风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| LeRobot 版本漂移 | `info.json` 的 `codebase_version` 决定布局（v2.0 / v2.1 / v3 差异明显） | 固定版本并写进 ADR；解析侧已按 v3 实现，写入侧对齐同一版本 |
| 「结构合规」被误读为「可训练」 | 只做元数据时下游可能直接开训 | `loadable_scope: METADATA_ONLY` + `verification_status: UNVERIFIED` 双标注 |
| 产物膨胀 | 每 Episode 一个 parquet，文件数随 Episode 增长 | 按 `chunks_size` 分 chunk，与 LeRobot 布局一致 |
| 依赖许可 | pyarrow Apache-2.0，已在使用 | 无需新增评估 |
