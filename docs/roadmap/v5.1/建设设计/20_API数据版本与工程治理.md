# 20｜API、数据版本与工程治理

## 1. 架构复用
沿用 domain → application → infrastructure → api / workers 分层；`data-platform` 承载纯数据处理能力；backend 不直接引入 pyarrow；沿用 PostgreSQL、MinIO、Outbox、Worker、现有 RBAC。

## 2. 设计前检查
每项需求先检查已有 Domain Model、Service、API、Migration、Frontend Hook、Worker、ADR 和测试。提交复用映射，避免重复实现。

## 3. 数据版本
Parser、Adapter、Pipeline、Cleaning RuleSet、Time Sync Config、Calibration Snapshot、Canonical Schema、Label Schema、QC RuleSet、LeRobot Writer、Dataset Manifest 必须版本化。

## 4. Migration 纪律
不修改历史 Migration；先证明字段/索引/关系不存在；Migration 有 upgrade/downgrade；在真实 PostgreSQL 执行 upgrade → test → downgrade → test → upgrade → test；大对象不进入 OLTP 主表；引用保护优先使用约束/事务级校验，不依赖前端按钮。

## 5. API 契约
OpenAPI 变更必须同步 TS Types、Client、contract baseline、oasdiff、strict parity。新增接口说明为什么不能复用已有路径。兼容性优先。

## 6. Outbox / Worker
沿用当前 Outbox；事件必须有消费者、幂等、测试和文档登记。不要为每个处理步骤创建独立消息系统。长任务需状态可见、可取消（安全时）、可重试、可追踪。

## 7. 错误规范
返回稳定 error_code、field_path、request_id、trace_id、retryable、remediation。禁止吞异常、空成功、静默跳过和把入队回执当处理结论。
