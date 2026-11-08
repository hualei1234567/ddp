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
| `ddp.device/1.0.0` | 阶段 1 设备域冻结目标 | 已批准 |
| `ddp.stage2/1.0.0` | 阶段 2 契约冻结版本 | v1.0 FROZEN |

阶段 1 尚未关闭的实现差距不在本快照中声明为可用，清单见
`docs/architecture/阶段1-设备域冻结字段实现差距与排期.md`。

阶段 2 的冻结字段、端点清单、错误码、`If-Match`/`Idempotency-Key` 与变更流程见
`docs/architecture/阶段2-契约冻结-v1.0.md`。

## 文件

| 文件 | 内容 |
|---|---|
| `openapi.yaml` | OpenAPI 3.1 唯一入口；声明信息、路径键与组件引用 |
| `fragments/paths.stage1.yaml` | 阶段 1 路径项与操作 |
| `fragments/paths.stage2.yaml` | 阶段 2 路径项与操作 |
| `fragments/schemas.stage1.yaml` | 阶段 1 Schema |
| `fragments/schemas.stage2.yaml` | 阶段 2 Schema |
| `fragments/schemas.stage2.required.yaml` | 阶段 2 服务端必填约束更正（Episode 标定快照、IngestionJob 输入） |
| `fragments/schemas.common.yaml` | 稳定错误码与错误体 |
| `fragments/components.yaml` | 公共参数与错误响应 |
| `redocly.yaml` | OpenAPI lint 规则（Redocly CLI） |
| `scripts/check-compatibility.sh` | 基于 git 基线的 `oasdiff breaking` 检查（支持 fragments） |
| `scripts/check-stage2-freeze.sh` | 阶段 2 基线冻结、lint 与兼容性检查 |
| `baselines/` | 自包含冻结基线（由脚本生成，禁止手工编辑） |
| `reports/` | 冻结/兼容性检查报告（由脚本生成） |
| `schemas/*.json` | 可复用 JSON Schema（错误体等） |

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

## 本地校验

```bash
# Redocly lint
npx --yes @redocly/cli@1 lint packages/contracts/openapi.yaml \
  --config packages/contracts/redocly.yaml

# 阶段 2 基线冻结（首次）
bash packages/contracts/scripts/check-stage2-freeze.sh --freeze

# 阶段 2 基线与当前契约兼容性
bash packages/contracts/scripts/check-stage2-freeze.sh --check

# git 基线兼容性（CI/PR）
bash packages/contracts/scripts/check-compatibility.sh \
  packages/contracts/openapi.yaml origin/main
```

CI 的 `contract` job 会对 PR 使用 `pull_request.base.sha`、对 push 使用
`github.event.before` 作为基线；首次提交或无基线时跳过并明确提示。

> 本次 DDP-312 会话无 Bash 工具，上述 Redocly/oasdiff/脚本命令均 **NOT RUN**；
> 不以此声称 lint 或兼容性检查已通过。执行记录见
> `docs/architecture/阶段2-契约冻结-v1.0.md`。

## 版本与变更流程

1. 提出差异与影响（前端/后端/测试/文档），附兼容性判断。
2. Architect 评审；破坏性变更（删除/改名/改类型/改语义/必填化/状态迁移变化）
   必须新建 ADR 并获用户批准。
3. 更新契约、lint 规则与兼容性基线；补契约测试。
4. 执行 Redocly lint、`check-stage2-freeze.sh --check` 与相关 API 测试。
5. 通知所有相关角色；冻结产物记录版本、日期、SHA256 与批准人。

版本策略：

- `1.0.0`：阶段 1 + 阶段 2 已实现端点冻结快照（DDP-312）。
- `ddp.device/1.0.0`：设备域冻结目标（已批准）；差距关闭后才允许调整设备域快照。
- `ddp.stage2/1.0.0`：阶段 2 冻结版本；破坏性变更需新 ADR 并提升契约版本。
- `/api/v1`：HTTP 主版本；移除/重命名/改语义属于破坏性变更。
