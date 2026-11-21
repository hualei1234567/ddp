# Claude Code 总执行 Prompt：DDP V3.1 全链路真实数据生产闭环

你现在继续建设已有 DDP 具身智能数据闭环平台。你的角色是 General Owner / Senior Product Manager / System Architect，协调 Claude Code 原生 Agent 完成实施。

## 一、必须先读的文件
1. `DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md`
2. `DDP_数据接入与样本验收中心_PRD_V1.3_全局复用优先版.md`
3. `DDP_V2.0_N0-N9_下一阶段建设任务蓝图_FINAL.md`
4. `DDP_V2.1_下一阶段建设包_FINAL.zip` 内全部文档
5. `05-现状审计报告-2026-11-14.md`（包含 2026-11-15 追加复核）
6. 本 V3.1 包内全部建设文档。

## 二、项目事实与边界

这是已有项目的持续升级，不是新建项目。审计快照并非当前 HEAD；必须重新读取仓库现状。已知基础能力包括设备、采集、上传、Episode、Annotation 后端、Dataset、Training Request 和 ModelOps。真实 HF / LeRobot v3.0 / MinIO / HTTP Range 视频 / 人工审核后端链路有历史 E2E 证据。标注控制台和质量前端是本次重点缺口，但仍须核对当前 HEAD 后再确认。

执行环境：macOS + VS Code + Claude Code CLI + Claude Code 原生 Agent；Git Branch + Git Worktree；GitHub 是唯一代码托管、PR、Review、CI/CD。不得引入 GitLab、DSH、Codex；不得擅自更换 FastAPI / React / PostgreSQL / MinIO / Ant Design；不得默认增加 Kafka / Celery / Kubernetes / 微服务。

## 三、第一步：只读复核，不要立即写代码

检查 Git status / HEAD / branch / uncommitted changes；current migrations / schema / OpenAPI；CollectionTask / Device / Sensor / Calibration；Upload / Ingestion / RawAsset / MinIO；Episode / MediaAsset / Stream；Annotation Task / Assignment / Schema / Result / Review；Dataset / DatasetVersion / Export；Training Adapter / ModelOps；前端真实路由、页面、组件和 API Client；现有测试、PG 测试、CI、E2E 证据。

对每项标记 VERIFIED / PARTIAL / UNVERIFIED / MISSING / BLOCKED，并附路径、接口、测试或运行证据。不要以文件名或旧报告代替现状验证。

## 四、第一轮交付后暂停

先输出：
1. `V3.1_Current_Baseline.md`
2. `V3.1_Capability_Matrix.md`
3. `V3.1_Annotation_Console_Gap.md`
4. `V3.1_End_to_End_Data_Lineage.md`
5. `V3.1_API_DB_Reuse_Map.md`
6. `V3.1_WBS_Backlog.md`
7. `V3.1_Risk_NoGo.md`
8. `V3.1_Dependency_DAG.md`

完成后停止，等待用户确认，不要擅自修改数据库或批量重构。

## 五、建设主线

N0 Baseline → N1 CollectionTask / CaptureSession → N2 Edge Capture Agent → N3 Upload / RawAsset → N4 Adapter / Canonical / Pipeline / Episode → N5 AnnotationTask / Assignment → N6 Annotation Console → N7 QC / Review / Rework → N8 Dataset Builder / Freeze / Export → N9 Real E2E Pilot / Hardening。

## 六、强制业务规则

- CollectionTask、AnnotationTask、LeRobot task_index 是三个不同概念，不得混用。
- 一个 CollectionTask 可有多个 CaptureSession；一个 Session 可产生多个 RawAsset 和 Episode。
- AnnotationTask 必须绑定 Episode/Clip、TaskSpec、LabelSchema Version。
- 一个 AnnotationTask 同一时刻最多一个 active primary assignee；双标需要两个独立 WorkItem。
- 非 assignee 不得编辑；Reviewer 不能修改 Annotator 原始提交。
- Draft 可自动保存；Submit 后形成不可变 Result Version。
- Rework 必须保留旧结果、审核意见和返工次数。
- Dataset Builder 默认只纳入 Raw VERIFIED、Processing READY、Annotation Submitted、Review ACCEPTED、Quality Gate PASS 的样本。
- Frozen DatasetVersion 不可静默修改。
- LeRobot Reader 已有不代表 LeRobot Writer 已有；导出必须 round-trip 验证。
- 外部训练 Adapter 若仍 503，必须明确 BLOCKED，不得 Mock 成成功。

## 七、复用优先

优先复用现有 Collection / Devices / Ingestion / S3 / Outbox / Workers、Episode / Annotation / Dataset / ModelOps、Identity / ROLE_PERMISSIONS / ProjectScope、If-Match / Idempotency / Audit、API Client / Upload Queue / PageContainer / PageState / AntD、GitHub Actions / OpenAPI freeze / Migration tests。

新功能开发前必须输出：`Existing / Reusable / Gap / Proposed Change / Risk / Tests`。

## 八、工程质量

Domain 不依赖 HTTP / ORM / SDK；Controller 不写业务规则；不重复状态机、校验、权限和上传实现；不写万能 utils；不吞异常；不使用动态 f-string 事件类型绕过静态审计；新增文件原则上不超过 800 行；历史 Migration 禁止修改；新 Migration 执行 PostgreSQL upgrade → tests → downgrade → tests → upgrade → tests；API 变更同步 OpenAPI / TS Types / Client / Tests / Contract Baseline；GitHub PR 必须含测试证据、风险和回滚方案。

## 九、标注控制台是本版本核心交付

必须实现真实交互：标注任务队列、指定任务/指定标注人、任务工作台、视频播放器、时间轴、Schema-driven Label Editor、Segment/Event 编辑、自动保存、版本冲突处理、提交、Review/Rework、我的任务和进度。不接受只做静态页面、截图或 Mock API。

## 十、真实端到端验收

至少跑通：真实设备或真实设备导出文件 → CollectionTask → CaptureSession → Edge Agent / File Adapter → Manifest → Upload / MinIO → RawAsset → Adapter / Canonical → Episode → AnnotationTask → 指定 Annotator → Annotation Console → Submit → QC / Review → DatasetVersion → Manifest / Hash → Export → 回读验证。

若没有物理硬件，明确标记 `HARDWARE_CAPTURE_NOT_TESTED`，可以先做真实导出文件 Pilot，但不能宣称实时硬件采集通过。

## 十一、执行纪律

每个任务一次只改一个明确范围。先检查 Git status；不覆盖用户改动；未经用户批准不执行破坏性 Migration、数据删除、主分支 Push/Merge、Release 或生产部署。

当前第一步只做 N0 复核和交付物，等待确认后再开发。
