### MEM-INC-033 | 处理器读 `run.input_asset_ids` 而 `ProcessingRun` 只有 `input_refs`——隐性契约断裂让阶段永远静默跳过

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-28`
- **适用范围**：项目 `DDP` | 环节 `处理流水线/阶段处理器` | 批次/资产 `backend/app/application/processing/timestamp_normalize_stage.py`、`video_decode_stage.py` | 密级 `INTERNAL`
- **核心结论**：`TimestampNormalizeHandler._pending_rows` 读 `run.input_asset_ids`，但领域实体 `ProcessingRun`（`domain/processing/models.py`）只有 `input_refs`（对象键元组）——`getattr(run, "input_asset_ids", None) or ()` 恒为空，归一化阶段在生产里**永远静默 SKIPPED**，单测却全绿（测试的 `_FakeRun` 恰好提供了这个不存在的字段）。这是「能力存在、接线断裂、测试用例与断裂同构」的三重盲区（MEM-INC-027 的变体）。
- **关键依据与细节**：
  - 实测（2026-11-28，PR #177）：真实装配下 3 帧视频解码落库成功，归一化列全 NULL；步骤日志「跳过：当前运行没有待归一化的帧行」。
  - 修法（两层来源，先显式后推导）：先取 `run.input_asset_ids`（测试/未来编排器扩展），为空再按 `(project_id, input_refs)` 反查 `raw_assets` 得资产 ID——与生产组合根注入的 `RawAssetVideoResolver` 同口径。
  - 同族缺陷：`VideoDecodeHandler._video_inputs` 的默认查询走 `MediaAssetModel`（`input_asset_ids` 同样恒空），生产必须注入 `VideoInputResolver`；默认路径只在"未来有人给 run 挂上资产 ID"时才生效。
  - **测试同构盲区**：当生产代码 `getattr` 一个不存在的字段并静默回退空值时，用"同样提供该字段"的 Fake 来测，等于把断裂固化进测试。集成测试必须走**真实领域实体**（或真实 Worker 装配），不能只用 Fake run。
- **影响与后续动作**：
  - 新增阶段处理器时，先核对 `StageContext.run` 领域实体的**真实字段**，不确定就写 `getattr(run, "field", None) is None` 的显式分支 + 集成断言「生产字段路径下阶段非 SKIPPED」。
  - 复查既有处理器是否存在同类「读不存在字段」：`grep -rn 'getattr(run' backend/app/application/processing/`。

#### 工程扩展字段

- **check_ref**：`backend/tests/workers/test_processing_worker_video.py`（真实装配下归一化列回填断言 `test_video_asset_decodes_to_frames_and_normalized_time`）
- **recurrence**：`1`
- **tags**：processing-pipeline,timestamp-normalize,video-chain,contract-drift,silent-skip,integration-test
