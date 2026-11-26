# 黄金题熔断 runbook（V52-008，蓝图 N3 Honeypot 接线）

> 适用范围：标注运营 / 项目负责人 / 平台运维。
> 领域规则权威实现：`backend/app/domain/annotation/honeypot_breaker.py`（#131 已合）；
> 接线实现：`backend/app/application/annotation/honeypot_wiring.py`（本卡）。
> 本文只写"怎么操作"，不重复领域规则——两处口径不一致时以领域代码为准并回报。

## 1. 它是什么

黄金题（gold item）按任务流随机混入标注员的队列；系统对每个标注员的黄金题
命中情况做滑动窗口统计，**连续 2 个满窗口准确率低于 85% 自动 PAUSED**：
不再给该标注员派新单（已有分配不收回），并冻结结算建议口径。

- 阈值：85%（`PAUSE_THRESHOLD`），最小窗口样本 10 条/窗（`MIN_WINDOW`/窗口大小），
  连续触发 2 窗（`CONSECUTIVE_WINDOWS`）——全部由领域模块钉死，不在配置层开放。
- 状态持久化：表 `annotation_honeypot_state`（迁移 0083），`(project_id, annotator_id)` 唯一。
- PAUSED 后提交的黄金题不再改状态（源头已被派单闸口拦截，属于异常路径）。

## 2. 开关与默认行为

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| `DDP_HONEYPOT_BREAKER_ENABLED` | `false` | **全部入口短路跳过**：状态行不写、派单不查、resume 幂等返回当前状态。行为与旧版完全一致。 |

开启后：
1. 黄金题提交评估链路调用 `record_gold_submission(...)` 推进滑窗；
2. 派单入口（`POST /annotation-assignments`）注入 `SqlHoneypotGatekeeper`：
   PAUSED 标注员（仅 ANNOTATOR 角色）返回
   `422 {"error": {"code": "annotation_annotator_paused_honeypot", "details": {"annotator_id": "...", "reason": "HONEYPOT_PAUSED"}}}`；
3. owner 端点 `POST /api/v1/projects/{project_id}/annotation-honeypot/{annotator_id}/resume`
   可手动解除。

上线灰度建议：先在测试项目开启，观察一周状态行数据（误伤率），再全量开启。
关闭开关即时回滚（无状态残留影响：状态行只增不改旧语义，重开时从既有窗口继续累计）。

## 3. 误伤处置

误伤的典型表现：标注员整体质量正常，但恰好在某个窗口段被大量难题黄金题命中。
判定顺序：

1. **看审计**：`GET /ops/dispatch-queue` 与 audit-logs 里查
   `annotation_annotator_paused_honeypot` 相关记录与该标注员近期的分配/复核结果；
2. **看窗口明细**：查 `annotation_honeypot_state` 的 `hits` / `total`
   （当前窗口累计）与 `paused_reason`（触发时的窗口准确率快照）；
3. **判定**：
   - 黄金题本身有问题（错标、口径漂移）→ 先修黄金集数据（黄金集 ACTIVE 态
     内容不可原地改，走 `retire` + 新版本），再解除暂停；
   - 标注员状态问题（疲劳、换项目）→ 直接解除并在排班里调整；
   - 真实质量下滑 → 不解除，等窗口证明能力恢复不适用于人工解除场景：
     保持 PAUSED，走培训/降级流程。

## 4. 解除流程（resume）

只有 owner 或平台管理员可调用：

```
POST /api/v1/projects/{project_id}/annotation-honeypot/{annotator_id}/resume
```

- 成功返回 `{"annotator_id": "...", "state": "NORMAL", "resumed": true, "checked_at": "..."}`
- 幂等：重复解除、本就 NORMAL、开关关闭、无状态行都返回 200 且 `resumed=false`
  ——**客户端按 `state` 渲染，不要把 `resumed=false` 当失败**。
- editor/viewer 调用返回 403 `forbidden`。
- 解除后窗口清零重新累计（不回放历史命中）；已收回的"结算冻结建议"
  由结算侧按 PAUSED 区间自行复核，本端点不回改任何结算记录。

解除前最小检查清单（防手滑）：

- [ ] 已确认不是黄金集数据问题（§3 第 3 步）；
- [ ] `paused_reason` 里的窗口口径已人工过目；
- [ ] 该标注员在本项目的其他在途分配无需先处理（resume 不影响已有分配）。

## 5. 回滚

| 场景 | 操作 |
| --- | --- |
| 行为回滚（最快） | 设 `DDP_HONEYPOT_BREAKER_ENABLED=false` 并重启服务：派单恢复旧口径，resume 端点退化为只读 |
| 数据回滚 | `alembic downgrade 0080`（0083 的 down_revision 是 0080，回退后 `annotation_honeypot_state` 删除、状态清零）。**注意**：当前迁移链为 `0074→0078→0081→0080→0083→0082→0084`，`downgrade 0081` 会连带删除 0080/0083/0082（0082 之后的迁移同样受影响），因此必须按"目标版本的相邻上一版"填，不要填跨越多个迁移的版本号。需按迁移审批流程单独批准 |
| 代码回滚 | revert 本 PR（迁移 0083 与接线代码同 PR，revert 后 downgrade 需先于代码回滚执行） |

## 6. 相关文档

- 领域规则单测：`backend/tests/unit/test_honeypot_breaker.py`
- 接线测试：`backend/tests/application/test_honeypot_wiring.py`、
  `backend/tests/api/test_annotation_honeypot_api.py`
- 迁移测试：`backend/tests/migrations/test_honeypot_state_migration.py`
- 蓝图：`docs/roadmap/v5.2/annotation-platform-blueprint-plan.md`（N3 Honeypot）
