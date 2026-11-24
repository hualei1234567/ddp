# Claude Code 执行总 Prompt｜DDP V5.1

你负责在现有 DDP 仓库上实施 V5.1。V5.1 是真实数据生产闭环与训练就绪版本。不要新建顶层业务模块，不要重写 V4.1 已 VERIFIED 能力，不要用 Mock 冒充真实验收。

## 执行环境
统一使用 VS Code + Claude Code CLI + Claude Code 原生 Agent + Git Branch/Worktree。不得引入 DSH / DeepSeek Harness 或 Codex。遵循仓库现有技术栈、CI、目录和 ADR。

## 必读材料
用户提供的 V4.1 全面审计报告（2026-11-24）；仓库 `docs/roadmap/v4.1/` 全部蓝图；V4.1 FR 矩阵、risk-list、gap-matrix、task-dag；Canonical、Adapter、Processing、Dataset、LeRobot、Lineage、Retention、Agent 相关 ADR；本 V5.1 蓝图全部文档；README、Architecture、CLAUDE.md、OpenAPI、Migration、tests。

## 第一阶段：只读复核，不改代码
检查 git status/branch/HEAD/remote；Alembic heads/当前 DB；OpenAPI version/path count；scripts/check.sh；backend/frontend tests/build；E2E；现有 Adapter 真实能力；Canonical SensorStream/Observation/Action 实际 producer；LeRobot 当前 metadata/frame/video/observation/action 读取能力；Lineage/Retention/RBAC/Recovery 现状；最新 risk-list；真实样本文件、许可、格式、传感器和动作源。

交付：
- `V51_Current_Baseline.md`
- `V51_FR_Status_Matrix.md`
- `V51_Existing_Code_Reuse_Map.md`
- `V51_Golden_Sample_Inventory.md`
- `V51_Canonical_Gap_Report.md`
- `V51_Adapter_Capability_Matrix.md`
- `V51_Risk_Delta.md`
- `V51_Execution_DAG.md`
- `V51_No_Duplicate_Work_Report.md`

第一阶段完成后暂停，向用户汇报 Golden Path 建议和真实样本缺口。未经用户确认，不做大规模 Migration 或 Canonical 破坏性变更。

## 实施主线
A. 先治理：RBAC 浏览器 E2E、标注冲突恢复、Frozen Dataset 引用保护、恢复演练、QC 证据定位。  
B. 数据主线：Golden Path Adapter → SensorStream → Observation/Action → Time Sync/Calibration → Episode → Cleaning → step retry。  
C. 训练就绪：LeRobot Frame-level Loader → Dataset Gate → Lineage E2E。  
D. 生产验收：Staging、真实硬件 Pilot。  
E. 条件性：GPU AI 推理、外部训练平台连接器。

## 强制规则
复用 V4.1 Agent 配对/心跳/Lease/Spool/Manifest/multipart 上传；复用现有 MinIO、Outbox、Worker、QC、Dataset Builder、RBAC；RawAsset 不可变；无真实 Action 源不生成 ground-truth Action；未知 Unit/Coordinate Frame 不猜测；原始时间戳保留，归一化时间另存；Adapter 能力分级，不把 DETECT 当完整支持；DATA_CLEANING 每条规则有版本、阈值、证据和可恢复策略；重试幂等；METADATA_ONLY 不等于 FRAME_LEVEL；Frozen Dataset 引用资产禁止静默删除；BLOCKED 必须有解除条件；公开数据测试不冒充真实硬件 Pilot；Mock/Stub/DRY_RUN 必须显式标识。

## 工程纪律
不修改历史 Migration；不为 UI 字段随意加 DB 列；Migration 在真实 PostgreSQL 做 upgrade/test/downgrade/test/upgrade/test；API 改动同步 OpenAPI、TS Types、Client、contract baseline；Outbox 事件必须有消费者和测试；不吞异常、不静默跳过；每个 PR 关联 V51 ID、测试、证据、风险和回滚；未经用户确认不 Push/Merge 主分支、不部署生产、不删除数据。

## 完成标准
V5.1 核心完成必须有一条真实数据链：RawAsset → Stream/Observation/Action → Sync → Episode → Cleaning → Annotation/QC → Dataset → LeRobot Frame-level Loader → Lineage。硬件、GPU、Staging、外部训练 API 缺失时分别标记 BLOCKED，不得以文档或 Mock 替代。
