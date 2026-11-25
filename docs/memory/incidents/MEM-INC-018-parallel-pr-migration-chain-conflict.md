### MEM-INC-018 | 多 PR 同 base 迁移链冲突：三个 PR 的 down_revision 都写同一个号 → 双 head，处置=串行合并+重排

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `数据库迁移/并行开发` | 批次/资产 `backend/migrations/versions/` | 密级 `INTERNAL`
- **核心结论**：≥2 个并行 PR 各自把 Alembic 迁移的 `down_revision` 指向同一个基线号时，合入后必然出现**多 head**。处置只有一条路：**串行合并 + 重排 down_revision**——按合入顺序把后合 PR 的 `down_revision` 改指前一个 head，保证链唯一；合并完成后必须跑全量迁移测试。
- **关键依据与细节**：
  - 实测（2026-11-25，PR #134/#135/#136 三线并行）：三个 PR 的迁移（0078 PRM 步级标注、0081 Token 成本台账、0080 技能路由）`down_revision` 都写了 `0074`，先后合入两个后即出现双 head。
  - 重排结果：0081 `down_revision = "0078"`（注释「重排：0081 依赖 0078（#134 先合，保持链线性）」），最终链 `0074 → 0078 → 0081 → 0080`，head 唯一；0080（#136）当时仍在 CI 收口，其重排随合入完成。
  - 佐证：`backend/tests/migrations/test_ai_token_usage_migration.py` 是 0081 的升级/回滚验证入口；迁移链改动后必须全量跑 `backend/tests/migrations/`，不能只跑单文件。
  - 这正是 MEM-SOP-002「共享编号资源预分配」要防的坑——预分配表给了区间，但三个 PR 都取了同一基线号做 `down_revision`，说明**预分配的不只是号段，还有基线指针**；并行批次开工前应明确「谁的 down_revision 指向谁」，或约定后合方负责重排。
- **影响与后续动作**：
  - 并行批次收尾时由 Owner 声明最终迁移链顺序；后合入 PR 的作者负责重排 `down_revision` 并在 PR 描述写明最终链。
  - 重排后跑 `pytest backend/tests/migrations/` 全量；head 不唯一不许合并。
  - 责任人：后合 PR 作者（重排）+ General Owner（声明链序）；每次并行迁移批次生效。

---

#### 工程扩展字段

- **check_ref**：`backend/tests/migrations/test_ai_token_usage_migration.py`
- **recurrence**：`1`
- **tags**：alembic,down-revision,merge-conflict,migration-chain,multiple-heads,parallel-pr,sqlite
