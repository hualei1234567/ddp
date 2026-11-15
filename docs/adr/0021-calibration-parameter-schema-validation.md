# ADR-0021 标定参数按契约 JSON Schema 校验（G-19）

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：ADR-0009（设备域契约冻结）；`CLAUDE.md`《复用优先与代码质量标准》第二/三节；
  阶段 1 差距清单 G-19
- 影响范围：`backend/app/domain/devices/`；标定记录创建/更新的参数形状；对外字段不变

## 1. 背景

`CalibrationRecord.parameters` 此前是 `Mapping[str, object]` 自由字典：
标定记录可以写入任意形状的参数，接口照样 201。后果是三方面的：

1. **下游无法消费**：手眼标定少 `rotation_quat_wxyz`、内参少 `fx`，
   到 Canonical 转换或训练数据构建时才暴露，届时不知道是采集问题还是录入问题。
2. **契约形同虚设**：`docs/architecture/contract-draft/device/calibration-parameters.v1.schema.json`
   已经定义了 8 种 `calibration_type` 的完整形状（含 `allOf` / `$ref` / 数值约束），
   但代码从不读它，契约与运行时脱节。
3. **测试被"简化"污染**：为了让测试通过，各处随手写 `{"fx": 1.0}`、`{}`，
   连"参数到底该长什么样"都没有一个权威答案。

审计把这一项记为 **G-19（P0）**。

## 2. 决策

**参数形状的唯一权威是契约文件；代码只负责"按类型选对 `$defs` 并校验"。**

实现 `backend/app/domain/devices/parameter_schema.py`：

- `_base_schema()` 读取并 `lru_cache` 契约文件；文件缺失直接抛
  `DomainValidationError`（配置错误必须显式失败，不能静默放行）。
- `_validator_for(calibration_type)` 把该类型的 `$defs` 条目提为根 schema，
  同时保留完整 `$defs` 以便 `$ref` 解析；按类型缓存 validator。
- `validate_parameters()` 在 `CalibrationRecord.__post_init__` 中调用，
  校验失败抛 `DomainValidationError`，消息形如：

  ```
  INTRINSICS 参数不合法：distortion_coefficients/2 — 'x' is not of type 'number'
  （共 2 处不合法，仅显示第一处）
  ```

  带字段路径是刻意的：标定工程师要知道改哪个字段，而不是收到"参数非法"。

- 领域层参数是 `MappingProxyType`（冻结视图），jsonschema 只认 `dict`，
  校验前 `_unfreeze()` 递归解包——校验是只读操作，语义不变。

## 3. 为什么是 jsonschema 而不是手写校验

契约 schema 用了 `allOf`、`$ref`（`vector3` / `quaternion_wxyz` / `matrix3` /
`int64_string` / `extensions`）、`enum`、数值范围与 `additionalProperties: false`。
手写等价实现有三个确定问题：

1. **漏分支**：8 种类型 × 若干约束，靠人工审阅保证等价不现实。
2. **无法跟随契约演进**：契约是独立评审的产物，手写校验会成为第二份真相。
3. **`$ref` 复用**：`vector3` 在 5 种类型里出现，手写要重复 5 次。

`CLAUDE.md` §二明确"协议、加密、存储、解析、调度、表格/表单等高成熟度能力，
一律使用官方 SDK 或主流开源实现"。JSON Schema 校验属于此类。

## 4. 依赖评估（`CLAUDE.md` §三）

| 项 | 结论 |
|---|---|
| 用途 | 按 Draft 2020-12 契约校验标定参数形状 |
| 替代方案 | 手写校验（第二份真相，漏分支）；Pydantic 逐类型建模（8 个 `extra="forbid"` 模型 + `$ref` 复用需手工展开，且会把契约复制进代码）；`fastjsonschema`（编译式，速度快但错误路径信息弱，且需为 8 类型预编译） |
| 版本固定 | `jsonschema==4.23.0`（`requirements.txt` 精确固定） |
| 体积/性能 | 纯 Python；传递依赖 `attrs`、`jsonschema-specifications`、`referencing`、`rpds-py`（后者为 Rust 扩展，wheel 齐全）。校验在领域对象构造时执行，单次微秒级；validator 按类型缓存，schema 只读一次 |
| 许可证 | MIT |
| 安全公告 | 4.23.0 为当前维护线；历史 CVE-2021-41103（`jsonschema` `$ref` 未限制时可能读本地文件）通过不启用 `RefResolver` 远程解析、只解析内置 `$defs` 规避；本项目 schema 无外部 `$ref` |

体积说明：`rpds-py` 是唯一二进制依赖，`psycopg[binary]`、`boto3` 已在栈内，
不引入新的构建工具链要求。

## 5. 影响与回滚

- **API 契约不变**：字段名、类型、响应结构全部不变；变化只在"非法参数从此 422"。
  这是有意的收紧，属于修 Bug 而非改契约。
- **数据迁移**：`calibration_records.parameters` 是 JSON 列，历史数据若形状非法，
  读取不受影响（校验只在构造领域对象时执行）。当前库里没有非法历史数据
  （阶段 1 仅在测试与本机验收中产生数据）。
- **测试改造**：新增 `backend/tests/calibration_fixtures.py` 提供 8 种类型的合法
  参数，6 个测试文件统一改为 `parameters_for("INTRINSICS")`；
  `apps/web/scripts/device_contract_snapshot.py` 的 `transform_type` 由
  `eye_in_hand`（旧样例值）改为契约枚举 `STATIC`，契约快照随之重生成。
- **回滚**：删除 `__post_init__` 中的 `validate_parameters()` 调用即可，
  无需迁移、无需改契约。

## 6. 验收证据

| 项 | 结果 |
|---|---|
| 8 种类型合法参数通过真实 schema | 8/8 |
| 非法参数被拒且消息含字段路径 | 见 `tests/unit/test_devices_domain.py` |
| SQLite 全量 | 892 passed / 21 skipped |
| 真实 PostgreSQL 全量 | 913 passed / 0 skipped |
| `make check` | 通过（ruff + pytest + tsc + eslint + build + secret 扫描 + 守卫测试） |
