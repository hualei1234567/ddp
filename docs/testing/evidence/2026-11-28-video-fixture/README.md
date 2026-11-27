# T1-01 视频链 Fixture 准入证据（lerobot-pusht AV1 Golden Sample）

- 任务：DDP V6.1 / T1-01「AV1 解码实测 + Fixture 登记」
- 分支/工作树：`feat/v61-video`（`/Users/hualei/vscodeworkspace/wt-v61-video`）
- 基线提交：`c5f78be`
- 执行日期：2026-11-28
- 数据等级：T2 公开数据集样本；lerobot 官方 pusht 数据集，Apache-2.0 许可体系
  （Hugging Face `datasets--lerobot--pusht` 缓存 blob，未做任何修改）。
- 工具版本：ffmpeg / ffprobe 9.0.2（`/opt/homebrew/bin`，local-adapter 同源依赖）

## 1. 样本登记

| 项 | 值 |
|---|---|
| 视频样本 | `~/.cache/huggingface/hub/datasets--lerobot--pusht/blobs/f58d11857651dad5983c021b56a50a68a6ce19068834c1fb5cac099219fb3a78` |
| SHA256 | `f58d11857651dad5983c021b56a50a68a6ce19068834c1fb5cac099219fb3a78` |
| 大小 | 6,890,970 字节（6.6 MB） |
| 状态/action 伴生样本 | `~/.cache/huggingface/hub/datasets--lerobot--pusht/blobs/9abc0431f12d10c33b5b37b1bf1e6e557c8ae1c8c29fb86658969e48fcbbbf01`（659 KB，Parquet） |
| Parquet SHA256 | `9abc0431f12d10c33b5b37b1bf1e6e557c8ae1c8c29fb86658969e48fcbbbf01` |
| 许可 | lerobot 官方 pusht 数据集，Apache-2.0 系（Hugging Face 数据集页声明） |

两个 blob 的文件名即 Hugging Face hub 的 SHA256 寻址，`shasum -a 256` 本地实测
与文件名一致，样本未被改动。

## 2. AV1 解码实测（2026-11-28）

命令与结果（完整解码校验，输出丢弃到 null）：

```bash
ffmpeg -v error -i <G1路径> -f null -
```

实测（ffmpeg 9.0.2，macOS arm64）：

- 退出码：**0**
- stderr：**空**（`-v error` 下无任何解码错误输出）
- 耗时：**2.705 s**（25650 帧全部解完，本机 AV1 软解可用）

结论：**AV1 可解**。96×96@10fps、6.6MB 的 AV1 流在本机 ffmpeg 下可完整解码，
逐帧准入流程可依赖此能力；如需硬解加速另行评估，与本任务无关。

## 3. ffprobe 实测输出

流级元数据：

```text
$ ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,duration \
    -of default <G1路径>
[STREAM]
codec_name=av1
width=96
height=96
r_frame_rate=10/1
avg_frame_rate=10/1
duration=2565.000000
nb_frames=25650
[/STREAM]
```

逐帧 `pts_time`（`ddp_data.media.video_decode.probe_frames` 的底层命令同参）：

```text
$ ffprobe -v error -print_format json -show_frames \
    -show_entries frame=media_type,pts_time -select_streams v:0 <G1路径>
```

- 返回帧数：**25650**（与流级 `nb_frames` 声明一致）
- JSON 体积：2,400,025 字节；探测耗时约 2.4 s
- 首帧：`{"media_type": "video", "pts_time": "0.000000"}`
- 末帧：`{"media_type": "video", "pts_time": "2564.900000"}`
- 等间距 0.1 s/帧（10fps × 2565 s = 25650 帧，自洽）

**expected 帧数 = 25650**。后续帧对账以此为准；`pts_time` 原始十进制字符串
（0.000000 → 2564.900000）为对账基准，模块层不做归一化。

## 4. 模块与测试验证

- 新模块：`data-platform/ddp_data/media/video_decode.py`
  （`probe_frames`，安全模式镜像 `mp4.py`：argv 列表、`file:` 前缀、超时
  kill、字节/帧数上限、错误脱敏、显式异常）
- 单测：`data-platform/tests/test_video_decode.py`（假 ffprobe 回放隔离 +
  `cam_front.mp4` 21KB 空壳作"无流"负样本）
- 默认命令：`backend/.venv/bin/python -m pytest data-platform/tests/test_video_decode.py -q`
  → **18 passed, 1 skipped**（真样本用例需 `DDP_TEST_REAL_VIDEO` 注入路径）
- 注入真样本实跑：
  `DDP_TEST_REAL_VIDEO=<G1路径> backend/.venv/bin/python -m pytest data-platform/tests/test_video_decode.py -q`
  → **19 passed**（含 golden 用例：帧数 25650、首末 pts_time、单调性校验）

## 5. 边界声明

- 本目录只登记证据，不含样本文件本体（6.6MB 样本留在 HF 缓存原位，不复制
  进仓库）；复现命令见上文。
- 只读不写：本次准入仅探测；未转码、未重封装、未修改时间戳。
- `cam_front.mp4`（`docs/testing/fixtures/stage2/samples/cameras/`）为 21KB
  空壳 MP4（ftyp 合法、无流无帧），单测中作为"无流"负样本，探测返回空列表。
