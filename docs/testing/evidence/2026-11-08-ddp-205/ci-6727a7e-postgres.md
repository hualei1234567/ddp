# CI PostgreSQL / 契约证据（Wave 3 修复后）

- 提交：`6727a7e`（代码与 `660d4bf` 相同，仅追加文档）
- CI run：https://github.com/hualei1234567/ddp/actions/runs/42482619622
- 结论：success
- 全部 4 个 job 通过：
  - 后端 lint / 迁移 / 测试（PostgreSQL 16 service）
  - 前端 类型 / lint / 测试 / 构建
  - Secret / 依赖 / Agent 守卫
  - 契约 OpenAPI lint / 兼容性 diff

## 覆盖内容

- `alembic upgrade head` 在 CI PostgreSQL 执行到 `0005`。
- `test_postgres_project_isolation` 与 `test_postgres_calibration_guards` 真实执行：
  - 复合外键拒绝跨项目目标；
  - 不可变触发器拒绝 VALIDATED 记录 checksum 直写；
  - JSON 列比较使用 `::text`（修复 `json = json` 缺陷）。
- `contract` job 执行 Redocly lint 与真实后端设备契约快照比对。

## 说明

- 本证据关闭 QA-205 `D-205-04` 的“当前提交 PostgreSQL 证据”部分。
- 数据库兜底已由 DDP-212 实现，并在本 run 的 PostgreSQL 测试中验证。
