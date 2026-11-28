# GATE-B 视频数据集真实训练联动（V6.1 Sprint 3）

## 验收结论：**GATE-B（多 Episode 含视频）通过**

| 项 | 结果 |
|---|---|
| 数据集 | DDP `frame_writer` 导出的 LeRobot v2.1 **多 Episode 含视频数据集**（2 个 Episode × 60 帧 = 120 帧，`observation.image` 96×96 H.264 MP4 + `observation.state` 2 维 + `observation.environment_state` 2 维 + `action` 2 维，来源 `lerobot/pusht`） |
| 训练脚本 | 官方 `lerobot==0.3.3` 的 `lerobot.scripts.train.train`（**非 mock**） |
| 策略 | `ACTPolicy`（`FeatureType.VISUAL` + `FeatureType.STATE` + `FeatureType.ENV`），CPU，2 个真实 step，batch_size 2，seed 42 |
| 训练耗时 | 6.168 秒（CPU） |
| **回载验证** | `ACTPolicy.from_pretrained` 加载成功，输入 `(1, 3, 96, 96)` 视频张量 + `(1, 2)` 状态张量，`select_action` 输出形状 `(1, 2)` 与 `action` 维度一致 |
| 模型 Hash | `model.safetensors` sha256 `3965931e0d8ae084...`（详见 `evidence.json`） |

**证据等级**：E1（真实环境实跑，含输入/命令/输出/版本/可复核产物）。

## 复现方式

```bash
/tmp/v61-train-venv/bin/python scripts/train/gate_b_video_train.py
```

## 诚实标注（不声称）

- **不声称**训练收敛性（仅 2 步，权重不具备可用性）
- **不声称** GPU 性能（本机为 CPU 执行）
- **不声称**硬件采集（`pusht` 为公开 B 级样本）
- 视频特征 stats 为占位统计（形状严格合规 `(3, 1, 1)`，用于验证多 Episode 聚合与训练消费链路）
