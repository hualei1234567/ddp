# 04｜端侧采集 Agent 功能设计

## 1. 现状和边界
Robot/Device/Sensor/Calibration 与 CaptureSession 已 VERIFIED；Edge Agent、身份、Lease、Heartbeat MISSING。Agent 是数据采集和交付进程，不是机器人运动控制器。

推荐部署在采集工作站、Ubuntu 工控机或边缘计算机；通过厂商 SDK、ROS/ROS2 导出或目录监听接入；向 DDP 发起出站 HTTPS；复用现有 Upload API；不安装/侵入机器人运动控制器，不提供任意远程 shell。

**D2 决策门禁：** 最新审计只说明 V31-010 ADR 的 D2 等待用户裁决，没有列出 D2 的具体选项。必须先读 ADR 原文，列出原始选项和影响，用户确认后再做 Agent 持久化结构和 Runtime。

## 2. 注册与绑定
管理员在现有设备/采集页面为工作站生成一次性配对凭证。Agent 首次启动提交配对凭证，服务端校验有效期、ProjectScope、设备范围并签发 Agent 专属身份。Agent Token 与用户 JWT 分离；支持轮换、撤销；凭据不回显、不写日志、不写 Manifest。

## 3. 心跳
Agent 上报 agent_id/version、OS/architecture、Adapter versions、disk free、network、current Session、Spool pending count/bytes、upload queue、last error、heartbeat timestamp。页面显示在线、延迟、离线、版本过旧、磁盘告警、上传积压。心跳周期和失联阈值可配置；建议初始 30 秒心跳、90 秒失联阈值，Pilot 后调整。

## 4. Lease
Agent 执行 CaptureSession 前申请 Lease，绑定 Agent、Session、设备集合和过期时间。一个 Session 同时只能有一个 active Lease。Agent 周期续租；Lease 过期不能启动新采集；服务端重新分配需记录原因。网络短断宽限期、长断后停止/挂起策略由 ADR 和安全要求确定。

## 5. Preflight
逐项 PASS/WARN/BLOCKED：身份、Agent 版本、任务分配、Session 状态、设备/传感器可用性、标定、Adapter、磁盘、时钟、网络、Spool 可写、上一 Session 未完成数据。BLOCKED 不能被普通继续按钮绕过；WARN 按任务策略允许继续并记录证据。

## 6. Adapter
统一能力：discover、capabilities、preflight、start/stop（仅真实支持时）、list_artifacts、read_metadata、health_check。首批 Filesystem Export；再按真实 ROS 版本验证 MCAP/rosbag2；之后 HDF5/MP4/JSON/CSV；厂商 SDK 必须有授权 SDK 和真实设备。

文件导入 Adapter 不得显示为实时控制/实时采集已支持。

## 7. 本地 Spool
文件先写临时目录，完成后 fsync、SHA256、Metadata、Manifest，再原子移动到 pending。上传时进入 uploading；服务端校验回执后进入 uploaded；永久失败进入 failed/quarantine。Agent 重启从本地状态恢复。默认不自动删除唯一原始副本。轻量状态可用 SQLite，但不与 DDP PostgreSQL 主库混用。

## 8. Manifest
包含 schema version、agent/version、capture_session_id、collection_task_id、project_id、robot/device/sensor IDs、calibration IDs/versions、start/end、clock source/unit/sync metadata、adapter/version、streams、artifacts（path/MIME/size/SHA256/duration/format）、warnings 和 manifest hash。服务端再次验证，不能信任 Agent 自报 verified。

## 9. 上传恢复
复用现有 multipart API。Agent 持久化 UploadSession ID、part number、ETag 和恢复状态。URL 过期重新申请签名，不重建 Session。重复 ACK/complete 幂等；Hash/ETag 不匹配隔离；服务端回执返回 RawAsset ID、checksum 和 Ingestion 状态。

## 10. 运维
页面显示版本、心跳、Session、磁盘、Spool、上传积压、Adapter 健康、最近错误。支持撤销、重新配对、诊断包、暂停新任务派发。禁止任意远程 shell 和机器人运动控制。

## 11. 验收
配对/撤销；心跳丢失检测；Lease 互斥；重启恢复；真实文件 Manifest；断网续传；SHA256 一致；Session/Task/设备关系一致；重复上传无资产分叉。未接真实硬件时标记 `HARDWARE_CAPTURE_NOT_TESTED`。
