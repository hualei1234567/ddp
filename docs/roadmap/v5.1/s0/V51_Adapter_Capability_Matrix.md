# V51 Adapter Capability Matrix（格式能力实证矩阵）

- 复核方式：逐个读取底层实现（E2）+ 实测样本可用性（E1）
- 分级标准（蓝图 §5）：**DETECT → INSPECT → PAYLOAD_PARSE → STREAM_EXTRACT → EPISODE_BUILD → ROUND_TRIP**

---

## 一、能力矩阵（真实状态）

| 格式 | DETECT | INSPECT | PAYLOAD_PARSE | STREAM_EXTRACT | EPISODE_BUILD | ROUND_TRIP | 可宣称 |
|---|---|---|---|---|---|---|---|
| **HDF5** | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | 可识别 + 可读元数据 |
| **ROS Bag** | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | 可识别 + 可读元数据 |
| **MP4** | ✅ | ✅ | ❌ | ❌ | ⚠️ 单文件可切 | ❌ | 可识别 + 可读元数据 + 有限分段 |
| **Parquet** | ✅ | ✅ | ⚠️ 只读 schema | ❌ | ❌ | ❌ | 可识别 + 可读 schema |
| **MCAP** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **无支持** |
| **LeRobot** | ✅ | ✅ | ❌ | ❌ | ❌ | ⚠️ 导出可被 Loader 读元数据 | 元数据级 |
| **JSON/JSONL** | ✅ | ✅ | ⚠️ 解析元数据行 | ❌ | ❌ | ❌ | 元数据 |

**结论：无任何格式达到 PAYLOAD_PARSE 及以上。全部止步于 INSPECT。**

---

## 二、逐格式实证

### 2.1 HDF5（**Golden Path 首选**）

| 项 | 事实 |
|---|---|
| 实现 | `data-platform/ddp_data/hdf5/metadata.py` |
| 依赖 | 官方 `h5py==3.16.0`（`data-platform/requirements.txt` 固定） |
| 做法 | `h5py.File(reader, "r")` 只读；自定义 `_BoundedReader` 做字节上限；错误消息脱敏（不含绝对路径） |
| 能读 | `read_hdf5_structure()` → `Hdf5Structure`：`dataset_count`/`group_count`/`attribute_count`/`link_count`/`top_level_entries`/每组每数据集 path+shape+dtype+compression+chunks+attributes |
| **不能读** | **数据集实际数值内容**（只读结构，不读 payload） |
| 真实样本验证 | `test_hdf5_real_public_sample.py`（RoboMimic 36.7MB，**默认 skip**，需 `DDP_TEST_REAL_HDF5`） |
| 合成测试 | `test_hdf5_probe.py` 6 条（用 h5py 现场造文件，真实 SDK 非 stub） |
| 达级 | **DETECT + INSPECT** |
| 升级到 PAYLOAD_PARSE 的工作量 | 小：已有 h5py，加 `dataset[()]` 读取 + 分块 |

### 2.2 ROS Bag

| 项 | 事实 |
|---|---|
| 实现 | `data-platform/ddp_data/rosbag/metadata.py` |
| 依赖 | `rosbags` SDK（rosbag1 + rosbag2 Reader） |
| 做法 | **只读索引/metadata，不遍历 `messages()`，不解码** |
| 能读 | `read_rosbag_structure()` → topic_count/message_count/start_time_ns/end_time_ns/duration_ns/每 topic 的 msgtype+message_count |
| **不能读** | **消息内容**（显式设计为不遍历） |
| 真实样本 | `test_rosbag_real_public_sample.py`（foxglove mcap testdata 5KB，**默认 skip**） |
| 达级 | **DETECT + INSPECT** |
| 升级工作量 | 中：需遍历 messages + 反序列化，且要处理 schema 类型 |

### 2.3 MP4

| 项 | 事实 |
|---|---|
| 实现 | `data-platform/ddp_data/media/mp4.py` |
| 依赖 | 受控 `ffprobe` 子进程 |
| 安全 | 超时、输出上限、错误分类（`ProbeTimeoutError`/`ProbeOutputLimitError`/`NotMp4Error` 等） |
| 能读 | `Mp4ProbeResult` → `MediaStreamInfo`：流信息、时长 |
| 真实样本 | 真实栈 MinIO 有 269 个真实 MP4（HF 导入） |
| 达级 | **DETECT + INSPECT + 有限 EPISODE_BUILD**（单文件可作 Episode 边界） |
| 升级工作量 | 小~中：帧级抽取需 ffmpeg |

### 2.4 MCAP

| 项 | 事实 |
|---|---|
| 实现 | **无** |
| 真实资产 | 真实栈有 **5 个 MCAP** RawAsset |
| 达级 | 全部 ❌ |
| 说明 | `mcap` 是 Foxglove 现代容器，robotics 主流；但当前无 parser，也无法验证 |

### 2.5 LeRobot

| 项 | 事实 |
|---|---|
| 实现 | `parser.py`（元数据行）、`export_writer.py`（写）、`event_graph_adapter.py` |
| 导出能力 | v2.1 结构齐全（9 类缺口已修），但 **`features` 仅 5 项**，无 `observation.*`、无 `action` |
| Loader 验证 | `lerobot==0.3.3` 双路构造通过（metadata），**逐帧实测无 observation/action** |
| 达级 | DETECT + INSPECT；ROUND_TRIP 仅元数据级 |

### 2.6 已注册 Adapter（对照）

| adapter_id | 实现 | 说明 |
|---|---|---|
| `lerobot.human1m_preview` | `parse_human1m_preview` | NVIDIA Human-1M preview 平铺元数据 |
| `generic.episode_jsonl` | `parse_generic_jsonl` | 通用 JSONL（含 detect） |

**注意**：`AdapterRegistry` 的 `AdapterCapability` 枚举是 `PARSE/DETECT/FETCH/VERIFY/WRITE`——**与蓝图六级语义不对齐**（详见 FR 矩阵 V51-005 决策项）。

---

## 三、能力升级路线（按 Golden Path）

### 3.1 HDF5 升级（P0，主路径）

| 步 | 目标级别 | 工作 |
|---|---|---|
| 1 | PAYLOAD_PARSE | 新增 `read_hdf5_dataset(path, dataset_path)`：用 h5py 读实际数值 + dtype + shape；分块避免 OOM |
| 2 | STREAM_EXTRACT | 识别 `obs/<sensor>` 与 `actions` 布局 → 产出 Canonical SensorStream/Observation/Action |
| 3 | EPISODE_BUILD | `data/demo_*` 每个 group 作为 Episode 边界 |
| 4 | ROUND_TRIP | 导出后由官方 Loader 逐帧读取验证 |

### 3.2 MP4 升级（辅助）

| 步 | 目标级别 | 工作 |
|---|---|---|
| 1 | PAYLOAD_PARSE | 帧级抽取（ffmpeg/decord） |
| 2 | STREAM_EXTRACT | VideoStream + Frame 序列 |

### 3.3 MCAP（次要）

需先引入 `mcap` SDK + 写 parser；当前 5 个真实资产可作 fixture。**建议排在 Golden Path 之后**。

---

## 四、能力矩阵的诚实口径

**当前绝不可对外宣称**：

| ❌ 不可宣称 | 事实 |
|---|---|
| 「支持 HDF5 数据」 | 只能读结构，不能读 payload |
| 「支持 ROS Bag」 | 只能读索引，不遍历消息 |
| 「支持 MCAP」 | 完全没有 parser |
| 「Adapter 已覆盖 7 种格式」 | 只有 2 个注册 Adapter，且都是元数据级 |
| 「LeRobot 训练就绪」 | 无 observation/action 逐帧数据 |

**可以宣称**：

| ✅ 可宣称 | 依据 |
|---|---|
| 「HDF5/ROS Bag/MP4 可识别并读取元数据」 | DETECT + INSPECT 有实现有测试 |
| 「LeRobot 导出可被官方 Loader 读取元数据」 | lerobot==0.3.3 双路验证 |
| 「解析过程有界且脱敏」 | `_BoundedReader` + 错误脱敏有测试 |

---

## 五、结论

1. 底层 SDK 能力**真实存在**（h5py/rosbags/ffprobe 都用官方库，非自研、非 stub）。
2. 但**全部止步于 INSPECT**——没有任何格式产出 payload 或 stream。
3. **HDF5 是升级成本最低的路径**（已有 h5py + RoboMimic facts 登记 + 测试骨架），升到 PAYLOAD_PARSE 只需加实际数值读取。
4. 蓝图六级分级需要落地为**显式的能力矩阵**（新增或扩展现有枚举，需 ADR），禁止「注册成功即完整支持」。
