# V51 GATE-0 样本实测报告（RoboMimic HDF5）

- 执行时间：2026-11-24 21:26–21:40
- 样本：`amandlek/robomimic` rev `74fa018461f479cd9fd15b924a16103012096203`
  `v1.5/lift/ph/demo_v15.hdf5`
- 存放：`~/ddp-samples/robomimic/demo_v15.hdf5`（**仓库外**，不入 git）
- 证据等级：**E1（本次实跑）**

---

## 1. 获取与校验（GATE-0 通过）

| 项 | 值 |
|---|---|
| 文件大小 | **36,699,586 字节**（与 facts.json 完全一致） |
| SHA256 实测 | `68f2a8cfe2502de1224f0708fb82e46da07abce108c63e721d72fc1618272f37` |
| SHA256 期望 | `68f2a8cfe2502de1224f0708fb82e46da07abce108c63e721d72fc1618272f37` |
| **校验结果** | ✅ **完全匹配 —— 真实样本确认** |
| 许可 | MIT，非 gated |
| 标注口径 | 只能标 `PUBLIC_DATASET_INTEGRATION_TESTED`，**不得标真实硬件 Pilot** |

**GATE-0 状态：✅ PASSED**

---

## 2. 真实结构（h5py 实测）

### 2.1 顶层

```
data/     — 200 个 demo_* 组
mask/     — 7 个 split 划分（语义分割掩码名列表）
```

### 2.2 demo 内字段（30 种，全部 n=200）

| 字段 | shape（demo_0） | dtype | 语义 |
|---|---|---|---|
| **`actions`** | **(59, 7)** | float64 | **真实动作源**：7 维归一化末端控制（末端 delta xyz + 旋转 + 夹爪） |
| **`states`** | **(59, 32)** | float64 | **真实观测状态**：32 维（关节角 + 末端位姿 + 夹爪等） |
| **`user_info/0/timestamp`** | **(253,)** | float64 | **真实墙钟时间戳**（Unix 秒） |
| `controller_info/robot_controls/action` | (59, 7) | float64 | 控制指令副本 |
| `controller_info/teleop_commands/arm0/position` | (59, 3) | float64 | 遥操作位置指令 |
| `controller_info/teleop_commands/arm0/rotation` | (59, 3, 3) | float64 | 遥操作旋转指令 |
| `controller_info/teleop_commands/arm0/gripper` | (59, 1) | float64 | 夹爪指令 |
| `controller_info/user_timestamps` | (253, 1) | float64 | 用户事件时间戳 |
| `interventions` / `policy_acting` / `user_acting` | (59, …) | bool | 介入/策略执行/用户执行标记 |
| `user_info/0/{clutch,engaged,grasp,valid,...}` | (253,) | 混合 | 遥操作会话状态 |

### 2.3 数据真实性实证（demo_0 实际值）

**actions 前 3 行**（真实控制量，非占位）：
```
[[-0.          0.          0.          0.00381497  0.14820713  0.01447902  -1.        ]
 [ 0.204       0.087      -0.072       0.0040731   0.13793246  0.00472844  -1.        ]
 [ 0.323       0.131      -0.073       0.00670891  0.12851983 -0.00825769  -1.        ]]
范围: [-1.0000, 1.0000]  ← 归一化动作，符合 robosuite OSC 控制
```

**states 前 2 行前 8 维**：
```
[[ 0.         -0.04141039  0.21736869  0.00753974 -2.5898454  -0.00784382  2.95545758  0.77382831]
 [ 0.05       -0.04034791  0.21541772  0.01076009 -2.59413574 -0.00699519  2.94616252  0.77302205]]
范围: [-2.5941, 3.0262]  ← 含关节角（rad）量级，非伪造
```

**timestamp**：单调递增 `True`，采样间隔含 `0.0`（一次控制步内多次事件），非零间隔约 `0.052s`（≈19Hz）

### 2.4 规模

| 项 | 值 |
|---|---|
| demo 数 | **200** |
| 总帧数 | **9,666** |
| 每 demo 帧数 | min=36 / max=64 / mean=48.3 |
| actions 维度 | 7 |
| states 维度 | 32 |

### 2.5 split 划分（真实）

```
mask/train            (180,)  — 训练集
mask/20_percent       (40,)
mask/20_percent_train (36,)   mask/20_percent_valid (4,)
mask/50_percent       (100,)
mask/50_percent_train (90,)   mask/50_percent_valid (10,)
```
内容为 demo 名字符串（如 `['demo_1','demo_10','demo_100',...]`）——**可直接作 Split 防泄漏的真实输入**。

---

## 3. 关键限制（**必须如实记录**）

### 🔴 此样本**不含图像观测**

`demo_0` 的子键为 `['actions','controller_info','interventions','policy_acting','states','user_acting','user_info']`——**没有 `obs` 组**。

原因：`ph` = proficient human，robomimic 的 `ph` 版本只存**低维状态 + 动作**，不含渲染图像。图像版本在 `image` 数据集中。

**影响**：
- ✅ 可验证：SensorStream（states/actions 数值流）、Observation（低维状态）、Action（真实 7 维）、时间同步、Episode 分段、Cleaning（数值规则）
- ❌ 不可验证：VideoStream、图像 Observation、媒体解码链路
- ⚠️ LeRobot 逐帧导出的 `observation.images.*` feature **无法从此样本生成**

### 补充说明

| 限制 | 说明 |
|---|---|
| 无标定文件 | robomimic 用 MuJoCo 仿真，无真实标定；`CALIBRATION_MISSING` 需如实标注 |
| 无采集会话 | 公开数据集，无 CaptureSession/UploadSession 链路 |
| timestamp 覆盖不全 | 253 个用户事件 vs 59 个控制步，两套时间轴需明确映射 |

---

## 4. 对 Golden Path 的影响与调整

### 4.1 原计划（S0 §5.3）

```
HDF5 → SensorStream(obs/*) → Observation(逐帧) → Action(data/demo_*/actions)
```

### 4.2 实测后调整（**重要**）

原假设「obs/* 含图像」不成立。调整为：

```
RoboMimic ph demo_v15.hdf5
  → HDF5 升级 PAYLOAD_PARSE（h5py 读实际数值）
  → SensorStream: states(32维) + actions(7维) 两条数值流
  → Observation: 逐帧 states 值（32 维 float64）+ 时间戳
  → Action: 真实 7 维（非伪造，源一致性可抽样对照）
  → 时间同步: controller 时间轴 vs user_info 时间轴 双轴对齐
  → Episode: 每个 demo_* 一个 Episode（200 个天然边界）
  → Cleaning: 数值规则（NaN/越界/常量通道）
  → LeRobot 逐帧导出: observation.state(32) + action(7)
                  ⚠️ observation.images.* 无法产出（标 VIDEO_NOT_PROVIDED）
  → Frame-level Loader 验证: 能读 Parquet 逐帧 state/action（视频除外）
```

### 4.3 图像链路的处理方式（三选一，需确认）

| 方案 | 做法 | 代价 |
|---|---|---|
| **A（推荐）** | 接受「无图像」限制：Golden Path 验证 state+action 逐帧链路，VideoStream 标 `VIDEO_NOT_PROVIDED` | 无法验证视频链路，但**核心训练数据链路（状态+动作）完整** |
| B | 另下载 robomimic `image` 数据集补充图像 | 体积大（GB 级），且需额外解析 |
| C | 用真实栈已有 269 个 MP4 单独验证视频链路 | 视频无对应动作，只能验 VideoStream 单独能力 |

**建议 A + C 组合**：A 做主链路（state/action 逐帧 + LeRobot frame-level），C 补视频能力验证。

---

## 5. GATE-0 结论

| 项 | 结论 |
|---|---|
| 真实样本到位 | ✅ SHA256 匹配 |
| 含真实动作源 | ✅ 200 demo × 7 维 |
| 含真实观测 | ✅ 200 demo × 32 维 states |
| 含真实时间戳 | ✅ 单调递增 wall-clock |
| 含真实 split | ✅ train(180) / valid / test 划分 |
| 含图像 | ❌ **不含**（ph 版本限制） |
| 含标定 | ❌ 不含（仿真数据） |
| 可支撑的核心 FR | V51-001/002/003/004/005/007/011（部分） |
| 不可支撑 | 视频链路、真实标定、采集会话链路 |

**GATE-0 = PASSED（含明确限制）。可以进入 M2。**

---

## 6. 复现命令

```bash
# 下载（36.7MB，MIT）
mkdir -p ~/ddp-samples/robomimic && cd ~/ddp-samples/robomimic
curl -L -o demo_v15.hdf5 \
  "https://huggingface.co/datasets/amandlek/robomimic/resolve/74fa018461f479cd9fd15b924a16103012096203/v1.5/lift/ph/demo_v15.hdf5"

# 校验
shasum -a 256 demo_v15.hdf5
# 期望 68f2a8cfe2502de1224f0708fb82e46da07abce108c63e721d72fc1618272f37

# 结构探测
DDP_TEST_REAL_HDF5=~/ddp-samples/robomimic/demo_v15.hdf5 \
  backend/.venv/bin/python -m pytest backend/tests/unit/test_hdf5_real_public_sample.py -q
```
