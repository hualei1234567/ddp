### MEM-CHG-001 | 迁移 0072 归正 outbox 历史留痕行

- **类型与状态**：`CHG` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-24`
- **适用范围**：项目 `DDP` | 环节 `迁移/运维` | 批次/资产 `outbox_events 历史行` | 密级 `INTERNAL`
- **核心结论**：迁移 0072 把「`status=PENDING` 且 `event_type` 不在当前 `DISPATCH_EVENTS` 白名单」的历史行**同时**改为 `dispatch=false + status=RECORDED`，共归正 23 条积压 9 天的行。
- **关键依据与细节**：
  - 根因：早期 `enqueue` 无条件写 `PENDING`，不分留痕/投递类；现在代码已正确分流（`PENDING if dispatch else RECORDED`），但历史行没回填
  - 23 条分布：`drill.event` 5（当前源码已完全不存在）、`datasource.registered` 6、`datasource.import.start` 6、`datasource.review.created` 3、`datasource.review.decided` 3
  - **白名单内联快照**而非 import 代码——迁移必须能在任意历史版本执行
  - 白名单内的真实投递类积压（如 `dataset.export.requested`）**原样保留**，那是真待办
  - 修复后：PENDING 23 → **0**；RECORDED 12230 → 12253
  - **downgrade 为空操作**：回填后无法区分「本应 PENDING」与「本应 RECORDED」，强行回滚会把真实待办也变积压（见 MEM-INC-003 的约束坑）
- **影响与后续动作**：
  - 数据修复类迁移不应回退；需要逆向时用备份恢复
  - 新增 `DDP_OUTBOX_DB_CHECK=1` 运行时校验：留痕类不得停留 PENDING
  - 责任人：后端；**已完成**（PR #100）

---

#### 工程扩展字段

- **check_ref**：`backend/migrations/versions/0072_outbox_retention_backfill.py` + `scripts/check_outbox_events.py`
- **recurrence**：`1`
- **tags**：`migration,outbox,backfill,pending,recorded,data-fix`
