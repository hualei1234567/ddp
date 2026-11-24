# 契约包

## 原则

- 契约优先：先定义 OpenAPI / JSON Schema，再实现两端。
- 破坏性变更必须版本化；新增字段优先保持兼容。
- `packages/contracts` 由 System Architect 作为唯一 Owner；其他角色不得并行修改。
- `openapi.yaml` 是**已实现端点快照**；冻结目标字段以对应冻结文档为准。
  未实现字段不得在快照中伪装为可用。

## 版本

| 版本 | 范围 | 状态 |
|---|---|---|
| `1.0.0` | 阶段 1 设备域 + 阶段 2 采集/上传/RawAsset/Episode/IngestionJob 已实现端点 | **FROZEN（DDP-312）** |
| `1.1.0` | 在 `1.0.0` 基础上增量加入阶段 3 标注域已实现端点 | **FROZEN（DDP-323）** |
| `1.2.0` | 在 `1.1.0` 基础上增量加入阶段 4 数据集与训练支撑已实现端点 | **FROZEN（DDP-331）** |
| `1.3.0` | 在 `1.2.0` 基础上增量加入阶段 5 模型闭环已实现端点 | **FROZEN（DDP-341）** |
| `1.4.0` | 在 `1.3.0` 基础上增量加入阶段 6 供应商与到货验收已实现端点 | **FROZEN（DDP-361）** |
| `1.5.0` | 在 `1.4.0` 基础上增量加入阶段 7 数据平面第一批（HF 数据源登记 + 导入任务） | **FROZEN（阶段 7 第一批）** |
| `1.6.0` | 在 `1.5.0` 基础上补齐设备域已实现字段（G-20：`frame_id`/`frame_convention`、`calibration_version`/Profile 快照） | 已发布 |
| `1.7.0` | 在 `1.6.0` 基础上增量加入 G-01 端点：`/robots` 全套与 `/calibration-profiles` | 已发布 |
| `1.8.0` | 在 `1.7.0` 基础上增量加入 N3–N9 已实现端点：采集素材四实体、标注质量检查与 AI 预标注建议、DatasetVersion 血缘；并补齐 `training-deliveries[].requirement_match`、`data-gaps[].items[].coverage_source` 与错误码 `training_delivery_requirement_unmet`；另收录质量检查「提交即自动执行」语义与 AI 建议采纳统计 `.../ai-suggestions/agreement` | 已发布（v0.2.0） |
| `1.35.0` | 在 `1.34.0` 基础上增量加入 QC 证据定位（V51-010 / FR-025）：`QualityRunFinding.evidence_location` 与 `QualityEvidenceLocation` / `QualityEvidenceTimeRange`，配合迁移 0073 的四列 | 当前 |
| `1.9.0` | 在 `1.8.0` 基础上增量补齐采集素材写路径（四类素材各 PATCH / `actions/{action}` / DELETE，共 12 个操作），状态机 `DRAFT → ACTIVE → ARCHIVED` 完整可达；解除 `1.8.0` 中「状态恒为 DRAFT」的限制 | 当前 |
| `ddp.device/1.0.0` | 阶段 1 设备域冻结目标 | 已批准 |
| `ddp.stage2/1.0.0` | 阶段 2 契约冻结版本 | v1.0 FROZEN |
| `ddp.stage3/1.0.0` | 阶段 3 标注契约冻结版本 | v1.0 FROZEN |
| `ddp.stage4/1.0.0` | 阶段 4 数据集与训练契约冻结版本 | v1.0 FROZEN |
| `ddp.stage5/1.0.0` | 阶段 5 模型闭环契约冻结版本 | v1.0 FROZEN |
| `ddp.stage6/1.0.0` | 阶段 6 供应商与到货验收契约冻结版本 | v1.0 FROZEN |
| `ddp.stage7/1.0.0` | 阶段 7 数据平面第一批契约冻结版本 | v1.0 FROZEN |

阶段 1 尚未关闭的实现差距不在本快照中声明为可用，清单见
`docs/architecture/阶段1-设备域冻结字段实现差距与排期.md`。

阶段 2 的冻结字段、端点清单、错误码、`If-Match`/`Idempotency-Key` 与变更流程见
`docs/architecture/阶段2-契约冻结-v1.0.md`。

阶段 3 的端点矩阵、错误码、状态机同步、兼容与变更流程见
`docs/architecture/阶段3-契约冻结-v1.0.md`。未实现的样本项、工作量、血缘、
审计与指标端点不在本快照中声明为可用。

阶段 4 的端点矩阵（55 个操作）、错误码、状态机与草案差异见
`docs/architecture/阶段4-契约冻结-v1.0.md`。设计草案中的资产目录、
Dataset/TrainingRequest 血缘与 DatasetVersion PATCH 未实现，不在本快照中声明为可用。

阶段 5 的端点矩阵（31 个操作）、错误码、状态机与草案差异见
`docs/architecture/阶段5-模型闭环契约冻结-v1.0.md`。自动标注优化（P1）与真实评测/推理执行
未实现，不在本快照中声明为可用。

阶段 6 的端点矩阵（17 个操作）、错误码与命名冲突处理见
`docs/architecture/阶段6-供应商与到货验收契约冻结-v1.0.md`。

## 文件

| 文件 | 内容 |
|---|---|
| `openapi.yaml` | OpenAPI 3.1 唯一入口；声明信息、路径键与组件引用 |
| `fragments/paths.stage1.yaml` | 阶段 1 路径项与操作 |
| `fragments/paths.stage2.yaml` | 阶段 2 路径项与操作 |
| `fragments/paths.stage3.yaml` | 阶段 3 标注域路径项与操作（仅已实现端点） |
| `fragments/schemas.stage1.yaml` | 阶段 1 Schema |
| `fragments/schemas.stage2.yaml` | 阶段 2 Schema |
| `fragments/schemas.stage2.required.yaml` | 阶段 2 服务端必填约束更正（Episode 标定快照、IngestionJob 输入） |
| `fragments/schemas.stage3.yaml` | 阶段 3 请求 DTO、分页包装与共享枚举 |
| `fragments/components.stage3.yaml` | 阶段 3 路径参数、动作白名单与响应引用 |
| `fragments/paths.stage4.yaml` | 阶段 4 数据集与训练域路径项与操作（仅已实现端点） |
| `fragments/schemas.stage4.yaml` | 阶段 4 请求 DTO、枚举、分页包装与动作请求体 |
| `fragments/components.stage4.yaml` | 阶段 4 路径参数与动作白名单 |
| `fragments/paths.stage5.yaml` | 阶段 5 模型闭环域路径项与操作（仅已实现端点） |
| `fragments/schemas.stage5.yaml` | 阶段 5 模型、评测、推理、反馈与数据需求 Schema |
| `fragments/components.stage5.yaml` | 阶段 5 路径参数与动作白名单 |
| `fragments/paths.stage6.yaml` | 阶段 6 供应商与到货验收路径项与操作 |
| `fragments/schemas.stage6.yaml` | 阶段 6 供应商、批次、片段与账本 Schema |
| `fragments/components.stage6.yaml` | 阶段 6 路径参数与动作白名单 |
| `fragments/schemas.common.yaml` | 稳定错误码与错误体 |
| `fragments/components.yaml` | 公共参数与错误响应 |
| `redocly.yaml` | OpenAPI lint 规则（Redocly CLI） |
| `scripts/check-compatibility.sh` | 基于 git 基线的 `oasdiff breaking` 检查（支持 fragments） |
| `scripts/check-stage2-freeze.sh` | 阶段 2 基线冻结、lint 与兼容性检查 |
| `scripts/check-stage3-freeze.sh` | 阶段 3 基线冻结、lint、SHA256 与兼容性检查 |
| `scripts/check-stage4-freeze.sh` | 阶段 4 基线冻结、lint、SHA256 与兼容性检查 |
| `scripts/check-stage5-freeze.sh` | 阶段 5 基线冻结、lint、SHA256 与兼容性检查 |
| `scripts/check-stage6-freeze.sh` | 阶段 6 基线冻结、lint、SHA256 与兼容性检查 |
| `baselines/` | 自包含冻结基线（由脚本生成，禁止手工编辑） |
| `reports/` | 冻结/兼容性检查报告（由脚本生成） |
| `schemas/*.json` | 可复用 JSON Schema（错误体、阶段 3/4/5 响应对象、状态机与错误码注册表） |
| `schemas/canonical-v1/*.json` | **生成产物**：Canonical v1 六个实体的 JSON Schema，由 `scripts/export_canonical_schemas.py` 从 `data-platform/ddp_data/canonical/v1.py` 的字段声明导出。**禁止手改**——`check.sh` 与 CI 做逐字节比对（ADR-0040） |

`openapi.yaml` 通过相对 `$ref` 引用 `fragments/`。Redocly 与 oasdiff 的输入始终是
`packages/contracts/openapi.yaml`；基线脚本会用 Redocly bundle 生成自包含文件。

## 当前覆盖（1.0.0）

阶段 0/1：

- `GET /health`、`GET /api/v1/platform/meta`
- Devices：列表、创建、详情、修改、`activate`、`maintenance`、`fault`、`suspend`、`retire`
- Sensors：列表、创建、详情、修改、`activate`、`maintenance`、`fault`、`suspend`、`retire`
- Calibrations：列表、创建、详情、修改、`submit`、`validate`、`reject`、`activate`、`revoke`、`expire`

阶段 2：

- CollectionTasks：列表、创建、详情、修改、`actions/{action}`
- CollectionBatches：列表、创建、详情、修改、`actions/{action}`
- UploadSessions：创建、断点详情、分片列表、`parts/{n}/url`（可选 `sha256`）、
  `parts/{n}/ack`、`complete`（202，返回 `uploaded_parts`）、`abort`
- RawAssets：列表、详情、`lineage`、`download-url`
- Episodes：创建、列表、详情、`actions/{action}`、`revisions`、`lineage`
- IngestionJobs：创建、列表、详情、`actions/{action}`（`cancel`/`retry`）

阶段 2 未实现/未冻结为 HTTP 端点的动作（如隔离放行、墓碑、重新校验、Job 日志 URL、
Episode Manifest 下载）**不在本快照中声明**。

阶段 3（40 个路径项 / 54 个操作）：

- AnnotationSchemas：列表、创建、详情、修改、`actions/{action}`
- AnnotationSampleSets：列表、创建、详情、`actions/{action}`（`preview`/`snapshot` 等）
- AnnotationTasks：列表、创建、详情、修改、`actions/{action}`、任务批次列表/拆分
- AnnotationBatches：详情、修改、`actions/{action}`
- AnnotationAssignments：列表、创建、详情、`actions/{action}`
- AnnotationSubmissions：列表、创建、详情、`actions/{action}`、`records`、`reviews`、`arbitrations`
- AnnotationReviews：创建、详情、`actions/{action}`
- AnnotationArbitrations：创建、详情、`actions/{action}`、`votes`
- GoldSets：列表、创建、详情、`answers`、`actions/{action}`、`evaluations`、`injections`
- QualitySamples：列表、创建、详情、`plan`（响应数组）、`actions/{action}`
- IAAReports：列表、创建（同步 201）、详情

阶段 3 未实现/未冻结为 HTTP 端点的能力（样本项列表、工作台、工作量、血缘、审计、
指标摘要、IAA 异步 Job、数据集导出）**不在本快照中声明**。

## 本地校验

```bash
# Redocly lint
npx --yes @redocly/cli@1 lint packages/contracts/openapi.yaml \
  --config packages/contracts/redocly.yaml

# 阶段 2 基线冻结（首次）
bash packages/contracts/scripts/check-stage2-freeze.sh --freeze

# 阶段 2 基线与当前契约兼容性
bash packages/contracts/scripts/check-stage2-freeze.sh --check

# 阶段 3 基线冻结（首次）
bash packages/contracts/scripts/check-stage3-freeze.sh --freeze

# 阶段 3 基线与当前契约兼容性（SHA256 + lint + oasdiff）
bash packages/contracts/scripts/check-stage3-freeze.sh --check

# git 基线兼容性（CI/PR）
bash packages/contracts/scripts/check-compatibility.sh \
  packages/contracts/openapi.yaml origin/main
```

CI 的 `contract` job 会对 PR 使用 `pull_request.base.sha`、对 push 使用
`github.event.before` 作为基线；首次提交或无基线时跳过并明确提示。

> 阶段 2（DDP-312）会话无 Bash 工具，相关命令当时均 **NOT RUN**；不以此声称
> lint 或兼容性检查已通过。执行记录见 `docs/architecture/阶段2-契约冻结-v1.0.md`。
>
> 阶段 3（DDP-323）已实际执行并通过 Redocly lint、`check-stage3-freeze.sh --freeze`
> 与 `--check`、`check-compatibility.sh`（main `7a2264b`）及后端 pytest；详细结果见
> `docs/architecture/阶段3-契约冻结-v1.0.md` 第 8 节。

## 版本与变更流程

1. 提出差异与影响（前端/后端/测试/文档），附兼容性判断。
2. Architect 评审；破坏性变更（删除/改名/改类型/改语义/必填化/状态迁移变化）
   必须新建 ADR 并获用户批准。
3. 更新契约、lint 规则与兼容性基线；补契约测试。
4. 执行 Redocly lint、`check-stage2-freeze.sh --check` / `check-stage3-freeze.sh --check`
   与相关 API 测试。
5. 通知所有相关角色；冻结产物记录版本、日期、SHA256 与批准人。

版本策略：

- `1.0.0`：阶段 1 + 阶段 2 已实现端点冻结快照（DDP-312）。
- `1.1.0`：在 `1.0.0` 上增量加入阶段 3 已实现端点（DDP-323）。
- `ddp.device/1.0.0`：设备域冻结目标（已批准）；差距关闭后才允许调整设备域快照。
- `ddp.stage2/1.0.0`：阶段 2 冻结版本；破坏性变更需新 ADR 并提升契约版本。
- `ddp.stage3/1.0.0`：阶段 3 冻结版本；破坏性变更需新 ADR 并提升契约版本。
- `/api/v1`：HTTP 主版本；移除/重命名/改语义属于破坏性变更。

阶段 7 的 7 个操作（DataSource 登记/查询/改档 + DataSourceImport 登记/查询/状态推进）
见 `fragments/paths.stage7.yaml` 与 `fragments/schemas.stage7.yaml`。
**解析、视频审看、人工审核尚未实现，不在本版本声明内**——不得据
`docs/ddp/01-目标业务架构蓝图.md` 的目标架构推断这些能力已具备。

`revision` 必须是 40 位 commit sha：`main` 等可变引用会移动，只存分支名的导入不可复现。
该约束同时写在 OpenAPI schema、Pydantic 模型与数据库 CHECK 三处，任一层都不放过。
