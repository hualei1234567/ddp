# ADR-0049 Canonical Schema 1.1：补齐真实 payload 必需字段

- 状态：**ACCEPTED**（已落地 SensorStream + Observation + Action 全量 1.1 字段与双向兼容）
- 日期：2026-11-24
- 决策者：System Architect + 后端
- 关联：V51-001/002/003、V5.1 蓝图 `06_SensorStream_Observation_Action物化设计.md` §3~§5、`07_多流时间同步标定与坐标系.md` §2
- 约束：**不删除任何现有字段**；不改变 `missing_fields` 强制登记语义；不动 `*_ns` 整数纳秒与十进制字符串序列化

## 1. 背景

S0 复核（`V51_Canonical_Gap_Report.md`）实测：`SensorStream` 字段覆盖约 60%，`Observation`/`Action` 缺关键字段。

关键缺口（对照蓝图硬要求）：

| 缺字段 | 蓝图依据 | 影响 |
|---|---|---|
| `dtype` | §3/§4/§5 必填 | 无法表达数值类型，下游无法正确反序列化 |
| `timestamp_raw` + `timestamp_normalized` 双时间 | §2 硬要求「保留原始时间戳，归一化另存」 | 当前只有单一时间，无法审计同步过程 |
| `parser_version` | §8「Parser Version」版本化 | 无法判断产物由哪个解析器版本生成 |
| `missing_count` / `invalid_count` | §7 缺失数据规则 | 无法量化数据完整度 |
| `validity` | §4/§5 必填 | 无法标记单帧/单样本是否有效 |
| `source_index` / `source_offset` | §4/§5 必填 | 无法回溯到源文件具体位置 |
| `sensor_id` / `device_id` (UUID) | §3 必填 | 只有名字，无 UUID 关联 |
| `start_timestamp_ns` / `end_timestamp_ns` | §3 必填 | 只有 `duration_ns`，无法定位绝对时间 |
| `semantics_version` | §5 必填（Action） | 动作语义不可版本化 |
| `transform_provenance` | §4 必填（Observation） | 坐标变换不可追溯 |

## 2. 候选方案

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A（本 ADR 采用）** | 新增 `SCHEMA_VERSION="1.1"`，补 10 类字段**全部可选**（默认 `None`），1.0 仍可读 | 向后兼容；历史 87 个 dataset_versions 不受影响 | 需维护两版字段表 |
| B | 直接扩展 1.0 | 改动最小 | 1.0 语义漂移；旧产物"看起来"有字段但值为 None，无法区分"新写的空"与"老版本没有" |
| C | 新建 `SensorStreamV2` 等独立类型 | 完全隔离 | 类型爆炸；下游需双路分支；`to_dict` 分派复杂 |

**决策：采用 A。** 理由：
1. 仓库已有 `SUPPORTED_SCHEMA_VERSIONS` 多版本机制（`_schema.py`），1.1 是既有路径的自然延伸
2. 全部新增字段可选 + `missing_fields` 显式登记，保持"缺失即显式"的核心不变量
3. 不破坏 1.0 可读性——`from_dict` 按 `schema_version` 分派字段表

## 3. 详细设计

### 3.1 版本声明

```python
# canonical/v1.py
SCHEMA_VERSION = "1.1"                    # 当前写出使用的版本
SUPPORTED_SCHEMA_VERSIONS = ("1.0", "1.1")  # 可读取的版本
```

**规则**：
- 新产物写 `1.1`
- 读 `1.0` 产物时，新增字段视为不存在（不补 `None`，避免伪造"有值但为空"）
- 读 `1.1` 时按完整字段表校验

### 3.2 新增字段清单（全部 `| None = None`）

#### SensorStream（+5）

| 字段 | 类型 | 说明 |
|---|---|---|
| `dtype` | `str \| None` | 如 `"float32"` / `"int64"`；对应蓝图 §3 |
| `start_timestamp_ns` | `int \| None` | 流起始绝对时间 |
| `end_timestamp_ns` | `int \| None` | 流结束绝对时间 |
| `missing_count` | `int \| None` | 缺失样本数（蓝图 §7） |
| `invalid_count` | `int \| None` | 无效样本数 |
| `parser_version` | `str \| None` | 解析器版本（蓝图 §8） |
| `sensor_id` / `device_id` | `str \| None` | UUID 字符串，与现有 `sensor_name` 并存 |

#### Observation（+6）

| 字段 | 类型 | 说明 |
|---|---|---|
| `timestamp_raw_ns` | `int \| None` | 源数据原始时间戳（蓝图 §2 硬要求） |
| `timestamp_normalized_ns` | `int \| None` | 归一化后时间戳 |
| `dtype` | `str \| None` | |
| `validity` | `str \| None` | `VALID` / `INVALID` / `SUSPECT` |
| `source_index` | `int \| None` | 源文件中的索引 |
| `source_offset` | `int \| None` | 源文件中的字节偏移 |
| `transform_provenance` | `str \| None` | 坐标变换来源 |

#### Action（+5）

| 字段 | 类型 | 说明 |
|---|---|---|
| `timestamp_raw_ns` / `timestamp_normalized_ns` | `int \| None` | 同 Observation |
| `dtype` | `str \| None` | |
| `validity` | `str \| None` | |
| `source_index` / `source_offset` | `int \| None` | |
| `semantics_version` | `str \| None` | 动作语义版本（蓝图 §5） |

### 3.3 `missing_fields` 语义保持

**关键**：新增字段如果为 `None`，**必须**出现在 `missing_fields` 里——这是现有机制，不改。

区别在于：
- 1.0 产物：新增字段**不在字段表**里，所以既不要求值也不要求登记
- 1.1 产物：新增字段**在字段表**里，为 `None` 时必须在 `missing_fields` 登记

这样"1.0 没有这个概念"与"1.1 有这个概念但数据缺"在结构上仍然可分。

### 3.4 JSON Schema 同步

`json_schema.py` 生成产物需同步：`check.sh` 第 9 步会校验一致性。1.1 的 schema 应包含新增字段（`type` 允许 `null`）。

## 4. 后果

### 正面
- 能表达蓝图 §3~§5 全部必填字段
- 向后兼容：87 个历史 dataset_versions 不变
- `missing_fields` 机制完整性保持

### 负面
- 两版字段表需维护
- 1.0 与 1.1 产物并存期，下游需处理"字段可能不存在"

### 风险与缓解
| 风险 | 缓解 |
|---|---|
| 下游按 1.1 字段读取 1.0 产物出错 | `from_dict` 按版本分派；文档明确；导出 sidecar 声明 `schema_version` |
| 忘记同步 `json_schema.py` | `check.sh` 第 9 步强制校验 |
| 新增字段被滥用（乱填） | 只允许在真实解析时填；无真实数据必须留 `None` + `missing_fields` |

## 5. 落地计划

1. `canonical/_schema.py`：新增 1.1 字段表常量
2. `canonical/v1.py`：三个 dataclass 加字段 + `__post_init__` 按版本选表
3. `canonical/json_schema.py`：导出 1.1 schema
4. `scripts/export_canonical_schemas.py`：重生成
5. 测试：1.0 读取兼容、1.1 往返、`missing_fields` 强制登记、版本拒绝未知

## 6. 不做的事

- ❌ 不删除任何现有字段
- ❌ 不改 `missing_fields` 机制
- ❌ 不改 `*_ns` → 字符串/浮点
- ❌ 不在本 ADR 中改 LeRobot 导出语义（那是 ADR-0046 范畴，单独处理）
- ❌ 不做破坏性迁移（无 DB 变更，Canonical 是内存/JSON 结构）
