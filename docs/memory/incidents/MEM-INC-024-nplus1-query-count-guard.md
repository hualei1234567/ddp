### MEM-INC-024 | 列表 N+1 用"查询次数不随页大小变化"的断言守护，而不是靠压测

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-26`
- **适用范围**：项目 `DDP` | 环节 `列表查询/性能回归` | 批次/资产 `backend/app/infrastructure/repositories/collection_tasks.py` | 密级 `INTERNAL`
- **核心结论**：N+1 的确定性证据是**查询次数与页大小无关**，不是延迟数字。用 SQLAlchemy `before_cursor_execute` 事件计数，参数化页大小（1/5/20）断言查询数 ≤ 固定上限——修复前 2N+1 必然超限，修复后恒定 3 次。延迟随机器状态波动，单次压测难以作为回归门槛。
- **关键依据与细节**：
  - 生产压测基线（2026-11-26）发现 `GET /collection-tasks` limit 1→20→50 = 8.6→38.9→87.7ms，逐行各查 2 次关联。
  - 修复方式：列表路径批量预取（`task_id IN (...)` 一条查询 + 按 task_id 分组），详情路径保持单行查询。
  - 回归测试 `backend/tests/api/test_collection_tasks_no_nplus1.py`：修复前页大小 20 → **41 次**查询（断言失败）；修复后 → **≤4 次**（通过）。页大小 1 时修复前仅 3 次不触发失败，恰好也证明是线性增长。
  - 关键技巧：消费查询结果（断言关联字段）才能逼出真实关联查询，否则惰性加载会掩盖 N+1。
- **影响与后续动作**：
  - 任何"列表 + 每行关联"的仓储方法都应配一条查询计数回归测试。
  - 新增列表端点时，把该测试当作验收项之一（与功能测试并列，不是可选）。

#### 工程扩展字段

- **check_ref**：`backend/tests/api/test_collection_tasks_no_nplus1.py`
- **recurrence**：`1`
- **tags**：n-plus-1,query-count,sqlalchemy-events,performance-regression
