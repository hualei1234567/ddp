### MEM-INC-027 | 「有能力无接线」是比「缺能力」更隐蔽的缺口——Webhook 投递用例曾无任何生产调用点

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-27`
- **适用范围**：项目 `DDP` | 环节 `能力交付/接线审计` | 批次/资产 `backend/app/application/webhook/` | 密级 `INTERNAL`
- **核心结论**：阶段 5 交付了 Webhook 订阅 CRUD 与投递用例 `deliver_event`，且**全部单测通过**——但 `deliver_event` 在生产代码里**没有任何调用点**。能力「看起来有」而实际从不发生。**审计「能力是否真的接线」要 grep 调用点，而不是看模块与测试是否存在。**
- **关键依据与细节**：
  - 实测（2026-11-27）：`grep -rn "deliver_event" backend/app backend/tests` 的结果**只出现在 `delivery.py` 定义处与 `tests/application/test_webhook_delivery.py`**，`backend/app/` 下零调用点。
  - 根因：阶段交付以「模块 + 单测」为单位验收，缺少「从业务写入到能力生效」的端到端链路验收。
  - 修法（阶段 6）：
    1. 新增独立 `webhook_outbox` 队列表（迁移 0086），与内部 `outbox_events.dispatch` 通道**分表**——两者失败语义不同（对外连续失败 → 订阅 PAUSED；内部 → 死信），混表会让「订阅熔断」看起来像普通内部死信。
    2. 登记点收敛到**唯一写入点**：包装 outbox 仓储 `OutboundRegisteringOutbox`（`record_change → outbox.enqueue → 登记器`）。业务用例**零签名变更**，30+ 个事件写入点不会漏接。
    3. 出站白名单只在登记器一处判断（`OUTBOUND_EVENT_TYPES`），敏感事件（黄金题答案访问等）不入白名单。
  - 同族判据：任何「异步能力」交付时都要回答「谁在什么事务里触发它」。
- **影响与后续动作**：
  - 新增异步能力（推送/回调/导出/通知）时，PR 描述里必须写清**触发点**（哪个业务动作、在哪个事务内），并有一条从业务动作到能力生效的端到端用例。
  - 审计时对每个「已有能力」做一次调用点 grep，零调用点即记为 `PARTIAL`（有能力、无接线），不能记为 `VERIFIED`。

#### 工程扩展字段

- **check_ref**：`backend/app/application/webhook/outbound.py`（对外登记器与白名单唯一权威；接线见 `backend/app/infrastructure/repositories/webhook_outbound_wiring.py`）
- **recurrence**：`1`
- **tags**：capability-wiring,outbox,webhook,audit-gap,dead-code,idempotency
