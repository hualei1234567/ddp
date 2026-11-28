# V61_Video_Gap_Report — 视频能力缺口报告（S0-B 实测）

## 1. 现状分级（Adapter 六级对照）

六级矩阵：**DETECT → INSPECT → PAYLOAD_PARSE → STREAM_EXTRACT → EPISODE_BUILD → ROUND_TRIP**

| 格式 | 当前级 | 证据 | 差距 |
|---|---|---|---|
| MP4 | INSPECT | `ddp_data/media/mp4.py`（ffprobe metadata：时长/帧率/分辨率/编码；唯一生产调用点 `processing/metadata_stage.py:531`，duration 用于 Episode 边界） | 无解码、无 PTS、无帧索引 |
| HDF5 | ROUND_TRIP | RoboMimic E1 | 自认「无图像流、视频帧级提取未验证」 |
| LeRobot 导出 | ROUND_TRIP | Frame Loader E1 | `video_provided: False`，parquet 列仅 state/action/timestamp/索引 |
| MCAP / ROS Bag | INSPECT | metadata 只读（footer/summary/channel，不解码消息体） | 图像 topic 无处理 |
| Parquet | INSPECT | footer/schema 只读 | — |
| local-adapter ffmpeg | —（推理预处理） | `frames.py`：抽帧缩放 N×N **灰度**喂模型 | 与 Episode 物化无关，不复用为解码链 |

## 2. 五层缺口（按数据流）

| # | 缺口 | 现状证据 | V6.1 需补 |
|---|---|---|---|
| 1 | **视频解码** | 全仓 0 处帧级解码代码 | VIDEO_DECODE 阶段：ffmpeg 子进程按 PTS 解码（复用 mp4.py 安全模式：argv 列表/超时/输出上限/脱敏） |
| 2 | **PTS/时间实测** | `pts` 全仓 0 命中；`_SHOW_ENTRIES` 不含 pts_time/start_time；offset/drift 全部来自声明值 | ffprobe `-show_frames -show_entries frame=pts_time` 实测链 + timestamp_normalized |
| 3 | **Frame Index 持久化** | 无 `video_frames` 表；领域层 `sync_frame_index.py` 有计算无落库 | VideoFrameIndex 模型 + 迁移 0087（字段按蓝图 06 号：pts/hash/keyframe/decode_status/frame_object_ref…） |
| 4 | **图像 Observation** | Canonical Episode 只有 `video_stream_ids` ID 引用，无像素字段 | Canonical 扩展 image feature 声明（尺寸/通道/压缩/源帧索引），媒体大对象不入库 |
| 5 | **LeRobot video feature** | `frame_writer.py` `video_provided: False`，video_path 仅模板串 | video key 写出 + 官方 Loader 解码验证（GATE-A video 分支） |

## 3. 时间同步缺口（专项）

- `sync_analysis.py` / `sync_drift.py`：整流窗口级比对，输入来自**声明值与合成 fixture**；E2E 如实标 `sync_status=DEGRADED / sync_method=NONE`；
- 缺：视频流 PTS 实测 → 与状态流时间戳的误差分布 → ClockSyncEvidence 落库；
- 蓝图红线遵守：**保留原始时间戳**（pts_time + time_base 原样入库，normalized 为衍生列）。

## 4. 工具前提

| 工具 | 本机状态 | 用途 |
|---|---|---|
| ffmpeg/ffprobe | ✅ 已是部署前提（compose local-adapter 已装） | 解码 + pts 实测 |
| AV1 解码器 | **待实测**（G1 样本为 AV1） | Video Fixture 第一步：`ffmpeg -i pusht.mp4 -f null -` 验证可解 |
| pyav / opencv | 未引入 | **默认不引入**（遵守 §4.2：ffmpeg 子进程即可，除非性能基准证明需要） |

## 5. M2 最小路径（3 个增量，全部复用现有底座）

1. **T1-01 Video Fixture**：登记 pusht G1+G2（sample_manifest + Hash + AV1 解码实测 + expected 25,650 帧）；
2. **T1-02/03 Decode+Index**：`media/` 内新增 `video_decode.py`（ffmpeg 帧抽取 + pts_time 实测）→ 写 `video_frames`（迁移 0087，复用 mp4.py 安全模式 + frames.py 子进程模式）→ STREAM_EXTRACTION 扩展 VIDEO_DECODE/FRAME_INDEX 两阶段（同迁移改 CHECK 枚举）；
3. **T1-04/06 Observation+Loader**：Canonical 扩展 image 引用字段（向后兼容，不破 1.40.0 已验字段）→ frame_writer 开 `video_provided=True` → GATE-A 官方 Loader 解码 E1。

**验收标准（蓝图 06 号 §8 原文执行）**：真实样本视频流解析+帧数/时间戳可核验；抽样帧与原视频解码一致；同步误差可报告；LeRobot video feature 导出；固定 Loader 版本解码成功；缺帧/损坏/可变帧率 fixture 有明确结果。
