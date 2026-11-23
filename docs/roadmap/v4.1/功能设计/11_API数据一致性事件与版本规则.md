# 11｜API、数据一致性、事件与版本规则

## 1. API 现状
审计基线 OpenAPI 1.32.0、244 条路径。V4.1 不以增加 API 数量为目标。先查现有 API；等价能力必须复用，只有无法表达的业务行为才做向后兼容增量扩展。

## 2. 分层职责
Domain：状态机、不变量和值对象；Application：用例编排、事务、幂等、Outbox；Infrastructure：DB/MinIO/模型/训练 Adapter；API：认证、校验、调用用例；Frontend：展示状态和发起动作，不实现权威业务规则。

## 3. 必须保持一致的关系
CaptureSession↔CollectionTask/Robot/Device/Sensor；UploadSession↔CaptureSession/Manifest；RawAsset Hash↔对象字节；ProcessingRun↔输入输出和 Pipeline 版本；Episode↔RawAsset/ProcessingRun；AnnotationTask↔Episode/TaskSpec/LabelSchema Version；Assignment 历史不可覆盖；Submission Version 不可静默覆盖；QCRun↔Submission/RuleSet Version；DatasetVersion 冻结 Episode/Annotation/QC/Pipeline/Export 版本。

## 4. Outbox
沿用 DB Outbox + Worker，不引入新消息中间件。事件消费者必须幂等。事件携带 event_id、event_type、schema_version、aggregate_id、project_id、actor_id、occurred_at、correlation_id、payload_version。事件命名遵循当前仓库约定，不创建重复事件体系。

## 5. 幂等和并发重点
Agent 注册/配对；Session 启停；Manifest 提交；Upload ACK/Complete；RawAsset 创建；ProcessingRun 创建/重试；Episode 物化；Annotation Draft；Submission；QC Run；Batch Rerun；Dataset Freeze；ExportJob；Training Request。沿用现有 Idempotency / If-Match。

## 6. 数据库
审计时 Alembic head 0070。开发前重新核对最新 head。历史 Migration 禁止修改；只有必要字段/关系确实不存在才加 Migration；执行 PostgreSQL upgrade→test→downgrade→test→upgrade→test。避免为每个 UI 字段单独加列。

## 7. 契约
API 变化同步 OpenAPI、oasdiff、TS Types、Client、contract baseline、parity strict。Canonical Schema 变化同步共享 JSON Schema 和验证测试。

## 8. 错误
统一 error_code、message、details、field_path、request_id、trace_id、retryable。前端提示具体恢复动作；不得回显服务端堆栈、Token 或签名 URL。
