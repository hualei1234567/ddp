# LeRobot v2.1 逐帧 Loader 验证通过报告（V51-011 / FR-034/035 升级）

- 日期：2026-11-25
- 结论：**PASS —— 从 `METADATA_ONLY` 升级到 `LOADER_FRAME`**
- 前置：`2026-11-23-lerobot-loader-verify-pass.md`（元数据级验证，PR #91）

---

## 1. 判定

官方 `lerobot==0.3.3` **逐帧读取**真实机器人数据成功：

| 检查项 | 结果 |
|---|---|
| Loader 构造 | ✅ PASS |
| 总帧数为真实值（>1） | ✅ **59 帧**（此前 METADATA_ONLY 只有 3 帧占位） |
| fps 为真实值（>1） | ✅ **20.0 Hz**（此前为标称 1 Hz） |
| `observation.state` 维度 | ✅ **32 维**（真实关节/位姿状态） |
| `action` 维度 | ✅ **7 维**（真实末端控制指令） |
| 全部帧可逐帧读取 | ✅ **59/59** |
| `loadable_scope` | ✅ `LOADER_FRAME` |

**这不是把 METADATA_ONLY 改名**——旧产物仍在，`loadable_scope=METADATA_ONLY`；
本产物是**新增的逐帧路径**，二者共存（见 `data-platform/ddp_data/lerobot/frame_writer.py` 模块文档）。

---

## 2. 真实样本

| 项 | 值 |
|---|---|
| 来源 | HuggingFace `amandlek/robomimic` rev `74fa018461f479cd9fd15b924a16103012096203` |
| 文件 | `v1.5/lift/ph/demo_v15.hdf5` |
| 大小 | 36,699,586 B |
| SHA256 | `68f2a8cfe2502de1224f0708fb82e46da07abce108c63e721d72fc1618272f37` |
| 许可 | MIT，非 gated |
| 标注口径 | `PUBLIC_DATASET_INTEGRATION_TESTED`（**不是**真实硬件 Pilot） |

---

## 3. 执行链路

```
RoboMimic demo_v15.hdf5（36.7MB，200 demos）
  ↓ ddp_data.hdf5.read_hdf5_dataset_slice()        真实 payload 读取
  ↓ stream_extraction.extract_robomimic_demo()     产出三件套
     observation = /data/demo_0/states  (59, 32) float64
     action      = /data/demo_0/actions (59, 7)  float64
  ↓ ddp_data.lerobot.frame_writer.write_frame_episode()
     逐帧展开为 observation.state[0..31] + action[0..6]
     生成 info.json（fps=20.0, total_frames=59, LOADER_FRAME）
  ↓ build_stats / build_stats_json / build_episodes_stats_jsonl
     生成官方必需的 stats 元数据
  ↓ lerobot==0.3.3 LeRobotDataset(root=...)
     逐帧读取 59/59 帧成功
```

---

## 4. 复现命令

```bash
# 步骤 1：backend 环境生成逐帧数据集（需 DDP_TEST_REAL_HDF5）
PYTHONPATH="data-platform:backend" backend/.venv/bin/python \
  docs/testing/evidence/2026-11-25-lerobot-frame-verify/gen_frame_dataset.py

# 步骤 2：lerobot 隔离环境做官方 Loader 逐帧验证
/tmp/lerobot-verify-venv-v21/bin/python \
  docs/testing/evidence/2026-11-25-lerobot-frame-verify/verify_frame_load.py
```

---

## 5. 实测踩到的三个坑（已固化到实现与测试）

1. **`video_path` 不能是 `None`** —— 官方 Loader 在建 split 时会失败，即使数据集无视频。
   必须是模板串 `videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4`。
2. **`chunks_size` 缺失直接 KeyError** —— 官方用它计算 chunk 目录编号。
3. **必须有 `meta/stats.json` 与 `meta/episodes_stats.jsonl`** —— Loader 会尝试加载，
   缺失导致构造失败。不是可选装饰。

另：`data_path` 必须是**模板形式**（`{episode_chunk:03d}` / `{episode_index:06d}`），
写死具体路径会让 Loader 无法推导其它 Episode 位置。

---

## 6. 诚实边界（不得扩大宣称）

| 项 | 状态 |
|---|---|
| 逐帧 Parquet 读取 | ✅ 已验证 |
| `observation.state` / `action` 数值读取 | ✅ 已验证 |
| **视频帧读取** | ❌ **未验证** —— RoboMimic `ph` 版本不含图像，`video_provided=false` |
| 真实硬件采集 | ❌ `HARDWARE_CAPTURE_NOT_TESTED` |
| 多 Episode 数据集 | ❌ 当前只验证单 Episode（多 Episode 需补 test） |
| 真实训练脚本消费 | ❌ 未验证（`VERIFIED_FOR_TRAINING_PIPELINE` 需目标训练环境） |

**状态口径**（ADR-0049 / 蓝图 §6）：
- `VERIFIED_BY_LOADER_METADATA` —— 旧产物（PR #91）
- **`VERIFIED_BY_LOADER_FRAME`** —— **本报告**
- `VERIFIED_FOR_TRAINING_PIPELINE` —— 未达成（需训练脚本）
- `TRAINING_HANDOFF_VERIFIED` —— 未达成（需外部平台）
