# DDP V2.1 N0-01 Current System Baseline（基线清单）

- 采集日期：2026-11-18
- 采集方式：只读命令 + 代码检视；所有数字均可按 §10 复现
- 事实基线提交：**`173d594`**（P2 批次合并）；P3 批次集成中（0058→0061）
- 上游文档：`docs/05-现状审计报告-2026-11-14.md`（含 §12 闭环与勘误）、
  `docs/roadmap/next-stage-v2/audit/N0-N9-项目最终报告-2026-11-17.md`
- 本文件地位：**V2.1 的唯一事实入口**。与历史报告冲突时，以本文件的命令与
  代码证据为准；历史报告用于解释「为什么变成这样」。

## 1. 版本控制

| 项 | 值 |
|---|---|
| 仓库 | `github.com/hualei1234567/ddp`（私有） |
| 默认分支 | `main` |
| 当前 HEAD | 批次 10 分支 `feat/v21-p10-batch`（基线提交 `51123ff`，批次内 5 项：N6-005 / N7-005 / N5-003 / N4-003 / N8-002） |
| 分支模型 | 任务分支 + PR；`main` 不做直推（纪律，平台未强制） |
| 工作区 | 干净（本轮 N0 只新增文档） |
| 分支保护 | **不可用**（私有仓 rulesets API 403），以 CI + 评审纪律替代 |
| 提交规范 | Conventional Commits |

本轮合并记录：PR #33（4 个提交）CI 4/4 全绿后合并；合并后 main CI 运行中。

## 2. 契约（OpenAPI）

| 项 | 值 | 证据 |
|---|---|---|
| 契约版本 | **1.25.0**（P12：数据源远端校验结构化 1.24.0、项目级逾期汇总 1.25.0） | `packages/contracts/openapi.yaml:4` |
| 路径数 | **239** | `grep -c '^  /' packages/contracts/openapi.yaml` |
| operationId 数 | **319** | `grep -rho 'operationId: *[A-Za-z0-9_]*' packages/contracts/fragments/ \| wc -l` |
| 冻结基线 | stage2–stage7 共 6 份 bundle + SHA256 | `packages/contracts/baselines/` |
| 一致性检查 | 通过；**基线内已接受 62 项差异**（含 9 条生效契约超前错误码、1 条映射表无产生点） | `python3 scripts/check_contract_parity.py --strict` |
| Lint | Redocly `@redocly/cli@1.34.24`（CI job） | `.github/workflows/ci.yml` |

## 3. 代码规模

| 层 | 文件数 | 行数 | 证据 |
|---|---:|---:|---|
| 后端 `backend/app`（Python） | 611 | 139,862 | `find backend/app -name '*.py'` |
| 前端 `apps/web/src`（TS/TSX） | 448 | 86,748 | `find apps/web/src \( -name '*.ts' -o -name '*.tsx' \)` |
| 前端页面文件 | 95 | — | `find apps/web/src/pages -name '*.tsx' \| grep -v test` |
| 后端路由模块 | 45 | 335 个端点 | `grep -rh '^@router\.' backend/app/api/v1/*.py` |
| 后端 Worker | 10 | — | `backend/app/workers/*.py` |
| Alembic 迁移 | 61 | 单头 `0067`（0062 审核 evidence / 0063 训练字段 / 0064 采集方案目标 / 0065 处理运行收口 / 0066 采集会话 / 0067 演练终态；0060 未使用） | `alembic heads` |
| ORM 表 | 90 | PostgreSQL 实表 90 | `__tablename__` 去重 / `information_schema` |
| ADR | 37 | — | `docs/adr/*.md` |

端点方法分布：GET 140、POST 144、PATCH 21、DELETE 5、PUT 1。

> **超限文件（>800 行，已登记 TD-01）**：
> `api/errors.py` 954、`application/annotation/submissions.py` 935、`application/annotation/ports.py` 888、
> `infrastructure/repositories/calibration_records.py` 869、`application/episodes/episodes.py` 850、
> `application/datasets/version_actions.py` 829、`apps/web/src/types/annotation.ts` 806（详见 TD-01）。

## 4. 领域模块（当前真实存在）

`backend/app/domain/`：annotation、audit、collection、common、datasets、datasources、
devices、episodes、governance、identity、ingestion、locations、modelops、suppliers。

`backend/app/application/`：annotation、collection、datasets、datasources、devices、
episodes、governance、identity、ingestion、locations、modelops、projects、suppliers
（横切：`audit.py`、`idempotency.py`）。

`data-platform/ddp_data/`：`huggingface/`（hub_client、adapter、importer）、
`lerobot/parser.py`、`adapters/{registry,builtin}.py`、`media/mp4.py`、
`parquet/`（官方 PyArrow 23.0.1 只读 footer）、`canonical/`。

`backend/app/workers/`：`outbox`、`datasources`、`ingestion_jobs`、`exports`、
`domain_sweeps`、`usage_snapshots`、`maintenance`。

## 5. 部署拓扑（本地真实栈）

`docker ps`：13 个容器全部 Up，其中 postgres/redis/minio/api 健康。

| 服务 | 作用 |
|---|---|
| `api` | FastAPI |
| `migrate` | 启动前 Alembic 升级（**用构建时镜像**，见 RK-04） |
| `worker` | RawAsset 校验 |
| `datasource-worker` | 数据源导入 |
| `ingestion-job-worker` | 接入任务 |
| `export-worker` | 导出 |
| `domain-worker` | 租约/采集数据检查/数据集有效性巡检 |
| `usage-snapshot-worker` | 用量快照 |
| `maintenance-worker` | 幂等记录清扫 |
| `local-adapter` | 本地训练 Adapter |
| `web` | Nginx 静态站点 |
| `postgres` / `redis` / `minio` | 依赖 |

数据库实际版本：`0061`（本地真实 PG 已升级）。

## 6. 测试基线（本轮实测）

| 项 | 命令 | 结果 |
|---|---|---|
| 后端用例收集 | `.venv/bin/python -m pytest --collect-only -q` | **3174 collected** |
| 后端全量 | `.venv/bin/python -m pytest -o addopts="" -q` | 见 §6.1（本轮实际输出） |
| 前端全量 | `npx vitest run` | **928 passed / 19 skipped**（121 文件，117 过 / 4 跳） |
| 契约 | `check_contract_parity.py --strict` | 通过（基线内 62 项） |
| make check | `bash scripts/check.sh` | 8 步：ruff / pytest / tsc+eslint+vitest / build / secret / Agent 守卫 / outbox / 契约 |

### 6.1 后端全量结果

```
3173 passed, 1 skipped（3174 collected；真实 PostgreSQL + Redis，P12 批次后，738.32s）
```

- 命令：`cd backend && DDP_TEST_POSTGRES_URL=... DDP_TEST_REDIS_URL=... .venv/bin/python -m pytest -q --junitxml=...`
- 集合 3174 = 3173 passed + 1 skipped；唯一 skip 为需显式 `DDP_RUN_REMOTE_VERIFY=1` 的 Hugging Face 手动用例（本机装有 ffprobe，MP4 真实样例未被跳过）。
- 前端：`928 passed / 19 skipped`（121 文件，P12 批次后）（`cd apps/web && npx vitest run`）。
- 结论：SQLite + 真实 PostgreSQL + 真实 Redis 全绿；迁移 0062（审核 evidence）在 SQLite 与真实 PG 上 upgrade→downgrade→upgrade 通过。

## 7. 数据事实（本地 PostgreSQL，非生产）

| 表 | 行数 | 表 | 行数 |
|---|---:|---|---:|
| `projects` | 40 | `collection_tasks` | 98 |
| `episodes` | 206 | `raw_assets` | 278 |
| `dataset_versions` | 87 | `annotation_tasks` | 154 |
| `annotation_records` | 222 | `training_requests` | 86 |
| `models` | 58 | `evaluations` | 25 |
| `data_requirements` | 93 | `audit_logs` | 12,086 |
| `locations` | 25 | `robots` | 56 |
| `suppliers` | 32 | | |

说明：
- 「数据缺口」是读时投影，不是独立表；行数不适用。
- 这些数据来自种子脚本与真实导入/审核链路，用于验收，不是生产数据。

## 8. 已验证能力（摘要，详见 `capability-matrix.md`）

- **主链真实跑通**：HF 数据源 → 导入 → 校验/下载 → LeRobot 解析 → MinIO →
  Episode/MediaAsset → 视频 Range 审看 → 人工审核 → 导入质量报告。
- **资产与数据集**：RawAsset 状态机与公开动作、DatasetVersion 生命周期 +
  `validity_status` 子轴（STALE/INVALIDATED 阻断导出）、Manifest、血缘投影。
- **标注**：Schema、样本集、任务/批次/分配、提交/复核/仲裁、黄金集、抽检、IAA、
  AI 建议、返工闭环。
- **模型闭环**：训练需求/交付（外部 Adapter）、模型版本、评测、反馈、数据缺口、
  数据需求、五轴覆盖。
- **治理**：项目/成员、场地、供应商、配额与用量报表、审计日志查询、分布式限流。

## 9. 当前明确缺口（状态口径）

| 缺口 | 状态 |
|---|---|
| 真实机器人设备格式端到端 | **BLOCKED**（无硬件） |
| Episode 级自动质检 | **VERIFIED**（N4-005：`application/quality/episode_checks.py` + 处理流水线 QUALITY 阶段逐 Episode 质检） |
| Processing Pipeline（八阶段） | **PARTIAL**（FILE_VALIDATION / QUALITY_PRE_CHECK 真实执行；解析/切片/清洗等处理器仍缺失） |
| Canonical Frame / SensorStream / Action / Observation | **PARTIAL**（N3-001 `data-platform/ddp_data/canonical/` 只读契约；无生产者、无持久化，ADR-0035 有意不建表） |
| Parquet / MCAP / ROS Bag / HDF5 字段级解析 | **PARTIAL**（Parquet footer 与 MCAP 元数据只读已实现；ROS Bag / HDF5 **MISSING**；均未接入导入链路） |
| 六类质量报告 UI | **MISSING** |
| 性能基线（100/1K/10K episodes） | **PARTIAL**（本机只读场景已测，见 `docs/operations/性能基线-DDP-V21-N8-002-2026-11-19.md`；staging/生产 **BLOCKED**） |
| staging / 生产发布 | **BLOCKED** |
| 经营成本核算 | **MISSING**（需财务口径） |
| `validity_status=UNKNOWN` 写入点 | **VERIFIED**（N5-004/N5-007：manifest 与 raw asset 双层探测，明确缺失写 UNKNOWN 并区分原因） |
| 真实训练平台 | **BLOCKED**（未配置）；MOCK/DRY_RUN 已是独立执行器（N7-007），但不代表真实训练 |
| 采集会话 → Episode / RawAsset 归属链路 | **MISSING**（N9-001 会话只记录执行事实，尚未关联产出） |

## 10. 复现命令清单

```bash
# 版本与规模
git log -1 --format='%H %ad' --date=iso
git rev-list --count main
find backend/app -name '*.py' | wc -l
find apps/web/src \( -name '*.ts' -o -name '*.tsx' \) | wc -l

# 契约
head -5 packages/contracts/openapi.yaml
grep -c '^  /' packages/contracts/openapi.yaml
python3 scripts/check_contract_parity.py --strict

# 迁移
cd backend && .venv/bin/alembic heads && .venv/bin/alembic history | wc -l

# 测试
cd backend && .venv/bin/python -m pytest -o addopts="" -q --co | tail -1
cd backend && .venv/bin/python -m pytest -o addopts="" -q
cd apps/web && npm test

# 部署与数据
docker ps --format '{{.Names}}\t{{.Status}}'
docker exec ddp-postgres psql -U ddp -d ddp -tAc "select version_num from alembic_version"
```

## 11. 基线冻结规则

1. 本文件的数字随每次合并更新；更新时必须同时给出采集命令与日期。
2. 修改历史 migration、删除冻结契约、重写已验证链路 = 违反 `no-go-list.md`。
3. 任何「已完成」声明必须能落到本文件 §8 或 `capability-matrix.md` 的状态与证据；
   落不上的，一律视为未完成。

