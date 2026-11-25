# ADR-0048 Adapter 六级能力分级（替换五值枚举）

- 状态：**ACCEPTED**（2026-11-25 落地 V51-005）
- 日期：2026-11-24（2026-11-25 验收同步）
- 决策者：System Architect + 后端
- 关联：V51-005、V5.1 蓝图 `05_真实数据样本准入与格式适配.md` §5、`03_痛点地图与根因分析.md` 痛点二
- 约束：不改 Canonical Schema；不破坏现有 Adapter 注册；不动 `data-platform` 公开 API 的解析签名

## 1. 背景

现状（S0 实测）：`data-platform/ddp_data/adapters/registry.py:48` 的 `AdapterCapability` 只有 5 个值：

```
PARSE / DETECT / FETCH / VERIFY / WRITE
```

这 5 个值描述的是**接口能力有无**（某个可选 callable 是否为 None），不是**数据解析深度**。

V5.1 蓝图 §5 要求按**六级**分级，因为痛点二的根因就是「Adapter 注册成功被误认为完整支持」：

| 蓝图级别 | 语义 | 可宣称 |
|---|---|---|
| DETECT | 识别文件/容器 | 可识别格式 |
| INSPECT | 读取元数据 | 可读取元数据 |
| PAYLOAD_PARSE | 读取真实 payload | 可解析数据内容 |
| STREAM_EXTRACT | 输出 SensorStream/Observation/Action | 可提取数据流 |
| EPISODE_BUILD | 构建带时间域 Episode | 可生成 Episode |
| ROUND_TRIP | 输出可被目标 Loader 读取 | 可通过往返验证 |

S0 实测结论：**所有格式全部止步 INSPECT**——HDF5 只读结构不读数值，ROS Bag 显式不遍历 messages，MP4 只探流信息。但用 5 值枚举无法表达这个事实。

## 2. 候选方案

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A（本 ADR 采用）** | 新建独立的 `FormatCapabilityLevel` 六级枚举 + `AdapterCapabilityProfile`，与现有 `AdapterCapability` **并存** | 不破坏 5 值枚举的既有语义（接口能力）；新维度表达解析深度 | 两套概念需文档说明 |
| B | 直接把 `AdapterCapability` 改成六级 | 概念统一 | **破坏性**：`PARSE`/`FETCH` 等语义无处安放；existing 代码需全部改 |
| C | 只对 Golden Path 格式做分级 | 工作量最小 | 无法支撑「格式能力矩阵」这一交付物 |

**决策：采用 A。** 理由：现有 5 值枚举回答的是「这个 Adapter 支持哪些操作」，六级回答的是「解析到了多深」。两者是**正交**维度，合并会丢失信息。

## 3. 详细设计

### 3.1 新增枚举

```python
class FormatCapabilityLevel(StrEnum):
    """数据解析深度。数字越大越深；用于「能宣称什么」而非「接口有无」。"""
    DETECT = "detect"                  # 1 能识别容器
    INSPECT = "inspect"                # 2 能读元数据
    PAYLOAD_PARSE = "payload_parse"    # 3 能读真实 payload
    STREAM_EXTRACT = "stream_extract"  # 4 能产出 SensorStream/Observation/Action
    EPISODE_BUILD = "episode_build"    # 5 能生成带时间域 Episode
    ROUND_TRIP = "round_trip"          # 6 产物可被目标 Loader 读取
```

**强制规则**：级别必须**连续**。宣称 `EPISODE_BUILD` 必须先满足 `STREAM_EXTRACT`。不连续声明在构造时抛错——这是防止虚报的结构性保障。

### 3.2 能力档案

```python
@dataclass(frozen=True, slots=True)
class FormatCapabilityProfile:
    format_name: str                    # "hdf5" / "rosbag" / "mcap" / ...
    max_level: FormatCapabilityLevel    # 已达最高级
    evidence: tuple[str, ...]           # 证据：fixture 路径 / 测试名 / 样本 SHA256
    limitations: tuple[str, ...]        # 明确不支持的（如「不读图像 obs」）
    parser_version: str
```

**关键约束**：
- `evidence` 为空时不允许 `max_level > DETECT`（无证据不能宣称深度）
- `limitations` 必须显式列出已知缺口，不许留空掩盖

### 3.3 与既有 `AdapterCapability` 的关系

| 维度 | 枚举 | 回答的问题 | 用途 |
|---|---|---|---|
| 接口能力 | `AdapterCapability`（保留） | 支持 PARSE/DETECT/FETCH/VERIFY/WRITE 哪些操作？ | 运行时分派 |
| 解析深度 | `FormatCapabilityLevel`（新增） | 解析到了第几级？能对外宣称什么？ | 能力矩阵、UI 展示、验收 |

不改 `AdapterCapability`，只在 `AdapterSpec` 上**新增可选字段** `capability_profile`。

## 4. 权威格式能力档案（S0 基线 → V5.1 实证落地状态）

### 4.1 S0 初始实测基线（2026-11-24）

| 格式 | S0 max_level | 当时依据 | 当时缺口 |
|---|---|---|---|
| hdf5 | **INSPECT** | `read_hdf5_structure()` 可读真实结构（RoboMimic 200 demos / 9666 帧） | 不读数值 payload |
| rosbag | **INSPECT** | `read_rosbag_structure()` 可读 topic/时间范围 | 显式不遍历 messages |
| mp4 | **INSPECT** | ffprobe 探流信息 | 不抽帧、不解码 |
| parquet | **INSPECT** | 只读 footer schema | 不读数据页行值 |
| mcap | **INSPECT** | `read_mcap_metadata()` 只读 header/footer/summary | 不读 chunk 数据页与消息体 |
| lerobot | **INSPECT** | 元数据 parser + `METADATA_ONLY` 导出 | 无逐帧 observation/action |

### 4.2 V5.1 落地后的权威矩阵（`ddp_data.capability.BUILTIN_FORMAT_CAPABILITY_MATRIX`）

| 格式 | 当前 `max_level` | 实证依据（CI 自动核验） | 诚实边界（`limitations`） |
|---|---|---|---|
| **hdf5** | **ROUND_TRIP**（仅 `robomimic_hdf5_v1.5` 已知布局）<br>通用任意 HDF5 仍为 **INSPECT** | `read_hdf5_dataset_slice()` 读真实数值 → `extract_robomimic_demo()` 产出三件套 → `write_frame_episode()` 写出逐帧 Parquet → `lerobot==0.3.3` 逐帧读取 59/59 帧通过（`verify_frame_load.py`） | 仅 `robomimic_hdf5_v1.5` 布局打通逐帧物化与往返，通用任意布局 HDF5 仅 `INSPECT`；`ph` 样本不含图像流（`video_provided=false`）；软/外部链接不跟随 |
| **rosbag** | **INSPECT** | `read_rosbag_structure()` 可读 rosbag1/rosbag2 索引（`test_rosbag_probe.py`） | 显式不遍历 `messages()` 且不反序列化消息体；孤立 bag 不含任务/指令语义 |
| **mp4** | **INSPECT** | 受控 `ffprobe` 子进程探流信息（`test_mp4_probe.py`） | 不解码视频帧、不抽帧；依赖宿主机 `ffprobe` |
| **parquet** | **INSPECT** | PyArrow 只读 footer schema 与期望列缺失声明（`test_parquet_adapter.py`） | 不读取数据页行值；加密 footer（`PARE`）显式拒绝 |
| **mcap** | **INSPECT** | `mcap` 官方 `SeekingReader` 只读 header/footer/summary（`test_mcap_adapter.py`） | 不读取 chunk 数据页且不解码消息体；无 summary 文件返回未知计数（`None`） |
| **lerobot** | **ROUND_TRIP**（导出侧有逐帧流时）<br>导入侧 `lerobot.human1m_preview` 为 **INSPECT** | `frame_writer.write_frame_episode()` + `lerobot==0.3.3` Loader 逐帧往返验证；`export_writer` 保留 `METADATA_ONLY` | 导出双路共存（无逐帧流时仅 `METADATA_ONLY`，严禁混称训练就绪）；导入侧仅解析 Human-1M `metadata.jsonl`；不含视频封装 |
| **generic_jsonl** | **INSPECT** | `parse_generic_jsonl()` 映射一行一 Episode 元数据（`test_adapter_registry.py`） | 仅解析元数据 JSONL 行，不读取底层媒体或传感器逐帧流 |

**代码中的 `BUILTIN_FORMAT_CAPABILITY_MATRIX` 与 `AdapterSpec.capability_profile` 是「当前可宣称范围」的唯一权威来源，由 `scripts/check_adapter_capability.py` 强制校验证据存在性；任何 UI/文档宣称不得超过它。**

## 5. 后果

### 正面
- 「注册成功 = 完整支持」的误解被结构性阻断
- 能力矩阵可自动化测试（每个格式一个 fixture + 断言 max_level）
- V5.1 升级路径清晰：HDF5 从 INSPECT → PAYLOAD_PARSE 只需加数值读取

### 负面
- 两套枚举并存，需要文档解释（本 ADR §3.3 承担）
- 每个格式需维护 evidence 与 limitations，有维护成本

### 风险
- 若 evidence 造假，分级失去意义 → 缓解：evidence 必须是**可执行的测试名或真实样本 SHA256**，CI 校验存在性

## 6. 落地计划

1. `data-platform/ddp_data/capability.py`：新增枚举 + profile + 连续性校验
2. `AdapterSpec` 新增可选 `capability_profile` 字段（向后兼容，None 时按 `DETECT` 处理）
3. `builtin.py` 为现有 2 个 Adapter 填 profile
4. 新增 `scripts/check_adapter_capability.py`：校验 profile 的 evidence 指向真实存在的测试/fixture
5. 前端/文档展示能力矩阵时只读此来源

## 7. 不做的事

- ❌ 不改 `AdapterCapability` 五值枚举
- ❌ 不重写现有 parser
- ❌ 不一次性把所有格式升到最高级（按 Golden Path 逐步）
- ❌ 不用「支持 X 格式」这种模糊表述替代分级
