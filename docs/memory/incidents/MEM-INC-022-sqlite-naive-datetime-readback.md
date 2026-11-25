### MEM-INC-022 | SQLite 读库 datetime 是 naive，领域层要求 aware——在仓储边界补 UTC，不放宽领域校验

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-26`
- **适用范围**：项目 `DDP` | 环节 `仓储读取/领域校验` | 批次/资产 `backend/app/infrastructure/repositories/` | 密级 `INTERNAL`
- **核心结论**：SQLite 的 `DateTime(timezone=True)` 实际**不保存时区**——写入 aware datetime 后读回是 naive。领域层强制 aware（`optional_aware_datetime` 等）时，读路径会 422/抛错。修法是在**仓储边界**把 naive 补成 UTC，而不是放宽领域校验：领域的时间语义是全库统一不变量。
- **关键依据与细节**：
  - 实测（2026-11-26，PR #140 评测失败案例回流）：`GET /projects/{id}/annotation-backflow` 首次实跑返回 422，日志 `领域异常 code=validation_failed: received_at 必须带时区（UTC）`；数据其实已落库（`RECEIVED` 行存在），是 `_to_domain()` 把 `row.received_at` 直接喂给领域实体触发校验。
  - 修复：仓储内 `_as_utc(value)`，`value if value.tzinfo is not None else value.replace(tzinfo=UTC)`；测试在 API 列表端点覆盖（迁移测试只验 schema 与约束，抓不到读路径问题）。
  - 同族问题：JSONB 回读 `marked_at` 是 ISO 字符串需容错解析（MEM-INC-021）。
- **影响与后续动作**：
  - 新增任何"含时间列的读路径"必须做等价 UTC 补齐；建议抽公共 helper 到 `app/infrastructure/db/` 层，避免每个仓储各写一份。
  - 读路径正确性只能靠 API 测试守护，迁移测试不覆盖。

#### 工程扩展字段

- **check_ref**：`backend/app/infrastructure/repositories/annotation_backflow.py`
- **recurrence**：`2`
- **tags**：sqlite,timezone,naive-datetime,repository-boundary,domain-invariant
