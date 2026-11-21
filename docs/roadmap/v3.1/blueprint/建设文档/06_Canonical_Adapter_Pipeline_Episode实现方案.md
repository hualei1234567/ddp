# Canonical、Adapter、Processing Pipeline 与 Episode 实施方案

## 1. 原则

DDP Canonical 是内部业务语义标准；LeRobot、ROS Bag、MCAP、HDF5、Parquet、MP4 是来源或交换格式。不能把某一种数据格式当作 DDP 唯一内部模型。

## 2. 解析主链

```text
RawAsset + Manifest
  ↓ format detection
Adapter Registry
  ↓ inspect
Parser Adapter
  ↓ parse raw metadata / streams
Canonical Mapping
  ↓ validate schema
Processing Pipeline
  ↓ normalize time / units / frames
Episode Segmentation
  ↓
Episode + MediaAsset + Stream + Observation + Action
  ↓
Pre-QC
```

## 3. 首批适配顺序

1. 已验证的 Hugging Face LeRobot v3.0：作为回归基线；
2. Generic MP4 + JSON / CSV：适合设备导出和轻量验证；
3. ROS2 MCAP：真实机器人数据常用候选，必须使用真实 fixture；
4. rosbag2 storage format：按实际 ROS 发行版和 storage plugin 验证；
5. HDF5 / Parquet：有真实样本后开发；
6. 厂商私有格式：按实际 SDK / 文档单独评估。

不能仅凭扩展名声称支持。Adapter 必须提供真实样本和测试夹具。

## 4. Adapter 合同

```python
class DatasetAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    supported_formats: tuple[str, ...]

    def detect(self, source: SourceDescriptor) -> DetectionResult: ...
    def inspect(self, source: SourceDescriptor) -> SourceMetadata: ...
    def validate(self, source: SourceDescriptor) -> ValidationReport: ...
    def parse(self, source: SourceDescriptor, options: ParseOptions) -> ParseResult: ...
    def map_to_canonical(self, parsed: ParseResult) -> CanonicalBundle: ...
```

Adapter 负责格式差异，不负责权限、项目归属、标注分配和 Dataset 发布。

## 5. Canonical 核心对象

- `SourceRef`：数据源、Repo/Revision 或硬件 CaptureSession；
- `CaptureSession`：一次采集；
- `RawAsset`：不可变原始文件；
- `Episode`：可独立审看、标注和训练的样本单元；
- `MediaAsset`：视频/图像/音频/深度文件；
- `Stream`：按时间组织的媒体或传感器序列；
- `Frame`：帧索引和时间语义；
- `Observation`：机器人/传感器观测；
- `Action`：动作/控制/技能数据；
- `CoordinateFrame`：坐标系语义；
- `CalibrationSnapshot`：采集时标定快照；
- `Annotation`：标注结果；
- `QualityResult`：质量检测；
- `DatasetItem`：进入数据集的样本引用。

字段必须有来源、版本、单位和缺失语义。

## 6. 时间语义

每条 Stream 尽可能保留 source_timestamp、normalized_timestamp、timestamp_unit、clock_source、time_base、sync_group、offset/drift estimate 和 conversion_version。

时间标准化不能覆盖原始时间戳。若缺少时钟同步信息，标记 `UNSYNCED` 或 `UNKNOWN`，不能假装多路视频完全同步。

## 7. 坐标与标定

记录 frame_id、frame_convention、parent_frame、transform、unit、calibration_profile_id、calibration_version、profile_snapshot/hash。设备域审计发现的 frame_id / frame_convention 写入缺口必须先复核当前 HEAD；若仍存在，先修复端到端写入，再让 Pipeline 消费该字段。

## 8. Pipeline Job / Step

每个 Pipeline Run 记录 pipeline_id/version、input_asset_ids、output_artifact_ids、adapter_id/version、canonical_schema_version、parameters_hash、started_at/ended_at、status、retry_count、worker_id、logs/error_code 和 idempotency_key。

步骤：`VALIDATE → DETECT → PARSE → NORMALIZE → SYNC → SEGMENT → PRE_QC → REGISTER`。

Pipeline 必须幂等；失败重试不重复创建 Episode；每个产物能反查原始资产。

## 9. Episode 生成规则

- 一个 Episode 是可独立标注和训练的样本边界；
- Episode 边界来源需记录：原始数据集、CaptureTask、解析规则或人工切分；
- 起止时间、帧范围、任务语义和设备引用完整；
- 不要默认一个文件等于一个 Episode；
- 不要默认一个 CollectionTask 只产生一个 Episode；
- 切片规则必须版本化；
- 切片调整产生新 Episode Version 或新处理产物，不静默覆盖旧版本。

## 10. 预质检

文件、视频、时间、传感器、标定、Episode 边界、字段映射和完整性分别检查。缺少某种传感器字段应区分 `NOT_PROVIDED` 与 `MISSING_REQUIRED`。

## 11. 验收

同一 HF LeRobot v3.0 样本回归不退化；一个真实端侧格式可进入 Canonical；原始时间戳和原始字段可追溯；至少一个多流 Episode 能查看同步状态；处理任务可重试、可取消、可定位错误；Raw 不被覆盖；未实现格式清楚标记 BLOCKED / UNSUPPORTED。
