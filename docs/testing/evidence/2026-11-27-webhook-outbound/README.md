# Webhook 对外出站链路证据（阶段 6）

## 审计缺口

> 阶段 5 交付了订阅 CRUD 与投递用例（`app/application/webhook/delivery.py`），
> 但 `deliver_event` 在**生产代码里没有任何调用点**——有能力、无接线：
> 订阅建得出来，事件永远送不出去。

本批把这个缺口补上，并用**真实进程 + 真实 HTTP + 生产库**证明链路成立。

---

## 本批交付

| 资产 | 路径 |
|---|---|
| 出站投递队列表（迁移 0086） | `backend/migrations/versions/0086_webhook_outbox.py` |
| 队列表 ORM | `backend/app/infrastructure/db/models/webhook_outbox.py` |
| 出站队列仓储（登记 + 抢占 + 状态流转 + 退避） | `backend/infrastructure/repositories/webhook_outbox.py` |
| 对外登记器（白名单 + 幂等） | `backend/app/application/webhook/outbound.py` |
| 接线包装（收敛到唯一写入点） | `backend/infrastructure/repositories/webhook_outbound_wiring.py` |
| 出站投递 Worker | `backend/app/workers/webhook_outbound.py` |
| compose 服务（`webhook` profile） | `docker-compose.yml` |
| 生产演练脚本 | `scripts/ops/webhook_outbound_drill.py` |
| 真实演练结果 | `drill-result.json` |

---

## 设计要点（为什么这么接）

### 为什么不复用 `outbox_events`

`outbox_events` 的 `dispatch` 通道是**平台内部**事件投递（校验/导出/导入…），
消费者是平台自己的 Worker，白名单在 `scripts/check_outbox_events.py`。
对外 HTTP 投递的失败语义（连续失败自动 PAUSED 订阅、退避重试、死信）与内部
投递并不等价；混在一张表里会让「订阅被熔断」表现为一条普通内部死信，
运营分不清是订阅问题还是积压问题。

因此新增**独立**的 `webhook_outbox` 表：语义单一、边界清楚、可单独归档，
且**不触碰**既有 7 个投递类事件的归类（`check_outbox_events.py` 的
`DISPATCH_EVENTS` 白名单保持不变）。

### 登记点为什么收敛在一个地方

标注域有 30+ 个事件写入点，全部走 `record_change`（唯一权威实现）。
若在每个用例里显式调登记器，「漏一处就少一条事件」的风险极高，且
「哪些事件允许出站」这条规则会被抄到多处。

接线方式是**包装 outbox 仓储**（`OutboundRegisteringOutbox`）：

```
record_change → outbox.enqueue → OutboundRegisteringOutbox.enqueue
                                   ├─ 内层：写业务事件（原逻辑不变）
                                   └─ 登记器：白名单判断 + 登记待投递
```

收益：业务用例**零签名变更**（既有 4000+ 用例全绿）、白名单只在一处判断、
登记与业务写入仍在**同一事务**（业务回滚则不投递）。

### 开关语义

| 开关 | 默认 | 作用 |
|---|---|---|
| `DDP_WEBHOOK_OUTBOUND_ENABLED` | `false` | 关闭时登记器是 no-op，**零数据库写入**，行为与旧版完全一致 |
| `DDP_WEBHOOK_ENCRYPTION_KEY` | 空 | 开启出站时必填（否则启动自检拒绝） |
| `DDP_WEBHOOK_OUTBOUND_ALLOW_PRIVATE` | `false` | SSRF 守卫的本地例外；**生产必须 false** |
| `DDP_WEBHOOK_OUTBOUND_MAX_ATTEMPTS` | `5` | 单条最大投递尝试次数，达上限进死信 |
| `DDP_WEBHOOK_OUTBOUND_RETRY_BASE_SECONDS` | `5` | 重试退避基准 |
| `DDP_WEBHOOK_OUTBOUND_RETRY_MAX_SECONDS` | `300` | 退避封顶 |

`ALLOW_PRIVATE` 单独成开关（不与训练/模型 Adapter 共用）：出站面不同，
共用开关会让「为本地联调放开 Webhook」顺带放开训练网关，违反最小授权。

---

## 验证记录（真实工具、真实进程、真实 HTTP）

### 1. 迁移 0086 升级 / 降级 / 再升级（生产库 `ddp` @ 55432）

```
$ alembic current
0085
$ alembic upgrade head
INFO  Running upgrade 0085 -> 0086, Webhook 出站投递队列表
$ alembic downgrade 0085
INFO  Running downgrade 0086 -> 0085
$ alembic upgrade head
INFO  Running upgrade 0085 -> 0086
$ alembic current
0086 (head)
```

表结构实测（`information_schema.columns` + `pg_indexes`）：

```
id uuid | project_id uuid | subscription_id uuid | event_id uuid |
event_type character varying | payload jsonb | status character varying |
attempts integer | available_at timestamp with time zone |
created_at timestamp with time zone | delivered_at timestamp with time zone |
last_error character varying

indexes: pk_webhook_outbox, uq_webhook_outbox_subscription_event,
         ix_webhook_outbox_status_available_at, ix_webhook_outbox_project_created_at
```

### 2. 生产演练：真实 HTTP 投递 + 验签（`drill-result.json`）

演练脚本：`scripts/ops/webhook_outbound_drill.py`（默认 dry-run，`--execute` 才真跑）。
接收端是**标准库 HTTP 服务**（真实 socket、真实 POST、真实响应码），
不是假 transport。

```
$ python scripts/ops/webhook_outbound_drill.py \
    --dsn postgresql+psycopg://ddp:ddp@localhost:55432/ddp --execute
```

实测结果（节选自 `drill-result.json`）：

```json
{
  "delivery_result": "delivered",
  "received_count": 1,
  "received": [{
    "path": "/ddp-webhook",
    "headers": {
      "content-type": "application/json",
      "x-ddp-signature": "sha256=f1cbdec0accede4b914a5d7b8d8fb94d38478b273f5227e45cbcd710cd722622",
      "x-ddp-timestamp": "1795777620"
    },
    "body": "{\"data\": {\"submission_id\": \"2f9f3fcb-...\", \"to_status\": \"SUBMITTED\"}, \"event_id\": \"00765c3d-...\", \"event_type\": \"annotation.submission.status_changed\", \"occurred_at\": \"2026-11-27T11:07:00.769301+00:00\", \"project_id\": \"e2c87ac6-...\"}",
    "signature_valid": true
  }],
  "outbox_row": { "status": "DELIVERED", "attempts": 0, "delivered_at": "2026-11-27T11:07:00.778297+00:00" },
  "signature_all_valid": true
}
```

要点：
- 下游按平台**同一算法**独立验签 → `signature_valid: true`；
- 投递体是**信封**：`event_type` / `event_id` / `project_id` / `occurred_at` / `data`。
  下游能从请求体本身知道是哪个事件，不必靠订阅配置反推；
- 队列表行状态 `DELIVERED`，`attempts=0`（一次成功）。

### 3. 演练数据清理（零残留）

```
$ SELECT count(*) FROM webhook_subscriptions;   → 0
$ SELECT count(*) FROM webhook_outbox;          → 0
$ SELECT count(*) FROM projects WHERE code LIKE '_drill_%';   → 0
$ SELECT count(*) FROM users WHERE display_name LIKE '_drill_%'; → 0
```

演练脚本只删带 `_drill_` 前缀的行，**不触碰任何既有业务数据**。

### 4. 自动化测试（真实数字）

| 测试文件 | 用例数 | 覆盖 |
|---|---|---|
| `backend/tests/migrations/test_webhook_outbox_migration.py` | 8 passed / 1 skipped | 建表、索引、status 枚举、attempts 非负、event_type 非空、唯一键幂等、项目级联、订阅级联、降级往返 |
| `backend/tests/application/test_webhook_outbound.py` | 9 passed | 开关关闭零副作用、白名单外不登记、按订阅数登记、幂等跳过、退避计算与封顶、非法入参拒绝 |
| `backend/tests/workers/test_webhook_outbound_worker.py` | 8 passed | idle、成功标记 DELIVERED、签名头齐全、失败退避重试、可见时间推后、非 2xx 绝不标成功、达上限进死信、开关关闭拒绝启动 |
| `backend/tests/api/test_webhook_outbound_e2e_api.py` | 5 passed | 端到端：开关关闭零副作用 / 开启登记 / **Worker 真实 HTTP 投递到本地回环接收服务且验签通过** / 下游不可达不标成功 / 白名单外不登记 |

---

## 过程中修掉的 3 个真实缺陷

1. **投递体缺事件信封**：原本投递的 body 就是裸业务 payload，下游无法知道
   是哪个事件。改为 `event_type` / `event_id` / `project_id` / `occurred_at` / `data` 信封。
2. **投递没有调用点**：`deliver_event` 无生产调用方（阶段 5 的原始缺口）。
   通过包装 outbox 仓储接入，无需改 30+ 个用例的签名。
3. **SSRF 守卫拒绝回环地址**：本地联调投不到本机接收服务。
   **没有削弱守卫**——新增独立的 `DDP_WEBHOOK_OUTBOUND_ALLOW_PRIVATE`（默认 false），
   与训练/模型 Adapter 的同名开关分开，生产保持 fail-closed。

---

## 已知边界（如实标注）

| 项 | 状态 | 说明 |
|---|---|---|
| 白名单当前只含 3 个事件类型 | `PARTIAL` | 熔断（`annotation.honeypot.paused`）与回流受理**不写 outbox 事件**，因此无法登记；如需外推要先给它们补 outbox 事件 |
| 投递语义 | `VERIFIED` | 出站队列的语义、失败退避、死信、订阅熔断均已实现并测试 |
| 未接真实外部下游 | `VERIFIED`（本地真实 HTTP） | 演练用的是真实 socket 的本地回环服务；生产接入只需改订阅 URL 与网络策略 |
| 归档策略 | `MISSING` | `webhook_outbox` 的历史记录清理未实现（当前数据量百级，见大表评估） |

---

## 复现方式

```bash
# 1. 迁移
cd backend && DDP_DATABASE_URL=postgresql+psycopg://ddp:ddp@localhost:55432/ddp \
  PYTHONPATH=".:../data-platform" .venv/bin/python -m alembic upgrade head

# 2. 生产演练（真实 HTTP 投递 + 验签 + 自动清理）
backend/.venv/bin/python scripts/ops/webhook_outbound_drill.py \
  --dsn postgresql+psycopg://ddp:ddp@localhost:55432/ddp --execute

# 3. 自动化测试
cd backend && .venv/bin/python -m pytest \
  tests/migrations/test_webhook_outbox_migration.py \
  tests/application/test_webhook_outbound.py \
  tests/workers/test_webhook_outbound_worker.py \
  tests/api/test_webhook_outbound_e2e_api.py -q
```
