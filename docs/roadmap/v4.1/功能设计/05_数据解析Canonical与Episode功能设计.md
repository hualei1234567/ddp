# 05｜数据接入、解析、Canonical 与 Episode 功能设计

## 1. 现状
Adapter Registry（HDF5/rosbag/MCAP/MP4/LeRobot）、Episode 自动物化、ProcessingRun/Artifact lineage、Canonical 1.0.0 六实体 Schema 已 VERIFIED。单文件 MP4 Episode Segmentation VERIFIED。SensorStream/Observation/Action 逐帧生产 PARTIAL；DATA_CLEANING SKIPPED；容器格式必须按真实 fixture 验证。

## 2. 用户操作
在上传/数据源详情点击“开始处理” → 系统识别格式 → 展示支持范围和未知文件 → 预览可输出流、Episode 数、缺失字段 → 选择 Pipeline 版本/参数 → 确认 → 创建 ProcessingRun → Worker 执行 → 页面显示每步状态/日志 → 成功打开 Episode Explorer，失败项可重试。

## 3. Pipeline 功能步骤
INPUT_VALIDATE（RawAsset/Manifest/Hash/权限）→ FORMAT_DETECT → PARSE_METADATA → EXTRACT_STREAMS → NORMALIZE_TIME → MAP_CANONICAL → SYNC_STREAMS → EPISODE_SEGMENTATION → DATA_CLEANING → PRE_QC → REGISTER_ARTIFACTS。

不适用步骤必须记录 SKIPPED + reason，不能记为 PASS。处理重试不得重复生成已成功产物。

## 4. SensorStream / Observation / Action
每条 Stream 记录 stream_id、source_asset_id、sensor/device ID、modality、timestamp source/unit、sampling rate、dtype/shape、unit、coordinate frame、calibration ref、data location、missing/invalid markers。Observation 表示真实观测；Action 表示真实动作/控制数据或有明确定义的动作标签。没有真实动作源时不得从视频推测并写成 ground-truth Action。

逐帧物化必须保留 source timestamp，生成 normalized timestamp，记录时间回退/重复帧/缺帧、采样率、单位转换、坐标系和标定版本。

## 5. 多流同步
识别主时钟，建立 sync group，记录 offset/drift 和同步依据。未同步流标记 UNSYNCED。不得因两个播放器 currentTime 一样就假定数据同步。人工偏移只作为审看参数，不覆盖原始 timestamp。

## 6. Episode
Episode 记录 Session、RawAsset、起止时间、frame range（如有）、stream refs、segmentation rule/version、ProcessingRun、任务语义和质量状态。一个文件不必然等于一个 Episode；一个 CollectionTask 可以产生多个 Session/Episode。边界调整产生新版本或处理产物，不覆盖历史。

## 7. DATA_CLEANING
V4.1 优先实现确定性规则：重复/非单调时间戳、传感器越界、缺失值标记、黑帧/损坏帧（仅支持媒体）、采样率异常、单位归一、坐标系/标定校验、重复 Episode、空片段/过短片段。每条规则有 rule_id/version/threshold/evidence。清洗生成衍生数据或质量标记，不改 RawAsset。

## 8. 容器格式
对每种格式分别核验 detect、metadata inspect、payload parse、stream extraction、Episode segmentation、sample read。不能把“识别文件”标记成“完整解析”。没有真实样本继续 UNVERIFIED。

## 9. Episode Explorer
在现有页面增强：项目/任务/Session/设备/格式/状态筛选；多视角媒体预览；Stream 元数据；同步状态；Canonical 预览；Raw 引用；ProcessingRun 历史；失败步骤和重试；批量选择创建 Annotation Batch。

## 10. 验收
至少一种真实机器人导出格式完成真实解析；真实 SensorStream/Observation/Action 可读取；时间同步状态诚实；清洗规则有测试；格式支持范围有证据；重试不重复物化 Episode。
