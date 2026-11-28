# S0 审计报告 B：多模态视频链路缺口（审计代理 B，只读）

- 日期：2026-11-28
- 仓库：`/Users/hualei/vscodeworkspace/DataPlatform` @ HEAD（2026-11-27 23:40）
- 范围：`media/mp4.py` 调用链、LeRobot 逐帧写出 feature、Golden Sample 盘点、时间同步实现
- 性质：只读审计，本文档为唯一产出

---

## ① 现状表（核验 + 补全）

| 项 | 主控已知 | 本审计核验/补全 |
|---|---|---|
| `ddp_data/media/mp4.py` | ffprobe metadata 只读探测（时长/帧率/分辨率/编码），无解码 | ✅ 属实。`probe_mp4()` 只读前 12 字节 ftyp + 受控 ffprobe 子进程（argv 传参、超时 5s/上限 120s、输出 256KB 上限、≤32 streams），产出 `Mp4ProbeResult.as_metadata()`（format_name/duration_ms/fps/width/height/frame_count/video_codec/audio_codec/stream_count） |
| **mp4.py 被谁调用（生产代码）** | — | 唯一生产调用点：`backend/app/application/processing/metadata_stage.py:531-537` `_extract_mp4()` → metadata 提取阶段写入 `RawAsset.raw_metadata`。下游消费：`backend/app/application/processing/episode_boundaries.py` 与 `episode_creation.py:37` 用 `duration_ms` 定 Episode 边界（注释明确"边界来自 probe_mp4 的 duration，不是人工输入"）。其余 4 处均为测试（`test_mp4_probe.py`、`test_processing_metadata_stage.py`、`test_processing_metadata_pipeline.py`、`test_processing_worker_segmentation.py`） |
| `services/local-adapter/app/frames.py` | ffmpeg 抽帧但缩放 N×N 灰度（推理预处理） | ✅ 属实。`frames.py:116`：`filter_expr = f"scale={size}:{size}:flags=area,format=gray"` —— 与 Episode 物化无关，但证明 ffmpeg 抽帧的工程模式在本仓已有先例 |
| Canonical v1 Episode | 只有 `video_stream_ids: tuple[str,...]` ID 引用 | ✅ 属实。`ddp_data/canonical/v1.py:213,493`（LEGACY 映射 `media_assets→video_stream_ids`）；`packages/contracts/schemas/canonical-v1/frame.v1.schema.json` 无任何 video/image 像素字段，Frame 仅有 `time_base_ref` |
| `frame_writer.py` | `"video_provided": False`，video_path 只是模板串 | ✅ 属实。`frame_writer.py:275` `video_path` 是纯模板（官方 Loader 占位），`:285` `video_provided: False`；docstring 明言"图像类 feature 需要真实视频，本模块不做" |
| **frame_dataset/frame_writer 支持的 feature（parquet 列清单）** | — | Parquet 列（`frame_writer.py:209-226`）：`episode_index`(int64)、`frame_index`(int64)、`index`(int64 跨集全局)、`task_index`(int64)、`timestamp`(float32 秒)、`split`(string)、`observation.state[i]`(float32×obs_dim)、`action[i]`(float32×act_dim)。**无任何图像/视频 feature**。多 Episode 数据集（`frame_dataset.py`）另产出 `meta/info.json、stats.json、episodes_stats.jsonl、episodes.jsonl、tasks.jsonl` |
| `ddp_data/` 各 adapter | hdf5/mcap/rosbag/droid/parquet 均为 metadata 级 | ✅ 属实，见六级矩阵 |
| **六级能力矩阵定义（capability.py 顶部）** | — | `FormatCapabilityLevel`（ADR-0048 §3.1）：`DETECT`(魔数) → `INSPECT`(结构/头信息) → `PAYLOAD_PARSE`(载荷) → `STREAM_EXTRACT`(逐流提取) → `EPISODE_BUILD`(组 Episode) → `ROUND_TRIP`(往返)。三条防虚报约束：连续无跳级、max_level>DETECT 时 evidence 必须指向仓库内可核验文件、limitations 严禁为空。当前矩阵（`BUILTIN_FORMAT_CAPABILITY_MATRIX`）：**hdf5=ROUND_TRIP**（仅 robomimic_hdf5_v1.5 布局；且 limitation 自认"ph 样本不含图像流，视频帧级提取与解码尚未验证"）、**lerobot=ROUND_TRIP**（LOADER_FRAME 逐帧 Parquet，video_provided=false）、**mp4/parquet/mcap/rosbag=INSPECT**、human1m_preview/generic_jsonl=INSPECT |

---

## ② Golden Sample 候选清单

盘点命令均已执行（2026-11-28）。

| # | 路径 | 大小 | 格式 | 含图像? | 可行性 | 说明 |
|---|---|---|---|---|---|---|
| 1 | `~/.cache/huggingface/hub/datasets--lerobot--pusht/blobs/f58d1185…b3a78` | **6.6MB** | MP4 (AV1, 96×96, 10fps, 25650 帧, 2565s) | **是** | **本机有** | 官方 LeRobot v2.1 `observation.image` 视频，真实像素。配套：659KB Parquet（state/action 逐帧）、104KB Parquet、info/stats JSON，共 7.3MB。⚠️ snapshot 符号链接已失效（`snapshots/**/chunk-000` 为空目录），需按 HF 布局重建 symlink 或直接解析 blobs |
| 2 | `~/.cache/huggingface/hub/…/blobs/9abc0431…bf01` | 659KB | Parquet（LeRobot v2.1 data chunk） | 否（数值列） | **本机有** | pusht 的逐帧 observation.state + action，与 #1 同帧率配对——#1+#2 即构成"视频+状态+动作"完整 Golden Path 输入 |
| 3 | `docs/testing/fixtures/stage2/samples/cameras/cam_front.mp4`、`cam_wrist.mp4` | 各 21KB | MP4 容器 | **否** | 本机有（不可用） | ffprobe 实测 **streams 为空数组**——只有容器壳无媒体流，仅够魔数/容器识别测试，不能做像素级 Golden Sample |
| 4 | `backend/.venv/.../h5py/tests/data_files/*.h5` | 各 <100KB | HDF5 | 否 | 本机有（不可用） | h5py 自带合成测试文件，非机器人 Episode |
| 5 | `docs/testing/fixtures/stage2/samples/formats/sample.h5 / sample.mcap / sample.db3` | 100–181B | HDF5/MCAP/rosbag | 否 | 本机有（不可用） | 极小探针 fixture，仅 INSPECT 级测试用 |
| 6 | MinIO `ddp-raw` 桶（`raw/`、`datasets/` 等 11 个前缀） | — | — | — | 空 | `find /bitnami/minio/data -name "*.mp4|*.hdf5|*.h5|*.mkv|*.mcap|*.bag" -size +1k` → **0 个文件**。全桶只有 UUID 目录骨架与元数据，无媒体对象 |
| 7 | `/Users/hualei/Downloads` | — | — | — | 空 | 无任何 *.hdf5/*.h5/*.mp4/*.mkv/*.mcap（仅 PDF/docx/zip 蓝图包） |
| 8 | `/Users/hualei/vscodeworkspace/ddp-samples` | — | — | — | **目录不存在** | — |
| 9 | `backend/tests/fixtures/public/robomimic-hdf5/` | 1.3KB | JSON only | 否 | 需外部提供 | 仅 `facts.json`，**真实 robomimic .hdf5 文件不在仓内**——六级矩阵 hdf5=ROUND_TRIP 的证据样本本体缺失，需重新下载 |
| 10 | HF `lerobot/pusht` 完整数据集 / `lerobot/svla_so101_pickplace` 等 | ~100MB–数GB | LeRobot v2.1（MP4+Parquet） | 是 | 可下载 | 已有 pusht 缓存兜底；若需更高分辨率/双机位可增量下载 |
| 11 | NVIDIA Human-1M / DROID 原始包 | GB 级 | 多模态 | 是 | 需外部提供 | import 侧仅 INSPECT（preview metadata.jsonl），需外部介质 |

**结论：全机唯一的"真实视频像素 + 配套逐帧状态/动作"样本是 HF 缓存里的 lerobot/pusht（#1+#2）。**

---

## ③ 时间同步缺口

grep 全仓 `time_base|pts|clock_offset|drift`（474 处命中）核验结果：

**已有的真实实现（除 mp4.py 外）：**

| 模块 | 内容 | 深度 |
|---|---|---|
| `backend/app/domain/episodes/sync_drift.py` | 多流时间窗口偏差检测：`StreamWindow(start/end_time_ns)` → ALIGNED/EXCEEDED/UNKNOWN/SINGLE_STREAM，默认容忍 40ms，纯函数无 IO | **区间级**（整流窗口重叠），非帧级 |
| `backend/app/domain/episodes/time_alignment_manifest.py` + `sync_analysis.py` + `sync_frame_index.py` | offset_to_utc_ns/drift_ppm/uncertainty_ns 对齐画像与 manifest，有单测（`test_time_alignment_manifest.py`） | 画像/manifest 级 |
| `backend/app/domain/episodes/validation.py:155` `validate_time_base()` | 9 键白名单 + sync_status ∉ {UNVERIFIED,FAILED} fail-closed，DB CHECK 四层 | 校验级（不测量） |
| Canonical v1 Frame `time_base_ref`（`v1.py:78,235`）+ 契约 `frame.v1.schema.json` | Frame→时间基准引用已入 schema | 引用级 |

**缺口（决定性）：**

1. **无 PTS/时钟级提取**：全仓 `*.py` 中 `\bpts\b` **0 命中**；`mp4.py` 的 `_SHOW_ENTRIES` 只取 duration/fps/width/height/nb_frames，**不含 `pts_time`、start_time、time_base**。视频帧的真实时钟信息从未进入管线。
2. **offset/drift 无一处从真实媒体流测得**：现有 drift_ppm/offset 全部来自声明值、合成 fixture（`docs/testing/fixtures/stage2/samples/metadata/time_alignment_cases.json`）或人工 manifest——E2E 里 `sync_status=DEGRADED / sync_method=NONE` 正是"如实标注未测量"的证据。**M2 若宣称 VERIFIED 必须有实测来源。**
3. 视频帧与状态流的对齐只能靠"帧序号 × 标称 fps"换算（`frame_dataset.py:245-247` 的 `pos/fps` 兜底路径），一旦视频有丢帧/变帧率即错位，且无法检出。

---

## ④ M2 Golden Path 推荐

**选样：`lerobot/pusht` HF 缓存（清单 #1+#2）**——唯一同时具备真实视频像素与配套逐帧 state/action、且本机已有的样本；LeRobot v2.1 布局与 `frame_writer.py` 输出格式完全同构，无格式翻译成本。cam_*.mp4 fixtures 无流、MinIO 与 Downloads 为空，均不可用。

**最小链（全部复用现成代码，仅 3 个新增量）：**

```
pusht MP4 blob ──① probe_mp4()──→ duration/fps/分辨率/编码 metadata（现有，零改动）
       │
       └─② [新增A] ffmpeg 抽帧（复用 local-adapter frames.py 的工程模式，
            去掉 scale/gray filter，保留原分辨率彩色）：
              ffmpeg -i in.mp4 -vf fps=10 -f image2 frame_%06d.png
            → 逐帧物化为 Observation(image)
pusht Parquet  ──③ 现有 frame_dataset.parse_stream_extraction_ndjson 同构读取
                     state/action/timestamp → Observation(state)/Action
                     （帧率同为 10fps，按 frame_index 对齐）
       ↓
④ STREAM_EXTRACT → EPISODE_BUILD（hdf5 adapter 已打通同级管线，模式可平移）
       ↓
⑤ 现有 write_frame_dataset() 扩展 image feature → video_provided=True，
   video_path 从模板串变为真实封装（新增B：ffmpeg H.264 封装回 mp4）
       ↓
⑥ [新增C] PTS 实测对齐：ffprobe -show_frames -show_entries frame=pts_time
   （有界截断，沿用 mp4.py 的 argv/超时/输出上限安全模式）
   → 视频帧 pts ↔ Parquet timestamp 求 offset/drift
   → 喂给现有 sync_drift.StreamWindow → 首个实测来源的 sync_status=VERIFIED
```

**验收口径（与六级矩阵对齐）：**
- mp4 能力从 INSPECT 抬升证据链：STREAM_EXTRACT（抽帧实测）→ 与状态流组 Episode（EPISODE_BUILD）
- lerobot 写出侧补上视频 feature 后，"video_provided=false" limitation 可移除
- 时间同步从"声明/合成"升级为"实测 offset/drift + 误差分布"，补上 §③ 缺口 2/3

**风险提示：** pusht 缓存 snapshot symlink 已失效需重建；AV1 解码依赖本机 ffmpeg（已确认 `/opt/homebrew/bin/ffmpeg` 存在）；96×96 分辨率偏小，仅够打通链路，训练级样本建议后续增量下载更高分辨率数据集（清单 #10）。
