# V61_Migration_Contract_Risk — 数据库与 API 契约风险（S0-D 实测）

## 1. 迁移链健康证明

| 项 | 实测值 |
|---|---|
| `alembic heads` | **单 head `0086`** ✅ |
| 文件数 / history 步数 / revision 声明 | 76 = 76 = 76，三者一致，无分叉/孤儿 |
| 生产库（55432/ddp） | 0086，与仓库一致 |

## 2. V6.1 表变更预判（0087+ 第一依赖）

| 预判变更 | 现状 | 所需动作 | 影响里程碑 |
|---|---|---|---|
| `video_frames`（VideoFrameIndex） | **表缺失**（领域层 `sync_frame_index.py` 有计算、无落库） | 新建表：asset_id/stream_id/frame_index/pts/timestamp_raw/normalized/time_base/宽高/pixel_format/codec/keyframe/decode_status/frame_object_ref/frame_hash/parser_version | M2 |
| STREAM_EXTRACTION 阶段 CHECK 枚举 | 现有 N 阶段 | 追加 VIDEO_DECODE / FRAME_INDEX / TIMESTAMP_NORMALIZE / SENSOR_SYNC / OBSERVATION_MAP（或按最小集）——同迁移改 CHECK + 领域枚举 + 契约 | M2 |
| `training_run_evidence` | 主表缺失（ADR-0036 引用行即 run） | 新建扩展表（见 Training_Environment_Report §5 方案 A） | M4 |
| `clock_sync_evidence`（如需查询级） | 领域计算有、落库无 | 可先随 video_frames 的 sync 列，独立表视 M3 需求 | M2/M3 |
| Canonical image 字段 | Episode 仅 `video_stream_ids` | Canonical v1 向后兼容扩展（data-platform 层，非 DB 迁移；json_schema 同步再生成 `export_canonical_schemas --check` 会把生成物差异暴露在 CI） | M2 |

**迁移纪律（既有）**：down_revision 预分配串行、双方言（PG+SQLite）同构、upgrade/downgrade 往返验证、多 PR 合并时重排——V6.1 继续强制。

## 3. 契约影响面

| 项 | 现状 |
|---|---|
| version | 1.40.0（**只允许一个 PR bump**） |
| parity | strict 通过（基线 68 项） |
| V6.1 新端点预判 | video frames 查询 / training run evidence / badcase 前端所需 API（部分已有）/ data requirement（已有）→ 触碰 `paths` fragments + components |
| 风险 | Canonical schema 再生成与 parity 基线联动；**建议每个里程碑单 PR bump version**（既有惯例），fragments 按域拆分降低冲突 |

## 4. 风险清单（Top 5）

1. ** Canonical 扩展牵动三处**：v1.py 字段声明 + `export_canonical_schemas.py --check` 生成物 + 前端类型——一处改漏即 CI 红。对策：单一 PR 内三件套同改。
2. **`video_frames` 体量决策**：25,650 帧（单样本）× 多样本后行数百万级——逐帧持久化需按蓝图 06 号"基于样本规模与查询需求"再评估；对策：M2 先按帧落库+分区阈值复用阶段 6 表审计结论（500 万行评估线）。
3. **AV1 解码兼容**：G1 样本 AV1 编码，ffmpeg 需带解码器——Video Fixture 第一步实测，不通则转码样本或换样本。
4. **外键与项目隔离**：video_frames 必须 project_id 隔离 + RawAsset CASCADE（对齐 webhook_outbox 模式），漏写即越权风险。
5. **迁移与长 CI**：76 迁移全量 downgrade 基线到 0003 再升级的 CI 步骤随迁移数线性变慢——已 45min CI 的背景下，V6.1 每批迁移建议 ≤3 个文件。

## 5. 回滚方案基线

全部新表 downgrade = drop table（严格逆操作）；CHECK 枚举扩展的 downgrade 收回枚举（需先确认无新值行——迁移内写防御断言，仓库已有同款先例）。
