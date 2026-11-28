# GATE-A 视频 Loader 端到端验证（V6.1 / V61-008）

## 验收结论：**GATE-A（视频）通过**

| 项 | 结果 |
|---|---|
| 数据集 | DDP 导出的 LeRobot v2.1 **含视频 feature**（observation.image，96×96，100 帧真实机器人推物视频） |
| Loader | 官方 `lerobot==0.3.3` `LeRobotDataset`（**非 mock**） |
| 加载 | 100 帧全部加载，帧数与导出一致 |
| **视频解码** | 首/中/尾 3 个采样点真实解码为 (3,96,96) float 张量，像素域 [0,1] 合规 |
| state/action | 同帧返回（obs 2 维 / act 2 维，来自 pusht 真实数据） |

**证据等级**：E1（真实环境实跑）。

## 与 GATE-B 的关系（蓝图红线 9）

GATE-A（本批）：官方 Loader 读得动含视频的数据集；
GATE-B（前批 PR #172）：目标训练脚本真实消费。
**两个独立状态，分别验收**——本批补齐 GATE-A 的视频分支。

## 环境依赖（torchcodec 的真实缺口，如实记录）

lerobot 0.3.3 的视频解码走 **torchcodec**，其 macOS 版通过 @rpath 加载
FFmpeg 4–7 的共享库（libavutil.56–59）。本机 ffmpeg 9.0.2 太新（libavutil.60），
torchcodec 四个候选 core 全部加载失败。

**修法**：`brew install ffmpeg@7`（libavutil.59 等共享库），并把
`/opt/homebrew/opt/ffmpeg@7/lib/*.dylib` 链接到 Python 运行时的 lib 目录
（torchcodec 的 @rpath 只搜该目录，DYLD_LIBRARY_PATH 不生效——实测踩到）。
共需链接：libavutil.59 / libavcodec.61 / libavformat.61 / libavdevice.61 /
libavfilter.10 / libswscale.8 / libswresample.5。

## 复现方式

```bash
# 前置：brew install ffmpeg@7（共享库）+ 训练 venv（见 gate-b README）
ln -sf /opt/homebrew/opt/ffmpeg@7/lib/*.dylib \
  "$(python3 -c 'import sysconfig;print(sysconfig.get_config_var("LIBDIR"))')/"

backend/.venv/bin/python scripts/train/gate_a_video_loader.py
```

## 诚实标注（不声称）

- **不声称**硬件采集（pusht 为公开数据集，B 级样本；A 级硬件 Pilot 仍 BLOCKED）
- **不声称**全量 25,650 帧（只取前 100 帧验证解码链路）
- 视频特征 stats 为占位零值（形状严格合规 (3,1,1)，统计精度不影响解码验证）
