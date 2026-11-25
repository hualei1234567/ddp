### MEM-INC-011 | 绕过用例直接建 Run 会导致 Pipeline 版本冲突

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `处理运行/编排` | 批次/资产 `ProcessingRun` | 密级 `INTERNAL`
- **核心结论**：`ProcessingPipeline.execute()` 依赖 `ProcessingRun.row_version` 做乐观锁。若调用方**绕过用例**（直接用仓储创建 Run 并手工 commit），后续 execute 会在内部 `_apply_internal` 撞 `VersionConflictError`（"处理运行版本冲突"），表现为 `ConcurrentRunUpdate`。
- **关键依据与细节**：
  - 实测场景（V5.1 全链路补测）：用 `ProcessingRunUseCases.create()` 建 Run 后，直接用
    `worker._build_pipeline(session).execute(scope, rid)` 执行，稳定报版本冲突
  - 更换 session、重试均无效——说明不是 stale session，而是**状态机前提被绕过**
  - 根因：Pipeline 的多个步骤（advance/step 状态推进）会读写 `row_version`，
    而绕过用例创建的 Run 可能缺少某些初始化字段（如 steps 预置、stage 初值）
  - **正确做法**：走 Worker 的 `process_once()` 事件驱动路径——它从 Outbox 事件
    领取任务，内部按既定顺序推进，不依赖外部手工构造的状态
  - 这与 MEM-INC-001 的教训同源：**绕过既有的用例/仓储边界去"直接操作"，
    会跳过那些看起来无关但实际必需的初始化**
- **影响与后续动作**：
  - 任何"为了测试方便"直接调内部 Pipeline 的做法都不可靠
  - 补全链路证据时如未走通 Worker 路径，必须在证据里**显式标注**
    （本次已标注"直接调 handler，未走完整 Pipeline 编排"）
  - 责任人：后端；**已记录，正确路径为 process_once 事件驱动**

---

#### 工程扩展字段

- **check_ref**：`backend/tests/workers/test_processing_worker.py::test_worker_consumes_requested_event_and_runs_all_stages`（正是走 process_once 的正确路径）
- **recurrence**：`1`
- **tags**：`processing-run,pipeline,optimistic-lock,version-conflict,bypass,boundary`
