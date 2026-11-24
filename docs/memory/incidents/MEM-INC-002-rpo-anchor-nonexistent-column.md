### MEM-INC-002 | RPO 锚点引用不存在的列，备份校验形同虚设

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-24`
- **适用范围**：项目 `DDP` | 环节 `运维/备份恢复演练` | 批次/资产 `PostgreSQL 全库` | 密级 `INTERNAL`
- **核心结论**：备份恢复演练用 `audit_logs.updated_at` 作为 RPO 锚点，但**该列根本不存在**（真实列是 `occurred_at`）。SQL 静默失败返回 `none`，而断言是 `源 == 恢复`，于是 **`none == none` 永远成立**——「RPO 一致」的校验从未真正工作过。
- **关键依据与细节**：
  - 实测：`grep -n "updated_at" scripts/ops/backup-restore-drill.sh` 命中作为 RPO 锚点的 SELECT
  - `audit_logs` 真实列：`id, project_id, actor_user_id, actor_role, resource_type, resource_id, action, from_status, to_status, reason, request_id, result, correlation_id, details, occurred_at`
  - 修复前演练输出：`RPO 锚点（audit_logs.max(updated_at)）=none`
  - 修复后：`RPO 锚点 = 2026-11-24 00:11:32.267469+00`（真实时间戳）
  - **更深的坑**：如果锚点取到空值就跳过校验，等于**校验器自己把校验关掉了**。正确做法是「取不到锚点 = 演练失败」，而不是「跳过」
  - 已加结构测试：断言 SQL 中不得出现 `audit_logs.*updated_at`
- **影响与后续动作**：
  - 任何"取一个基准值再比对"的校验，都必须在**取不到基准值时失败**，不能静默降级
  - 所有写 SQL 的地方必须核对真实列名（IDE 不报错，只有运行才知道）
  - 责任人：运维；**已完成**（PR #103）

---

#### 工程扩展字段

- **check_ref**：`scripts/tests/test_backup_restore_drill.py::test_rpo_anchor_uses_an_existing_column`
- **recurrence**：`1`
- **tags**：`backup-restore,rpo,sql,column-name,silent-failure,drill`
