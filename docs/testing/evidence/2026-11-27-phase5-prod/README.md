# 阶段 5 生产环境验证证据（2026-11-27）

> 不设 staging，全部在 prod 验证。生产 = 本机 docker-compose 数据面
> （PG 16.16 @ 55432、MinIO @ 9000）+ 真实 API 进程。

## 一、迁移 0085（webhook 订阅表）

按「迁移五步法」执行：

| 步骤 | 结果 |
|---|---|
| 1 离线 SQL 审查 | ✅ `alembic upgrade head --sql` 输出可读 DDL |
| 2 备份点 | ✅ `ddp-pre0085-1106.dump`（5.7 MB） |
| 3 一次性验证库往返 | ✅ `ddp_chk85`：`0084 → 0085 → 0084 → 0085` 全通后 DROP |
| 4 生产实跑 | ✅ `0084 → 0085` |
| 5 落地确认 | ✅ `version_num=0085`，`webhook_subscriptions` 表存在 |

## 二、Webhook 订阅端到端（真实 API + 真实 PG）

### 创建
```
POST /api/v1/projects/{pid}/webhook-subscriptions
→ 201 {"id":"372a07b8-...","url":"https://hook.example.com/notify",
       "event_types":["annotation.completed"],"status":"ACTIVE",
       "consecutive_failures":0,"secret":"example-prod-key-0123456789"}
```
**明文 secret 仅此一次返回**（创建响应含，后续列表/详情都没有）。

### 列表（不得暴露 secret）
```
GET /api/v1/projects/{pid}/webhook-subscriptions
→ 200 {"items":[{"id":"372a07b8-...","url":"...","status":"ACTIVE",...}]}
```
响应中**既无明文 secret 也无 hash**。

### 库内形态（直接查 PG）
```
select url, status, left(secret_encrypted,20) as enc, length(secret_hash) from webhook_subscriptions;
→ https://hook.example.com/notify | ACTIVE | aNiAymXkLAv5iW6L4Zor | 64
```
即：`secret_encrypted` 是**密文**（base64 密文流），`secret_hash` 是 64 位 SHA-256，
明文不落库。

### 删除
```
DELETE /api/v1/projects/{pid}/webhook-subscriptions/{id} → 204
→ 库内剩余 0 条（演练数据已清理）
```

## 三、发现的真实缺陷（已修复）

**未配置加密密钥时返回 500 internal_error**：生产首次调用即暴露。日志显示
`RuntimeError: 未配置 DDP_WEBHOOK_ENCRYPTION_KEY`。

问题在于**部署配置缺失被当成内部故障**：
- 500 把"运维要配环境变量"伪装成"平台内部故障"，排障方向被带偏；
- 422 会暗示"改请求参数就能好"——同样不对，调用方无法解决。

修复：新增领域错误 `WebhookNotConfiguredError`（code=service_unavailable），
标注域错误表登记 → **503**；并加 API 测试断言。修复后生产复测通过（本次演练
已配 `DDP_WEBHOOK_ENCRYPTION_KEY`）。

## 四、阶段 5 其余交付的生产/本地实测

| Track | 实测 |
|---|---|
| A 训练闭环 | SFT loss **73.5495 → 9.3581**；DPO 判对率 **0.5 → 1.0**；同种子两次运行一致 |
| B Webhook | 本节（创建/列表/删除 + 库内密文 + 503 语义） |
| C 维护补测 | `workers/maintenance.py` 覆盖率 **0% → 79%**（12 项测试） |
| D 版本对比 | 端点 + 5 项测试；血缘查询复核确认既有能力（深度上限 3、节点上限 100、显式 truncated） |

## 五、诚实边界

- Webhook **未接真实下游**：投递路径用假 transport 验证（不发网络），
  真实下游需要用户提供可访问的 URL 与自验签实现
- 生产库数据量仍小（最大表 240 行），压测基线不代表大数据量
- 迁移 0085 的 downgrade 已在验证库验证，**未在生产库实际执行 downgrade**
  （生产不做破坏性回滚演练）
