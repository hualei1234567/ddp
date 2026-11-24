### MEM-INC-003 | 只改 status 不改 dispatch，撞 outbox 一致性约束

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-24`
- **适用范围**：项目 `DDP` | 环节 `outbox 数据修复/迁移` | 批次/资产 `outbox_events` | 密级 `INTERNAL`
- **核心结论**：`outbox_events` 有数据库 CHECK 约束 `ck_outbox_events_dispatch_status_consistent`，语义是 **`dispatch=false ⟺ status='RECORDED'`**。任何数据修复必须**同时**改 `dispatch` 与 `status`；只改一个会被约束直接拒绝。
- **关键依据与细节**：
  - 约束定义来源：`backend/migrations/versions/0030_outbox_dispatch_semantics.py:50`
  - 表达式：`(dispatch = 0 AND status = 'RECORDED') OR (dispatch = 1 AND status <> 'RECORDED')`
  - 首次尝试迁移只写 `SET status='RECORDED'`，报警：`psycopg.errors.CheckViolation: new row violates check constraint "ck_outbox_events_dispatch_status_consistent"`
  - 正确写法：`SET dispatch = false, status = 'RECORDED'`
  - **这个约束是资产不是障碍**：它禁止了「标记为待投递却写成留痕终态」的自相矛盾行
  - 迁移 0072 因此采用「dispatch + status 同时归正」
- **影响与后续动作**：
  - 任何改 `outbox_events` 状态的脚本/迁移，必须同时检查 `dispatch`
  - 新增类似的「双字段语义约束」时，必须在字段注释里写明，避免下一个人只改一半
  - 责任人：后端/迁移；**已完成**（PR #100）

---

#### 工程扩展字段

- **check_ref**：`backend/migrations/versions/0072_outbox_retention_backfill.py`（迁移本身即验证）+ 真实 PG upgrade 通过
- **recurrence**：`1`
- **tags**：`outbox,check-constraint,migration,dispatch,status,postgres`
