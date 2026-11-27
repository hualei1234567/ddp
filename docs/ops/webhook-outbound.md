# Webhook 对外出站运行手册（阶段 6）

> 版本：v1.0 · 2026-11-27
> 定位：对外 Webhook 投递的**启用、排查、处置**手册。领域规则（签名算法、
> 熔断阈值、事件白名单）以代码为准；本文只写「谁、在什么条件下、调哪个命令、
> 按什么标准判定、出问题怎么处置」。
> 状态标注口径见 `CLAUDE.md`「状态标记」。

---

## 1. 这条链路是什么

```
业务动作（标注提交 / 复核 / 仲裁）
   └→ record_change（同事务写审计 + outbox 事件）
        └→ OutboundRegisteringOutbox.enqueue
             ├→ 写 outbox_events（内部留痕，原有行为不变）
             └→ 登记到 webhook_outbox（对外待投递，迁移 0086）
                  └→ webhook-outbound-worker 拉取
                       └→ HMAC 签名 POST 到订阅方 URL
                            ├→ 2xx   → webhook_outbox 行标记 DELIVERED
                            └→ 非 2xx → 退避重试；连续失败达阈值 → 订阅 PAUSED
```

**与内部 outbox 的区别**（重要）：`outbox_events` 是**平台内部**事件投递
（校验/导出/导入），消费者是平台自己的 Worker；`webhook_outbox` 是**对外**
投递，消费者是外部下游系统。两者失败语义不同，故分表。详见
`docs/architecture/outbox事件语义界定-v1.0.md` §3.3。

---

## 2. 当前可用范围（不夸大）

| 项 | 状态 | 说明 |
|---|---|---|
| 订阅 CRUD（创建/列表/删除） | `VERIFIED` | `POST/GET/DELETE /api/v1/projects/{id}/webhook-subscriptions`（owner 口径） |
| 标注提交/复核/仲裁状态变更出站 | `VERIFIED` | 白名单 `annotation.submission.status_changed` / `annotation.review.status_changed` / `annotation.arbitration.status_changed` |
| 熔断（PAUSED）出站 | `MISSING` | 熔断路径**不写 outbox 事件**，因此无法登记；需先补 outbox 事件 |
| 回流受理出站 | `MISSING` | 同上，回流受理不写 outbox 事件 |
| 签名 | `VERIFIED` | `X-DDP-Signature: sha256=<HMAC-SHA256(secret, timestamp + "\n" + body)>`，`X-DDP-Timestamp` 为 Unix 秒 |
| 重试退避 | `VERIFIED` | 指数退避 `base * 2^(n-1)`，封顶 `max`；达 `max_attempts` 进死信 |
| 订阅熔断 | `VERIFIED` | 连续失败达 5 次 → 订阅自动 `PAUSED`，不再投递；需人工恢复 |
| 出站队列表归档 | `MISSING` | `webhook_outbox` 的历史清理未实现（当前数据量百级） |

---

## 3. 启用步骤

### 3.1 必需配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `DDP_WEBHOOK_OUTBOUND_ENABLED` | `false` | 总开关。关闭时登记器是 no-op，**零数据库写入** |
| `DDP_WEBHOOK_ENCRYPTION_KEY` | 空 | 订阅明文 secret 的加密密钥。**必须 ≥32 字符**，且**创建订阅与投递时必须一致** |
| `DDP_WEBHOOK_OUTBOUND_ALLOW_PRIVATE` | `false` | SSRF 守卫本地例外。**生产必须 false** |
| `DDP_WEBHOOK_OUTBOUND_MAX_ATTEMPTS` | `5` | 单条最大尝试次数，达上限进死信 |
| `DDP_WEBHOOK_OUTBOUND_RETRY_BASE_SECONDS` | `5` | 退避基准 |
| `DDP_WEBHOOK_OUTBOUND_RETRY_MAX_SECONDS` | `300` | 退避封顶 |

> **密钥不一致的后果**：订阅创建时用密钥 A 加密、投递时用密钥 B 解密 →
> 解密失败 → 记录进死信，**事件永远送不出去**，但不会静默丢失（可在
> `webhook_outbox.last_error` 与订阅的 `last_error_message` 看到）。

### 3.2 启动 Worker

```bash
# 本地/生产 compose（webhook profile）
docker compose --profile webhook up -d webhook-outbound-worker
docker compose logs -f webhook-outbound-worker
```

Worker 在 `DDP_WEBHOOK_OUTBOUND_ENABLED=false` 时**拒绝启动**（退出码 2），
避免"以为在投递"。compose 里该开关只写在这个服务内（不写进 `.env`），
理由见 `docker-compose.yml` 里该服务的注释。

### 3.3 创建订阅

```bash
BASE=http://127.0.0.1:8000/api/v1
AUTH="Authorization: Bearer ${BEARER_JWT}"

curl -sS -X POST "$BASE/projects/$PROJECT_ID/webhook-subscriptions" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{
        "url": "https://downstream.example.com/ddp-events",
        "secret": "<下游约定的验签密钥，>=8 字符>",
        "event_types": ["annotation.submission.status_changed"]
      }'
```

- **明文 secret 只在创建响应里返回一次**，之后库里只存 hash + 密文；
  下游必须自己保存明文（用于验签）。
- `url` 约束：只允许 `https://`，或**本机** `http://`（`normalize_url` 强制）。
  外联必须用 https。
- 权限：**owner** 口径（`get_owner_scope`）——editor/viewer 一律 403。
- 幂等：同一项目同一 URL 只允许一份订阅，重复创建返回 409。

---

## 4. 下游如何验签

下游收到 POST 后：

1. 取 `X-DDP-Timestamp`（Unix 秒）与 `X-DDP-Signature`；
2. 计算 `HMAC-SHA256(secret, timestamp + "\n" + body)`，前缀 `sha256=`；
3. 与 `X-DDP-Signature` 做**常量时间**比较；
4. 按需校验时间戳窗口（平台侧不做重放窗口限制，由下游决定）；
5. 解析 body 信封：

```json
{
  "event_type": "annotation.submission.status_changed",
  "event_id": "00765c3d-b49b-4780-ab5f-cf5c952ab12a",
  "project_id": "e2c87ac6-fca3-4a59-b486-37453f4da474",
  "occurred_at": "2026-11-27T11:07:00.769301+00:00",
  "data": { "...": "业务事件字段" }
}
```

> `event_id` 是**稳定的事件标识**：同一条业务事件重复投递时 `event_id` 相同，
> 下游应按它做幂等（不要按请求次数去重）。

参考实现见 `scripts/ops/webhook_outbound_drill.py` 的 `Receiver.verify`。

---

## 5. 排查手册

### 5.1 订阅一直收不到事件

按顺序排查：

1. **开关开了吗**：`GET /api/v1/projects/{id}/webhook-subscriptions` 看订阅
   `status` 是不是 `ACTIVE`（`PAUSED` 说明连续失败已被自动熔断）；
2. **事件类型在白名单吗**：只有
   `annotation.submission.status_changed` /
   `annotation.review.status_changed` /
   `annotation.arbitration.status_changed` 会出站。
   权威定义见 `backend/app/application/webhook/outbound.py` 的
   `OUTBOUND_EVENT_TYPES`；
3. **有登记记录吗**：

```sql
SELECT status, event_type, attempts, available_at, last_error
FROM webhook_outbox
WHERE project_id = '<项目 id>'
ORDER BY created_at DESC
LIMIT 20;
```

   - **0 行** → 业务事件没走到登记点：确认用的是标注提交/复核/仲裁路径，
     且 `DDP_WEBHOOK_OUTBOUND_ENABLED=true`；
   - **有行且 `status='PENDING'`** → Worker 没在跑，或 `available_at` 还没到
     （退避中）；
   - **有行且 `status='FAILED'`** → 达重试上限，看 `last_error`；
4. **Worker 在跑吗**：`docker compose ps webhook-outbound-worker` +
   `docker compose logs webhook-outbound-worker`；
5. **订阅状态**：

```sql
SELECT status, consecutive_failures, last_error_message, last_success_at
FROM webhook_subscriptions
WHERE id = '<订阅 id>';
```

### 5.2 订阅被自动 PAUSED

连续失败达 5 次（`FAILURE_PAUSE_THRESHOLD`）自动暂停。

处置顺序：

1. 先看 `last_error_message` 判断是**下游挂了**（5xx / 超时）还是
   **平台侧配置错了**（解密失败 / URL 守卫拒绝）；
2. 下游恢复后**手动恢复订阅**——平台**不会**自动恢复（自动恢复会在下游
   长时间故障时反复打它）；
3. 「URL 守卫拒绝」说明订阅地址是环回/私网/链路本地/云元数据地址。
   这类地址在生产**必须拒绝**；只有本地联调才开
   `DDP_WEBHOOK_OUTBOUND_ALLOW_PRIVATE=true`。

> 当前**没有**恢复订阅的 HTTP 端点（`docs/ops/README.md` §5 已登记该缺口）。
> 恢复方式：更新 `webhook_subscriptions` 行的 `status='ACTIVE'`、
> `consecutive_failures=0`，并清空 `last_error_message`。

### 5.3 死信（`webhook_outbox.status='FAILED'`）

达 `max_attempts` 后不再自动投递。处置：

1. 读 `last_error` 判断根因；
2. 根因修复后，把该行改回 `PENDING`、`attempts=0`、
   `available_at=now()`，Worker 会重新投递；
3. **不要**盲目重置——重置前必须确认下游已恢复，否则会再打一轮。

### 5.4 订阅 URL 被拒（守卫）

```
Webhook 投递 的地址 127.0.0.1 解析到环回地址 127.0.0.1，已拒绝
```

这是 **fail-closed 的正确行为**。生产环境不要为了"让它通"而开例外开关。

---

## 6. 生产接入清单

- [ ] `DDP_WEBHOOK_OUTBOUND_ENABLED=true`（只在 webhook worker 服务内设）
- [ ] `DDP_WEBHOOK_ENCRYPTION_KEY` 由 Secret 注入，≥32 字符，
      **与创建订阅时用的密钥一致**
- [ ] `DDP_WEBHOOK_OUTBOUND_ALLOW_PRIVATE=false`（生产**必须**）
- [ ] 订阅 URL 使用 `https://`，且解析到公网地址
- [ ] 下游实现 HMAC 验签 + 按 `event_id` 幂等
- [ ] 确认出站事件类型都在白名单内（否则下游收不到）
- [ ] `webhook-outbound-worker` 容器在跑且不反复重启
- [ ] 监控：`webhook_outbox` 的 `FAILED` 数量与订阅的 `PAUSED` 数量需有告警
      （**当前尚未接入告警**，见下）

---

## 7. 已知缺口（诚实声明）

| 缺口 | 影响 | 现状 |
|---|---|---|
| 没有恢复订阅的 HTTP 端点 | 订阅被熔断后只能改库恢复 | `webhook_routes.py` 只有 create/list/delete |
| `webhook_outbox` 无归档 | 长期运行会累积历史行 | 当前数据量百级，未实施 |
| 熔断/回流事件不出站 | 下游收不到熔断与回流信号 | 这两类事件不写 outbox，需先补事件 |
| 出站队列无 Prometheus 指标 | 积压与死信需查库才知道 | `metrics.py` 未暴露该口径 |
| `webhook_outbox` 无告警规则 | 死信堆积不会被叫醒 | `alerts.yml` 未覆盖该表 |

---

## 8. 复现与验证

```bash
# 生产演练（真实 HTTP 投递 + 验签 + 自动清理演练数据）
backend/.venv/bin/python scripts/ops/webhook_outbound_drill.py \
  --dsn postgresql+psycopg://ddp:ddp@localhost:55432/ddp --execute

# 自动化测试
cd backend && .venv/bin/python -m pytest \
  tests/migrations/test_webhook_outbox_migration.py \
  tests/application/test_webhook_outbound.py \
  tests/workers/test_webhook_outbound_worker.py \
  tests/api/test_webhook_outbound_e2e_api.py -q
```

演练证据见 `docs/testing/evidence/2026-11-27-webhook-outbound/`。
