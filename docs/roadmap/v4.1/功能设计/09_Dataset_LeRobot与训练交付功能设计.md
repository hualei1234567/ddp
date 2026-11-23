# 09｜Dataset、LeRobot Loader 与训练交付功能设计

## 1. 审计事实
Dataset Builder、Gate、去重、划分、快照、Manifest 已 VERIFIED；Manifest 落对象存储 VERIFIED；Canonical Export VERIFIED；LeRobot v2.1 结构合规 Writer VERIFIED；官方 Loader 实际加载仍 BLOCKED。当前 `loadable_scope=METADATA_ONLY`、`verification_status=UNVERIFIED`。TrainingRequest/ModelOps/Inference 基础域 VERIFIED，但 MOCK/DRY_RUN 不能代表外部平台真实同步。

## 2. Dataset Builder 完整操作
保留现有 Builder，补齐可理解的候选预览：
- 合格样本数、排除样本数；
- 每个排除项的具体 Gate 原因；
- 场景、任务、设备、模态、标签分布；
- 重复/近重复检查；
- 按 CaptureSession / Subject / Scene 分组的泄漏检查；
- Train/Validation/Test 比例、策略、随机种子；
- 冻结前再次执行 Gate；
- 冻结后不可变；任何数据变化生成新 DatasetVersion。

## 3. Dataset Gate
默认只纳入 RawAsset VERIFIED、ProcessingRun 所需步骤成功、Episode READY、Annotation Submission 已提交、必需 QC PASS、Review ACCEPTED、Schema/Pipeline/RuleSet 版本明确、来源许可和权限合规的样本。豁免必须记录原因、操作者和时间，不能静默跳过。

## 4. LeRobot v2.1 Loader 切片 5
V4.1 必须完成：
1. 创建隔离 Python venv；
2. 固定官方 LeRobot 版本和依赖锁；
3. 从 DDP ExportJob 下载真实导出包；
4. 使用官方 Loader 实际打开数据集；
5. 读取 info / tasks / stats / episode metadata；
6. 读取至少一个完整 Parquet Episode；
7. 读取并解码至少一个视频片段；
8. 校验 feature shape、dtype、fps、task metadata；
9. 对比 Manifest 的 Episode 数、帧数和文件 Hash；
10. 记录 Loader 版本、命令、输出、错误栈和环境；
11. 全部通过后才能把 `verification_status` 改为 VERIFIED；
12. 若只能读取 metadata，则保持 METADATA_ONLY / UNVERIFIED。

## 5. 导出格式和版本
保留 DDP Canonical 与 LeRobot v2.1。每种格式都记录 format version、writer version、manifest、checksums、round-trip/loader test 和已知限制。不能因 Writer 文件结构合规就宣称训练可直接消费。

## 6. Dataset 卡片
显示 DatasetVersion、样本数、总时长、模态覆盖、标签分布、设备/场景覆盖、split 比例、Pipeline/Adapter/Canonical/LabelSchema/QC RuleSet 版本、数据来源、导出状态、Loader 验证状态和 Manifest Hash。

## 7. 训练交付
具备真实目标平台接口后，支持提交 DatasetVersion/ExportJob、训练配置、external_run_id、callback 或 polling、queued/running/completed/failed/cancelled 状态、训练日志、模型产物和评估结果同步。外部请求幂等，回调验签，轮询补偿。无 API/认证/回调契约时显示“演练模式/未连接”，不得显示“已同步”。

## 8. 验收
Dataset Gate 正确；Manifest/Hash 可复核；split 无明显泄漏；LeRobot 官方 Loader 实际加载 Parquet 和视频；状态如实；MOCK/DRY_RUN 与 REAL 清晰区分；DatasetVersion 可追溯到原始 Session 和标注结果。
