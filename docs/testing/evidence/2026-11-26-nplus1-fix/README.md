# N+1 修复证据：`GET /collection-tasks`（V52-035）

## 缺陷

压测基线（`../2026-11-26-prod-readonly-perf/README.md`）发现列表端点每行各查 2 次关联
（设备 / 传感器），页大小 N 时 SQL 次数为 **2N+1**，延迟随页大小线性增长：
limit 1→20→50 = 8.6→38.9→87.7ms。

根因：`SqlCollectionTaskRepository._from_row()` 对每行无条件调用
`_assignment_ids(CollectionTaskDevice...)` 与 `_assignment_ids(CollectionTaskSensor...)`。

## 修复

列表路径改为**批量预取**：先用一条 `task_id IN (...)` 查询取回全部设备/传感器关联，
按 `task_id` 分组后再构造领域对象。详情路径保持原单行查询（行为不变）。

查询数从 `2N+1` 变为固定 **3**（任务行 + 设备批量 + 传感器批量），与页大小无关。

## 回归证据（可重复执行）

测试：`backend/tests/api/test_collection_tasks_no_nplus1.py`
方法：SQLAlchemy `before_cursor_execute` 事件计数真实执行 SQL，参数化页大小 1/5/20。

**修复前**（`git stash` 掉修复后跑）：

```
页大小 5 时查询 11 次，超过上限 4——N+1 回归（修复前 2N+1）
页大小 20 时查询 41 次，超过上限 4——N+1 回归（修复前 2N+1）
FAILED ... [5]
FAILED ... [20]
```

（页大小 1 时 3 次，恰好未超上限，所以修复前只有 5/20 两条失败——这本身也证明是线性增长。）

**修复后**：

```
...                                                                      [100%]
3 passed
```

## 语义等价保证

- 既有 `tests/api/test_collection_api.py`（12 项）全绿：列表/详情/创建/状态机/幂等/权限零回归
- 关联 ID 排序与单行版一致（批量查询按 `(task_id, column)` 升序）
- 详情路径未改动，仍走单行查询

## 诚实边界

- 未做修复前后**真实延迟**对比（只做查询次数对比）：因为需要在生产反复起停 API 实例，
  查询次数才是与页大小无关的确定性证据；延迟随机器状态波动，单次对比说明力弱
- 未覆盖：`limit` 极大时的内存占用、跨项目混合负载、写路径
