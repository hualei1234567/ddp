# 14｜Lineage 全链路血缘设计

## 1. 目标
FR-040 当前 PARTIAL：有 lineage 查询和 Episode graph，但缺少一次真实 Dataset → RawAsset / CaptureSession E2E。V5.1 证明正向和反向血缘，不重建第二套 lineage。

## 2. 必须追溯的链
DatasetVersion → DatasetItem → Episode → ProcessingRun → Artifact → RawAsset → UploadSession → CaptureSession → CollectionTask → Robot / Device / Sensor / Calibration Snapshot。
标注链：DatasetItem → AnnotationSubmission → AnnotationTask → Assignment → Annotator → LabelSchema Version → QC Run → Review Record。

## 3. 每条边的要求
edge_type、from_id、to_id、created_at、created_by/process identity、version/hash、relationship evidence。删除、重跑、重分段、重新标注不能覆盖历史边。

## 4. UI 查询
从 DatasetVersion 点开任一 DatasetItem，查看 Episode、原始文件、采集 Session、设备、传感器、标定、Parser/Pipeline、标注提交、QC 和 Export；支持反向从 RawAsset 查看被哪些 Episode / Dataset 引用。

## 5. 完整性检查
DatasetItem 没有 Episode：阻断冻结；Episode 没有 RawAsset 或合法来源：阻断冻结或显式豁免；ProcessingRun / Parser Version 缺失：标记不可复现；Calibration Snapshot 缺失：按任务规则 WARN/BLOCK；Annotation/QC 版本缺失：阻断合格 Dataset；公开数据集来源许可缺失：阻断发布/共享。

## 6. 验收
真实数据完成一次 Dataset → RawAsset → CaptureSession 正向追溯和 RawAsset → Dataset 反向查询；跨 Project 不泄漏；深度上限和循环保护有效；冻结版本血缘不可变。
