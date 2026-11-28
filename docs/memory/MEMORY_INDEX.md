# 记忆索引（自动生成，勿手改）

> 本文件由 `python3 scripts/build_memory_index.py` 生成。
> 手改会被 `scripts/check_memory.py` 判为漂移并让 CI 失败。
> 新增/修改记忆请改卡片文件，然后重新生成本索引。

**总计 45 张卡片**：DRAFT 0 / ACTIVE 45 / SUPERSEDED 0 / DEPRECATED 0

其中 **ACTIVE 45 张**（只有 ACTIVE 可被当作约束引用）。

---

## REQ · 需求与验收口径（1）

| ID | 标题 | 状态 | 复发 | tags |
|---|---|---|---|---|
| [MEM-REQ-001](requirements/MEM-REQ-001-metadata-only-not-training-ready.md) | METADATA_ONLY 不等于训练就绪，状态必须分层 | ACTIVE | 1 | `claim-boundary`, `frame-level`, `lerobot`, `metadata-only`, `status`, `training-ready` |

## DEC · 决策（2）

| ID | 标题 | 状态 | 复发 | tags |
|---|---|---|---|---|
| [MEM-DEC-001](decisions/MEM-DEC-001-adapter-capability-levels.md) | Adapter 能力分级用独立枚举，不改现有五值枚举 | ACTIVE | 1 | `adapter`, `adr`, `capability`, `enum`, `format`, `level` |
| [MEM-DEC-002](decisions/MEM-DEC-002-preference-pair-as-json-value.md) | 偏好对比标注用 JSON 约定值而非新增标签类型 | ACTIVE | 1 | `dpo`, `json-label`, `pairwise`, `position-bias`, `preference`, `schema` |

## SOP · 标准操作流程（4）

| ID | 标题 | 状态 | 复发 | tags |
|---|---|---|---|---|
| [MEM-SOP-001](sop/MEM-SOP-001-real-sample-admission.md) | 真实样本准入检查清单（GATE-0） | ACTIVE | 1 | `fixture`, `gate`, `golden-path`, `hdf5`, `sample`, `sha256`, `sop` |
| [MEM-SOP-002](sop/MEM-SOP-002-parallel-agent-development.md) | 并行智能体开发 SOP：worktree 隔离 + 资源预分配 + check.sh 串行 | ACTIVE | 1 | `check-lock`, `migration-number`, `parallel`, `resource-preallocation`, `sop`, `version-bump`, `worktree` |
| [MEM-SOP-003](sop/MEM-SOP-003-subagent-parallel-collaboration.md) | 子代理并行开发协作模式：迁移号独占 + 契约策略 + 禁改共享文件 + 坑点统一入库 + check.sh 全局串行 | ACTIVE | 1 | `check-lock`, `contract-strategy`, `migration-number`, `parallel`, `resource-preallocation`, `sop`, `subagent` |
| [MEM-SOP-004](sop/MEM-SOP-004-semgrep-sast-suppression.md) | Semgrep SAST 接入与误报基线的正确姿势 | ACTIVE | 1 | `baseline`, `ci`, `false-positive`, `nosemgrep`, `sast`, `semgrep`, `sql-injection`, `ssrf` |

## INC · 事故与缺陷复盘（32）

| ID | 标题 | 状态 | 复发 | tags |
|---|---|---|---|---|
| [MEM-INC-001](incidents/MEM-INC-001-update-branch-missing-integrity-error.md) | 仓储 UPDATE 分支漏捕获 IntegrityError 导致无限重试 | ACTIVE | 1 | `integrity-error`, `outbox`, `raw_asset`, `retry`, `savepoint`, `unique-index`, `worker` |
| [MEM-INC-002](incidents/MEM-INC-002-rpo-anchor-nonexistent-column.md) | RPO 锚点引用不存在的列，备份校验形同虚设 | ACTIVE | 1 | `backup-restore`, `column-name`, `drill`, `rpo`, `silent-failure`, `sql` |
| [MEM-INC-003](incidents/MEM-INC-003-outbox-dispatch-status-constraint.md) | 只改 status 不改 dispatch，撞 outbox 一致性约束 | ACTIVE | 1 | `check-constraint`, `dispatch`, `migration`, `outbox`, `postgres`, `status` |
| [MEM-INC-004](incidents/MEM-INC-004-synthetic-container-as-real-sample.md) | 合成容器头被当成真实样本 | ACTIVE | 1 | `fixture`, `golden-path`, `hdf5`, `mcap`, `parquet`, `sample`, `sha256`, `synthetic` |
| [MEM-INC-005](incidents/MEM-INC-005-hardcoded-test-count-in-ci.md) | CI 里写死测试计数，新增用例就红 | ACTIVE | **2** | `assertion`, `ci`, `e2e`, `flaky`, `golden`, `magic-number` |
| [MEM-INC-006](incidents/MEM-INC-006-contract-constraint-no-producer.md) | 契约/DB 约束/代码三者脱节：UPLOADED_BY 边无生产者 | ACTIVE | 1 | `contract`, `db-constraint`, `dead-code`, `lineage`, `projection`, `session` |
| [MEM-INC-007](incidents/MEM-INC-007-lineage-limit-breach.md) | 血缘 limit 被投影边吃穿，返回条数超过上限 | ACTIVE | 1 | `budget`, `limit`, `lineage`, `pagination`, `projection`, `recursion` |
| [MEM-INC-008](incidents/MEM-INC-008-nanosecond-precision-in-json.md) | 纳秒时间戳经 JSON 传输会静默丢精度 | ACTIVE | 1 | `2^53`, `bigint`, `contract`, `json`, `nanosecond`, `precision`, `timestamp` |
| [MEM-INC-009](incidents/MEM-INC-009-parallel-check-contention.md) | 多个 check.sh 并发跑同一 PostgreSQL 会互相污染 | ACTIVE | **3** | `flaky`, `minio`, `parallel`, `postgres`, `test-contention`, `time-sensitive`, `worktree` |
| [MEM-INC-010](incidents/MEM-INC-010-payload-length-vs-shape.md) | 区分「实际读取行数」与「数据集总行数」 | ACTIVE | 1 | `chunked-read`, `data-fabrication`, `hdf5`, `off-by-n`, `shape`, `slice` |
| [MEM-INC-011](incidents/MEM-INC-011-pipeline-version-conflict.md) | 绕过用例直接建 Run 会导致 Pipeline 版本冲突 | ACTIVE | 1 | `boundary`, `bypass`, `optimistic-lock`, `pipeline`, `processing-run`, `version-conflict` |
| [MEM-INC-012](incidents/MEM-INC-012-enum-multi-table-check-constraints.md) | 新增阶段与产物枚举值必须同步全部相关表的 CHECK 约束 | ACTIVE | 1 | `check-constraint`, `enum`, `migration`, `postgres`, `processing-run`, `stage`, `stream-extraction` |
| [MEM-INC-013](incidents/MEM-INC-013-alembic-pg-sqlite-constraint-pitfalls.md) | PostgreSQL 与 SQLite 双库迁移的 Schema 限定、同名约束与冒号绑定陷阱 | ACTIVE | 1 | `alembic`, `check-constraint`, `dialect`, `migration`, `postgres`, `schema`, `sqlalchemy`, `sqlite` |
| [MEM-INC-014](incidents/MEM-INC-014-canonical-per-entity-version-slicing.md) | Canonical 多版本演进必须按「实体 × 版本」双向裁剪字段表并重导出 Schema | ACTIVE | 1 | `backward-compatibility`, `canonical`, `deserialization`, `json-schema`, `schema-evolution`, `serialization`, `v1.1` |
| [MEM-INC-015](incidents/MEM-INC-015-contract-version-parallel-bump-conflict.md) | 并行 PR 各自 bump 契约版本必然冲突，version 是共享单点 | ACTIVE | 1 | `api-contract`, `merge-conflict`, `openapi`, `parallel-pr`, `resource-preallocation`, `single-point`, `version-bump` |
| [MEM-INC-016](incidents/MEM-INC-016-contract-parity-strict-red.md) | 契约门禁强制 parity：契约超前于 API 实现会被 `--strict` 判红，契约与实现必须同批交付 | ACTIVE | **2** | `api-contract`, `ci-gate`, `contract-first`, `parity`, `strict-mode` |
| [MEM-INC-017](incidents/MEM-INC-017-redocly-path-parameter-traps.md) | Redocly 路径参数三坑：动态段必须显式声明、组件必须落进 parameters: 段、fragment 内 $ref 相对解析 | ACTIVE | 1 | `api-contract`, `openapi`, `redocly`, `ref-resolution`, `swagger-lint`, `yaml` |
| [MEM-INC-018](incidents/MEM-INC-018-parallel-pr-migration-chain-conflict.md) | 多 PR 同 base 迁移链冲突：三个 PR 的 down_revision 都写同一个号 → 双 head，处置=串行合并+重排 | ACTIVE | 1 | `alembic`, `down-revision`, `merge-conflict`, `migration-chain`, `multiple-heads`, `parallel-pr`, `sqlite` |
| [MEM-INC-019](incidents/MEM-INC-019-sqlalchemy-duplicate-model-class-name.md) | SQLAlchemy 模型类名全局唯一：pytest filterwarnings=["error"] 把重复类名告警升级为硬错误 | ACTIVE | 1 | `alembic`, `filterwarnings`, `model-naming`, `pytest`, `sawarning`, `sqlalchemy` |
| [MEM-INC-020](incidents/MEM-INC-020-sqlite-batch-cross-table-trigger.md) | SQLite batch 重建撞跨表触发器：被引用表 batch 改名瞬间触发器报 no such table，处置=drop→改表→重装 | ACTIVE | **2** | `0041`, `0052`, `alembic`, `batch-alter-table`, `guard-triggers`, `sqlite`, `trigger-rename` |
| [MEM-INC-021](incidents/MEM-INC-021-jsonb-readback-and-decimal-money.md) | JSONB 回读类型不宽容 + Decimal 金额纪律：JSON 字段读回是 ISO 字符串、金额全程 Decimal 量化 4 位小数对外输出字符串 | ACTIVE | **2** | `datetime`, `decimal`, `jsonb`, `precision`, `serialization`, `type-coercion` |
| [MEM-INC-022](incidents/MEM-INC-022-sqlite-naive-datetime-readback.md) | SQLite 读库 datetime 是 naive，领域层要求 aware——在仓储边界补 UTC，不放宽领域校验 | ACTIVE | **2** | `domain-invariant`, `naive-datetime`, `repository-boundary`, `sqlite`, `timezone` |
| [MEM-INC-023](incidents/MEM-INC-023-unique-key-rejection-rows.md) | 唯一键表的"拒绝类判决"不能落库——重复跳过/限流写入会撞自身唯一键 | ACTIVE | 1 | `409`, `idempotency`, `unique-constraint`, `webhook`, `write-amplification` |
| [MEM-INC-024](incidents/MEM-INC-024-nplus1-query-count-guard.md) | 列表 N+1 用"查询次数不随页大小变化"的断言守护，而不是靠压测 | ACTIVE | 1 | `n-plus-1`, `performance-regression`, `query-count`, `sqlalchemy-events` |
| [MEM-INC-025](incidents/MEM-INC-025-lock-platform-marker-greenlet.md) | macOS 上生成的 pip lock 在 Linux CI 因 greenlet 平台标记直接红 | ACTIVE | 1 | `ci`, `dependency-lock`, `greenlet`, `hash-checking`, `pip-compile`, `platform-marker`, `sbom` |
| [MEM-INC-026](incidents/MEM-INC-026-sqlalchemy-flush-ordering-fk.md) | SQLAlchemy 批量插入时父行必须先显式 flush——依赖排序不可靠，子行 FK 检查会失败 | ACTIVE | 1 | `composite-fk`, `flush-ordering`, `foreign-key`, `sqlalchemy`, `test-seeding` |
| [MEM-INC-027](incidents/MEM-INC-027-webhook-outbound-wiring-and-ssrf.md) | 「有能力无接线」是比「缺能力」更隐蔽的缺口——Webhook 投递用例曾无任何生产调用点 | ACTIVE | 1 | `audit-gap`, `capability-wiring`, `dead-code`, `idempotency`, `outbox`, `webhook` |
| [MEM-INC-028](incidents/MEM-INC-028-webhook-ssrf-guard-local-exception.md) | 出站 URL 守卫默认拒绝回环地址——本地联调必须走独立开关，不能削弱守卫 | ACTIVE | 1 | `least-privilege`, `local-dev-exception`, `outbound`, `ssrf`, `url-guard` |
| [MEM-INC-029](incidents/MEM-INC-029-http-header-case-and-sqlite-naive-datetime.md) | 断言 Webhook 投递时踩到的两个隐性事实：HTTP 头名大小写不敏感、SQLite 读回 datetime 无时区 | ACTIVE | 1 | `e2e-test`, `http-header-case`, `naive-datetime`, `seed-data`, `sqlite`, `webhook` |
| [MEM-INC-030](incidents/MEM-INC-030-ci-security-job-minimal-deps.md) | CI 的 security job 只装 pytest/pip-audit/pyyaml——`scripts/` 下测试文件不得顶层 import 数据库驱动 | ACTIVE | 1 | `ci-environment`, `lazy-import`, `local-green-vs-ci-green`, `minimal-deps`, `scripts-tests` |
| [MEM-INC-031](incidents/MEM-INC-031-edge-agent-upload-version-drift.md) | Edge Agent 分片完成未刷新 UploadSession.version 必撞 409 且 403 预签名过期未重签 | ACTIVE | 1 | `403`, `409`, `edge-agent`, `fake-drift`, `if-match`, `optimistic-lock`, `presigned-url`, `version-conflict` |
| [MEM-INC-032](incidents/MEM-INC-032-sys-modules-app-namespace-collision.md) | 同进程脚本测试加载 services/alert-receiver/app 后未还原 sys.modules["app"] 导致 backend/app 导入失败 | ACTIVE | 1 | `alert-receiver`, `app-package`, `namespace-collision`, `pytest`, `scripts-tests`, `sys-modules` |

## CHG · 变更记录（4）

| ID | 标题 | 状态 | 复发 | tags |
|---|---|---|---|---|
| [MEM-CHG-001](changes/MEM-CHG-001-outbox-history-backfill.md) | 迁移 0072 归正 outbox 历史留痕行 | ACTIVE | 1 | `backfill`, `data-fix`, `migration`, `outbox`, `pending`, `recorded` |
| [MEM-CHG-002](changes/MEM-CHG-002-lerobot-env-state-key.md) | LeRobot 导出必须同时写 `observation.environment_state`——官方策略只认 ENV 键，只写 `observation.state` 会拒绝训练 | ACTIVE | 1 | `env-state`, `export`, `feature-naming`, `lerobot`, `training-gate`, `v21` |
| [MEM-CHG-003](changes/MEM-CHG-003-lerobot-nested-vector-columns.md) | 官方 LeRobot 策略消费的是**嵌套向量列**，只有扁平 `observation.state[0..N]` 列会 KeyError | ACTIVE | 1 | `act-policy`, `lerobot`, `nested-columns`, `parquet`, `training-gate` |
| [MEM-CHG-004](changes/MEM-CHG-004-lerobot-stats-count-shape.md) | LeRobot stats 的 `count` 必须是 shape `(1,)`——0 维标量与错误序列化都会被 aggregate_stats 拒绝 | ACTIVE | 1 | `aggregate-stats`, `count-shape`, `jsonl`, `lerobot`, `stats`, `training-gate` |

## OPS · 运维参数与门禁（2）

| ID | 标题 | 状态 | 复发 | tags |
|---|---|---|---|---|
| [MEM-OPS-001](ops/MEM-OPS-001-backup-restore-invariants.md) | 备份恢复演练的 7 个不变量与 RPO/RTO 基线 | ACTIVE | 1 | `backup`, `drill`, `invariant`, `minio`, `postgres`, `restore`, `rpo`, `rto`, `spool` |
| [MEM-OPS-002](ops/MEM-OPS-002-table-growth-partition-thresholds.md) | 小表当前不需要分区；n_live_tup 因 PG 容器重启而不可信 | ACTIVE | 1 | `archive`, `audit_logs`, `container_restart`, `maintenance`, `outbox`, `partitioning`, `pg_stat`, `postgres`, `readiness`, `retention`, `table_growth`, `threshold` |

---

## 复发的坑（recurrence > 1）

> 复发说明「记住」没生效，应升级为更强的自动化约束。

| ID | 标题 | 复发 | 现有 check |
|---|---|---|---|
| [MEM-INC-009](incidents/MEM-INC-009-parallel-check-contention.md) | 多个 check.sh 并发跑同一 PostgreSQL 会互相污染 | **3** | `无（流程性约束，无法自动化断言；靠并发执行策略规避）` |
| [MEM-INC-005](incidents/MEM-INC-005-hardcoded-test-count-in-ci.md) | CI 里写死测试计数，新增用例就红 | **2** | `.github/workflows/ci.yml`（E2E job 的 passed/skipped 断言处）+ `backend/tests/unit/test_error_mapping_no_leak.py` |
| [MEM-INC-016](incidents/MEM-INC-016-contract-parity-strict-red.md) | 契约门禁强制 parity：契约超前于 API 实现会被 `--strict` 判红，契约与实现必须同批交付 | **2** | `scripts/check_contract_parity.py` |
| [MEM-INC-020](incidents/MEM-INC-020-sqlite-batch-cross-table-trigger.md) | SQLite batch 重建撞跨表触发器：被引用表 batch 改名瞬间触发器报 no such table，处置=drop→改表→重装 | **2** | `backend/migrations/versions/0041_collection_asset_state_guards.py + backend/migrations/versions/0052_calibration_profile_supersedes_and_guards.py` |
| [MEM-INC-021](incidents/MEM-INC-021-jsonb-readback-and-decimal-money.md) | JSONB 回读类型不宽容 + Decimal 金额纪律：JSON 字段读回是 ISO 字符串、金额全程 Decimal 量化 4 位小数对外输出字符串 | **2** | `backend/app/domain/annotation/prm_step.py` |
| [MEM-INC-022](incidents/MEM-INC-022-sqlite-naive-datetime-readback.md) | SQLite 读库 datetime 是 naive，领域层要求 aware——在仓储边界补 UTC，不放宽领域校验 | **2** | `backend/app/infrastructure/repositories/annotation_backflow.py` |

---

## 非 ACTIVE（不可作为现行约束）

无。

---

## 按 tag 检索（跨类型）

- `0041`：MEM-INC-020
- `0052`：MEM-INC-020
- `2^53`：MEM-INC-008
- `403`：MEM-INC-031
- `409`：MEM-INC-023, MEM-INC-031
- `act-policy`：MEM-CHG-003
- `adapter`：MEM-DEC-001
- `adr`：MEM-DEC-001
- `aggregate-stats`：MEM-CHG-004
- `alembic`：MEM-INC-013, MEM-INC-018, MEM-INC-019, MEM-INC-020
- `alert-receiver`：MEM-INC-032
- `api-contract`：MEM-INC-015, MEM-INC-016, MEM-INC-017
- `app-package`：MEM-INC-032
- `archive`：MEM-OPS-002
- `assertion`：MEM-INC-005
- `audit-gap`：MEM-INC-027
- `audit_logs`：MEM-OPS-002
- `backfill`：MEM-CHG-001
- `backup`：MEM-OPS-001
- `backup-restore`：MEM-INC-002
- `backward-compatibility`：MEM-INC-014
- `baseline`：MEM-SOP-004
- `batch-alter-table`：MEM-INC-020
- `bigint`：MEM-INC-008
- `boundary`：MEM-INC-011
- `budget`：MEM-INC-007
- `bypass`：MEM-INC-011
- `canonical`：MEM-INC-014
- `capability`：MEM-DEC-001
- `capability-wiring`：MEM-INC-027
- `check-constraint`：MEM-INC-003, MEM-INC-012, MEM-INC-013
- `check-lock`：MEM-SOP-002, MEM-SOP-003
- `chunked-read`：MEM-INC-010
- `ci`：MEM-INC-005, MEM-INC-025, MEM-SOP-004
- `ci-environment`：MEM-INC-030
- `ci-gate`：MEM-INC-016
- `claim-boundary`：MEM-REQ-001
- `column-name`：MEM-INC-002
- `composite-fk`：MEM-INC-026
- `container_restart`：MEM-OPS-002
- `contract`：MEM-INC-006, MEM-INC-008
- `contract-first`：MEM-INC-016
- `contract-strategy`：MEM-SOP-003
- `count-shape`：MEM-CHG-004
- `data-fabrication`：MEM-INC-010
- `data-fix`：MEM-CHG-001
- `datetime`：MEM-INC-021
- `db-constraint`：MEM-INC-006
- `dead-code`：MEM-INC-006, MEM-INC-027
- `decimal`：MEM-INC-021
- `dependency-lock`：MEM-INC-025
- `deserialization`：MEM-INC-014
- `dialect`：MEM-INC-013
- `dispatch`：MEM-INC-003
- `domain-invariant`：MEM-INC-022
- `down-revision`：MEM-INC-018
- `dpo`：MEM-DEC-002
- `drill`：MEM-INC-002, MEM-OPS-001
- `e2e`：MEM-INC-005
- `e2e-test`：MEM-INC-029
- `edge-agent`：MEM-INC-031
- `enum`：MEM-DEC-001, MEM-INC-012
- `env-state`：MEM-CHG-002
- `export`：MEM-CHG-002
- `fake-drift`：MEM-INC-031
- `false-positive`：MEM-SOP-004
- `feature-naming`：MEM-CHG-002
- `filterwarnings`：MEM-INC-019
- `fixture`：MEM-INC-004, MEM-SOP-001
- `flaky`：MEM-INC-005, MEM-INC-009
- `flush-ordering`：MEM-INC-026
- `foreign-key`：MEM-INC-026
- `format`：MEM-DEC-001
- `frame-level`：MEM-REQ-001
- `gate`：MEM-SOP-001
- `golden`：MEM-INC-005
- `golden-path`：MEM-INC-004, MEM-SOP-001
- `greenlet`：MEM-INC-025
- `guard-triggers`：MEM-INC-020
- `hash-checking`：MEM-INC-025
- `hdf5`：MEM-INC-004, MEM-INC-010, MEM-SOP-001
- `http-header-case`：MEM-INC-029
- `idempotency`：MEM-INC-023, MEM-INC-027
- `if-match`：MEM-INC-031
- `integrity-error`：MEM-INC-001
- `invariant`：MEM-OPS-001
- `json`：MEM-INC-008
- `json-label`：MEM-DEC-002
- `json-schema`：MEM-INC-014
- `jsonb`：MEM-INC-021
- `jsonl`：MEM-CHG-004
- `lazy-import`：MEM-INC-030
- `least-privilege`：MEM-INC-028
- `lerobot`：MEM-CHG-002, MEM-CHG-003, MEM-CHG-004, MEM-REQ-001
- `level`：MEM-DEC-001
- `limit`：MEM-INC-007
- `lineage`：MEM-INC-006, MEM-INC-007
- `local-dev-exception`：MEM-INC-028
- `local-green-vs-ci-green`：MEM-INC-030
- `magic-number`：MEM-INC-005
- `maintenance`：MEM-OPS-002
- `mcap`：MEM-INC-004
- `merge-conflict`：MEM-INC-015, MEM-INC-018
- `metadata-only`：MEM-REQ-001
- `migration`：MEM-CHG-001, MEM-INC-003, MEM-INC-012, MEM-INC-013
- `migration-chain`：MEM-INC-018
- `migration-number`：MEM-SOP-002, MEM-SOP-003
- `minimal-deps`：MEM-INC-030
- `minio`：MEM-INC-009, MEM-OPS-001
- `model-naming`：MEM-INC-019
- `multiple-heads`：MEM-INC-018
- `n-plus-1`：MEM-INC-024
- `naive-datetime`：MEM-INC-022, MEM-INC-029
- `namespace-collision`：MEM-INC-032
- `nanosecond`：MEM-INC-008
- `nested-columns`：MEM-CHG-003
- `nosemgrep`：MEM-SOP-004
- `off-by-n`：MEM-INC-010
- `openapi`：MEM-INC-015, MEM-INC-017
- `optimistic-lock`：MEM-INC-011, MEM-INC-031
- `outbound`：MEM-INC-028
- `outbox`：MEM-CHG-001, MEM-INC-001, MEM-INC-003, MEM-INC-027, MEM-OPS-002
- `pagination`：MEM-INC-007
- `pairwise`：MEM-DEC-002
- `parallel`：MEM-INC-009, MEM-SOP-002, MEM-SOP-003
- `parallel-pr`：MEM-INC-015, MEM-INC-018
- `parity`：MEM-INC-016
- `parquet`：MEM-CHG-003, MEM-INC-004
- `partitioning`：MEM-OPS-002
- `pending`：MEM-CHG-001
- `performance-regression`：MEM-INC-024
- `pg_stat`：MEM-OPS-002
- `pip-compile`：MEM-INC-025
- `pipeline`：MEM-INC-011
- `platform-marker`：MEM-INC-025
- `position-bias`：MEM-DEC-002
- `postgres`：MEM-INC-003, MEM-INC-009, MEM-INC-012, MEM-INC-013, MEM-OPS-001, MEM-OPS-002
- `precision`：MEM-INC-008, MEM-INC-021
- `preference`：MEM-DEC-002
- `presigned-url`：MEM-INC-031
- `processing-run`：MEM-INC-011, MEM-INC-012
- `projection`：MEM-INC-006, MEM-INC-007
- `pytest`：MEM-INC-019, MEM-INC-032
- `query-count`：MEM-INC-024
- `raw_asset`：MEM-INC-001
- `readiness`：MEM-OPS-002
- `recorded`：MEM-CHG-001
- `recursion`：MEM-INC-007
- `redocly`：MEM-INC-017
- `ref-resolution`：MEM-INC-017
- `repository-boundary`：MEM-INC-022
- `resource-preallocation`：MEM-INC-015, MEM-SOP-002, MEM-SOP-003
- `restore`：MEM-OPS-001
- `retention`：MEM-OPS-002
- `retry`：MEM-INC-001
- `rpo`：MEM-INC-002, MEM-OPS-001
- `rto`：MEM-OPS-001
- `sample`：MEM-INC-004, MEM-SOP-001
- `sast`：MEM-SOP-004
- `savepoint`：MEM-INC-001
- `sawarning`：MEM-INC-019
- `sbom`：MEM-INC-025
- `schema`：MEM-DEC-002, MEM-INC-013
- `schema-evolution`：MEM-INC-014
- `scripts-tests`：MEM-INC-030, MEM-INC-032
- `seed-data`：MEM-INC-029
- `semgrep`：MEM-SOP-004
- `serialization`：MEM-INC-014, MEM-INC-021
- `session`：MEM-INC-006
- `sha256`：MEM-INC-004, MEM-SOP-001
- `shape`：MEM-INC-010
- `silent-failure`：MEM-INC-002
- `single-point`：MEM-INC-015
- `slice`：MEM-INC-010
- `sop`：MEM-SOP-001, MEM-SOP-002, MEM-SOP-003
- `spool`：MEM-OPS-001
- `sql`：MEM-INC-002
- `sql-injection`：MEM-SOP-004
- `sqlalchemy`：MEM-INC-013, MEM-INC-019, MEM-INC-026
- `sqlalchemy-events`：MEM-INC-024
- `sqlite`：MEM-INC-013, MEM-INC-018, MEM-INC-020, MEM-INC-022, MEM-INC-029
- `ssrf`：MEM-INC-028, MEM-SOP-004
- `stage`：MEM-INC-012
- `stats`：MEM-CHG-004
- `status`：MEM-INC-003, MEM-REQ-001
- `stream-extraction`：MEM-INC-012
- `strict-mode`：MEM-INC-016
- `subagent`：MEM-SOP-003
- `swagger-lint`：MEM-INC-017
- `synthetic`：MEM-INC-004
- `sys-modules`：MEM-INC-032
- `table_growth`：MEM-OPS-002
- `test-contention`：MEM-INC-009
- `test-seeding`：MEM-INC-026
- `threshold`：MEM-OPS-002
- `time-sensitive`：MEM-INC-009
- `timestamp`：MEM-INC-008
- `timezone`：MEM-INC-022
- `training-gate`：MEM-CHG-002, MEM-CHG-003, MEM-CHG-004
- `training-ready`：MEM-REQ-001
- `trigger-rename`：MEM-INC-020
- `type-coercion`：MEM-INC-021
- `unique-constraint`：MEM-INC-023
- `unique-index`：MEM-INC-001
- `url-guard`：MEM-INC-028
- `v1.1`：MEM-INC-014
- `v21`：MEM-CHG-002
- `version-bump`：MEM-INC-015, MEM-SOP-002
- `version-conflict`：MEM-INC-011, MEM-INC-031
- `webhook`：MEM-INC-023, MEM-INC-027, MEM-INC-029
- `worker`：MEM-INC-001
- `worktree`：MEM-INC-009, MEM-SOP-002
- `write-amplification`：MEM-INC-023
- `yaml`：MEM-INC-017
