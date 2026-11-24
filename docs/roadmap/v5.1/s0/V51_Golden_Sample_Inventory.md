# V51 Golden Sample Inventory（真实数据样本盘点）

- 复核方式：全仓文件扫描 + 环境变量检查 + LeRobot 实测（E1）
- **核心结论：仓库内没有任何真实机器人数据样本。V5.1 Golden Path 的关键阻塞在此。**

---

## 一、仓库内样本实际盘点（E1）

### 1.1 全部二进制样本文件（共 11 个，剔除 4 个负例）

| 文件 | 大小 | **真实性质** |
|---|---|---|
| `docs/testing/fixtures/stage2/samples/formats/sample.h5` | **166 B** | ⚠️ 内嵌字符串 `SYNTHETIC_HDF5_CONTAINER_ONLY` |
| `docs/testing/fixtures/stage2/samples/formats/sample.mcap` | **102 B** | ⚠️ 内嵌 `SYNTHETIC_MCAP_CONTAINER_ONLY` |
| `docs/testing/fixtures/stage2/samples/formats/sample.parquet` | **105 B** | ⚠️ 内嵌 `SYNTHETIC_PARQUET_CONTAINER_ONLY` |
| `docs/testing/fixtures/stage2/samples/cameras/cam_wrist.mp4` | 21,648 B | 合成测试媒体 |
| `docs/testing/fixtures/stage2/samples/cameras/cam_front.mp4` | 21,648 B | 合成测试媒体 |
| `backend/tests/fixtures/parquet/generic_types.parquet` | 1,951 B | 类型测试用小表 |
| `backend/tests/fixtures/parquet/lerobot_v21_data.parquet` | 1,731 B | LeRobot 结构测试小表 |
| `backend/tests/fixtures/parquet/logical_types.parquet` | 847 B | 类型测试小表 |
| `backend/tests/fixtures/parquet/empty_table.parquet` | 303 B | 空表边界测试 |
| neg: `text_as_mp4.mp4` / `truncated_cam_front.mp4` / `empty.mp4` | 60 / 24 / 0 B | 负例（损坏/空/伪装） |

**十六进制实证**（`xxd` 实测）：
```
sample.h5:      89 48 44 46 0d 0a 1a 0a | "SYNTHETIC_HDF5_CONTAINER_ONLY" | 00 01 02 03 04...
sample.mcap:    89 4d 43 41 50 30 0d 0a | "SYNTHETIC_MCAP_CONTAINER_ONLY" | 00 01 02 03 04...
sample.parquet: 50 41 52 31             | "SYNTHETIC_PARQUET_CONTAINER_ONLY" | 00 01 02 03...
```

→ 这些是**只有合法魔数 + 标注自己是合成的字节序列**，不含任何数据集、组、流或时间戳。

### 1.2 真实公开样本：**只登记了事实，没有文件本体**

仓库有 4 份样本事实登记（`backend/tests/fixtures/public/`），**但样本文件本身均不在仓库**：

| 样本 | 来源 | 大小 | 事实登记 | 文件本体 |
|---|---|---|---|---|
| **RoboMimic HDF5** | HF `amandlek/robomimic` rev `74fa018…`，MIT | **36.7 MB** | ✅ `robomimic-hdf5/facts.json`：dataset 6008 / group 1802 / attr 402，含 SHA256 `68f2a8cf…` | ❌ **不可用** |
| **MCAP/ROS Bag** | `foxglove/mcap` rev `d330d56…`，MIT | 5,482 B | ✅ `rosbag/facts.json`：rosbag1、2 topics、3 messages、start/end/duration ns | ❌ **不可用** |
| **LeRobot DROID** | `lerobot/droid_100` rev `87301a2…`，MIT | — | ⚠️ 目录存在但**无 facts.json** | ❌ **不可用** |
| **DROID 硬件配置** | `droid-dataset/droid` commit `33ae6a6…` | — | ✅ `droid-hardware/facts.json`：shopping-list / assembly / robot config（SHA256 齐全） | 仅文档，非数据 |

对应的 2 个测试文件**默认 skip**：
```python
# backend/tests/unit/test_hdf5_real_public_sample.py:42
pytestmark = pytest.mark.skipif(
    not _REAL_PATH or not Path(_REAL_PATH).is_file(),
    reason="需要 DDP_TEST_REAL_HDF5 指向本地 RoboMimic demo_v15.hdf5",
)
```
同理 `test_rosbag_real_public_sample.py:21`。

### 1.3 环境实测

```
$ env | grep DDP_TEST
（无输出 —— 没有任何 DDP_TEST_* 环境变量）
```

→ **真实样本测试当前 100% 跳过，从未在本机跑过。**

### 1.4 唯一的"真实产物"：8KB 合成 LeRobot 元数据

`/tmp/lerobot-dataset-v2/`（PR #91 遗留）：
- 3 个 parquet（每个仅 1 行元数据）、`meta/info.json`、`ddp/sidecar.json`
- `fps=1`（标称）、`total_frames=3`、features 仅 5 项
- `loadable_scope="METADATA_ONLY"`

**这是元数据验证产物，不是训练样本。**

---

## 二、真实栈数据资产（E1）

| 项 | 值 | 可信度 |
|---|---|---|
| RawAsset | 283 | 格式：**MP4 269 / JSON 9 / MCAP 5** |
| 来源 | **IMPORT 269 / UPLOAD 14** | 绝大多数为 HuggingFace 导入的 MP4 |
| Episode | 206 | 无逐帧物化 |
| ProcessingRun | 4（全部失败/取消） | **processing_artifacts = 0** |
| CaptureSession | 7 | 极少 |
| 对象存储 | MinIO 2094 对象 / 744 MiB | 含真实 MP4 与 JSON |

**关键判断**：真实栈的 269 个 MP4 来自 HF 导入，属于「有许可的公开数据」范畴，但**只有视频，没有传感器流、关节状态、动作或标定**。无法支撑 SensorStream/Observation/Action 物化验证。

---

## 三、Adapter 能力现状（E1）

### 3.1 已注册 Adapter（仅 2 个）

| adapter_id | 格式 | parser | 真实能力 |
|---|---|---|---|
| `lerobot.human1m_preview` | LeRobot preview metadata.jsonl | `parse_human1m_preview` | 元数据（一行一机位视频） |
| `generic.episode_jsonl` | DDP generic episode JSONL | `parse_generic_jsonl` | 元数据（含 detect） |

### 3.2 底层解析能力（有实现，未注册为 Adapter）

| 格式 | 底层实现 | 能力 |
|---|---|---|
| HDF5 | `ddp_data/hdf5/metadata.py`（h5py 3.16.0） | DETECT + INSPECT（结构/形状/dtype/属性/压缩）✅ |
| ROS Bag | `ddp_data/rosbag/metadata.py`（rosbags SDK） | DETECT + INSPECT（topic/msgtype/count/时间范围）✅ |
| MP4 | `ddp_data/media/mp4.py`（ffprobe） | DETECT + INSPECT（流信息/时长）✅ |
| Parquet | `ddp_data/parquet/` | DETECT + INSPECT ✅ |
| **MCAP** | **无** | ❌ |
| LeRobot | `ddp_data/lerobot/parser.py` | 元数据 ✅ |

### 3.3 与蓝图六级能力矩阵的差距

| 级别 | HDF5 | ROS Bag | MP4 | Parquet | MCAP |
|---|---|---|---|---|---|
| DETECT | ✅ | ✅ | ✅ | ✅ | ❌ |
| INSPECT | ✅ | ✅ | ✅ | ✅ | ❌ |
| PAYLOAD_PARSE | ❌ | ❌（不遍历 messages） | ❌ | ⚠️ 部分 | ❌ |
| STREAM_EXTRACT | ❌ | ❌ | ❌ | ❌ | ❌ |
| EPISODE_BUILD | ❌ | ❌ | ⚠️ 单文件 MP4 可切 | ❌ | ❌ |
| ROUND_TRIP | ❌ | ❌ | ❌ | ❌ | ❌ |

**没有任何格式达到 PAYLOAD_PARSE 及以上。**

---

## 四、LeRobot 逐帧读取实测（E1，本次亲自跑）

```python
from lerobot.datasets.lerobot_dataset import LeRobotDataset
ds = LeRobotDataset('local/ddp-export', root=Path('/tmp/lerobot-dataset-v2'), episodes=None)
# 结果：
#   构造成功: episodes=3 frames=3
#   features: ['episode_index', 'length', 'split', 'task_index', 'timestamp']
#   ds[0] 返回 keys: ['episode_index','task_index','length','timestamp','split','task']
#   -> observation 存在: False
#   -> action 存在:      False
```

**结论**：Loader 可构造、可索引，但**没有任何 observation/action 逐帧数据**。与 `METADATA_ONLY` 声明完全一致。

---

## 五、Golden Path 候选与推荐

### 5.1 候选方案对比

| 方案 | 格式 | 样本可得性 | 覆盖 FR | 动作源 | 推荐度 |
|---|---|---|---|---|---|
| **A. RoboMimic HDF5** | HDF5 | ⭐⭐⭐ 公开 MIT，36MB，已有 facts 登记与测试骨架 | V51-001/002/003/004/005/007 | ✅ **有 actions**（`data/demo_*/actions`） | ⭐⭐⭐⭐⭐ |
| B. ROS Bag（foxglove mcap testdata） | rosbag1 | ⭐⭐⭐ MIT，5KB，已有 facts | 001/002/004/005 | ⚠️ 仅 String 消息，无动作 | ⭐⭐⭐ |
| C. LeRobot DROID | LeRobot | ⭐⭐ 需下载完整数据集 | 002/011 | ✅ 有 action | ⭐⭐⭐ |
| D. 现有 MP4（真实栈 269 个） | MP4 | ⭐⭐⭐⭐⭐ 已在 MinIO | 仅 video stream | ❌ 无 | ⭐⭐ |
| E. 等待真实硬件 | 未知 | ⭐ 阻塞 | 全部 | 未知 | ⭐ |

### 5.2 推荐：**方案 A（RoboMimic HDF5）为主 + 方案 D（MP4）为辅**

**理由**：
1. **唯一同时具备「真实 payload + 真实动作源 + 真实时间戳 + 多模态」的可得样本**——`demo_v15.hdf5` 含 `data/demo_*/obs/*`（图像/关节状态）与 `actions`，是 embodied AI 标准格式
2. **仓库已有基础**：`ddp_data/hdf5` 已能读真实结构；`facts.json` 已记录 SHA256 与观察结构（6008 dataset/1802 group）；测试骨架 `test_hdf5_real_public_sample.py` 已存在
3. **许可清晰**：MIT、非 gated，可作 `PUBLIC_DATASET_INTEGRATION_TESTED`
4. **HDF5 是 robomimic / LIBERO / RoboTurk 等主流基准的容器**，覆盖面广
5. **MP4 辅线**：真实栈已有 269 个 MP4，可验证 VideoStream 链路与单文件 Episode 切分

**明确不选**：
- 方案 B（rosbag testdata 只有 String 消息，无动作/传感器语义）
- 方案 E（真实硬件不可得，不能等）

### 5.3 Golden Path 端到端路径

```
RoboMimic demo_v15.hdf5 (36.7MB, MIT, SHA256 已登记)
  → 下载到本地（不进仓库，走 DDP_TEST_REAL_HDF5 环境变量）
  → Adapter 六级分级：HDF5 达 PAYLOAD_PARSE + STREAM_EXTRACT + EPISODE_BUILD
  → SensorStream 生产者：obs/agentview_image(视频流) + obs/robot0_joint_pos(关节流)
  → Observation 生产者：逐帧 payload → 列式存储 + DB 索引
  → Action 生产者：data/demo_*/actions（7 维末端动作）→ 真实动作源，非伪造
  → 时间同步：单 demo 内多流共享时间轴，验证 offset=0 / 采样率一致
  → Episode 分段：每个 demo_* 为一个 Episode（HDF5 天然边界）
  → DATA_CLEANING：FLAG 规则先上（NaN/越界/常量通道）
  → Dataset → LeRobot 逐帧导出（observation + action features）
  → lerobot==0.3.3 Frame-level Loader 读取验证
  → Lineage：DatasetItem → Episode → RawAsset → (无 Session，标 UNVERIFIABLE)
```

### 5.4 样本获取需求（**需用户确认**）

| 项 | 需求 | 说明 |
|---|---|---|
| 下载 | `huggingface_hub` 下载 `amandlek/robomimic` rev `74fa018` 的 `v1.5/lift/ph/demo_v15.hdf5` | 36.7MB，需网络；可用代理 |
| 存放 | 仓库外目录，通过 `DDP_TEST_REAL_HDF5` 指定 | 不进 git（超仓库体积） |
| 校验 | SHA256 应为 `68f2a8cfe2502de1224f0708fb82e46da07abce108c63e721d72fc1618272f37` | 与 facts.json 一致才算真实样本 |
| 合规 | MIT 许可，非 gated | 可标 `PUBLIC_DATASET_INTEGRATION_TESTED`，**不可标真实硬件 Pilot** |

---

## 六、样本缺口结论

| 缺口 | 严重度 | 解除方式 |
|---|---|---|
| **无任何真实机器人数据文件** | 🔴 **V5.1 唯一硬阻塞** | 下载 RoboMimic HDF5（推荐）或用户提供真实设备导出 |
| 无多传感器同步样本 | 🟠 高 | RoboMimic 单 demo 多流可部分覆盖；完整需真实设备 |
| 无真实标定文件 | 🟠 高 | RoboMimic 无标定；需真实设备导出 |
| 无采集会话链路（Session→Upload）数据 | 🟡 中 | FR-040 已因此 UNVERIFIABLE；需一次真实采集 |
| MCAP 无 parser | 🟡 中 | 真实栈有 5 个 MCAP，可作次要目标 |
| LeRobot 无逐帧样本 | 🔴 高 | Golden Path 跑通后自动产出 |

**没有样本，V5.1 的 P0 主体（V51-001~006/011）无法验收。这不是开发能力问题，是输入缺失。**
