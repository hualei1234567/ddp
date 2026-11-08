# DDP Canonical 数据标准草案

- 状态：草案（待评审，**未冻结**）
- Owner：System Architect
- 日期：2026-11-08
- 关联任务：DDP-101
- 关联规范：§6、§7、§10.4、§16.5、§17.4
- 关联架构：`docs/architecture/阶段1-领域模型与契约草案.md`
- Schema 版本：`ddp.canonical/1.0.0-draft`
- 边界：本文件定义数据标准，不修改代码、不修改 `packages/contracts/**`。

## 1. 目标与原则

Canonical 是 Raw 与 Processed/Labeled/Dataset 之间的统一内部模型，用于屏蔽 MCAP、ROS bag、HDF5、MP4、Parquet、LeRobot 与厂商格式差异。

原则：

1. **Raw 不可变**：Canonical 是从 Raw 派生的新产物，任何处理不得覆盖原始文件。
2. **语义显式**：每个时间、坐标、数值都必须带来源、单位、坐标系或标定引用；禁止隐式默认。
3. **可复现**：记录 Schema、Parser、Pipeline、配置、标定版本与 Hash，能从 Raw 重建。
4. **失败关闭**：无法对齐时间、缺失必需标定、坐标系链断裂时，标记质量问题并隔离，不得静默进入可训练数据集。
5. **版本化**：Schema、标定、规则、Pipeline 均版本化；旧版本保留可读。
6. **大对象引用**：Canonical 元数据与 Manifest 存数据库/对象；大体量帧数据存对象存储列式文件。
7. **LeRobot 是导出格式之一**，不是 DDP 内部唯一模型。

## 2. 命名、标识与序列化

- 字段名：`snake_case`；枚举值：大写下划线；时间戳字段后缀 `_ns`；时长后缀 `_ns`；单位字段统一 `unit`。
- 标识：`project_id`、`robot_id`、`device_id`、`sensor_id`、`calibration_id`、`episode_id`、`asset_id` 使用 UUIDv7 字符串；业务编码 `code` 不作为外键。
- 必填字段不得省略；未知值使用 `null` 并附 `*_status` 或 `*_reason`，不得用 `0`、空字符串或 `UNKNOWN` 冒充真实值。
- 扩展：仅允许 `extensions` 下按厂商命名空间（如 `vendor.agibot`）存放；未注册扩展不得进入 Canonical 核心字段。
- JSON 中 `timestamp_ns`、`duration_ns` 使用**十进制字符串**，避免 JavaScript 超过 2^53 的精度丢失；Parquet/Arrow 使用 `int64`。其他整数按 JSON 安全范围规则处理。
- JSON 禁止 `NaN`、`Infinity`；缺失值用 `null` + `valid=false`；浮点数组必须显式给出长度与顺序。

## 3. 时间模型

### 3.1 时间域与基准

每个 Session/Episode/Stream 必须声明 `time_base`：

```json
{
  "time_domain": "UTC",
  "clock_source": "PTP",
  "clock_id": "host-01",
  "offset_to_utc_ns": "0",
  "drift_ppm": 0.0,
  "uncertainty_ns": "1000000",
  "sync_method": "PTP",
  "sync_ref": "ptp-master-01",
  "sync_status": "VERIFIED"
}
```

- `time_domain`：`UTC`、`ROS`、`DEVICE`、`HOST`、`MONOTONIC`、`UNKNOWN`；Canonical 统一时间线必须是 `UTC`。
- `clock_source`：`PTP`、`NTP`、`ROS_CLOCK`、`DEVICE_CLOCK`、`HOST_CLOCK`、`MONOTONIC`、`EXTERNAL`、`UNKNOWN`。
- `sync_method`：`PTP`、`NTP`、`HARDWARE_TRIGGER`、`SOFTWARE_ESTIMATE`、`MANUAL`、`NONE`。
- `sync_status`：`VERIFIED`、`DEGRADED`、`UNVERIFIED`、`FAILED`；`UNVERIFIED/FAILED` 的 Episode 不得进入可训练数据集。

### 3.2 时间戳

逻辑类型 `timestamp_ns`：UTC Unix epoch 起算的 int64 纳秒。每个观测至少包含：

| 字段 | 必填 | 说明 |
|---|---|---|
| `time_ns` | 是 | Canonical UTC 时间，JSON 为字符串 |
| `sequence` | 是 | 该流内序号，从 0 开始；用于识别丢帧/重复 |
| `source_time_ns` | 是 | Raw 原始时间值，JSON 为字符串 |
| `source_time_domain` | 是 | `DEVICE/ROS/HOST/MONOTONIC/UTC` |
| `clock_id` | 是 | 产生源时间的时钟标识 |
| `time_base_ref` | 是 | 指向所属 `time_base` 的 ID |
| `uncertainty_ns` | 是 | 该时间点不确定度，不能省略 |
| `is_dropped` | 否 | 是否丢帧；丢帧也要保留序号与原因 |
| `drop_reason` | 否 | `DEVICE_LOST`、`QUEUE_OVERFLOW`、`PARSE_ERROR` 等 |

转换必须保留“原始时间语义 + 标准化时间 + 转换依据”，不得只保存一个无法解释的时间戳。

### 3.3 时间对齐

`time_alignment` 对象记录：

- `reference_stream_id`、`method`（`PTP`、`NTP`、`HARDWARE_TRIGGER`、`CROSS_CORRELATION`、`SOFTWARE_ESTIMATE`）。
- `offset_ns`、`drift_ppm`、`uncertainty_ns`、`residual_ns`、`sample_count`。
- `per_stream[]`：每个流的偏移、漂移、丢帧数、延迟、同步状态。
- `aligned_at`、`alignment_job_id`、`pipeline_version`。

规则：

- 对齐后每个流内时间必须非递减；重复时间戳标记 `DUPLICATE` 并进入质检。
- 无法估计同步关系时 `sync_status=UNVERIFIED`，保留原始时间与原因，不得伪造 `offset_ns=0`。
- 采样频率检查：实际频率与声明频率偏差超过规则阈值（默认 ±5%，按项目配置）标记质量问题。
- 时区仅用于展示；Canonical 不存本地时间作为权威时间。可选 `captured_local` 与 IANA `timezone` 仅作显示。

### 3.4 三类时间

- `captured_at`：设备/传感器采集时间。
- `ingested_at`：平台接收/登记时间。
- `processed_at`：处理完成时间。

三者必须分开，不得互相替代；均使用 UTC。

## 4. 坐标系与姿态

### 4.1 基本约定

- 右手坐标系；长度单位米；旋转标准表达为四元数 `[w, x, y, z]`（实部在前），必须归一化，容差 `1e-6`。
- 旋转矩阵如使用，必须声明 `row_major`；欧拉角仅用于展示，必须声明轴序（默认 `intrinsic XYZ`）与单位 `rad`。
- 每个坐标帧必须声明 `frame_convention`：`FLU`（x 前、y 左、z 上）、`RDF`/`OPTICAL`（x 右、y 下、z 前）、`ENU`、`NED`。
- 左右手/左右侧必须显式：`side` ∈ `LEFT`、`RIGHT`、`BILATERAL`、`NOT_APPLICABLE`；关节/末端/相机帧名包含侧别。

### 4.2 帧命名

- 世界：`world`。
- 机器人基座：`<robot_id>/base_link`。
- 末端/工具：`<robot_id>/ee_link`、`<robot_id>/tool0`。
- 相机：`<sensor_id>/link`、`<sensor_id>/optical_frame`。
- IMU：`<sensor_id>/imu_link`。
- 关节：`joint/<joint_name>`。
- 帧名全局唯一；禁止用同一名称表达不同语义。

### 4.3 Transform

```json
{
  "parent_frame": "robot-01/base_link",
  "child_frame": "sensor-01/optical_frame",
  "translation_m": [0.12, -0.03, 0.25],
  "rotation_quat_wxyz": [0.7071, 0.0, 0.7071, 0.0],
  "timestamp_ns": "1730000000000000000",
  "transform_type": "STATIC",
  "source": "CALIBRATION",
  "calibration_id": "cal-01",
  "calibration_version": 3,
  "uncertainty": {"translation_m": 0.001, "rotation_rad": 0.002}
}
```

- `transform_type`：`STATIC`、`DYNAMIC`。
- 变换链必须能解析到 `world` 或声明的根帧；禁止循环。
- `source`：`CALIBRATION`、`KINEMATICS`、`ESTIMATE`、`HARDWARE`；估算变换必须带不确定度。
- 动态变换必须有时间戳；静态变换也必须带来源标定版本。

## 5. 单位

Canonical 内部统一 SI；Raw 单位必须记录并可换算。

| 量 | 字段示例 | Canonical 单位 |
|---|---|---|
| 长度/位移 | `translation_m`、`depth_m` | `m` |
| 角度 | `joint_position`、`rotation` | `rad` |
| 角速度 | `angular_velocity` | `rad/s` |
| 线速度 | `linear_velocity` | `m/s` |
| 加速度 | `linear_acceleration` | `m/s^2` |
| 力 | `force` | `N` |
| 力矩 | `torque` | `N*m` |
| 质量 | `mass` | `kg` |
| 时间/时长 | `time_ns`、`duration_ns` | `ns`（逻辑 int64） |
| 频率 | `sample_rate_hz` | `Hz` |
| 图像/像素 | `fx`、`cx`、`width` | `px` |
| 压力 | `pressure` | `Pa` |
| 温度 | `temperature` | `K`（展示可转 °C） |
| 磁场 | `magnetic_field` | `T` |
| 音频采样 | `sample_rate_hz` | `Hz` |

约束：

- 每个数值数组必须有唯一 `unit` 与 `quantity_kind`；不得同一数组混用单位。
- 原始为角度/毫米/毫秒时，转换后必须保留 `raw_unit` 与换算因子。
- 四元数无量纲；协方差矩阵单位必须与对应量一致并声明。
- 深度图 Canonical 使用米；无效值用掩码 `invalid_value` 表示，不得用 `0` 冒充无效。
- 图像编码必须声明通道顺序（`rgb8`、`bgr8`、`mono8`、`mono16`、`16UC1` 等）与位深。

## 6. 标定模型

### 6.1 Calibration 对象

| 字段 | 必填 | 说明 |
|---|---|---|
| `calibration_id` | 是 | 标定记录 ID |
| `profile_id` / `profile_version` | 是 | 标定配置版本 |
| `schema_version` | 是 | 标定参数 Schema 版本 |
| `scope` | 是 | `ROBOT`、`DEVICE`、`SENSOR`、`SYSTEM` |
| `target_type` / `target_id` | 是 | 目标对象 |
| `calibration_type` | 是 | `INTRINSICS`、`EXTRINSICS`、`HAND_EYE`、`IMU`、`JOINT_OFFSET`、`TIME_OFFSET`、`FORCE_TORQUE`、`DEPTH_ALIGNMENT` |
| `method` | 是 | 标定方法/工具与版本 |
| `parameters` | 是 | 按类型 Schema 校验的参数对象 |
| `parent_frame` / `child_frame` | 视类型 | 外参/手眼必需 |
| `valid_from` / `valid_to` | 是 | 有效区间；`valid_to` 可为空表示长期有效 |
| `status` | 是 | `DRAFT/SUBMITTED/VALIDATED/ACTIVE/REJECTED/SUPERSEDED/EXPIRED/REVOKED` |
| `evidence_asset_ids` | 是 | 标定数据/报告的 RawAsset 引用 |
| `checksum` | 是 | 参数与证据的规范化 Hash |
| `uncertainty` | 是 | 平移/旋转/时间不确定度 |
| `performed_by` / `validated_by` | 是 | 执行人与校验人，必须分离 |
| `supersedes_id` | 否 | 被替代记录 |

### 6.2 内参

```json
{
  "camera_model": "PINHOLE",
  "image_width": 1280,
  "image_height": 720,
  "fx": 900.1, "fy": 900.0, "cx": 640.2, "cy": 360.3, "skew": 0.0,
  "distortion_model": "plumb_bob",
  "distortion_coefficients": [0.1, -0.02, 0.0, 0.0, 0.0],
  "unit": "px"
}
```

- `camera_model`：`PINHOLE`、`FISHEYE`、`OMNIDIRECTIONAL`。
- 畸变模型必须显式；系数顺序由模型 Schema 固定，不得依赖约定。
- 深度与 RGB 对齐必须记录 `DEPTH_ALIGNMENT` 标定与目标分辨率。

### 6.3 外参与手眼

- 外参：`parent_frame`、`child_frame`、`translation_m`、`rotation_quat_wxyz`、时间戳、来源、标定版本。
- 手眼：`HAND_EYE` 参数必须声明 `eye_in_hand`（`true/false`）、`base_frame`、`tool_frame`、`camera_frame`、求解器与残差。
- IMU：`noise_density`、`random_walk`、`bias`、`gravity`、轴对齐与温度条件；单位按 SI。
- 关节偏移：`joint_order` 显式数组、每关节偏移与单位（旋转关节 rad，移动关节 m）。
- 时间偏移：`sensor_id`、`reference_sensor_id`、`offset_ns`、不确定度、估计方法；不得用零偏移冒充已同步。

### 6.4 选择规则

- Episode 必须保存 `calibration_snapshot[]`：`{calibration_id, version, checksum, target_type, target_id}`。
- 选择条件：`valid_from <= captured_at < valid_to` 且 `status=ACTIVE`。
- 同一目标/类型存在多个有效标定时，按 `CalibrationProfile.selection_rule` 选择；规则缺失或冲突则**失败关闭**，不得静默取最新。
- 缺少必需标定、标定过期/撤销、校验和变化：Episode 标记 `QUALITY_FAILED` 或 `INVALIDATED`，禁止进入可训练数据集。
- 标定快照与 Episode 绑定；标定被撤销不得静默改写历史 Episode，必须触发重新评估并保留审计。

## 7. 传感器与观测

### 7.1 SensorDescriptor

`sensor_id`、`sensor_type`、`vendor`、`model`、`serial_number`、`sample_rate_hz`、`resolution_w/h`、`bit_depth`、`channels`、`frame_id`、`frame_convention`、`intrinsic_calibration_id`、`extrinsic_calibration_id`、`sync_group`、`sync_mode`、`firmware_version`、`calibration_profile_id`。

### 7.2 Observation

| 字段 | 说明 |
|---|---|
| `observation_id` | 观测 ID |
| `sensor_id` | 传感器 |
| `observation_type` | `IMAGE/DEPTH/POINT_CLOUD/IMU/JOINT_STATE/END_EFFECTOR_POSE/FORCE_TORQUE/GRIPPER/AUDIO/MOCAP/LANGUAGE` |
| `timestamp` | 时间戳对象（§3.2） |
| `frame_id` | 坐标系 |
| `calibration_ref` | 标定 ID/版本/校验和 |
| `unit` / `quantity_kind` | 数值单位与量纲 |
| `shape` / `dtype` | 数组形状与数据类型 |
| `payload_ref` | 对象 Key、格式、Hash、大小 |
| `encoding` | `JSON/PARQUET/ARROW/PNG/JPEG/MP4/WAV/BINARY` |
| `quality_flags` | 丢帧、饱和、模糊、时间异常等 |
| `valid` / `invalid_reason` | 数据是否可用 |

模态约定：

- **图像**：宽高、编码、通道顺序、位深；时间戳为曝光/采集时间，不用传输完成时间。
- **深度**：米；`invalid_value` 与掩码；与 RGB 对齐需标定引用。
- **点云**：`xyz_m`、可选 `intensity`、`organized`、坐标系；不得把毫米当米。
- **IMU**：`linear_acceleration_m_s2`、`angular_velocity_rad_s`、姿态四元数、协方差；注明是否含重力。
- **关节状态**：`joint_names` 显式顺序、位置/速度/力矩单位按关节类型；禁止依赖隐式顺序。
- **末端位姿**：`frame_id`、transform、速度/角速度单位；左右侧显式。
- **力/力矩**：六维向量顺序固定 `[fx, fy, fz, tx, ty, tz]`，单位 `N`/`N*m`，坐标系显式。
- **语言指令**：`text`、`language`、`source`、`timestamp`、可选 `confidence`；不得只存模型生成文本而无来源。
- **音频**：采样率、通道数、样本格式、时长；PCM 位深显式。

## 8. Episode / Frame / Action

### 8.1 EpisodeManifest

```json
{
  "schema_version": "ddp.canonical/1.0.0-draft",
  "episode_id": "018f...",
  "episode_version": 1,
  "project_id": "018e...",
  "code": "EP-000123",
  "task_id": "018d...",
  "robot_id": "018c...",
  "device_ids": ["018b..."],
  "sensor_ids": ["018a..."],
  "start_time": {"time_ns": "1730000000000000000", "time_domain": "UTC", "uncertainty_ns": "1000000"},
  "end_time": {"time_ns": "1730000010000000000", "time_domain": "UTC", "uncertainty_ns": "1000000"},
  "duration_ns": "10000000000",
  "time_base": {"time_domain": "UTC", "clock_source": "PTP", "sync_status": "VERIFIED"},
  "time_alignment": {"method": "PTP", "offset_ns": "0", "uncertainty_ns": "1000000"},
  "canonical_schema_version": "ddp.canonical/1.0.0-draft",
  "pipeline_id": "parse-mcap", "pipeline_version": "1.2.0", "config_hash": "sha256:...",
  "segmentation_rule_version": "seg-v1",
  "calibration_snapshot": [
    {"calibration_id": "cal-01", "version": 3, "checksum": "sha256:...", "target_type": "SENSOR", "target_id": "sensor-01"}
  ],
  "streams": [
    {"stream_id": "cam-front", "sensor_id": "sensor-01", "modality": "IMAGE", "frame_count": 300,
     "sample_rate_hz": 30.0, "dropped_count": 0, "payload_manifest_ref": "s3://.../stream.json",
     "chunk_refs": ["s3://.../part-000.parquet"]}
  ],
  "frame_count": 300,
  "actions": [],
  "annotations": [],
  "quality": {"status": "PASS", "rule_version": "q-v1", "issues": []},
  "provenance": {"raw_asset_ids": ["018a..."], "ingestion_job_ids": ["018b..."], "created_by": "user-01"},
  "manifest_sha256": "sha256:..."
}
```

### 8.2 Frame 与存储

逻辑 Frame：

- `frame_index`（从 0 开始）、`timestamp`、`sequence`、`observations[]`（按 `sensor_id` 索引）。
- 帧数据不内联到 Manifest；按流写入 Parquet/Arrow 分块，Manifest 存 `chunk_refs` 与 `payload_manifest_ref`。
- 每块必须带 `chunk_index`、`row_count`、`sha256`、`time_range`。
- 帧时间必须落在 Episode `[start_time, end_time]` 内；跨流对齐误差超过阈值写质量问题。

### 8.3 Action

- `action_id`、`action_type`（`JOINT_TARGET`、`JOINT_TRAJECTORY`、`CARTESIAN_POSE`、`CARTESIAN_VELOCITY`、`GRIPPER`、`LANGUAGE_INSTRUCTION`、`CUSTOM`）。
- `start_time`/`end_time`、`values`、`unit`、`frame_id`、`joint_names`、`source`（`HUMAN_DEMO/AUTONOMOUS/AI/TELEOP`）、`confidence`。
- 自定义动作必须带 `schema_ref` 与 `schema_version`，不得用自由文本替代结构。
- 语言指令既可以作为 Action，也可以作为 Episode 元数据；同一内容不得重复计数。

## 9. 质量、标注与血缘引用

- `QualityResult`：`rule_id`、`rule_version`、`run_id`、`status`（`PASS/FAIL/WARN`）、`score`、`issues[]`、`input_manifest_sha256`、`evaluated_at`。
- 问题项：`code`、`severity`、`time_range`、`sensor_id`、`message`、`evidence_ref`；不得只存“失败”无证据。
- `AnnotationRef`：`annotation_id`、`schema_id/version`、`task_id`、`status`、`review_status`、`payload_ref`、`version`；标签由版本化 Schema 管理。
- `DataLineage`：节点类型 `RAW_ASSET/INGESTION_JOB/EPISODE/ANNOTATION/QUALITY_RUN/DATASET_VERSION/TRAINING_RUN`；边类型 `DERIVED_FROM/USED/GENERATED`；只追加，不修改历史。
- 事件：`event_id`、`event_type`、`event_version`、`occurred_at`、`producer`、`aggregate_id`、`project_id`、`correlation_id`、`payload`；消费者必须幂等。

## 10. 校验规则与质量门禁

接入/处理时必须检查：

- 文件：大小、Hash、格式、内容签名、可解析性、文件数量、路径安全。
- 时间：原始时间存在、时间基准声明、对齐方法、单调性、采样频率、丢帧、延迟、同步不确定度。
- 坐标：帧名合法、链可解析、无循环、左右侧/约定显式、变换来源与版本。
- 单位：每个数值的量纲/单位合法、无混用、Raw 单位与换算因子保留。
- 标定：必需类型齐全、有效期内、校验和一致、执行/校验分离、冲突选择失败关闭。
- Episode：起止时间、帧数、流完整性、Action 时间范围、标签引用、Manifest Hash、血缘完整。
- 安全：RawAsset 不可覆盖；隔离区资产不得进入 Dataset；下载审计。

门禁结果：`PASS` 可进入下一层；`WARN` 可进入但记录；`FAIL` 进入隔离/返工，不得进入可训练数据集。规则必须版本化、可测试、可追溯到输入 Hash。

## 11. 存储与序列化

- Raw：`raw/{project_id}/{yyyy}/{mm}/{asset_id}/...`；对象 Key 不包含原始文件名与用户可控路径。
- Canonical：`canonical/{project_id}/{episode_id}/{episode_version}/manifest.json` 与 `streams/{stream_id}/part-*.parquet`。
- Processed/Labeled/Dataset/Training 使用独立前缀；跨层复制必须记录血缘，不共享可变对象。
- 数据库只存 Manifest 指针、Hash、大小、状态、版本、索引字段；不存大体量帧/视频二进制。
- 批量数据用 Parquet/Arrow；Manifest 用 JSON；事件用 JSON Schema。所有产物不可变，更新即新版本。
- 对象存储生命周期与保留策略由 `retention_policy_id` 控制；删除/墓碑需审批与审计。

## 12. 格式映射

| 来源 | 映射要点 | 风险/约束 |
|---|---|---|
| MCAP / ROS bag / rosbag2 | topic→sensor；`header.stamp`→`source_time`；`/clock`/TF→time base/transform；QoS/丢帧记录 | ROS 时间必须与 UTC 建立映射；TF 链完整性校验 |
| HDF5 | 数据集属性映射 `unit`/`frame_id`/`sample_rate_hz`；时间数组显式 | 不得推断单位；缺失属性需映射配置 |
| MP4/MOV | PTS→采集时间；视频流→Observation；音频轨独立流 | 容器时间与设备时钟偏移必须记录 |
| CSV/JSON | 依赖显式 mapping schema；字段、单位、帧、时间列必须声明 | 禁止自动猜测单位/时间格式 |
| Parquet | 直接映射列式流；保留 Schema 与 chunk 元数据 | 校验列类型与单位元数据 |
| LeRobot | 导出格式；字段映射到 LeRobot 列；附带 DDP sidecar 保留标定/时间/血缘 | LeRobot 不能表达完整标定与时间语义，不能作为内部唯一模型 |
| 厂商自定义 | Parser/Adapter 注册；输出 Canonical；未识别字段保留在 Raw 元数据 | 不得在核心业务写型号分支；Unsupported 必须显式返回 |

## 13. Schema 版本与迁移

- 版本：`ddp.canonical/<major>.<minor>.<patch>`；Manifest 必填 `schema_version` 与 `canonical_schema_version`。
- 兼容：新增可选字段为 minor；删除/改名/改类型/改语义、必填化、单位或坐标系语义变化为 major。
- 新增枚举值：客户端必须容忍未知值；否则按 major。未知枚举不得默认成功。
- 迁移：提供纯函数式、确定性的版本转换器；保留输入版本与输出版本；旧 Canonical 不原地改写，生成新产物。
- 废弃：`deprecated` + `sunset_at`，至少 30 天或一个 minor 版本；期间新旧版本可读。
- 重建：从 RawAsset + Parser/Pipeline 版本 + 配置 Hash + 标定快照可复现；重建结果与历史 Hash 不一致必须进入质检。

## 14. 最小示例（节选）

```json
{
  "schema_version": "ddp.canonical/1.0.0-draft",
  "episode_id": "018f1f7e-7c00-7a2b-9d10-2f6c1a9b3c4d",
  "project_id": "018e2a10-0000-7000-8000-000000000001",
  "time_base": {
    "time_domain": "UTC",
    "clock_source": "PTP",
    "clock_id": "host-01",
    "offset_to_utc_ns": "0",
    "drift_ppm": 0.0,
    "uncertainty_ns": "1000000",
    "sync_method": "PTP",
    "sync_status": "VERIFIED"
  },
  "streams": [{
    "stream_id": "cam-front",
    "sensor_id": "018e2a10-0000-7000-8000-000000000010",
    "modality": "IMAGE",
    "sample_rate_hz": 30.0,
    "frame_count": 300,
    "dropped_count": 0
  }],
  "observations": [{
    "observation_id": "obs-000001",
    "sensor_id": "018e2a10-0000-7000-8000-000000000010",
    "observation_type": "IMAGE",
    "timestamp": {
      "time_ns": "1730000000000000000",
      "sequence": 0,
      "source_time_ns": "1730000000000000000",
      "source_time_domain": "UTC",
      "clock_id": "host-01",
      "time_base_ref": "episode-time-base",
      "uncertainty_ns": "1000000",
      "is_dropped": false
    },
    "frame_id": "sensor-01/optical_frame",
    "calibration_ref": {"calibration_id": "cal-01", "version": 3, "checksum": "sha256:..."},
    "unit": "px",
    "shape": [720, 1280, 3],
    "dtype": "uint8",
    "encoding": "rgb8",
    "payload_ref": {"object_key": "canonical/.../part-000.parquet", "sha256": "sha256:...", "size_bytes": 123456},
    "valid": true
  }]
}
```

## 15. 验收自检与未决项

| 验收项 | 位置 | 状态 |
|---|---|---|
| Canonical 字段覆盖时间 | §3 | 已覆盖 |
| Canonical 字段覆盖坐标系 | §4 | 已覆盖 |
| Canonical 字段覆盖单位 | §5 | 已覆盖 |
| Canonical 字段覆盖标定 | §6 | 已覆盖 |
| 兼容与迁移策略 | §13 | 已覆盖 |
| 校验与质量门禁 | §10 | 已覆盖 |

未决项（待评审/用户批准）：

1. `time_ns` 在 JSON 中使用字符串、Parquet 使用 int64：需前端/数据团队确认编码工具链。
2. 采样频率默认 ±5% 阈值是否按项目可配置，需质量规则评审确认。
3. 标定同目标多有效记录的 `selection_rule` 具体语法与优先级，需设备域评审。
4. LeRobot sidecar 字段集合与导出规范需在阶段 4 冻结。
5. 深度/点云无效值掩码格式需与解析器实现对齐。
6. Canonical 是否直接作为标注/训练内部格式，还是仅作为中间层：当前定位为内部统一层，导出格式另行定义。
