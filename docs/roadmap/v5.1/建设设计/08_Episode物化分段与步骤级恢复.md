# 08｜Episode 物化、分段和步骤级恢复

## 1. Episode 定义
Episode 是可独立标注、质检和训练的时间片段，不能简单等同于一个文件。一个 Session 可产生多个 Episode；多个流共同组成一个 Episode。

## 2. 分段依据
源数据 episode marker、任务开始/结束事件、控制状态变化、人工确认边界、媒体时间范围、明确的 pipeline segmentation rule。不能仅凭固定时长切片替代任务语义，除非项目规则明确允许。

## 3. Episode 字段
episode_id、capture_session_id、collection_task_id、start/end raw timestamp、normalized start/end、time_base_id、stream refs、source asset refs、segmentation rule/version、processing_run_id、calibration snapshots、quality status、creation provenance。

## 4. 分段预览
处理前展示预计 Episode 数、每段时间范围、必需流覆盖、空片段/过短片段、时间范围不一致、可能重复/重叠、分段规则和版本。人工调整后生成新的分段配置版本。

## 5. 步骤级 ProcessingRun
INPUT_VALIDATE → FORMAT_DETECT → METADATA_PARSE → STREAM_EXTRACT → TIME_NORMALIZE → CANONICAL_MAP → SYNC → SEGMENT → CLEAN → QC_PRECHECK → REGISTER_ARTIFACTS。
每步记录 step_id、status、attempt、input hash、config hash、output artifact IDs、started/finished、error code、retryable、worker identity。

## 6. 幂等规则
幂等键至少由 input asset hash + parser version + pipeline version + config hash + step identity 构成。相同输入和配置重复执行应复用已成功产物。不同配置产生新版本，不覆盖历史。

## 7. 失败恢复
单步失败仅重试失败步骤及其下游受影响步骤；上游输入变化使依赖步骤失效；已成功 Artifact 不可被失败重试覆盖；Worker 崩溃后租约过期恢复；永久失败隔离并要求人工处理，不无限重试。

## 8. 验收
重复执行不产生产物分叉；中断后从正确步骤恢复；分段边界可解释；所有 Episode 可追溯到 RawAsset 和 ProcessingRun。
