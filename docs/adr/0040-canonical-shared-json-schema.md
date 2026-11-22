# ADR-0040 Canonical 共享 JSON Schema 与写入版本白名单

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-22
- 决策者：System Architect + 后端；批准人：用户（长期授权）
- 任务：DDP-V31-017（`docs/roadmap/v3.1/n0/task-dag.md`）
- 关联：ADR-0035（Canonical Schema v1 只读契约）、ADR-0038（Episode 可训练口径与
  Canonical 版本收敛 §2.5 / §2.6 / §6 步骤 2）、ADR-0014（复用优先）
- 影响范围：`packages/contracts/schemas/canonical-v1/`（新增生成产物）、
  `data-platform/ddp_data/canonical/json_schema.py`（新增生成器）、
  `scripts/export_canonical_schemas.py`（新增导出脚本）、
  `backend/app/application/episodes/canonical_version.py`（新增写入校验）、
  `backend/app/application/episodes/episodes.py`（创建用例加白名单）。
  **不改数据库、不改 OpenAPI、不迁移历史数据、不新增依赖。**

## 1. 背景

ADR-0035 把 Canonical v1 冻结为只读 dataclass 契约，并在 §9 备选方案 4 明确
"用 Pydantic / JSON Schema 生成模型：**暂缓**……若未来需要跨语言 Schema，再评估
`packages/contracts` 侧生成"。当时不做的理由是没有消费者。V31-017 把这个延后项
转成待办，触发条件是两条：

1. **跨语言消费者出现了**。Dataset 导出（V31-041 Canonical 导出、V31-042 LeRobot
   升级）与未来的训练侧 Loader 不在 Python 进程内读 dataclass；它们需要一个
   与语言无关、可单独校验的契约。
2. **写入入口没有版本门禁**。ADR-0038 §2.6 登记了这一结论但未实现：
   `EpisodeCreate.canonical_schema_version`（`api/v1/episode_schemas.py:55`）是
   自由字符串，`Episode` 领域层只校验 `min_length=1 / max_length=64`
   （`domain/episodes/models.py:222-231`），数据库列无 CHECK 约束。
   在 24 个测试夹具里 `ddp.canonical/1.0.0-draft` 仍在被直接写库。

这两条合起来意味着：**没有共享 Schema 时，写入一个不支持的版本不会立刻报错，
要等到下游消费者读到它才失败**——那时数据已经落库、被引用。

## 2. 候选方案

| 方案 | 做法 | 问题 |
|---|---|---|
| A. 手写 `packages/contracts/schemas/canonical-v1/*.json` | 6 个实体、近百个字段逐个手抄 | 必然与 `FieldSpec` 分叉；改一个字段要改两处，没有任何机制能发现漏改 |
| B. 从 `FieldSpec` 生成，产物提交入库，测试锁"产物 == 生成结果" | 生成器 + 导出脚本 + 一致性测试 | 生成器本身要写对类型映射；产物是派生物，必须防止手改 |
| C. 运行时用 `dataclass` 反射生成 schema，不落文件 | 消费者每次现算 | 跨语言消费者拿不到；契约审查看不到 diff；无版本快照可回溯 |
| D. 直接用 Pydantic 模型替换 dataclass | 一份模型两用（校验 + schema） | 与 ADR-0035 决策冲突（dataclass + 显式校验已受测）；`data-platform` 不引入 Pydantic 依赖 |

## 3. 决策

**选方案 B。**

1. **字段声明仍是唯一权威**。`*_FIELDS`（`v1.py`）不变，生成器只做机械翻译：
   `kind` → JSON 类型、`*_ns` → 十进制字符串、可选 → 可空、
   `missing_fields` → 可选字段名枚举。语义（跨字段约束）仍只在 `v1.py`。
2. **产物入库，但由测试锁死**。`test_committed_schema_matches_generator` 做
   逐字节比对，手改生成文件立刻失败；`check.sh` 与 CI 跑
   `export_canonical_schemas.py --check`。
3. **schema 不表达跨字段约束**。`Frame` 至少一个时间戳、`Action.end >= start`、
   `is_dropped` 与 `drop_reason` 一致、`SensorStream.frame_convention` 枚举——
   这些留在 `__post_init__`。硬塞 `if/then` 会让 schema 不可读，且形成
   "领域一份、schema 一份" 的两份真相。README 与每个 schema 的 `description`
   都写明这条边界。
4. **写入白名单位于应用用例，不引领域 → 数据包依赖**。
   `ensure_writable_canonical_schema_version()` 放在
   `application/episodes/canonical_version.py`，直接引用
   `ddp_data.canonical.v1.SUPPORTED_SCHEMA_VERSIONS`。application 层已依赖
   `ddp_data`（`episode_materialization.py:47` 等多处有先例），domain 层没有——
   保持这个方向，不在领域层新增依赖。
5. **历史 `-draft` 不迁移**。按 ADR-0038 §2.4，`episodes.canonical_schema_version`
   记录的是溯源事实；读取路径不校验版本，只有**新写入**被白名单拦住。

## 4. 为什么不在 schema 里表达跨字段约束

`Frame` 的"至少一个时间戳"用 JSON Schema 表达需要：

```json
{"if": {"not": {"anyOf": [{"required": ["timestamp_ns"]}, {"required": ["source_time_ns"]}]}},
 "then": {"not": {}}}
```

这既不能表达"至少一个非 null"，也让 6 个实体各带一段不可读的 `if/then`。
更重要的是：领域层已经在 `__post_init__` 里做同样的事并有测试覆盖
（`test_frame_requires_at_least_one_timestamp` 等）。再写一遍等于制造
"两处都可能改错"的机会。

**schema 的职责是"字段存在性、类型、可空性"**，不是重放业务规则。

## 5. 实现拆分

| 步骤 | 内容 | 状态 |
|---|---|---|
| 1 | 生成器 `ddp_data/canonical/json_schema.py` | 本 ADR 一并落地 |
| 2 | 导出脚本 `scripts/export_canonical_schemas.py`（含 `--check`） | 本 ADR 一并落地 |
| 3 | 6 个 schema 产物 `packages/contracts/schemas/canonical-v1/` | 本 ADR 一并落地 |
| 4 | 一致性测试 `tests/unit/test_canonical_json_schema.py`（33 条） | 本 ADR 一并落地 |
| 5 | 写入白名单 + 用例接线 + API 测试 | 本 ADR 一并落地 |
| 6 | `check.sh` / CI 门禁 | 本 ADR 一并落地 |
| 7 | Canonical 实体持久化 / 导出策略 | V31-041、V31-042 |

## 6. 后果

### 6.1 正面

- 跨语言消费者拿到与领域校验同源的 schema，不必读 Python。
- `FieldSpec` 改动会自动要求重新生成；忘记生成 → CI 红。
- 写入入口第一次真正拦住未知版本，ADR-0038 §2.6 的结论从"登记"变成"执行"。

### 6.2 代价与残余风险

| 残余风险 | 等级 | 处置 |
|---|---|---|
| 生成器类型映射写错（例如把 `*_ns` 生成为整数） | 中 | `test_domain_output_passes_schema` 用真实 `to_dict` 输出跑校验，把类型错误变成失败 |
| 直接写库绕过白名单（脚本 / 手工 SQL / 未来 Worker） | 中 | 与 ADR-0038 §2.1 同一条：数据库无 CHECK；当前靠应用入口 + 读取侧不信任版本 |
| 历史 `-draft` 行仍存在 | 低 | 有意保留（溯源事实）；读取路径不校验，消费者按 `SUPPORTED_SCHEMA_VERSIONS` 自行判断 |
| schema 与领域规则分叉（有人只改 schema） | 低 | 逐字节比对测试；schema 文件头 `description` 注明"生成产物，勿手改" |

### 6.3 不做的

- 不把跨字段约束搬进 schema。
- 不迁移历史 `-draft` 数据。
- 不改 OpenAPI（Canonical 不是 HTTP 契约）。
- 不引入 `pydantic` 到 `data-platform`。

## 7. 回滚

删除 `packages/contracts/schemas/canonical-v1/`、生成器、导出脚本与一致性测试，
并移除 `episodes.py` 中的白名单调用即可。数据与既有读取路径不受影响；
历史 `-draft` 行本来就没被改写。

## 8. 验证

```bash
cd backend && .venv/bin/python -m pytest -q \
  tests/unit/test_canonical_json_schema.py \
  tests/unit/test_canonical_v1.py \
  tests/unit/test_canonical_v1_projection.py \
  tests/api/test_episodes_api.py
python3 scripts/export_canonical_schemas.py --check
```
