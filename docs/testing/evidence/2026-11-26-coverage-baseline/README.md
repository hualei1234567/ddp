# 后端覆盖率基线（审计 R3 落地）

## 结论

**行覆盖率 90.0%**（55472 语句，5736 未覆盖），316 个文件 100% 覆盖。

## 采集方式

```bash
cd backend
PYTHONPATH=".:../data-platform" .venv/bin/python -m coverage run --source=app -m pytest tests -q
PYTHONPATH=".:../data-platform" .venv/bin/python -m coverage report
```

口径见仓库根 `.coveragerc`：只统计 `backend/app`，排除 `migrations/`（历史快照，
不随后续演进），`workers/` 保留（真实运行路径，覆盖率低即风险）。

## 未覆盖最集中的目录（按未覆盖行数）

| 目录 | 未覆盖行数 | 评价 |
|---|---|---|
| `infrastructure/repositories` | 749 | 多为异常分支与回滚路径；SQL 分支难在 SQLite 覆盖 |
| `application/annotation` | 511 | 标注域是核心，值得优先补 |
| `domain/annotation` | 493 | 领域层未覆盖**不该**发生在纯逻辑上，优先补 |
| `domain/datasets` | 447 | 同上 |
| `application/datasets` | 432 | — |
| `infrastructure/db` | 339 | 守卫/约束辅助函数，多为防御分支 |
| `api/v1` | 212 | 错误码分支 |
| `application/modelops` | 199 | — |

## 单文件覆盖率最低 TOP10（< 60%）

| 文件 | 覆盖率 | 备注 |
|---|---|---|
| `workers/maintenance.py` | **0%** | 维护任务无测试——优先级最高的补测点 |
| `infrastructure/storage/processing_objects.py` | 28% | 存储适配，多为 S3 异常路径 |
| `infrastructure/storage/s3.py` | 28% | 同上 |
| `workers/usage_snapshots.py` | 48% | — |
| `api/v1/adapter_callback_routes.py` | 53% | 回调鉴权分支 |
| `infrastructure/db/collection_guards.py` | 53% | 触发器/守卫 |
| `infrastructure/repositories/retention.py` | 56% | 数据保留策略 |
| `workers/lineage_backfill.py` | 56% | — |
| `infrastructure/db/guards.py` | 57% | — |
| `workers/outbox.py` | 57% | outbox 投递核心——建议补 |

## 门槛决策

- **设定 85%**（当前 90%，留 5 点余量）：既能防退化，又不因正常波动误杀
- 门槛写在 `scripts/check.sh` 第 2 步的 `--fail-under=85`
- **不要为过门槛写无断言测试**：未覆盖就让它未覆盖，直到补真实用例

## 补测优先级建议

1. `workers/maintenance.py`（0%）——维护任务无任何测试，是最危险的空白
2. `workers/outbox.py`（57%）——outbox 是事件投递核心，异常路径必须覆盖
3. `domain/annotation` + `domain/datasets`（合计 940 行未覆盖）——纯领域逻辑理应最易测
4. `application/annotation`（511 行）——核心标注链路

## 诚实边界

- 仅**行覆盖率**，无分支覆盖率（`--branch` 未启用；分支覆盖率会显著低于行覆盖率）
- 未覆盖行包含大量"异常/防御分支"，这类分支覆盖率天然偏低，不代表主路径没测
- 迁移文件的 downgrade 由 47 个迁移测试单独覆盖（不在本统计内）
