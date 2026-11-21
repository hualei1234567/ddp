# 端侧机器人采集 Agent 与硬件适配实施方案

## 1. 目标与边界

在机器人旁边的采集工作站、工控机或边缘计算机运行轻量 Edge Capture Agent。首期职责是**采集数据接入与可靠交付**，不是机器人运动控制器。

Agent 负责：
- 识别已配置的 Robot / Device / Sensor；
- 执行采集前检查；
- 读取厂商 SDK、ROS/ROS2 Topic、MCAP/Bag 或设备导出的文件；
- 记录 CaptureSession、设备元信息、标定版本、时间源；
- 生成文件清单和 Manifest；
- 本地校验、缓存和断点续传；
- 将数据交给 DDP Ingestion；
- 回传上传与采集状态。

首期不负责关节控制、轨迹控制、急停、安全 PLC；不修改机器人固件或厂商控制策略；不在无厂商授权情况下逆向私有协议。机器人安全控制仍由厂商控制系统负责。Agent 只读接入或调用经批准的数据导出接口。

## 2. 部署形态

```text
Robot / Sensors
   ├─ Vendor SDK / ROS2 Topics
   ├─ Camera / IMU / Joint / End-effector
   └─ Calibration / Device Metadata
          ↓
Edge Workstation / IPC
   ├─ DDP Edge Capture Agent
   ├─ Device Adapter Plugins
   ├─ Local Spool (disk queue)
   ├─ Manifest Generator
   ├─ File Validator / SHA256
   └─ Upload Client
          ↓ HTTPS
DDP Backend
   ├─ CaptureSession API
   ├─ Upload Intent / Multipart
   ├─ Ingestion
   └─ MinIO
```

Agent 可以部署在 Ubuntu 工作站，不要求安装到机器人本体控制器。若设备只能通过厂商 PC 软件导出文件，Agent 首期可以使用目录监听 + Manifest 方式接入。

## 3. Agent 内部模块

```text
edge_agent/
├── app/                 # CLI、服务生命周期
├── adapters/            # filesystem_export、ros2_bag、mcap、vendor_sdk
├── capture/             # session、metadata、clock、manifest
├── storage/             # spool、checksum
├── upload/              # client、multipart、retry
├── security/            # device_identity、credential
└── tests/
```

这是边缘端独立部署包的建议结构；中央 DDP 后端不因此拆成微服务。实际目录按仓库组织方式确认。

## 4. Adapter 接口

```python
class CaptureAdapter(Protocol):
    def discover(self) -> list[DeviceDescriptor]: ...
    def get_capabilities(self, device_id: str) -> DeviceCapabilities: ...
    def preflight(self, session_config: CaptureConfig) -> PreflightReport: ...
    def start_capture(self, session_config: CaptureConfig) -> CaptureHandle: ...
    def stop_capture(self, handle: CaptureHandle) -> CaptureArtifacts: ...
    def list_artifacts(self, handle: CaptureHandle) -> list[ArtifactDescriptor]: ...
    def read_metadata(self, artifact: ArtifactDescriptor) -> ArtifactMetadata: ...
    def health_check(self) -> AdapterHealth: ...
```

接口只表达数据采集/导出能力。若某设备不支持 start/stop，但可从厂商软件导出文件，则实现 `FilesystemExportAdapter`，不伪装支持实时控制。

## 5. 采集前 Preflight

检查：Agent 版本与配置、DDP 连通性、Robot/Device/Sensor 是否登记、设备状态、传感器在线、存储剩余容量、系统时间与时钟源、标定档案、CollectionTask 权限、采集参数和 SOP 版本。

Preflight 输出 `PASS / WARN / BLOCKED` 和具体证据。涉及机器人安全的告警不得由 Agent 自动绕过。

## 6. CaptureSession 生命周期

建议状态：
`CREATED → PREFLIGHT → READY → CAPTURING → FINALIZING → LOCAL_VALIDATED → UPLOADING → SERVER_VERIFIED → INGESTION_QUEUED → COMPLETED`

异常状态：`PAUSED / INTERRUPTED / FAILED / CANCELLED / BLOCKED`。

规则：
- 每次实际采集有唯一 `capture_session_id`；
- Session 绑定一个 CollectionTask、Robot、Device、Sensor Set、Calibration Profile、Collector；
- 支持一任务多 Session；
- 一 Session 可有多个 RawAsset；
- 上传重试不创建新的 Session；
- 重采集创建新 Session，并关联旧 Session；
- Session 完成前必须生成最终 Manifest。

## 7. Manifest 规范

每个 CaptureSession 生成 `capture_manifest.json`，至少包含：

```json
{
  "manifest_schema": "ddp.capture-manifest/1.0.0",
  "capture_session_id": "uuid",
  "collection_task_id": "uuid",
  "project_id": "uuid",
  "robot": {"robot_id": "uuid", "vendor": "vendor-name", "model": "robot-model", "firmware_version": "if-known"},
  "devices": [{"device_id": "uuid", "device_type": "camera", "calibration_profile_id": "uuid"}],
  "clock": {"source": "device|host|ros|ptp|unknown", "time_unit": "ns|us|ms|s", "monotonic_available": true},
  "streams": [{"stream_id": "camera-head", "stream_type": "rgb_video", "topic_or_channel": "/camera/head/image", "frame_id": "camera_head_optical_frame", "frame_convention": "ROS_REP_103", "nominal_rate_hz": 30}],
  "artifacts": [{"artifact_id": "uuid", "relative_path": "media/head.mp4", "media_type": "video/mp4", "size_bytes": 123456, "sha256": "hex", "duration_ms": 12000}],
  "started_at": "RFC3339",
  "ended_at": "RFC3339",
  "agent_version": "x.y.z",
  "adapter_id": "filesystem-export",
  "adapter_version": "1.0.0"
}
```

字段可以缺省，但必须区分 `unknown`、`not_provided`、`not_applicable`，不得用 0 伪造。

## 8. 本地 Spool 与弱网恢复

建议目录：
```text
DDP_EDGE_HOME/
  config/
  spool/pending/
  spool/uploading/
  spool/uploaded/
  spool/failed/
  manifests/
  logs/
  cache/
```

要求：文件先写临时文件，fsync 后原子 rename；Manifest 在文件关闭后生成；每个文件独立 SHA256；上传状态持久化到本地轻量状态存储；断网后保留待上传队列；恢复网络后继续；服务器校验回执成功后才标记 uploaded；本地清理遵循保留策略，默认不自动删除唯一副本；日志脱敏。

## 9. 首批适配顺序

1. `FilesystemExportAdapter`：厂商软件导出的 MP4 / JSON / CSV / Parquet / MCAP 文件；
2. `MCAPAdapter`：若机器人侧使用 ROS2，优先评估 MCAP；
3. `ROS2BagAdapter`：按实际 ROS 发行版和 storage plugin 验证；
4. Vendor SDK Adapter：仅在拿到真实 SDK、协议和硬件后开发；
5. HDF5 / 其他格式按真实样本推进。

HF LeRobot v3.0 是已验证的公开数据集导入链路，不等于已完成真实机器人采集适配。

## 10. 安全与认证

Agent 使用专用设备身份，不复用个人网页登录 Token；凭据放操作系统安全存储或受限权限配置文件；支持设备身份撤销；TLS；上传授权短时有效；设备只能访问授权 Project / CollectionTask；Manifest 与上传文件做完整性校验；Agent 不接收或执行任意机器人控制命令。

## 11. 验收

- Agent 可注册/识别一个实际接入设备或导出目录；
- Preflight 能识别缺设备、缺标定、磁盘不足和网络失败；
- 生成合法 Manifest；
- 至少 3 个真实 Episode 对应的文件被正确索引；
- 中断上传后可恢复，且服务器 SHA256 一致；
- 重复触发上传不会生成重复 RawAsset；
- CaptureSession 与 CollectionTask / Device / Sensor 关联正确；
- 未提供的硬件能力标记 BLOCKED，不得 Mock 成成功。
