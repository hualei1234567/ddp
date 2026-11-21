# 采集任务、CaptureSession 与任务关联实施方案

## 1. 目标

将现有 `domain/collection` 和 `domain/devices` 能力与新增 Edge Agent 贯通。CollectionTask 是采集业务指令，CaptureSession 是一次实际采集发生，RawAsset 是采集产物。

## 2. 任务对象职责

### CollectionTask
定义 Project、Requirement、Scenario、ActionSpec、Robot/Device/Sensor requirements、Calibration Profile requirement、SOP version、capture parameter、target episode count/duration、assigned collector、due date/priority 和 acceptance criteria。

### CaptureSession
记录 collection_task_id、collector_id、robot_id/device_ids/sensor_ids、calibration snapshot references、edge_agent_id/agent_version、started_at/ended_at、clock metadata、session status、manifest checksum、upload status、processing status 和 failure/interruption reason。

### RawAsset
每个上传文件独立记录 capture_session_id、asset role、object key/storage ref、checksum/size/MIME/media format、source relative path、verification status、raw metadata。

## 3. 任务流程

```text
DataRequirement → CollectionPlan / SOP → CollectionTask (DRAFT)
 → APPROVED → READY → ASSIGNED → IN_PROGRESS
 → SUBMITTED → DATA_CHECKING
       ├─ failed → REWORK / BLOCKED
       └─ accepted → PROCESSING → EPISODE_READY
```

最终状态必须核对现有 Collection 状态机。不得直接在前端写状态字符串或绕过领域状态机。

## 4. 任务执行页

采集员打开任务后必须看到任务目标、场景、动作说明、SOP 版本、设备/传感器要求、标定状态、预期数据流、采集参数、任务负责人和截止时间、最近 CaptureSession、上传状态与错误。

采集员操作：
1. 执行 Preflight；
2. 确认设备/传感器；
3. 开始 Session；
4. 暂停/恢复（设备支持时）；
5. 停止采集；
6. 检查本地 Manifest；
7. 上传；
8. 等待服务器校验；
9. 提交采集任务。

## 5. CollectionTask 与 AnnotationTask 的边界

一个 CollectionTask 可产生多个 CaptureSession；一个 CaptureSession 可解析出多个 Episode；一个或多个 Episode 可组成 AnnotationTask Batch。不要把 CollectionTask 直接改造成标注任务。

## 6. 任务与设备约束

设备必须处于可用状态；传感器和标定配置必须符合任务要求；采集时记录设备状态快照；任务完成后不能静默改写实际使用设备；设备异常需生成故障记录；未配置的机器人类型允许使用文件导入方式，不得阻断通用数据集导入。

## 7. 验收

- 任务能指定 Collector；
- Session 创建时校验任务权限和设备状态；
- Session 与 Robot / Device / Sensor / Calibration 正确关联；
- 上传后 RawAsset 能反向找到 Session 和 CollectionTask；
- 同一任务可有多个 Session；
- 任务取消、Session 中断和重新采集均有审计；
- 真实状态转换有测试覆盖。
