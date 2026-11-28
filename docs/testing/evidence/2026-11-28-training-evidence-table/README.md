# T3-03 `training_run_evidence` 训练证据表迁移证据

- 任务：DDP V6.1 / T3-03「迁移 0088 `training_run_evidence` 训练证据表」
- 分支/工作树：`feat/v61-evidence`（`/Users/hualei/vscodeworkspace/wt-v61-evidence`）
- 基线提交：`85f0823`
- 执行日期：2026-11-28
- 关联先例：迁移 0087（`video_frames`，建表 + CHECK + 索引 + 回滚防御）、
  GATE-B（`scripts/train/gate_b_train_smoke.py`，见
  [2026-11-28-gate-b-training](../2026-11-28-gate-b-training/README.md)）

## 1. 表结构要点

`training_run_evidence`（迁移 0088，`down_revision=0087`）：一次训练 run 的
过程证据。核心字段与约束：

| 列 | 类型 | 约束/说明 |
|---|---|---|
| `id` | uuid | 主键 |
| `project_id` | uuid | FK `projects.id` **CASCADE**（随项目清理） |
| `external_training_reference_id` | uuid | FK `external_training_references.id` **RESTRICT**（有证据行时拒绝删引用，防静默丢证据） |
| `dataset_version_id` | uuid | 可空 FK `dataset_versions.id`（环境自检类 smoke 可不绑版本） |
| `run_kind` | varchar(16) | CHECK ∈ `('SMOKE','REAL')` |
| `device` | varchar(64) | 训练设备（GATE-B 为 `cpu`） |
| `runtime_versions` | JSON/JSONB | 运行时版本留痕（torch/lerobot/python 等） |
| `config_sha256` | varchar(71) | CHECK `sha256:` 前缀格式 |
| `seed` | int | CHECK `>= 0` |
| `batch_size` / `steps` | int | `steps` CHECK `>= 1` |
| `started_at` / `finished_at` | timestamptz | `finished_at` 可空（失败/被中断的 run） |
| `metrics_summary` | JSON/JSONB 可空 | 失败 run 没有指标；NULL 表示"没有"，不是"零" |
| `checkpoint_ref` / `checkpoint_sha256` | 可空 | checkpoint 路径与哈希；有值时 sha256 必须带前缀 |
| `exit_code` | int | **开放域**：0 成功、非 0（含负数，信号杀死）失败，不设枚举 CHECK |
| `stdout_tail` | varchar(8192) 可空 | 失败 run 的 stdout 尾部留痕（有界，不存全量日志） |
| `created_at` / `created_by` | timestamptz / uuid | 审计；FK `users.id` |

索引：`ix_training_run_evidence_project_created_at`（项目分页/审计，
键集扫描）、`ix_training_run_evidence_reference_id`（按引用反查）。

双方言（MEM-INC-013）：建表 DDL 两侧同构，JSON 列用
`JSON().with_variant(JSONB, "postgresql")`（同 0086/0087 先例），无 ALTER。
回滚：收索引 → PostgreSQL 显式回收本表 CHECK 约束（0087 `_drop_all` 口径）→
drop table（外键/主键随表回收）。

## 2. 与 ADR-0036 的关系：补充而非推翻

ADR-0036（`docs/adr/0036-training-lineage-relation.md`）确立
`external_training_references` 是「**引用行即 run 记录**」的权威落点，外部
训练平台仍是 run 与状态的权威系统。本表**不改变**这一权威：

- **身份仍在引用行**：`training_run_evidence` 没有自己的 run 身份语义，每条
  证据必须挂 `external_training_reference_id`（非空 FK，RESTRICT 删除保护）；
- **补充过程证据**：引用行只承载引用与状态，没有 seed / checkpoint_hash /
  逐步 metrics。证据行把 GATE-B 这类训练过程的可验证事实落成正式记录；
- **一对多**：一个引用可有多条证据（smoke 多次重跑、smoke + real 并存），
  按 `created_at` 排序，最新在前；
- **不替代状态**：run 的成功/失败仍以引用行状态为权威，证据行的 `exit_code`
  是过程留痕，不驱动状态机。

## 3. GATE-B 证据如何落库

GATE-B（`scripts/train/gate_b_train_smoke.py`，真实 lerobot 0.3.3 训练入口，
CPU 2 步）产出的 `evidence.json` 与本表字段的映射：

| evidence.json | 表列 | 说明 |
|---|---|---|
| `training.device` / `training.steps` / `training.batch_size` / `training.seed` | `device` / `steps` / `batch_size` / `seed` | 直接映射 |
| `training.torch` / `training.python` / `training.platform` | `runtime_versions` | 运行时版本留痕（JSON） |
| `training.policy` 对应的训练配置摘要哈希 | `config_sha256` | 配置指纹，`sha256:` 前缀口径 |
| `dataset.root` / `dataset.obs_shape` / `dataset.act_shape` | `dataset_version_id` + `metrics_summary` | 数据集版本绑定；形状等指标进 `metrics_summary` |
| `checkpoint_reload.from` / `checkpoint_reload.model_safetensors_sha256` | `checkpoint_ref` / `checkpoint_sha256` | checkpoint 路径与哈希（本体不入库，只留路径与 sha256） |
| 训练退出码 | `exit_code` | 0 成功；非 0（含信号负码）失败 |
| `training.wall_seconds` 等运行指标 | `metrics_summary` | 逐步/汇总指标汇总 |

落库路径：GATE-B 跑通后，由训练提交侧在**同一事务**内通过
`SqlTrainingEvidenceRepository.add` 登记证据行（仓储不提交，随业务提交），
读取侧用 `list_for_project`（项目分页）与 `find_by_reference`（按引用反查）。

## 4. 交付物与验证

- 迁移：`backend/migrations/versions/0088_training_run_evidence.py`
- ORM：`backend/app/infrastructure/db/models/training_evidence.py`（已注册进
  `models/__init__.py` 的 import 与 `__all__`）
- 仓储：`backend/app/infrastructure/repositories/training_evidence.py`
  （`add` 不提交 / `list_for_project` 分页 / `find_by_reference`）
- 测试：
  - `backend/tests/migrations/test_training_evidence_migration.py`
    （建表/索引/CHECK/可空面/级联/RESTRICT/回滚往返；SQLite 实跑，
    PG 走 `DDP_TEST_POSTGRES_URL` 跳过模式，对齐
    `test_webhook_outbox_migration.py`）
  - `backend/tests/unit/test_training_evidence_repository.py`
    （add+list+find、项目隔离、NULL 读回、不提交语义）

验证命令（收尾写死）：`ruff check/format` + 两个测试文件 `pytest -q`，
另跑 `scripts/check_secrets.py` 与 `scripts/check_memory.py`，全部通过后提交。
