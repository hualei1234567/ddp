# 记忆索引（自动生成，勿手改）

> 本文件由 `python3 scripts/build_memory_index.py` 生成。
> 手改会被 `scripts/check_memory.py` 判为漂移并让 CI 失败。
> 新增/修改记忆请改卡片文件，然后重新生成本索引。

**总计 13 张卡片**：DRAFT 0 / ACTIVE 13 / SUPERSEDED 0 / DEPRECATED 0

其中 **ACTIVE 13 张**（只有 ACTIVE 可被当作约束引用）。

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

## SOP · 标准操作流程（1）

| ID | 标题 | 状态 | 复发 | tags |
|---|---|---|---|---|
| [MEM-SOP-001](sop/MEM-SOP-001-real-sample-admission.md) | 真实样本准入检查清单（GATE-0） | ACTIVE | 1 | `fixture`, `gate`, `golden-path`, `hdf5`, `sample`, `sha256`, `sop` |

## INC · 事故与缺陷复盘（7）

| ID | 标题 | 状态 | 复发 | tags |
|---|---|---|---|---|
| [MEM-INC-001](incidents/MEM-INC-001-update-branch-missing-integrity-error.md) | 仓储 UPDATE 分支漏捕获 IntegrityError 导致无限重试 | ACTIVE | 1 | `integrity-error`, `outbox`, `raw_asset`, `retry`, `savepoint`, `unique-index`, `worker` |
| [MEM-INC-002](incidents/MEM-INC-002-rpo-anchor-nonexistent-column.md) | RPO 锚点引用不存在的列，备份校验形同虚设 | ACTIVE | 1 | `backup-restore`, `column-name`, `drill`, `rpo`, `silent-failure`, `sql` |
| [MEM-INC-003](incidents/MEM-INC-003-outbox-dispatch-status-constraint.md) | 只改 status 不改 dispatch，撞 outbox 一致性约束 | ACTIVE | 1 | `check-constraint`, `dispatch`, `migration`, `outbox`, `postgres`, `status` |
| [MEM-INC-004](incidents/MEM-INC-004-synthetic-container-as-real-sample.md) | 合成容器头被当成真实样本 | ACTIVE | 1 | `fixture`, `golden-path`, `hdf5`, `mcap`, `parquet`, `sample`, `sha256`, `synthetic` |
| [MEM-INC-005](incidents/MEM-INC-005-hardcoded-test-count-in-ci.md) | CI 里写死测试计数，新增用例就红 | ACTIVE | **2** | `assertion`, `ci`, `e2e`, `flaky`, `golden`, `magic-number` |
| [MEM-INC-006](incidents/MEM-INC-006-contract-constraint-no-producer.md) | 契约/DB 约束/代码三者脱节：UPLOADED_BY 边无生产者 | ACTIVE | 1 | `contract`, `db-constraint`, `dead-code`, `lineage`, `projection`, `session` |
| [MEM-INC-007](incidents/MEM-INC-007-lineage-limit-breach.md) | 血缘 limit 被投影边吃穿，返回条数超过上限 | ACTIVE | 1 | `budget`, `limit`, `lineage`, `pagination`, `projection`, `recursion` |

## CHG · 变更记录（1）

| ID | 标题 | 状态 | 复发 | tags |
|---|---|---|---|---|
| [MEM-CHG-001](changes/MEM-CHG-001-outbox-history-backfill.md) | 迁移 0072 归正 outbox 历史留痕行 | ACTIVE | 1 | `backfill`, `data-fix`, `migration`, `outbox`, `pending`, `recorded` |

## OPS · 运维参数与门禁（1）

| ID | 标题 | 状态 | 复发 | tags |
|---|---|---|---|---|
| [MEM-OPS-001](ops/MEM-OPS-001-backup-restore-invariants.md) | 备份恢复演练的 7 个不变量与 RPO/RTO 基线 | ACTIVE | 1 | `backup`, `drill`, `invariant`, `minio`, `postgres`, `restore`, `rpo`, `rto`, `spool` |

---

## 复发的坑（recurrence > 1）

> 复发说明「记住」没生效，应升级为更强的自动化约束。

| ID | 标题 | 复发 | 现有 check |
|---|---|---|---|
| [MEM-INC-005](incidents/MEM-INC-005-hardcoded-test-count-in-ci.md) | CI 里写死测试计数，新增用例就红 | **2** | `.github/workflows/ci.yml`（E2E job 的 passed/skipped 断言处）+ `backend/tests/unit/test_error_mapping_no_leak.py` |

---

## 非 ACTIVE（不可作为现行约束）

无。

---

## 按 tag 检索（跨类型）

- `adapter`：MEM-DEC-001
- `adr`：MEM-DEC-001
- `assertion`：MEM-INC-005
- `backfill`：MEM-CHG-001
- `backup`：MEM-OPS-001
- `backup-restore`：MEM-INC-002
- `budget`：MEM-INC-007
- `capability`：MEM-DEC-001
- `check-constraint`：MEM-INC-003
- `ci`：MEM-INC-005
- `claim-boundary`：MEM-REQ-001
- `column-name`：MEM-INC-002
- `contract`：MEM-INC-006
- `data-fix`：MEM-CHG-001
- `db-constraint`：MEM-INC-006
- `dead-code`：MEM-INC-006
- `dispatch`：MEM-INC-003
- `dpo`：MEM-DEC-002
- `drill`：MEM-INC-002, MEM-OPS-001
- `e2e`：MEM-INC-005
- `enum`：MEM-DEC-001
- `fixture`：MEM-INC-004, MEM-SOP-001
- `flaky`：MEM-INC-005
- `format`：MEM-DEC-001
- `frame-level`：MEM-REQ-001
- `gate`：MEM-SOP-001
- `golden`：MEM-INC-005
- `golden-path`：MEM-INC-004, MEM-SOP-001
- `hdf5`：MEM-INC-004, MEM-SOP-001
- `integrity-error`：MEM-INC-001
- `invariant`：MEM-OPS-001
- `json-label`：MEM-DEC-002
- `lerobot`：MEM-REQ-001
- `level`：MEM-DEC-001
- `limit`：MEM-INC-007
- `lineage`：MEM-INC-006, MEM-INC-007
- `magic-number`：MEM-INC-005
- `mcap`：MEM-INC-004
- `metadata-only`：MEM-REQ-001
- `migration`：MEM-CHG-001, MEM-INC-003
- `minio`：MEM-OPS-001
- `outbox`：MEM-CHG-001, MEM-INC-001, MEM-INC-003
- `pagination`：MEM-INC-007
- `pairwise`：MEM-DEC-002
- `parquet`：MEM-INC-004
- `pending`：MEM-CHG-001
- `position-bias`：MEM-DEC-002
- `postgres`：MEM-INC-003, MEM-OPS-001
- `preference`：MEM-DEC-002
- `projection`：MEM-INC-006, MEM-INC-007
- `raw_asset`：MEM-INC-001
- `recorded`：MEM-CHG-001
- `recursion`：MEM-INC-007
- `restore`：MEM-OPS-001
- `retry`：MEM-INC-001
- `rpo`：MEM-INC-002, MEM-OPS-001
- `rto`：MEM-OPS-001
- `sample`：MEM-INC-004, MEM-SOP-001
- `savepoint`：MEM-INC-001
- `schema`：MEM-DEC-002
- `session`：MEM-INC-006
- `sha256`：MEM-INC-004, MEM-SOP-001
- `silent-failure`：MEM-INC-002
- `sop`：MEM-SOP-001
- `spool`：MEM-OPS-001
- `sql`：MEM-INC-002
- `status`：MEM-INC-003, MEM-REQ-001
- `synthetic`：MEM-INC-004
- `training-ready`：MEM-REQ-001
- `unique-index`：MEM-INC-001
- `worker`：MEM-INC-001
