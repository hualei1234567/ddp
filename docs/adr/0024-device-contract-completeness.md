# ADR-0024 OpenAPI 完整反映设备域已实现字段（G-20）

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：ADR-0019（frame_id）、ADR-0020（版本分离与 Profile 快照）、ADR-0021（参数 Schema）；
  `packages/contracts` README「未实现字段不得在快照中伪装为可用」
- 影响范围：`packages/contracts/fragments/schemas.stage1.yaml`；契约版本 1.5.0 → 1.6.0

## 1. 背景

审计 G-20 要求「OpenAPI 完整反映冻结 v1；错误码可达」。逐字段比对
契约（`fragments/schemas.stage1.yaml`）与实现（`app/api/v1/schemas.py`）后，
发现**实现了但契约没有声明**的字段：

| Schema | 实现有、契约缺 |
|---|---|
| `Sensor` | `frame_id`、`frame_convention`（ADR-0019 / G-08 已上线） |
| `SensorCreate` / `SensorPatch` | 同上（请求体也接受这两个字段） |
| `Calibration` | `calibration_version`、`profile_version`、`profile_schema_version`、`profile_snapshot`（ADR-0020 / G-04、G-05） |

这是**反方向的差距**：不是"契约超前于实现"，而是实现已经返回了契约里没有的字段。
后果是前端与第三方无法一次性从契约生成类型——只能靠读代码猜；而
`packages/contracts/README.md` 的原则只约束了"未实现不得声明为可用"，
没有约束"已实现必须声明"。

`Calibration` 的缺漏更实质：ADR-0020 明确写了「API 同时返回两者」，
但 `CalibrationOut` 当时并没有返回 `calibration_version`。也就是说
ADR-0020 的验收项「旧客户端仍可只读 version」只兑现了一半——
新客户端读不到 `calibration_version`，无从区分"内容第几版"与"第几次写入"。

## 2. 决策

**以实现为准补齐契约，同时把 ADR-0020 承诺的字段补进 API 响应。**

1. `CalibrationOut` 增加 `calibration_version` / `profile_version` /
   `profile_schema_version` / `profile_snapshot` —— 这是实现补齐，
   不是契约先行（ADR-0020 早已要求）。
2. 契约新增 `FrameConvention` 枚举，`Sensor` / `SensorCreate` / `SensorPatch`
   补 `frame_id` / `frame_convention`。
3. `coordinate_frame` 的描述由「冻结目标更名 frame_id（差距 G-08）」
   改为「DEPRECATED（ADR-0019）：读 frame_id；保留至阶段 2 移除」——
   G-08 阶段 1 已交付，描述里的"差距"字样已过期。
4. `CalibrationCreate.parameters` 补 description 指明形状来源
   （ADR-0021 的契约 schema），使契约读者知道 `additionalProperties: true`
   不代表任意形状都被接受。
5. 契约版本 `1.5.0` → `1.6.0`。

### 2.1 为什么新字段不进 `required`

响应 schema 的 `required` 会约束**所有**响应，包括旧服务的响应。
把新字段标 `required` 等于声明"缺这些字段的响应不合规"，而
`calibration_records` 里 `profile_version` 等列可以为 `NULL`（回填历史记录）。
因此四个新字段只声明类型、不声明必填，保持**纯新增**。

同理 `Sensor` 的 `frame_id` / `frame_convention` 也不进 `required`。

### 2.2 兼容性判定

无 oasdiff 环境（本机无 Go），因此用**结构 diff**代替：
对比 HEAD 与工作区的 `fragments/schemas.stage1.yaml`，结果是
新增顶层定义 1 个（`FrameConvention`）、新增属性 6 个、**删除 0 个**、
`required` 集合**无增减**、两处属性仅改动 `description` 文字。
主版本不变（`1.x`），没有 ERR 级破坏性变更。CI 的 oasdiff 会再确认一次。

## 3. 未纳入本次的内容

| 项 | 为什么不做 |
|---|---|
| G-01 Robot / CalibrationProfile 路由 | 功能新增，需要 PRD 与契约评审；不能"顺手加进契约" |
| G-03 Profile `version` → `profile_version` 改名 | 破坏性，留阶段 2；本次仍保留 `version` |
| 错误码可达性（`calibration_invalid` 等部分无触发路径） | 需要逐条核对错误映射与触发条件，属独立批次；本次只做字段完整性 |

## 4. 影响与回滚

- **纯增量**：新增字段与枚举，不删不改既有字段语义。前端解析器同步新增
  `calibration_version` / `profile_*` 读取（严格校验，缺字段即报错）。
- **契约快照**：`apps/web/src/services/__fixtures__/deviceContract.snapshot.json`
  由真实后端重新生成，多出 4 个字段（两处：单条与列表）。
- **`deviceContract.test.ts`** 的字段集合断言同步更新——这个测试正是为了
  抓这类漂移而存在的，它先失败、后修复，属于预期行为。
- **回滚点**：`fragments/schemas.stage1.yaml` 与 `app/api/v1/schemas.py` 的
  改动均为新增行；回滚时注意前端解析器与快照需一并回滚，否则严格解析会报错。

## 5. 验收证据

| 项 | 结果 |
|---|---|
| Redocly lint | valid（4 warnings，均为既有已知项） |
| 结构 diff（HEAD → 工作区） | 新增 6 属性 / 删除 0 / required 无变化 |
| 后端真实 PostgreSQL 全量 | 951 passed / 0 skipped |
| 前端测试 | 333 passed / 19 skipped（含真实后端契约快照比对） |
| `make check` | 通过 |
