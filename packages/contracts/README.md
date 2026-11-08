# 契约包

## 原则

- 契约优先：先定义 OpenAPI / JSON Schema，再实现两端。
- 破坏性变更必须版本化；新增字段优先保持兼容。
- `packages/contracts` 由 System Architect 作为唯一 Owner；其他角色不得并行修改。
- `openapi.yaml` 是**已实现端点快照**；冻结目标字段以
  `docs/architecture/阶段1-设备域契约冻结-v1.md` 为准。未实现字段不得在快照中伪装为可用。

## 文件

| 文件 | 内容 |
|---|---|
| `openapi.yaml` | HTTP API 契约；`0.2.0` 覆盖已实现设备域端点与错误码 |
| `redocly.yaml` | OpenAPI lint 规则（Redocly CLI） |
| `scripts/check-compatibility.sh` | 基于 `oasdiff breaking` 的基线兼容性检查 |
| `schemas/*.json` | 可复用 JSON Schema（错误体等） |

## 当前覆盖

`openapi.yaml` `0.2.0` 已覆盖：

- `GET /health`、`GET /api/v1/platform/meta`
- Devices：列表、创建、详情、修改、`activate`、`maintenance`、`fault`、`suspend`、`retire`
- Sensors：列表、创建、详情、修改、`activate`、`maintenance`、`fault`、`suspend`、`retire`
- Calibrations：列表、创建、详情、修改、`submit`、`validate`、`reject`、`activate`、`revoke`、`expire`
- 错误体 `ErrorEnvelope` 与已映射错误码：`not_authenticated`、`forbidden`、
  `not_found`、`resource_not_found`、`conflict`、`state_transition_invalid`、
  `version_conflict`、`resource_immutable`、`duplicate_resource`、
  `calibration_active_conflict`、`validation_failed`、`calibration_invalid`、
  `calibration_target_invalid`、`calibration_profile_invalid`、
  `calibration_checksum_mismatch`、`calibration_not_active`、
  `precondition_required`、`rate_limited`

尚未实现的冻结字段（`frame_id`/`frame_convention`/`calibration_version`/`lock_version`/
`profile_version`/`schema_version`、审计与 DB 兜底等）不在快照中声明为可用；清单见
`docs/architecture/阶段1-设备域冻结字段实现差距与排期.md`。

## 本地校验

```bash
# 语法与规范 lint
npx --yes @redocly/cli@1 lint packages/contracts/openapi.yaml \
  --config packages/contracts/redocly.yaml

# 与基线比较破坏性变更（需安装 oasdiff）
go install github.com/oasdiff/oasdiff@latest
bash packages/contracts/scripts/check-compatibility.sh \
  packages/contracts/openapi.yaml origin/main
```

CI 的 `contract` job 会对 PR 使用 `pull_request.base.sha`、对 push 使用
`github.event.before` 作为基线；首次提交或无基线时跳过并明确提示。

## 版本与变更流程

1. 提出差异与影响（前端/后端/测试/文档），附兼容性判断。
2. Architect 评审；破坏性变更（删除/改名/改类型/改语义/必填化）必须新建 ADR。
3. 更新契约、lint 规则与兼容性基线；补契约测试。
4. 通知所有相关角色；冻结产物记录版本、日期、SHA256 与批准人。

版本策略：

- `0.2.0`：当前已实现端点快照（DDP-214）。
- `ddp.device/1.0.0`：设备域冻结目标（已批准）；差距清单关闭后才允许把
  `openapi.yaml` 升级为 `1.0.0`。
- `/api/v1`：HTTP 主版本；移除/重命名/改语义属于破坏性变更。
