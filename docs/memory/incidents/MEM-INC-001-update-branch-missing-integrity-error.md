### MEM-INC-001 | 仓储 UPDATE 分支漏捕获 IntegrityError 导致无限重试

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-24`
- **适用范围**：项目 `DDP` | 环节 `数据摄入/校验` | 批次/资产 `全部 RawAsset` | 密级 `INTERNAL`
- **核心结论**：`SqlRawAssetRepository.save()` 的 UPDATE 分支原本**只翻译了 `add()` 的 `IntegrityError`，UPDATE 路径没有捕获**，导致 `UniqueViolation` 直接冒到 Worker 被判定为「可重试」，而它**永远不可能成功**。任何「会把状态迁入部分唯一索引条件」的更新都必须翻译冲突异常。
- **关键依据与细节**：
  - 真实事故：`ingestion.raw_asset.validation_requested` 事件永久 FAILED，`attempts` 打满
  - 索引语义：`uq_raw_assets_project_content_active` 是**部分唯一索引**（只覆盖活跃状态），`VALIDATING → VALID` 是**进入**索引条件，若已有同内容活跃资产必然撞索引
  - 三层根因链：① 上传完成时内容去重按 `(sha256,size_bytes)` 查一次，并发时双双查不到对方，各留一个 VALIDATING；② 转 VALID 时第二个撞索引；③ 异常未翻译 → 无限重试
  - 修复：仓储统一走 `_flush_or_conflict`，冲突翻译为 `ConflictError`；用例捕获后**墓碑化本资产并指向已有活跃资产**（outcome=`DUPLICATE`）
  - **关键陷阱**：不能用 `begin_nested()` 包正常写入路径——savepoint 正常退出会 release，破坏「审计失败→整体回滚」原子性（被 `test_audit_failure_rolls_back_business_and_retries` 当场抓住）
  - 修复后真实栈重放：事件 FAILED → **PUBLISHED**，卡死资产 VALIDATING → **TOMBSTONED + duplicate_of**
- **影响与后续动作**：
  - 所有「状态迁移会进入部分唯一索引」的仓储写入点都需复查是否翻译了 IntegrityError
  - 已加回归测试；新增同类仓储时必须走 `_flush_or_conflict` 模式
  - 责任人：后端；**已完成**（PR #100）

---

#### 工程扩展字段

- **check_ref**：`backend/tests/workers/test_outbox_worker.py::test_content_duplicate_is_tombstoned_not_failed`
- **recurrence**：`1`
- **tags**：`outbox,raw_asset,integrity-error,unique-index,worker,retry,savepoint`
