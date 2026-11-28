# V61_Golden_Sample_Inventory — Golden Sample 盘点（S0-B 实测）

> 采集时间 2026-11-28 · 全部本机实测（ls / ffprobe / MinIO 列桶）

## 1. 候选样本清单

| # | 样本 | 路径 | 大小 | 格式 | 含图像/视频 | 配套状态/动作 | 可行性 |
|---|---|---|---|---|---|---|---|
| **G1** | **lerobot/pusht 视频** | `~/.cache/huggingface/hub/datasets--lerobot--pusht/blobs/f58d1185…` | **6.6 MB** | MP4（AV1 96×96 @10fps） | **是（25,650 帧真实像素）** | — | **本机有** ✅ |
| **G2** | **lerobot/pusht Parquet** | 同目录 `blobs/9abc0431…` | 659 KB | Parquet | — | **是（逐帧 state/action）** | **本机有** ✅ |
| G3 | cam_front.mp4 / cam_wrist.mp4 | `docs/testing/fixtures/stage2/samples/cameras/` | 各 21 KB | MP4 | **否（ffprobe 实测 streams 为空，仅容器壳）** | 否 | 不可用（仅 DETECT 级 fixture） |
| G4 | RoboMimic `ph` HDF5 | 验收报告引用（36.7MB，SHA256 已核验） | 36.7 MB | HDF5 | **否（不含图像）** | 是（59 帧/20Hz/obs32/act7） | 仓内仅 facts.json，原始文件不在仓 |

> G1+G2 组合 = **完整的「视频 + 状态 + 动作」输入**，且同属 lerobot/pusht 官方数据集（许可明确、版本 0.3.3 兼容），天然对齐 LeRobot video feature 目标格式。

## 2. 其他来源核验结果（均无媒体文件）

| 来源 | 结果 |
|---|---|
| MinIO `ddp-raw` 桶 | 无媒体文件 |
| `~/Downloads` | 无 hdf5/mp4/mkv/mcap |
| `~/vscodeworkspace/ddp-samples` | 目录不存在 |
| HF 缓存其余 | 仅 lerobot/pusht 一族 |

## 3. 推荐 Golden Path（M2 主链）

**选定 G1+G2（lerobot/pusht）作为 V6.1 主 Golden Path**，理由：

1. **可获得性**：本机已有，无需外部提供，零等待；
2. **许可**：LeRobot 官方示例数据集（Apache-2.0 系），用途限制明确；
3. **格式文档完整度**：LeRobot v2.1 规范即本仓导出目标格式，双向对齐；
4. **同步质量**：官方数据集视频与 Parquet 逐帧 state/action 天然同步（同 episode_index/frame_index）；
5. **可复现性**：HF hub 固定 revision 可锁 Hash；
6. **业务价值**：直接支撑 V61-005/006/007/008/011 五个 P0。

**RoboMimic `ph`（G4）保留为「无图像 HDF5 回归样本」**：继续验证"视频缺失不破坏既有链路"的反向用例。

**硬件现场样本（A 级）**：待 Pilot 硬件到位后补录，与 M5 绑定，不阻塞 M2。

## 4. 准入门禁（按蓝图 05 号执行）

进入 Golden Path 前必须登记：sample_id、来源（HF dataset + revision）、许可、SHA256、容器/编码、帧率口径（nominal vs measured）、时间戳来源、传感器清单、Calibration（pusht 无真机标定 → 标 ABSENT）、脱敏状态、expected frame count/timestamps、复现命令。

**pusht 已知限制（如实）**：96×96 低分辨率、AV1 编码（ffmpeg 需带 libaom/dav1d 解码器——需在 Video Fixture 步骤先实测本机 ffmpeg 可否解码 AV1）、10fps 与状态流对齐方式需在 Frame Index 步骤核实。
