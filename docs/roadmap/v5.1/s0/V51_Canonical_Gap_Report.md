# V51 Canonical Gap Report（Schema 差距分析）

- 复核对象：`data-platform/ddp_data/canonical/v1.py`（6 实体）+ `_schema.py`（字段机制）+ `json_schema.py`
- 核心问题：**Schema 是否够用？需要改吗？**

---

## 一、现有 Canonical 能力（E2）

### 1.1 六个实体

| 实体 | 行号 | V5.1 用途 |
|---|---|---|
| `Frame` | 162 | 单帧引用 |
| `VideoStream` | 199 | 视频流 |
| **`SensorStream`** | **235** | FR-009 核心 |
| **`Action`** | **279** | FR-011 核心 |
| **`Observation`** | **322** | FR-010 核心 |
| `Episode` | 370 | 容器 |

### 1.2 字段机制（设计质量高）

`_schema.py` 的 `missing_fields` 机制值得保留：
> **任何空值（None/空串/空元组/空映射）都必须显式登记到 `missing_fields`，否则构造失败。**

这意味着「数据没有」与「字段漏填」在**结构上不可混淆**——恰好满足蓝图 §7「缺失数据规则」的要求（`ACTION_SOURCE_ABSENT`/`CALIBRATION_MISSING`/`UNIT_UNKNOWN`/`TIMEBASE_UNRESOLVED`）。

另外：时间戳统一 `*_ns` 整数纳秒，`to_dict` 输出十进制字符串避免 JSON 2^53 精度丢失——符合蓝图 §2「保留 timestamp_raw」。

---

## 二、逐字段对照蓝图要求

### 2.1 SensorStream（FR-009）

| 蓝图 §3 要求 | Canonical 字段 | 状态 |
|---|---|---|
| stream_id | `stream_id` | ✅ |
| episode_id | ❌ 无 | 🟡 归属由 Episode 承载 |
| source_asset_id | ❌ 无（`storage_reference` 近似） | 🟡 需补显式字段 |
| sensor_id / device_id | ❌ 无（`sensor_name` 近似） | 🟡 **需补 UUID 引用** |
| modality | `modality` | ✅ |
| stream_name | `sensor_name` | ✅ 等价 |
| timestamp_source | ❌ 无 | 🟡 由 `Episode.time_base_id` 承载 |
| timestamp_unit | ❌ 无 | 🟡 |
| time_base_id | ❌ 无（在 Episode 上） | 🟡 |
| sampling_rate | `sample_rate_hz` | ✅ |
| dtype | ❌ 无 | 🔴 **缺** |
| shape | ❌ 无（`channel_names` 近似） | 🟡 |
| unit | `unit` | ✅ |
| coordinate_frame | `frame_id` + `frame_convention` | ✅ 且更强（有约定校验） |
| calibration_ref/version | `calibration_id` + `calibration_version` | ✅ |
| payload_location | `storage_reference` | ✅ |
| sample_count | `sample_count` | ✅ |
| start/end timestamp | `duration_ns` 近似 | 🟡 **缺显式 start/end** |
| missing_count / invalid_count | ❌ 无 | 🔴 **缺** |
| parser_version | `schema_version` 近似 | 🟡 **缺独立 parser_version** |

**SensorStream 结论：字段覆盖约 60%，需补 4 类（dtype、start/end_ts、missing/invalid_count、parser_version、sensor/device UUID）。**

### 2.2 Observation（FR-010）

| 蓝图 §4 要求 | Canonical 字段 | 状态 |
|---|---|---|
| observation_id | — | 需看实际定义 |
| episode_id / stream_id | | |
| timestamp_raw / timestamp_normalized | `*_ns` 机制 | 🟡 需确认是否双时间 |
| modality / feature_name | | |
| value 或 payload | | |
| dtype / shape / unit / coordinate_frame | | |
| validity / source_index / source_offset | | |
| calibration_ref / transform provenance | | |

> 注：本节字段级别需在实施前用 `read` 逐字段核对 `v1.py:322-370`，S0 已确认**存在该实体但字段细节需在 M2 开工前精查**。

### 2.3 Action（FR-011）

| 蓝图 §5 要求 | 状态 |
|---|---|
| action_id / episode_id | 待精查 |
| timestamp_raw / normalized | |
| action_type | **蓝图强调「不同来源不可混为同一语义」** |
| value/payload / dtype / shape / unit / coordinate_frame | |
| source_channel / source_asset_id / source_index | |
| validity / semantics_version | |

---

## 三、关键差距汇总

| # | 差距 | 严重度 | 处理建议 |
|---|---|---|---|
| 1 | SensorStream/Observation/Action **无 `*_id` 之外的 UUID 外键**（sensor_id/device_id/raw_asset_id） | 🟠 高 | 新增可选 UUID 字段；走 ADR-0035 增量路径 |
| 2 | **无 `dtype` 字段**（SensorStream/Observation/Action 都需） | 🔴 高 | 必须补；这是蓝图 §3/§4/§5 明确要求 |
| 3 | **无 `missing_count`/`invalid_count`** | 🟠 高 | 必须补；对应蓝图 §7 缺失规则 |
| 4 | **无独立 `parser_version`**（只有 `schema_version`） | 🟠 高 | 必须补；蓝图 §8 要求「Parser Version」版本化 |
| 5 | 无显式 `timestamp_raw` 与 `timestamp_normalized` 双字段 | 🔴 高 | 蓝图 §2 硬要求「保留原始时间戳，归一化另存」 |
| 6 | 无 `validity` 字段（Observation/Action） | 🟠 高 | 补 |
| 7 | 无 `source_index`/`source_offset`（Observation/Action） | 🟠 高 | 补；蓝图 §4/§5 要求 |
| 8 | 无 `semantics_version`（Action） | 🟡 中 | 补 |
| 9 | 无 `transform provenance`（Observation） | 🟡 中 | 补 |
| 10 | SensorStream 无显式 `start/end` 时间戳 | 🟡 中 | 补 |

---

## 四、Schema 变更策略（**重要：不许破坏性改动**）

### 4.1 现状约束

- `check.sh` 第 9 步守护「Canonical JSON Schema 与字段声明一致」→ 改 Schema 必须同步 `json_schema.py`
- `SCHEMA_VERSION` 常量控制版本；`from_dict` 拒绝不支持的版本，**不做静默升级**
- 已有 `CANONICAL` 格式导出 `verification_status=UNVERIFIED`，改动会影响导出兼容

### 4.2 推荐路径（三选一，需 ADR）

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A（推荐）** | 新增 `SCHEMA_VERSION="1.1"`，字段全部**可选**（带默认 None），保留 1.0 可读 | 向后兼容；旧产物仍可 parse | 需同时维护两版字段表 |
| B | 直接扩展 1.0（加可选字段） | 改动最小 | 语义上 1.0 变了，历史产物语义漂移 |
| C | 新建 `SensorStreamV2` 等 | 完全隔离 | 类型爆炸，下游需双路分支 |

**建议 A**：符合仓库既有 ADR-0035「不在通用工具里塞业务规则」与「显式版本」精神。

### 4.3 绝对禁止

- ❌ 改 `missing_fields` 强制登记语义（破坏了「缺失即显式」的核心价值）
- ❌ 把 `*_ns` 改成字符串/浮点（破坏精度保证）
- ❌ 删除任何现有字段（会破坏 87 个 dataset_versions 的历史兼容）
- ❌ 在 S0 阶段动 Schema（本次只读）

---

## 五、结论

1. **Canonical 骨架正确、机制优秀**（`missing_fields` 强制登记 + 纳秒整数 + 版本拒绝静默升级），**不需要重写**。
2. **字段覆盖不足**：SensorStream 约 60%，Observation/Action 需精查但可确定缺 `dtype`/`parser_version`/`timestamp_raw`/`missing_count`/`validity`/`source_index`。
3. **建议在 M2 开工前**提交 ADR：新增 `SCHEMA_VERSION=1.1`，补 10 类字段（全部可选，向后兼容）。
4. **本 S0 阶段不动 Schema**——只登记差距，待用户确认 Golden Path 后随 M2 一并实施。
