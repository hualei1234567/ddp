# DDP 文档索引

> 项目：DDP（Embodied AI Data Platform）
> 更新：2026-11-08
> 建设基线：`docs/DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md`

## 阶段 0 交付（本次）

| 文档 | 内容 | 状态 |
|---|---|---|
| [01-现状审计报告-2026-11-08.md](01-现状审计报告-2026-11-08.md) | 只读审计：目录、代码、GitHub、平台、工具链、部署目标、风险 | 待用户审阅 |
| [02-差距分析与实施计划-2026-11-08.md](02-差距分析与实施计划-2026-11-08.md) | 16 领域差距矩阵、目标架构、阶段 0–6 计划、质量门禁、平台映射、决策清单 | 待用户批准 |
| [03-1+6团队与平台纳管方案-2026-11-08.md](03-1+6团队与平台纳管方案-2026-11-08.md) | 1+6 角色、协作协议、平台纳管步骤、执行器策略、启动检查清单 | 待用户批准 |
| [04-阶段0平台纳管与并行开工记录-2026-11-08.md](04-阶段0平台纳管与并行开工记录-2026-11-08.md) | 平台纳管、团队、任务、知识库、评测套件与并行执行记录 | 进行中 |
| [architecture/阶段1-设备域契约冻结-v1.md](architecture/阶段1-设备域契约冻结-v1.md) | 设备域契约冻结 v1：字段、版本、状态、错误码、参数 Schema | 已批准（2026-11-08） |
| [architecture/阶段1-设备域冻结字段实现差距与排期.md](architecture/阶段1-设备域冻结字段实现差距与排期.md) | 冻结字段实现差距 `G-01`–`G-20`、建议批次与关闭门禁 | 跟踪中 |
| [adr/0009-device-contract-freeze.md](adr/0009-device-contract-freeze.md) | ADR-0009 设备域契约冻结与不可变决策 | 已批准 |
| [testing/阶段1设备域安全与契约验收.md](testing/阶段1设备域安全与契约验收.md) | DDP-205 独立验收：安全门禁 PARTIAL，数据库兜底/契约 fixture/PG 证据待关闭 | 待整改 |
| [testing/阶段0-1测试策略.md](testing/阶段0-1测试策略.md) | 阶段 0/1 测试矩阵、CI 门禁、验收清单与 P0 阻断项 | 待审阅 |
| [operations/JWT认证与staging启动.md](operations/JWT认证与staging启动.md) | Bearer JWT 配置、启动自检、staging 重建与回滚 | 现行 |
| [reviews/2026-11-08-security-device-review.md](reviews/2026-11-08-security-device-review.md) | 设备域安全审查：1 个 P0、5 个 P1、7 个 P2 | 待修复 |
| [reviews/2026-11-08-qa-device-review.md](reviews/2026-11-08-qa-device-review.md) | QA 代码审查：契约漂移、激活原子性、测试盲区 | 待修复 |
| [reviews/2026-11-08-architect-device-review.md](reviews/2026-11-08-architect-device-review.md) | 架构审查：F1–F15 与 D1–D9 契约偏差 | 契约已冻结 |
| [reviews/2026-11-08-ux-device-review.md](reviews/2026-11-08-ux-device-review.md) | UX 审查：分页、Stale、移动端、操作路径 | 待修复 |
| [reviews/2026-11-08-consolidated-remediation.md](reviews/2026-11-08-consolidated-remediation.md) | 四路审查整改计划与 DDP-201～210 任务映射 | 执行中 |
| [adr/](adr/) | 技术栈、模块化单体、数据分层、隔离、存储、测试、部署、Bash 策略 | 现行 |
| [../packages/contracts/README.md](../packages/contracts/README.md) | OpenAPI `0.2.0` 已实现快照、lint/兼容性检查与版本流程 | 现行（未提交） |
| [../ai-team/](../ai-team/) | 1+6 角色状态、worktree 映射、阶段 0 并行报告 | 进行中 |
| [DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md](DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md) | 统一建设基线 | 现行有效 |

## 审计结论摘要

1. 当前工程为空：无 Git、无前后端、无数据库、无测试、无 CI。
2. 旧 DDP 已下线且无代码残留，不可复用。
3. Agent 平台已具备纳管能力；DDP 尚未注册。
4. 两个阻断项：部署目标未定；平台执行器禁用 Bash。
5. 首期按用户指定链路：机器纳管 → 采集 → 上传 → 标注 → 训练。

## 下一步

用户确认 `02` 第 8 节决策后，进入阶段 0（`DDP-000`～`DDP-012`）。
在批准前不创建代码、不初始化仓库、不修改平台配置。

## 2026-11-08 DDP-214 更新

- ADR-0009 与设备域契约冻结 v1 已标记为**已批准**（用户长期授权）。
- `packages/contracts/openapi.yaml` 已升级为 `0.2.0`，覆盖已实现的
  devices / sensors / calibrations 端点与错误码。
- CI 新增 `contract` job：Redocly lint + oasdiff 破坏性兼容性 diff。
- 冻结字段实现差距与建议排期见
  [architecture/阶段1-设备域冻结字段实现差距与排期.md](architecture/阶段1-设备域冻结字段实现差距与排期.md)。
