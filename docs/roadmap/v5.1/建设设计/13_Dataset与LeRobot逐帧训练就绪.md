# 13｜Dataset / LeRobot 逐帧训练就绪设计

## 1. 现状边界
V4.1 FR-034/035 通过 `lerobot==0.3.3` 官方 Loader 的 metadata 双路构造验证，状态 `VERIFIED_BY_LOADER`，但 `loadable_scope=METADATA_ONLY`。V5.1 必须另建逐帧验收，不覆盖旧证据。

## 2. 训练就绪定义
Dataset 只有同时满足下列条件，才能标记 `LOADER_FRAME_VERIFIED`：
- info / tasks / episode metadata 可读；
- 至少一个完整 Episode 的 Parquet 数据可读；
- observation features 真实存在且 shape/dtype 正确；
- action features 若数据集定义了真实 Action，则可读取并与源数据一致；
- 视频文件可解码，帧数、时间范围、fps 口径明确；
- episode_index、frame_index、timestamp 映射一致；
- stats / normalization 与实际 feature 对齐；
- Manifest 中文件数量、Hash、Episode 数可复核；
- 官方 Loader 版本和执行环境固定；
- 测试结果可复现。
没有 Action 源的数据集可以声明 `ACTION_NOT_PROVIDED`，但不能宣称具备 Action 训练输入。

## 3. 数据字段映射
Canonical feature → LeRobot feature 必须有显式映射：name、dtype、shape、unit、modality、source stream、transform、normalization、missing policy。任何丢弃/降采样/重采样均记录。

## 4. Split 防泄漏
沿用 V4.1 Session / Subject 默认分组和 Scene / Batch / 相似簇规则。增加 split 结果解释报告：分组依据、样本数、重复簇、跨 split 风险、policy_sha256。不得为追求比例破坏分组隔离。

## 5. Loader 测试矩阵
metadata load；Parquet frame read；video decode；observation feature read；action feature read（有源时）；timestamp/fps consistency；multi-episode iteration；batch iteration；stats validation；checksum validation；empty/corrupt episode rejection。

## 6. 状态口径
- `VERIFIED_BY_LOADER_METADATA`：仅元数据。
- `VERIFIED_BY_LOADER_FRAME`：逐帧数据读取通过。
- `VERIFIED_FOR_TRAINING_PIPELINE`：目标训练代码实际消费通过（需要明确训练脚本/环境）。
- `TRAINING_HANDOFF_VERIFIED`：外部平台确认接收（需要外部 API）。
这些状态必须在 UI 和 API 中明确区分，不得把 metadata 状态向上覆盖。

## 7. Dataset 页面
展示逐帧可读 Episode 数、总帧数、Observation features、Action features、视频覆盖率、fps 来源、Loader version、frame-level evidence、缺失字段和限制。

## 8. 验收
真实样本在固定版本 Loader 下读取 Parquet + 视频 + Observation；有真实 Action 时读取 Action；与 Canonical/源文件抽样比对；失败时保留准确限制状态。
