# Dataset Builder、版本冻结、Manifest 与训练数据导出

## 1. 目标

让数据负责人能从 Accepted Episode + Accepted AnnotationResult 构建可复现训练集。Dataset 是逻辑集合，DatasetVersion 是不可变快照。

## 2. Dataset Builder 页面

筛选 Project、CollectionTask、Robot/Device、Scenario/Action、Task Instruction、Episode duration、Annotation Schema/Version、Review Status、Quality status、Source/License 和 Date range。

展示候选 Episode 数、已通过 QC 数、已审核通过数、不符合门禁数及原因、预计导出体积、标签分布、场景/动作覆盖、设备/视角覆盖和重复样本检查。

## 3. 纳入规则

默认只纳入 RawAsset VERIFIED、Processing READY、Episode READY、Annotation Result SUBMITTED 且符合指定 Schema、Review ACCEPTED、Quality Gate PASS、Source License 可用的数据。

草稿、待审核、返工中、隔离区数据默认排除。

## 4. 划分策略

支持 Train / Validation / Test。优先按 Episode / CaptureSession / Subject / Scene group 切分，避免相邻帧或同一连续采集会话泄漏到不同集合。保存 split_strategy、random_seed、group_key、ratio 和 exclusions。不能只做 frame-level random split。

## 5. DatasetVersion 冻结清单

必须记录 Dataset ID / Version、Episode IDs、Annotation Result IDs + versions、RawAsset IDs + SHA256、Canonical Schema Version、Adapter ID / Version、Pipeline ID / Version + parameters hash、LabelSchema ID / Version、QualityRuleSet ID / Version、review policy / accepted records、split manifest、Manifest、Hash、export format/version、license summary、创建人和时间。

冻结后不可修改。任何样本增删、标注修订或规则变化都创建新版本。

## 6. Export

首选输出：
1. DDP Canonical Manifest + referenced media/data files；
2. 若算法训练消费端要求 LeRobot，则实现 LeRobot v3.0 exporter，并使用官方 reader 回读校验；
3. 其他格式按目标训练框架需求扩展。

不要仅因为已能读取 LeRobot，就宣称已支持 LeRobot 写出。Reader 和 Writer 是不同能力。

## 7. 导出目录建议

```text
dataset_export/
  manifest.json
  dataset_card.md
  schema/
    canonical.schema.json
    label_schema.json
  splits/
    train.jsonl
    validation.jsonl
    test.jsonl
  episodes/<episode_id>/
    media/
    observations/
    actions/
    annotations/
  checksums.sha256
```

真实导出布局最终按训练框架规范确定；此结构是 DDP 中间交换包示意。

## 8. 导出任务

状态：`CREATED → QUEUED → EXPORTING → VERIFYING → COMPLETED`；异常：`FAILED / CANCELLED / PARTIAL / QUARANTINED`。

导出应异步、幂等、可恢复；记录输出对象、总大小、Hash、Manifest、耗时和错误。

## 9. 训练交付

Training Request 绑定 DatasetVersion、ExportJob、requested format、destination/adapter、delivery status 和 external training run reference（若外部平台真实可用）。如果 Adapter 当前仍 503，UI 明确标记“未连接/不可用”，但 Dataset 导出仍可独立完成。禁止 Mock 成训练成功。

## 10. 验收

只有符合 Dataset Gate 的 Episode 能进入；Manifest 完整；冻结后内容不可变；split 无明显泄漏；导出可回读；文件 Hash 一致；可从导出项反查原始 CaptureSession 和 RawAsset。
