### MEM-INC-012 | 新增阶段与产物枚举值必须同步全部相关表的 CHECK 约束

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `处理编排/数据库迁移` | 批次/资产 `ProcessingStage / ProcessingArtifactKind` | 密级 `INTERNAL`
- **核心结论**：`ProcessingStage` 与 `ProcessingArtifactKind` 在数据库中分布在多张表的独立 CHECK 约束上（`processing_runs`、`processing_run_steps`、`processing_artifacts` 的 `stage` 约束 + `processing_artifacts` 的 `kind` 约束）。新增枚举值时若未一次性更新全部四处约束，Worker 执行会逐表撞上 `CheckViolation` 并陷入无意义的 `RETRYING`。
- **关键依据与细节**：
  - 实测场景（V5.1 全链路 `process_once()` 落库验证）：新增 `STREAM_EXTRACTION` 枚举后首次运行撞 `ck_processing_runs_stage_allowed`；只修 `processing_runs` 后撞 `ck_processing_run_steps_stage_allowed`；再修后撞 `ck_processing_artifacts_stage_allowed`；最后撞 `ck_processing_artifacts_kind_allowed`，共四轮才彻底跑通。
  - 修复后 Worker 单次 `process_once()` 完整跑通 9 个阶段，`processing_runs.status = SUCCEEDED`、落库 66 条 `processing_artifacts`（含 3 个真实逐帧 NDJSON）。
- **影响与后续动作**：
  - 新增防回归测试断言迁移声明的 `stage` 与 `kind` 允许值集合与领域枚举**完全相等**（集合相等而非子集），并断言三张表均受覆盖。
  - 责任人：后端；已通过迁移 `0074` 与契约一致性单测闭环。

---

#### 工程扩展字段

- **check_ref**：`backend/tests/migrations/test_processing_stage_constraints.py::test_stage_migration_covers_every_enum_value`
- **recurrence**：`1`
- **tags**：`check-constraint,enum,migration,postgres,processing-run,stage,stream-extraction`
