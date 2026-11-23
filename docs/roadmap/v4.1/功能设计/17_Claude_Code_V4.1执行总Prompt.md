# Claude Code 执行总 Prompt｜DDP V4.1 功能级建设

你负责继续建设已有 DDP 具身智能数据闭环平台。V4.1 的目标是把现有功能真正完成，不增加业务模块，不重复实现已 VERIFIED 能力。

## 必读资料
1. DDP V3.1 全面审计报告（2026-11-23）；
2. `docs/roadmap/v3.1/n0/audit-method.md`；
3. `gap-matrix.md`、`task-dag.md`、`current-system-baseline.md`；
4. V31-010 对应 ADR，尤其 D2 待裁决项；
5. 本 V4.1 蓝图全部文档；
6. 当前仓库 README、Architecture、ADR、OpenAPI、Migration、tests。

## 执行环境和限制
使用 VS Code + Claude Code CLI + Claude Code 原生 Agent + Git Branch/Worktree。以当前仓库 remote 和 CI 配置为准。不使用 DSH/DeepSeek Harness，不使用 Codex。不重建项目、不更换 FastAPI/React/PostgreSQL/MinIO/Ant Design、不默认增加 Kafka/Celery/Kubernetes/微服务、不增加顶层业务模块、不做帧级框选画布。

## 第一轮只读复核
检查 git status、HEAD、origin/main、log、alembic heads、OpenAPI version/path count、scripts/check.sh、backend tests、frontend tests/build、E2E、风险登记册、V31-010 ADR D2。

对 FR-001 至 FR-045 标记 VERIFIED/PARTIAL/UNVERIFIED/MISSING/BLOCKED/NOT_IN_SCOPE，附代码路径、API、测试和运行证据。不要用旧审计状态覆盖新代码状态。

## 第一轮交付后暂停
输出：
- `V41_Current_Baseline.md`
- `V41_FR_Status_Matrix.md`
- `V41_D2_Decision_Brief.md`
- `V41_Existing_Code_Reuse_Map.md`
- `V41_Risk_Register_Reconciliation.md`
- `V41_Execution_DAG.md`
- `V41_No_Duplicate_Work_Report.md`

完成后暂停等待用户确认。不擅自改数据库、批量重构、删除数据或开始 Agent Migration。

## 实施顺序
S0 基线/D2；S1 三级语义服务端校验、QC 批量重跑 UI、LeRobot Loader；S2 Edge Agent；S3 真实传感器数据物化、同步、清洗；S4 真实模型推理；S5 Dataset/Training 收口；S6 Staging/硬件 Pilot/恢复演练。

## 强制复用
每个 FR 开发前说明 Current Implementation、Evidence、Reusable Code、Actual Gap、Proposed Change、DB/API impact、Permission impact、Tests、Rollback。复用现有 Upload/MinIO/状态机/Outbox/QC/Dataset/Workbench/AI Suggestion/Canonical/Identity。

## 强制业务规则
Agent 不控制机器人运动；Agent 独立身份和 Lease；断网本地保留数据；SensorStream/Observation/Action 必须来自真实解析；RawAsset 不被清洗覆盖；L2⊆L1、L3⊆L2 服务端校验；OUTCOME_SPAN 服务端校验；AI 只是建议；批量 QC 幂等；LeRobot Writer 结构合规不等于 Loader 可加载；Loader 未通过保持 UNVERIFIED；MOCK/DRY_RUN 不得显示 REAL；Frozen Dataset 不可静默修改；无硬件标记 HARDWARE_CAPTURE_NOT_TESTED；无模型/训练接口/staging 时如实 BLOCKED。

## 工程纪律
历史 Migration 禁止修改；Migration 做 PG upgrade/test/downgrade/test/upgrade/test；API 同步 OpenAPI/TS Types/Client/Contract Baseline；不吞异常、不静默跳过、不用 Mock 冒充 E2E；前端真实交互测试；每个任务关联 FR/PR/证据；未经用户确认不 Push/Merge 主分支、不发布、不部署生产。

## 最终目标
真实机器人采集或真实机器人导出文件进入平台，完成上传、真实流解析、Episode、标注、质检、Dataset 冻结、LeRobot Loader 加载验证；目标训练平台接口具备时再完成真实训练提交。以用户真实操作和数据链路证据验收，不以菜单或 API 数量验收。
