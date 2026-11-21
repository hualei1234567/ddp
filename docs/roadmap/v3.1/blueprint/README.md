# DDP V3.1 全链路数据生产闭环建设蓝图包

**版本定位：** V3.1 — End-to-End Data Production & Annotation Console Blueprint  
**基线承接：**
- `DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md`
- `DDP_数据接入与样本验收中心_PRD_V1.3_全局复用优先版.md`
- `DDP_V2.0_N0-N9_下一阶段建设任务蓝图_FINAL.md`
- `DDP_V2.1_下一阶段建设包_FINAL.zip`
- `05-现状审计报告-2026-11-14.md`（报告追加验证至 2026-11-15）

## 本版本核心升级

V3.1 把 DDP 从“已有多个业务域和部分真实数据验证链路”推进到“端侧采集—上传—解析—Episode—标注任务分配—标注控制台—质量审核—Dataset 冻结—训练数据导出”的可执行产品闭环。

重点解决：
1. 机器人/端侧设备如何把真实采集数据可靠交给 DDP；
2. 上传后如何关联 CollectionTask、CaptureSession、Robot、Device、Sensor 和 RawAsset；
3. 如何按明确的标注任务 TaskSpec 生成 AnnotationTask，并指定给具体标注人；
4. 如何建设此前缺失的完整 Annotation Console，而不是只有后端状态机；
5. 如何将已审核通过的 Episode 和标注结果构建为可复现训练数据集；
6. 如何用真实端到端证据证明整条链路。

## 重要事实边界

现有系统事实来自 2026-11-14 审计报告及 2026-11-15 追加复核。V2.1 规划包生成于 2026-11-18。当前未提供更新于 2026-11-18 之后的仓库审计结果，因此：
- 本包将上述资料作为“已知基线”，不宣称它等于 2026-11-21 当前代码状态；
- Claude Code 必须先读取当前仓库、Git 状态、Migration、OpenAPI、前端路由、测试和 CI；
- 如果仓库现状与本包基线不同，以最新代码证据为准，并更新 Capability Matrix；
- 没有真实运行证据的功能不得标记 VERIFIED。

## 文件目录

| 文件 | 内容 |
|---|---|
| 01 | V3.1 总建设蓝图与范围 |
| 02 | 当前模块实现地图与差距矩阵 |
| 03 | 端侧机器人采集 Agent 与硬件适配 |
| 04 | 采集任务、CaptureSession 与任务关联 |
| 05 | 上传、断点续传、RawAsset 与 Ingestion |
| 06 | Canonical、Adapter、Pipeline、Episode |
| 07 | 标注任务创建、指定分配与工作量管理 |
| 08 | 标注控制台 PRD、UI/UX 与交互实现 |
| 09 | Label Schema 与 Annotation Result 数据规范 |
| 10 | 质量检查、审核、返工与仲裁 |
| 11 | Dataset Builder、版本冻结、Manifest 与训练导出 |
| 12 | API、事件、状态机与契约演进建议 |
| 13 | 真实端到端链路与验收测试 |
| 14 | 权限、安全、存储、可观测性 |
| 15 | 信息架构、页面路由与组件复用规范 |
| 16 | V3.1 实施 WBS、依赖 DAG 与里程碑 |
| 17 | Claude Code V3.1 总执行 Prompt |
| 18 | 任务卡、PR 模板与 Definition of Done |
| 19 | Manifest 样例与核心数据字典 |

## 建议落地方式

1. 将本包放入当前 DDP GitHub 仓库的 `docs/roadmap/v3.1/`（如仓库已有文档分层，遵循现有目录）。
2. 将 V1.0、V1.3、V2.1、最新审计与本包一起交给 Claude Code。
3. 第一轮只做“现状复核 + 标注控制台缺口拆解 + 端到端任务 DAG”，不立即大规模改代码。
4. 经确认后按 WBS 逐项实施，每个 PR 只覆盖一个可验收任务。
5. 先以 Hugging Face 已验证数据链路做回归，再用真实机器人/端侧采集文件完成 Pilot。

## 当前版本明确不做

- 不重写现有设备、采集、上传、Episode、标注、Dataset、ModelOps 领域；
- 不替换 PostgreSQL、MinIO、FastAPI、React、Ant Design、现有 Outbox Worker；
- 不默认新建微服务、Kafka、Celery、Kubernetes；
- 不做机器人运动控制/远程控制；端侧 Agent 首期只负责采集数据读取、元信息、文件封装、校验与上传；
- 不提前建设多甲方商务、供应商结算、多城市基地经营系统；
- 不把 AI 预标注直接视为最终真值；
- 不把未配置的外部训练平台标记为已接通。

V3.1 是建设蓝图版本，不代表代码版本已发布，也不代表其中所有能力已经实现。
