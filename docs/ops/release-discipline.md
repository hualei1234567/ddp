# 生产发布纪律（阶段 4-3）

> 前提：本项目**不设 staging**，所有变更直接在 prod 验证。因此发布纪律不是"建议"，
> 而是唯一的安全网。本文记录已实际执行过的流程与证据。

## 一、数据库迁移五步法（强制）

每次有迁移变更时，按序执行，**不得跳步**：

```bash
# 1) 离线 SQL 审查：不连库，只看会执行什么
cd backend
DDP_DATABASE_URL="postgresql+psycopg://ddp:ddp@localhost:55432/ddp" \
  PYTHONPATH=".:../data-platform" .venv/bin/python -m alembic upgrade head --sql > /tmp/migrate.sql
#    人工检查：是否有 DROP/ALTER TYPE/长时间表锁；新表是否带约束

# 2) 备份点（有数据的库必做）
PGPASSWORD=ddp pg_dump -h localhost -p 55432 -U ddp -d ddp -Fc \
  -f /tmp/ddp-pgbackup/ddp-pre-$(date +%H%M).dump

# 3) 一次性验证库跑双向往返（丢弃式，不是 staging）
PGPASSWORD=ddp psql -h localhost -p 55432 -U ddp -d postgres -c "CREATE DATABASE ddp_mig_check;"
PGPASSWORD=ddp pg_restore -h localhost -p 55432 -U ddp -d ddp_mig_check /tmp/ddp-pgbackup/<最新dump>
DDP_DATABASE_URL="postgresql+psycopg://ddp:ddp@localhost:55432/ddp_mig_check" \
  .venv/bin/python -m alembic upgrade head       # 正向
DDP_DATABASE_URL="...ddp_mig_check" .venv/bin/python -m alembic downgrade <上一版>  # 反向
DDP_DATABASE_URL="...ddp_mig_check" .venv/bin/python -m alembic upgrade head        # 再正向
#    三步都成功才继续；然后 DROP DATABASE ddp_mig_check

# 4) 生产实跑
DDP_DATABASE_URL="postgresql+psycopg://ddp:ddp@localhost:55432/ddp" \
  PYTHONPATH=".:../data-platform" .venv/bin/python -m alembic upgrade head

# 5) 落地确认
PGPASSWORD=ddp psql ... -c "select version_num from alembic_version;"   # 版本正确
PGPASSWORD=ddp psql ... -c "select extname from pg_extension;"          # 扩展（如 pg_trgm）已装
PGPASSWORD=ddp psql ... -c "select tablename from pg_tables where ..."  # 新表齐备
```

**已执行记录**：
- 2026-11-26 `0074 → 0082`（5 个迁移）：备份 5.7MB → 验证库往返 → 生产实跑 → 5 表齐备 ✅
- 2026-11-26 `0082 → 0084`（pg_trgm + 指纹列）：同法执行，`pg_trgm` 扩展与 2 个索引确认 ✅

## 二、迁移号填写铁律

`down_revision` 填的是**当前 main 的实际 head**，不是"我以为的上一版"。填写前必须：

```bash
cd backend && PYTHONPATH=".:../data-platform" .venv/bin/python -c \
  "from alembic.config import Config; from alembic.script import ScriptDirectory; \
   print(ScriptDirectory.from_config(Config('alembic.ini')).get_heads())"
```

多个 PR 并行时各自的 `down_revision` 都会指向同一个 head，**后合并的必须重排**，
否则出现双 head（`get_heads()` 返回两个值）。重排后必须重跑 `tests/migrations/`。

**已知坑**：runbook 里"回滚到某版本"的版本号必须是目标迁移的**直接上游**；
写成跨越多个迁移的版本号会连带删除后续迁移的表（`honeypot-runbook.md` 曾误写
`downgrade 0081`，实际会连带删除 0080/0083/0082，已修正为 `downgrade 0080`）。

## 三、特性开关清单

新能力一律"默认关闭 + 开关开启"，异常时一键回旧行为：

| 开关 | 默认 | 作用 | 开启前置 |
|---|---|---|---|
| `DDP_HONEYPOT_BREAKER_ENABLED` | `false` | 黄金题熔断（提交推进状态 + 派单拦截 + resume） | 需先确认黄金题集已配置 |
| `DDP_AI_TOKEN_USAGE_ENABLED` | `false` | LLM 调用 token 计量与成本台账 | 需先配置 `DDP_AI_TOKEN_PRICE_MAP` |
| `DDP_RATE_LIMIT_BACKEND` | 内存 | Redis 分布式限流 | 需 Redis 可用 |

**开启顺序**：先在单个项目开 → 观察 1 天指标（错误率/延迟/熔断次数）→ 全量开。

## 四、回滚演练记录

| 日期 | 演练内容 | 结果 |
|---|---|---|
| 2026-11-26 | 0074→0082→0074→0082 往返（验证库） | ✅ 通过 |
| 2026-11-26 | 0082→0084→0082→0084 往返（验证库） | ✅ 通过 |
| 2026-11-26 | 回流幂等缺陷：生产实测发现 409 → 修复 PR #143 → 生产复测 200 | ✅ 闭环 |

## 五、发布检查清单（每次合并前）

- [ ] `--sql` 离线 SQL 已审查
- [ ] 有数据的库已备份
- [ ] 双向往返在验证库通过（如有迁移）
- [ ] 迁移 `down_revision` 指向 main 实际 head；合并后 `get_heads()` 唯一
- [ ] 新能力有开关且默认关闭
- [ ] 回滚路径写进 PR body 或 runbook
- [ ] 生产验证：至少调一次真实端点确认行为（不是只跑单测）
