# 17｜Staging 与真实硬件 Pilot

## 1. 现状
FR-038 Staging 因主机/网络/Secrets 缺失 BLOCKED；FR-039 真实硬件 Pilot 因采集硬件缺失 BLOCKED。V5.1 可以准备自动化部署和 Pilot Runbook，但不能宣称已经部署或验收。

## 2. Staging 最小环境
前端构建产物、FastAPI、PostgreSQL、MinIO、Outbox Worker / QC Worker / Processing Worker、Edge Agent 测试工作站、Secrets 管理、日志和指标、TLS/域名/网络策略、Migration 和回滚策略、E2E 测试数据。沿用现有部署方式，不默认引入新基础设施。

## 3. Staging Gate
健康检查、Migration、OpenAPI、MinIO 写读、Worker 消费、Outbox 无异常积压、Agent 配对/心跳/上传、Processing、Workbench、QC、Dataset Export、权限 E2E、日志脱敏和备份恢复通过后才标 READY。

## 4. Hardware Pilot 前置
机器人/设备/传感器型号明确；厂商 SDK / ROS / 导出方式获授权；真实样本可采集；Calibration 可获取；采集任务和 SOP；Agent 工作站、网络、磁盘容量；数据许可和隐私审查；故障应急联系人。

## 5. Pilot 流程
设备盘点 → Calibration Snapshot → Preflight → CaptureSession → Agent Lease → 采集或导出 → Manifest → Upload → RawAsset → Parser → SensorStream/Observation/Action → Sync → Episode → Cleaning → QC → Dataset → Loader Frame Verify → Lineage。

## 6. Pilot 验收证据
设备型号、固件、传感器列表、样本 Hash、Manifest、Agent 版本、Session ID、ProcessingRun、Episode IDs、Stream 样本、同步报告、清洗报告、Dataset Manifest、Loader 输出和录屏/日志。无真实硬件时状态必须是 `HARDWARE_CAPTURE_NOT_TESTED`；公开数据集只能标记 `PUBLIC_DATASET_INTEGRATION_TESTED`。
