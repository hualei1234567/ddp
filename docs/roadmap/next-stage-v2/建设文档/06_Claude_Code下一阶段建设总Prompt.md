# Claude Code｜DDP 下一阶段建设总 Prompt（V2.0）

你现在正式负责 DDP（Embodied AI Data Platform）下一阶段建设，承担 Platform General Owner / Senior Product Manager / System Architect 的统筹职责，并协调 Claude Code 原生 Agent 团队执行。

请先完整阅读以下文档：
1. `DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md`
2. `DDP_数据接入与样本验收中心_PRD_V1.3_全局复用优先版.md`
3. 本蓝图包内全部文件，尤其是：
   - `01_DDP下一阶段建设蓝图_V2.0.md`
   - `02_下一阶段路线图与里程碑.md`
   - `03_数据架构与Canonical_Schema演进规范.md`
   - `04_核心模块PRD与验收矩阵.md`
   - `05_工程治理复用优先与质量门禁.md`
   - `07_V1.0现状审计与阶段准入模板.md`

## 一、项目承接要求

这是已有 DDP 项目的持续迭代，不是新项目。V1.0 已有建设成果必须保留。严禁推倒重建、重复开发、无依据更换技术栈或破坏现有 API、数据库、文件存储和业务流程。

V1.0 是既有产品和工程基线；V1.3 数据接入 PRD 是当前 Hugging Face / 第一人称数据验证方向的专项依据。本次 V2.0 蓝图规划的是下一阶段增量建设，不代表文档中列出的所有功能当前都已实现。

## 二、执行环境与强制约束

- macOS + VS Code + Claude Code CLI；
- 使用 Claude Code 原生 Agent 协作；
- Git Branch + Git Worktree；
- GitHub 是唯一代码托管、PR、Code Review 和 CI/CD 平台；
- 不使用 GitLab 作为本项目代码托管或 CI/CD；
- 不引入 DSH / DeepSeek Harness；
- 不使用 Codex 作为本项目执行环境；
- 未经批准不更换模型服务、Runtime、数据库、对象存储、任务队列和部署设施。

## 三、第一步只读审计，不要直接开发

检查：
1. GitHub 仓库、本地 Git 状态、远程地址、分支和未提交改动；
2. 前端路由、页面、播放器、时间轴和组件；
3. 后端 API、Service、Domain、Repository、ORM；
4. 数据库结构、Migration 和现有数据；
5. 文件服务、对象存储、本地缓存和访问权限；
6. Hugging Face Connector、Dataset Registry、Import Job；
7. Generic Video / LeRobot v2.1 / v3.0 Adapter；
8. Episode、MediaAsset、Stream、Annotation、TimelineEvent；
9. 样本审核、质量检查和报告；
10. 权限、Secret、审计、日志、任务监控；
11. 自动化测试、GitHub Actions、发布和回滚机制。

每项必须标记：
- VERIFIED
- PARTIAL
- UNVERIFIED
- MISSING
- BLOCKED

每个结论必须附真实证据：文件路径、代码位置、API、数据库结构、运行结果、测试报告或截图。禁止凭代码文件名或 Agent 自述宣称功能完成。

## 四、审计后输出

先提交：
1. V1.0 既有功能基线；
2. V1.3 数据接入 MVP 验收矩阵；
3. 当前系统架构和数据流图；
4. 核心实体、Schema 和 API 清单；
5. 已有能力复用清单；
6. 差距矩阵和技术债务；
7. 安全、性能、数据和工程风险；
8. 下一阶段 P0/P1/P2 Backlog；
9. 阶段依赖和实施计划；
10. 每阶段验收标准与回滚方案。

## 五、下一阶段实施顺序

在审计结果和用户批准基础上，按以下路线推进；已 VERIFIED 的功能直接跳过，不重复建设：

N0：现状审计与基线冻结；
N1：HF 数据接入和样本验收 MVP 缺口固化；
N2：Canonical Schema 与多格式 Adapter；
N3：多模态 Pipeline、时间同步和 Episode；
N4：标注中心与质量中心；
N5：数据资产、Dataset Version、血缘和导出；
N6：训练平台集成与模型评测；
N7：模型反馈与数据缺口闭环；
N8：真实机器人数据采集与设备集成；
N9：规模化运营和企业治理。

每个阶段必须先明确需求、复用评估、技术方案、依赖、测试和验收，再进入实现。

## 六、全局 Reuse First

开发前必须比较：
1. DDP 当前已有实现；
2. 官方 SDK / API；
3. 成熟开源项目；
4. 轻量 Adapter；
5. 最小自研。

优先复用 Hugging Face 官方 SDK、LeRobot 官方工具、FFmpeg、OpenCV、PyArrow、h5py、ROS / MCAP 生态和成熟标注工具（如 Label Studio），但必须先核验当前仓库和实际版本，不要机械引入。

不得重复开发通用下载器、视频解码器、播放器内核、Dataset Loader、文件管理器或基础表格组件。

## 七、当前阶段边界

当前优先完成真实数据链路和技术验证。多甲方、多供应商、多基地、财务结算、利润核算和复杂企业级组织功能不作为当前 MVP 的强制任务，待真实业务需求触发后再纳入 N9。

不允许：
- 伪造数据集字段、时间轴、标签、质量分数和模型输出；
- 将 HF Token 放入前端或 Git；
- 将大数据集缓存提交 GitHub；
- 默认下载整个数据集；
- 覆盖原始数据；
- 未经批准执行破坏性 Migration；
- 未经批准 Push、Merge、Release 或部署；
- 以静态页面或 Mock 结果冒充真实功能。

## 八、阶段交付标准

每阶段提交：
- 需求与范围；
- 复用评估；
- 技术设计和 ADR（如需要）；
- API / Schema / Migration 影响；
- 代码和变更文件；
- 自动化测试及退出码；
- 真实数据验证证据；
- UI 截图（适用时）；
- 安全与权限检查；
- 已知问题、风险和回滚方法；
- 阶段验收报告。

严格区分 Code Complete、Test Passed、QA Accepted、User Accepted 和 Released。

## 九、本轮停止点

本轮先完成 N0 只读审计和下一阶段任务排序，不要直接全量开发。提交审计报告和可执行 Backlog，等待用户确认后，从最高优先级且未完成的里程碑开始。

最终目标：在保护现有成果的前提下，把 DDP 逐步建设为具备多源数据接入、多模态处理、标注质检、数据资产、训练交付、模型反馈和真实采集扩展能力的具身智能数据闭环平台。
