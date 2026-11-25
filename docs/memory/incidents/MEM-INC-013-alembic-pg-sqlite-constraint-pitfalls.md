### MEM-INC-013 | PostgreSQL 与 SQLite 双库迁移的 Schema 限定、同名约束与冒号绑定陷阱

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `Alembic 数据库迁移` | 批次/资产 `backend/migrations/versions/*` | 密级 `INTERNAL`
- **核心结论**：DDP 生产与集成环境运行在 PostgreSQL（可能含历史隔离测试 schema），而 `tests/migrations/` 运行在 SQLite。编写约束 DDL 迁移时必须按 `op.get_bind().dialect.name` 分支：PG 侧显式限定 `public.<table>`、循环清空同名重复 CHECK 约束、避免 `:param::regclass` 冒号语法冲突；SQLite 侧跳过 PG 专有 `pg_constraint` 与 `DROP CONSTRAINT` 语句。
- **关键依据与细节**：
  - 踩坑 1（多 schema 幽灵约束）：本地 PG 存在历史测试留下的 `ddp_dbg_*` / `ddp_v51*` schema，查询 `pg_constraint` 未限定 `public.` 导致读到 3 条同名约束、误判迁移未生效。
  - 踩坑 2（同名重复 CHECK）：PostgreSQL 允许同表存在多条同名不同 OID 的 CHECK 约束，单次 `ALTER TABLE ... DROP CONSTRAINT` 只删一条，旧约束仍拦截新值，必须循环删至计数归零。
  - 踩坑 3（SQLAlchemy `text()` 冒号解析）：`sa.text("... :relation::regclass")` 会把 `::regclass` 误解析为绑定参数引发 `SyntaxError`，表名常量应内联并用命名参数绑定值。
  - 踩坑 4（SQLite 方言差异）：硬写 `public.processing_runs` 在 SQLite 下报 `no such table`；`batch_alter_table` 重建表时又会复制旧 CHECK 报 `already exists`。
- **影响与后续动作**：
  - 所有涉及 `pg_constraint` 或 `ALTER TABLE ... CONSTRAINT` 的新迁移统一采用 `_dialect() == "postgresql"` 守卫 + `public.` 限定模式，并在真实 PG 验证 `upgrade → downgrade → upgrade` 往返。

---

#### 工程扩展字段

- **check_ref**：`backend/tests/migrations/test_processing_stage_constraints.py::test_migration_is_dual_dialect_safe`
- **recurrence**：`1`
- **tags**：`alembic,check-constraint,dialect,migration,postgres,schema,sqlalchemy,sqlite`
