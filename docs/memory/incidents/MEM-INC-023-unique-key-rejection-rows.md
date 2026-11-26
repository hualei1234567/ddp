### MEM-INC-023 | 唯一键表的"拒绝类判决"不能落库——重复跳过/限流写入会撞自身唯一键

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-26`
- **适用范围**：项目 `DDP` | 环节 `幂等接口/领域判定/仓储写入` | 批次/资产 `backend/app/application/annotation/backflow.py` | 密级 `INTERNAL`
- **核心结论**：表上若有业务唯一键（如 `(source, case_ref)`），**拒绝类判决（重复跳过、限流拒绝）不能再写同键的行**——它们与既有受理行同键，插入必撞唯一约束，最终被兜底逻辑翻译成 409，把"幂等成功"变成"报错"。正确做法：拒绝判决只回响应（含逐条 reason），不落库；或改唯一键为部分唯一索引（只约束受理状态）。
- **关键依据与细节**：
  - 生产实测（2026-11-26，生产库升级到 0082 后的端到端演练）：同一 BadCase 第二次回调返回 `409 duplicate_resource`，而契约承诺"重发不报错，判 DUPLICATE_SKIPPED"。
  - 根因：`ingest` 对 DUPLICATE_SKIPPED / RATE_LIMITED 也调 `add_skipped` 写行留痕，与既有受理行同 `(source, case_ref)`。
  - 修复（PR #143）：移除跳过行的写入与仓储死代码；限流同理（风暴期逐条写拒绝行反而是写放大）；补回归测试断言"重复回调后表内仍只有一条受理行"。
  - 副带收益：消除了限流风暴下的写放大。
- **影响与后续动作**：
  - 设计任何"接收外部回调 + 幂等键"的表时，先问：**拒绝路径会不会写同键行**？会就不要写。
  - 若确实需要留痕审计，用独立审计表/outbox，不要与业务唯一键表混存。

#### 工程扩展字段

- **check_ref**：`backend/app/application/annotation/backflow.py`
- **recurrence**：`1`
- **tags**：idempotency,unique-constraint,webhook,write-amplification,409
