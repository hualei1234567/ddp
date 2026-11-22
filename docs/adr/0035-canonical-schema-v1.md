# ADR-0035 Canonical Schema v1：只读契约、版本化与 LeRobot 投影

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-18
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：`docs/data-standards/Canonical草案.md`、`docs/roadmap/next-stage-v2/建设文档/03_数据架构与Canonical_Schema演进规范.md`、
  `docs/roadmap/v2.1/n0/v2.1-backlog.md`（DDP-V21-N3-001）、ADR-0018（`data-platform/` 分层）、
  `data-platform/ddp_data/adapters/registry.py`
- 影响范围：`data-platform/ddp_data/canonical/`（新增）；不涉及数据库、API、后端领域模型、
  `packages/contracts/`

## 1. 背景

Canonical 目前只有 **Episode 级**声明：

- `data-platform/ddp_data/adapters/registry.py::CANONICAL_EPISODE_FIELDS` 是字段声明的
  权威表（10 个字段）；
- `data-platform/ddp_data/lerobot/parser.py::CanonicalEpisode` 是其对应的数据结构；
- `docs/data-standards/Canonical草案.md` 定义了更完整的时间、坐标系、单位、帧、动作与
  观测语义，但版本是 `ddp.canonical/1.0.0-draft`，**未冻结，也没有代码承载**。

能力矩阵（`docs/roadmap/v2.1/n0/capability-matrix.md` §3）判定：12 类 Canonical 实体中
DataSource / Dataset / DatasetVersion / RawAsset / Episode / Annotation 已在 backend domain
存在；**Frame / VideoStream / SensorStream / Action / Observation 全仓不存在**。

由此产生三个具体问题：

1. 下游无法回答"这批数据有没有帧级/动作级内容"，只能在 Episode 的 `metadata` 里放一句
   自由文本；
2. 没有统一版本号，Adapter 输出无法声明"按哪一版 Canonical 映射"；
3. "缺字段"与"字段为空"无法区分，容易被下游当成 0 或空动作使用。

本 ADR 只解决**只读契约**：定义 v1 实体、版本、缺失语义与既有 Episode 的投影方式。
不建表、不改 API、不重写 LeRobot 解析器。

## 2. 决策

### 2.1 分层

```text
Raw            原始文件 / 原始字段 / 原始时间戳（不可覆盖）
  ↓ Adapter 解析（lerobot、generic jsonl、后续 Parquet/MCAP…）
Canonical v1   本 ADR 定义的只读契约（Episode / Frame / VideoStream /
               SensorStream / Action / Observation）
  ↓ Processing（N4，另行设计）
Derived        Processed / Labeled / Dataset / Asset
```

Canonical v1 是**格式无关的内部契约**：LeRobot、MCAP、ROS bag、HDF5、MP4、Parquet
都是输入或交换格式，不是 DDP 内部模型。`data-platform/` 仍是纯数据能力包，不依赖
FastAPI / SQLAlchemy / 队列 / 云 SDK（ADR-0018）。

模块结构：

```text
data-platform/ddp_data/canonical/
  __init__.py      对外导出
  _schema.py       字段声明、校验、缺失字段一致性、序列化（通用机制）
  v1.py            6 个实体 + v1 字段清单 + 跨字段约束
  projection.py    既有 CanonicalEpisode → v1 Episode / VideoStream（只读）
```

### 2.2 实体与字段清单

| 实体 | 职责 | 关键字段 |
|---|---|---|
| `Episode` | 任务级样本单元，引用下层实体 | `episode_key`、`task_name`、`instruction`、`video_stream_ids`、`sensor_stream_ids`、`action_ids`、`observation_ids`、`frame_count`、`raw` |
| `Frame` | 帧级时间锚点 | `frame_index`、`timestamp_ns`、`source_time_ns`、`source_time_domain`、`clock_id`、`time_base_ref`、`uncertainty_ns`、`observation_ids`、`is_dropped` |
| `VideoStream` | 视频/相机流声明 | `stream_id`、`camera_name`、`camera_name_raw`、`modality`、`media_format`、`storage_reference`、`duration_ns`、`fps`、`frame_count`、`width`、`height`、`checksum` |
| `SensorStream` | 非视频传感器流声明 | `stream_id`、`sensor_type`、`sample_rate_hz`、`channel_names`、`unit`、`frame_id`、`frame_convention`、`calibration_id/version` |
| `Action` | 动作序列/指令 | `action_id`、`episode_key`、`action_type`、`start_time_ns`、`end_time_ns`、`values`、`unit`、`frame_id`、`joint_names`、`source` |
| `Observation` | 单次观测声明 | `observation_id`、`episode_key`、`observation_type`、`stream_id`、`timestamp_ns`、`frame_id`、`calibration_id`、`shape`、`dtype`、`encoding`、`payload_ref` |

每个实体导出 `*_FIELDS`（`FieldSpec` 元组），供 Adapter 声明支持度；字段名与
`docs/data-standards/Canonical草案.md` 对齐。

### 2.3 Episode 与既有 Canonical 的关系

现有 Episode 字段原样保留：`episode_key`、`task_name`、`instruction`、`environment`、
`setting`、`episode_seconds`、`source_fps`、`has_wrist`、`raw`。

唯一改名为：**`media_assets` → `video_stream_ids`**。媒体资产从 Episode 内嵌列表
提升为 `VideoStream` 实体，Episode 只保存引用。该映射登记在
`LEGACY_EPISODE_FIELD_MAPPING`，由测试保证 `CANONICAL_EPISODE_FIELDS` 的每个字段
要么直接存在、要么有显式映射，避免"悄悄丢字段"。

## 3. ID 规则

- **持久化 ID**：沿用草案 §2，`episode_id` / `project_id` 等使用 UUIDv7 字符串；由
  服务端在落库时分配，客户端传入一律不信任。只读契约中允许为空并登记缺失。
- **业务编码**：`episode_key` 是来源侧稳定键（如 Human-1M 的 `example_id`），
  可作为追踪依据，但**不作为数据库外键**。
- **流/观测/动作 ID**：格式为不透明字符串。投影阶段使用
  `<episode_key>:video:<序号>`，保证同一 Episode 的同一顺序投影结果确定；不按内容
  Hash 生成，避免路径改名导致 ID 漂移。
- 所有实体 ID 一旦分配不得复用；实体不可变，修订产生新版本（草案 §11）。

## 4. 版本与兼容策略

- 版本串：`ddp.canonical/<major>.<minor>.<patch>`；v1 冻结为 **`ddp.canonical/1.0.0`**。
- 每个实体都带 `schema_version` 字段；构造与 `from_dict` 都只接受
  `SUPPORTED_SCHEMA_VERSIONS` 内的精确值。**不做静默升级**，未知版本显式失败。
- 兼容规则（与草案 §13 一致）：
  - 新增**可选**字段、增加枚举值 → minor；
  - 删除/改名/改类型/改语义、可选变必填、单位或坐标系语义变化 → major，需新 ADR；
  - 旧版本数据不原地改写；需要转换时提供纯函数转换器并保留输入/输出版本。
- 当前只实现读取；写入与迁移器留到有真实来源时按版本增量添加。

## 5. 缺失字段必须显式

每个实体都有 `missing_fields: tuple[str, ...]`。规则由 `_schema.py` 强制执行：

1. 任何可选字段为空（`None` / 空字符串 / 空元组 / 空映射）时，必须列入
   `missing_fields`，否则抛 `CanonicalFieldError`；
2. 声明为缺失的字段不得同时有值；
3. `missing_fields` 只能引用已声明字段，不得重复；
4. 身份/语义必填字段（如 `episode_key`、`stream_id`、`frame_index`、
   `action_type`、`observation_type`）不能声明为缺失，必须直接提供。

这条规则是"不得把缺失字段伪装成 0 或空动作"（草案 §5.4）的**结构性保证**：
下游读到的空值一定伴随显式缺失声明。

`0` 与 `False` 是真实值，不算缺失；因此 `frame_count=0`、`has_wrist=False` 不会被
误判为缺数据。

## 6. 为什么不建表

1. **当前没有真实消费者**：backend 的 Episode/Annotation 表已能支撑现有主链；
   帧级/动作级数据尚未接入。先建表会产生空表与无法验证的迁移。
2. **Canonical 是派生层**：帧数据体量大，按草案 §11 应放对象存储（Parquet/Arrow），
   数据库只存 Manifest 指针、Hash、状态与索引字段。表结构应等 N4 ProcessingRun
   与真实 Adapter 的产物形态确定后再设计。
3. **资产保护**：新建表会牵动迁移编号、领域模型与权限；在本任务范围内没有收益。
4. **契约先行**：只读 dataclass 能被 Adapter、测试和未来 Worker 直接复用，且
   不引入 ORM/迁移风险。

后续若需要持久化，按"扩展现有表 > 新建表"的顺序评估，并单独提交迁移与 ADR。

## 7. 与 LeRobot 的关系

- LeRobot 是**导出/交换格式之一**，不是 DDP 内部唯一模型（草案 §1.7、§12）。
- `lerobot/parser.py::CanonicalEpisode` 保持不动，仍是 Human-1M preview 的唯一映射
  实现；`projection.py::project_episode()` 只做只读投影。
- 投影如实保留/登记：
  - `camera_name`（归一化）与 `camera_name_raw`（原始 `view`）；
  - `excerpt_start_seconds` / `excerpt_seconds` 转成 `excerpt_start_ns` /
    `duration_ns`（确定性单位换算，非猜测）；
  - `modality="unknown"` 是解析器占位，按缺失处理；
  - `media_format`、`width`、`height`、`frame_count`、`encoding`、`dropped_count`
    不在 preview 元数据中，显式登记为缺失；
  - `sensor_stream_ids` / `action_ids` / `observation_ids` / `frame_count` /
    `episode_id` / `project_id` / 起止时间无来源，全部留空并登记缺失。
- 不产出空 Frame、零动作或空观测。等真实格式（Parquet/MCAP/ROS/HDF5）提供数据后，
  再由对应 Adapter 映射。

## 8. 与 Canonical 草案的差异

| 项 | 草案 | v1 实现 | 原因 |
|---|---|---|---|
| 版本 | `ddp.canonical/1.0.0-draft` | `ddp.canonical/1.0.0` | 本 ADR 冻结只读契约；草案仍保留为设计背景 |
| Episode 字段名 | `episode_id`、`start_time` 等 | 保留 `episode_key`、`episode_seconds` 等既有名，新增 `episode_id`、`start_time_ns` 可选 | 保证现有 Adapter 声明与历史导入可解释；不破坏已有字段 |
| 时间戳 JSON | 十进制字符串 | 同左（`*_ns` 字段 `to_dict` 输出字符串） | 避免 2^53 精度丢失 |
| 流实体 | 统一 `streams[]` | 拆为 `VideoStream` / `SensorStream` | 媒体字段与传感器字段差异大，拆开后缺失声明更准确 |
| `media_assets` | 无 | 映射为 `video_stream_ids` | 媒体资产提升为实体，Episode 只存引用 |

## 9. 备选方案

1. **直接建 Canonical 业务表**：否决。没有真实数据与消费者，迁移无法验证，
   且违反"资产保护 / 增量迭代"。
2. **继续只维护 Episode 级字段**：否决。无法表达帧/动作/观测，能力矩阵缺口不关闭。
3. **复用 backend domain 模型**：否决。`data-platform/` 必须保持纯数据能力、
   不依赖 Web/ORM；Canonical 也不应被项目隔离与权限模型绑死。
4. **用 Pydantic / JSON Schema 生成模型**：暂缓。当前无新增依赖批准；dataclass
   + 显式校验已满足契约与测试需求。若未来需要跨语言 Schema，再评估
   `packages/contracts` 侧生成。
   > **已落地（V31-017，ADR-0040）**：共享 JSON Schema 由
   > `ddp_data.canonical.json_schema` 从 `*_FIELDS` 生成，产物在
   > `packages/contracts/schemas/canonical-v1/` 并逐字节锁死。
   > 未引入新依赖，dataclass 仍是唯一权威。

## 10. 影响、风险与回滚

- **依赖**：仅标准库（`dataclasses`、`collections.abc`、`typing`、`math`），
  无新增依赖，无版本固定问题。
- **数据库 / API / 配置**：无变化。
- **风险**：
  - 字段贴合草案但仍是 v1 初版；若 N3-002/Parquet 暴露命名问题，按 minor/major 演进；
  - 严格校验可能拒绝部分真实脏数据；这是刻意的失败关闭，调用方需显式登记缺失或
    修正 Adapter，而不是放宽 Schema。
- **回滚**：删除 `data-platform/ddp_data/canonical/` 与本 ADR、移除测试即可；
  无数据迁移、无契约冻结、无外部依赖，回滚无副作用。

## 11. 验证

- `backend/tests/unit/test_canonical_v1.py`、`test_canonical_v1_projection.py`：
  - 6 个实体往返序列化（`to_dict` / `from_dict`）；
  - 版本精确校验与未知版本拒绝；
  - 缺失字段显式性（空值未登记、声明缺失但有值、未知字段、必填字段声明缺失）；
  - 非法输入（未知字段、错误类型、错误纳秒字符串）拒绝；
  - `Frame` / `Action` / `Observation` 跨字段约束；
  - 与 `CANONICAL_EPISODE_FIELDS` 的字段声明一致性；
  - 真实 Human-1M 元数据（`parse_human1m_preview`）投影，验证原始字段保留与缺口登记。
- 命令（真实测试环境为 `backend/.venv`，`data-platform` 无独立 pytest 配置）：

  ```bash
  cd backend && .venv/bin/python -m pytest -q \
    tests/unit/test_canonical_v1.py tests/unit/test_canonical_v1_projection.py
  cd backend && .venv/bin/ruff check ../data-platform
  ```
