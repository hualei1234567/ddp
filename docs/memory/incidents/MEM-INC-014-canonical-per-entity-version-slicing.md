### MEM-INC-014 | Canonical 多版本演进必须按「实体 × 版本」双向裁剪字段表并重导出 Schema

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `Canonical 数据契约` | 批次/资产 `ddp_data.canonical.v1` | 密级 `INTERNAL`
- **核心结论**：Canonical Schema 从 `1.0.0` 增量演进到 `1.1.0` 时，不能用单一全局集合剥离新增字段，也不能只在 `__post_init__` 裁剪而忽略 `to_dict` / `from_dict`。必须按「实体类名 × schema_version」维护专属新增字段集，在构造、序列化、反序列化三处同步裁剪，并在每次改动字段声明后立即运行 `scripts/export_canonical_schemas.py` 重导出 JSON Schema。
- **关键依据与细节**：
  - 踩坑 1（硬编码单表）：初版 `_fields_for_version` 误硬编码返回 `SENSOR_STREAM_FIELDS_1_0`，导致 `Observation` / `Action` 读 1.0 数据时被套用 `SensorStream` 字段表而报「未定义字段」。
  - 踩坑 2（跨实体同名字段引入版本不同）：`dtype` 与 `sensor_id` 在 `Observation` 1.0 中本来就存在，但在 `SensorStream` / `Action` 中属于 1.1 新增；若用全局 `SCHEMA_1_1_ONLY_FIELDS` 一刀切过滤，会把 `Observation` 1.0 的合法字段误删。
  - 踩坑 3（`from_dict` / `to_dict` 未按版本分派）：`from_dict` 若固定传 1.1 全量 `SPECS`，读取不含 1.1 键的真实 1.0 JSON 时会把缺失的 1.1 字段解码为 `None`；`to_dict` 若不按版本过滤，会把 1.0 实例多写出 1.1 的 `None` 键。
  - 踩坑 4（生成物漂移）：增补 `Action` / `Observation` 字段后未同步重跑 `export_canonical_schemas.py`，直接触发 `test_committed_schema_matches_generator` 与 `test_missing_fields_enum_only_lists_optional_fields` 失败。
- **影响与后续动作**：
  - `_ADDED_IN_1_1_BY_ENTITY` 按实体精确声明增量字段，`__post_init__`、`to_dict`、`from_dict` 三处统一调用 `_fields_for_version(entity_name, specs, version)`。
  - 单测显式覆盖不含 1.1 键的纯 1.0 字典 `from_dict` 读取、1.0 混入 1.1 键拒绝、以及 1.1 完整往返。

---

#### 工程扩展字段

- **check_ref**：`backend/tests/unit/test_canonical_v1.py::test_canonical_1_1_observation_and_action_fields_and_1_0_compat`
- **recurrence**：`1`
- **tags**：`backward-compatibility,canonical,deserialization,json-schema,schema-evolution,serialization,v1.1`
