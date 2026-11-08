# CI PostgreSQL 证据（基线 15af240）

- 提交：`15af240d0c59cce303cf3c9a2cb93e1fc0d72d7e`
- CI run：https://github.com/hualei1234567/ddp/actions/runs/42478599004
- 结论：success
- 环境：GitHub Actions `postgres:16-alpine`，`DDP_DATABASE_URL` 指向该实例

## 关键日志

```text
2026-11-08T12:06:01.4470551Z  2026-11-08 12:06:00.067 UTC [89] ERROR:  insert or update on table "devices" violates foreign key constraint "fk_devices_project_robot"
2026-11-08T12:06:01.4488434Z  PostgreSQL init process complete; ready for start up.
```

该错误来自 `test_postgres_project_isolation` 的跨项目设备→机器人引用用例：测试期望
`IntegrityError`，PostgreSQL 实际以复合外键拒绝，证明迁移与隔离测试在真实 PG 上执行。

## 说明

- 本证据关闭 QA-205 `D-205-04` 的“当前提交 PG 证据未归档”部分。
- 数据库兜底缺口（`calibration_target_refs` / 不可变触发器）仍由 DDP-212 处理，未因此关闭。
