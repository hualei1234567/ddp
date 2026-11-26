# 黄金题熔断误伤申诉与解除 SOP（V52-008 / 蓝图 N3）

- 版本：v1.0 · 2026-11-26
- 机制说明：[`docs/roadmap/v5.2/honeypot-runbook.md`](../roadmap/v5.2/honeypot-runbook.md)
- 权威实现：
  - 领域状态机 `backend/app/domain/annotation/honeypot_breaker.py`
  - 接线（提交钩子 / 派单闸口 / resume）`backend/app/application/annotation/honeypot_wiring.py`
  - HTTP 面 `backend/app/api/v1/annotation_honeypot_routes.py`
- 权限：解除动作 = `get_owner_scope`（**项目 owner 或平台管理员**）
- 开关：`DDP_HONEYPOT_BREAKER_ENABLED`，默认 `false`（`backend/app/core/config.py:211`）

> 本文是**操作手册**；阈值与判定口径以领域代码为准。下文所有常量均按代码实值书写，
> 若与 `honeypot-runbook.md` 的表述冲突（例如把 `MIN_WINDOW` 与窗口大小混写），
> 以 `honeypot_breaker.py` 为准。

---

## 1. 什么情况算「被熔断」（申诉触发条件）

### 1.1 判定口径（代码实值）

| 参数 | 实值 | 位置 |
|---|---|---|
| `PAUSE_THRESHOLD` | `0.85`（准确率 < 85% 算低） | `honeypot_breaker.py:23` |
| `WINDOW_SIZE`（窗口大小，接线层默认） | `10` 条/窗 | `honeypot_wiring.py:279`（`WINDOW_SIZE_DEFAULT`） |
| `CONSECUTIVE_WINDOWS` | `2` | `honeypot_breaker.py:27` |
| `MIN_WINDOW` | `5`（构造不变式：窗口大小不得小于它，**不是**判定样本门槛） | `honeypot_breaker.py:25` |

判定规则：只对**满窗口**做判定；连续 `2` 个满窗口准确率 `< 0.85` → `PAUSED`。
因此**至少累计 20 条黄金题结果**才可能触发；样本不足时状态保持 `NORMAL`。

### 1.2 可观察信号

1. **派单被拦**：`POST /api/v1/projects/{project_id}/annotation-assignments` 返回
   `422`，错误体（`backend/app/main.py:93`）形如：

   ```json
   {
     "error": {
       "code": "annotation_annotator_paused_honeypot",
       "message": "标注员黄金题准确率熔断，暂停派单",
       "request_id": "…",
       "details": {"annotator_id": "…", "reason": "HONEYPOT_PAUSED"}
     }
   }
   ```

2. **状态行**：表 `annotation_honeypot_state`，`(project_id, annotator_id)` 唯一，
   `state=PAUSED`，`paused_reason` 记录触发时的窗口准确率快照。
3. **注意**：熔断只挡**新分配**，不收回已有分配；`resume` 也不改动已有分配、
   提交与结算记录。

### 1.3 什么能申诉、什么不能

| 情形 | 是否受理 | 判定依据 |
|---|---|---|
| 黄金题本身错标 / 标注口径漂移 | ✅ 受理 | 黄金集内容问题，应修数据后再解除 |
| 窗口内被集中投放高难题（难题段） | ✅ 受理 | `window_history` / `paused_reason` 与同批标注员对比 |
| 黄金集版本错误 / 注入错配 | ✅ 受理 | 黄金集 `retire` + 新版本后解除 |
| 标注员真实质量下滑 | ❌ 不受理 | 保持 `PAUSED`，走培训 / 降级流程 |
| 开关关闭却称"被熔断" | ❌ 不受理 | 开关关闭时派单闸口不注入，不会产生该 422；属其他错误码 |

---

## 2. 时限与责任（运营约定）

| 环节 | 责任角色 | 时限 |
|---|---|---|
| 受理申诉、登记工单 | QA lead | **4 小时内**首次响应（工作日） |
| 取证与判定（§4） | QA lead | 受理后 **24 小时内**给出结论 |
| 执行解除（§3 Step 3） | 项目 owner / 平台管理员 | 结论为「误伤」后 **1 小时内**执行 |
| 结论升级 / 争议裁决 | 项目 owner | 24 小时内未闭环即升级 |
| 申诉关闭与归档 | QA lead | 解除后 1 个工作日内 |

> 申诉工单必填：`project_id`、`annotator_id`、被拦截请求的 `request_id`（错误体里带）、
> 拦截时间（UTC）、申诉理由、近期任务/黄金集信息。

---

## 3. 操作步骤（可复制 curl）

所有端点前缀 `/api/v1`；认证见 [README §3.1](README.md#31-认证curl-通用写法)。

```bash
BASE=http://127.0.0.1:8000/api/v1
AUTH="Authorization: Bearer ${BEARER_JWT}"
PROJECT_ID="<project-uuid>"
ANNOTATOR_ID="<annotator-user-uuid>"
```

### Step 1：只读取证（QA lead / owner）

**1.1 看该标注员近期分配与状态**（READ，任何成员可查）：

```bash
curl -sS -G "${BASE}/projects/${PROJECT_ID}/annotation-assignments" \
  -H "${AUTH}" \
  --data-urlencode "assignee_id=${ANNOTATOR_ID}" \
  --data-urlencode "limit=100"
```

看：是否有 `PAUSED` 后仍积压的在途分配、最近分配的角色（`ANNOTATOR`/`REVIEWER`）、
`status` 分布。

**1.2 看审计时间线**（仅 owner / 平台管理员；`get_owner_scope`）：

```bash
curl -sS -G "${BASE}/projects/${PROJECT_ID}/audit-logs" \
  -H "${AUTH}" \
  --data-urlencode "resource_type=ANNOTATION_ASSIGNMENT" \
  --data-urlencode "occurred_from=$(date -u -v-7d +%Y-%m-%dT%H:%M:%SZ)" \
  --data-urlencode "limit=100"
```

> `occurred_from` / `occurred_to` 必须是**带时区**的 ISO 8601（闭区间）。
> Linux 用 `date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ`。
> 注意：熔断拦截本身**不写审计**（派单在创建前即被拒），审计只能还原历史动作。

**1.3 查熔断状态行与黄金题评估（只读 SQL，平台管理员执行）**

本版**没有读取熔断状态的 HTTP 端点**（只有 `POST .../annotation-honeypot/{annotator_id}/resume`），必须查库：

```sql
-- 熔断状态行（表来自迁移 0083）
SELECT annotator_id, state, hits, total, window_history,
       paused_reason, paused_at, updated_at
FROM annotation_honeypot_state
WHERE project_id = :project_id
  AND annotator_id = :annotator_id;

-- 该标注员近期黄金题评估结果
SELECT submission_id, evaluated_count, passed_count, accuracy, computed_at
FROM annotation_gold_set_evaluations
WHERE project_id = :project_id
ORDER BY computed_at DESC
LIMIT 20;
```

校验要点：
- `window_history` 是 `0`/`1` 序列（最长 20），按提交顺序分窗；`hits`/`total` 是派生聚合；
- `paused_reason` 里应有「连续 N 个窗口黄金题准确率低于 0.85：a/b=xx%, …」的可解释依据；
- 若 `accuracy` 正常但 `paused_reason` 显示黄金题本身有错标 → 属数据问题，走 §4 第 1 条。

### Step 2：判定是否误伤（复核标准）

按顺序核验，全部通过才可解除：

- [ ] **状态真实**：`annotation_honeypot_state.state = 'PAUSED'`，且 `paused_reason` 非空；
- [ ] **窗口可解释**：`window_history` 按 10 条分窗后，确有连续 2 个满窗 < 85%；
- [ ] **黄金题无错**：抽查 `paused_reason`/`annotation_gold_set_evaluations` 涉及的黄金题，
      确认答案与当前标注口径一致（口径漂移需先走黄金集 `retire` + 新版本）；
- [ ] **非本人复核**：判定人不得是被申诉标注员本人（四眼原则，与复核提交一致，
      `reviews.py:148`）；
- [ ] **不越权**：QA lead 只出结论，不执行解除；解除必须 owner/平台管理员。

判定结论三分支：

| 结论 | 处理 |
|---|---|
| 误伤（数据/配置/难题段） | 执行 Step 3；黄金集问题先修数据再解除 |
| 真实质量下滑 | **不解除**；走培训/降级，保留 `PAUSED` 作为排班依据 |
| 证据不足 | 不解除，补充取证后重新判定（最长 48 小时出结论） |

### Step 3：执行解除（仅 owner / 平台管理员）

```bash
curl -sS -X POST \
  "${BASE}/projects/${PROJECT_ID}/annotation-honeypot/${ANNOTATOR_ID}/resume" \
  -H "${AUTH}"
```

期望响应：

```json
{"annotator_id":"…","state":"NORMAL","resumed":true,"checked_at":"2026-11-26T04:05:06Z"}
```

语义与坑：

- **幂等**：重复解除、本就 `NORMAL`、开关关闭、无状态行都返回 `200` 且
  `resumed=false` —— **按 `state` 渲染，不要把 `resumed=false` 当失败**；
- **403 `forbidden`**：editor/viewer 调用被拒（治理动作只开 owner/管理员）；
- **窗口清零**：解除后 `hits/total/window_history` 清空，从空窗口重新累计，
  **不回放历史命中**；PAUSED 区间的结算复核由结算侧按区间自理，本端点不回改；
- **不写审计**：`resume()` 目前没有 `audit_logs` 写入，操作人必须手工留证（见 Step 4）；
- **开关关闭时**：端点退化为只读幂等（返回当前 state + `resumed=false`），此时
  不存在"解除"效果，需先确认 `DDP_HONEYPOT_BREAKER_ENABLED` 状态。

### Step 4：验证与留证（QA lead）

```bash
# 4.1 幂等复核：应返回 state=NORMAL、resumed=false
curl -sS -X POST \
  "${BASE}/projects/${PROJECT_ID}/annotation-honeypot/${ANNOTATOR_ID}/resume" \
  -H "${AUTH}"

# 4.2 状态行复核（只读 SQL）：state 应为 NORMAL，hits/total 为 0，window_history 为空
#    SELECT annotator_id, state, hits, total, window_history, paused_at
#    FROM annotation_honeypot_state WHERE project_id=:p AND annotator_id=:a;
```

工单归档必填：操作人、UTC 时间、`request_id`、解除前后 `state`、
`paused_reason` 快照、结论分支、后续排班调整。

---

## 4. 复核标准细则（按结论落地）

1. **黄金题数据问题**（错标 / 口径漂移 / 版本错误）
   - 黄金集在 `ACTIVE` 态内容**不可原地改**：先 `POST .../gold-sets/{gold_set_id}/actions/retire`，
     再建新版本并 `activate`；
   - 修完数据后执行解除；
   - 受影响的其他标注员需按同一结论批量复核（同一黄金集版本可能误伤多人）。
2. **窗口难题集中**
   - 对比同批标注员的 `paused_reason`；若同期多人命中同一段难题，按"系统性难题段"处理；
   - 解除 + 排班调整（降低该段难度或换任务）；
   - 不修改领域阈值（85%/2 窗是代码常量，不在配置层开放）。
3. **真实质量下滑**
   - 保持 `PAUSED`；不提供"证明恢复后自动解除"的机制，只能人工 `resume`；
   - 走培训 / 降级 / 换项目流程，并在结算侧按 PAUSED 区间复核。

---

## 5. 回滚与止损

| 场景 | 操作 | 备注 |
|---|---|---|
| 熔断误伤面扩大（批量误判） | 设 `DDP_HONEYPOT_BREAKER_ENABLED=false` 并重启服务 | 立即回到旧行为：派单不查、状态行不写、resume 只读幂等 |
| 单点误伤 | 走 §3 解除 | 优先，不影响他人 |
| 数据回滚 | **优先不删表**；如必须回退迁移，注意链序 `0078→0081→0080→0083→0082`：`0083` 的 `down_revision` 是 `0080`，而 `0082`（回流表）又排在 `0083` 之后，所以 `alembic downgrade 0080` 会**连带删除 0082 回流表** | **需按迁移审批流程单独批准 + 先做备份**；`honeypot-runbook.md` 写的 `downgrade 0081` 会连带回退 0080/0083/0082，勿照抄 |
| 代码回滚 | revert 本能力 PR | 迁移 0083 与接线同批；revert 后先按上一条处理迁移再回代码 |

---

## 6. 已知缺口（诚实声明）

| 缺口 | 影响 | 现状 |
|---|---|---|
| 无熔断状态读端点 | 标注员不能自助查询；运营需查库 | 仅有 `POST /api/v1/projects/{project_id}/annotation-honeypot/{annotator_id}/resume` |
| `resume` 不写审计 | 解除动作不留 `audit_logs` | 应用层 `honeypot_wiring.resume()` 无审计写入，必须人工留证 |
| 无自动解冻策略 | PAUSED 后只能人工处理 | 领域层无"观察期自动恢复" |
| 阈值不可配 | 85% / 2 窗 / 10 条由代码默认值钉住（接线层未开放配置） | 属有意设计（避免配置漂移），不是遗漏 |

---

## 7. 验证命令（写文档时已执行的 grep）

```bash
grep -rn "annotation-honeypot" backend/app/api/v1/
grep -n "honeypot_breaker_enabled" backend/app/core/config.py      # DDP_HONEYPOT_BREAKER_ENABLED 来源（env_prefix=DDP_）
# 注意：字面量 grep "HONEYPOT_BREAKER" backend/app/core/config.py 无匹配——
# 环境变量名由「DDP_ + 字段名」拼出，不是以全大写字面量写在配置里。
grep -n "PAUSE_THRESHOLD\|MIN_WINDOW\|CONSECUTIVE_WINDOWS" backend/app/domain/annotation/honeypot_breaker.py
grep -n "WINDOW_SIZE_DEFAULT" backend/app/application/annotation/honeypot_wiring.py
grep -n "__tablename__" backend/app/infrastructure/db/models/annotation_honeypot_state.py
```
