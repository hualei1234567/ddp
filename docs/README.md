# DDP 文档索引

> 项目：DDP（Embodied AI Data Platform）
> 更新：2026-11-18
> 建设基线：`docs/DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md`
> 治理基线：`docs/ddp/00–09`、`prompts/ddp/01–04`、`templates/ddp/*`

## V2.1 执行基线（2026-11-18 起）

| 文档 | 内容 | 状态 |
|---|---|---|
| **[roadmap/v2.1/README.md](roadmap/v2.1/README.md)** | **V2.1 建设路线入口**：N0 基线、能力矩阵、复用矩阵、技术债、Backlog、DAG、风险、No-Go | **现行执行基线** |
| [roadmap/v2.1/n0/current-system-baseline.md](roadmap/v2.1/n0/current-system-baseline.md) | 当前系统事实：`7a9bb4b`、契约 1.9.0、迁移 0052、后端 2240 passed、前端 739 passed | **现行** |
| [roadmap/v2.1/n0/capability-matrix.md](roadmap/v2.1/n0/capability-matrix.md) | N1–N9 共 47 项能力唯一状态（12 VERIFIED / 30 PARTIAL / 4 MISSING / 1 BLOCKED） | **现行** |

## 最新审计（先读）

| 文档 | 内容 | 状态 |
|---|---|---|
| **[N0-N9-项目最终报告-2026-11-17.md](roadmap/next-stage-v2/audit/N0-N9-项目最终报告-2026-11-17.md)** | **当前事实基线**：N0–N9 全量建设的阶段完成度、主链逐跳状态、数据可追溯性、修掉的 8 个真实缺陷、11 项缺口、部署情况、真实测试数字 | **现行** |
| [05-现状审计报告-2026-11-14.md](05-现状审计报告-2026-11-14.md) | 阶段 0–6 实现后的只读全量审计（历史）：技术栈事实、模块状态、复用清单、P0/P1 债务、目标架构差距、任务 DAG | 历史记录 |
| [ddp/00-项目基线与使用说明.md](ddp/00-项目基线与使用说明.md) | 持续迭代治理文件包入口：基线、原则、模块映射、迭代工作流、Agent 协作、代码质量、数据治理、GitHub 规范、测试验收 | 现行 |

## 阶段 7 数据平面（第一批已实现）

| 文档 | 内容 | 状态 |
|---|---|---|
| [product/阶段7-数据平面第一批PRD.md](product/阶段7-数据平面第一批PRD.md) | 外部数据源登记与导入任务：需求、页面状态、明确不做的范围 | 已实现 |
| [architecture/阶段7-数据平面第一批契约冻结-v1.0.md](architecture/阶段7-数据平面第一批契约冻结-v1.0.md) | `ddp.stage7/1.0.0` 端点矩阵、三条硬约束、状态机、兼容流程 | FROZEN |
| [adr/0018-stage7-datasource-registry.md](adr/0018-stage7-datasource-registry.md) | 分层决策（`data-platform/` 边界）、revision 必须 sha、许可与 gated 一致性 | ACCEPTED |
| [adr/0017-video-review-player.md](adr/0017-video-review-player.md) | 视频审看播放器选型：hls.js + 原生 video，不自研内核 | ACCEPTED |
| [adr/0016-jwt-verification-via-pyjwt.md](adr/0016-jwt-verification-via-pyjwt.md) | JWT 校验迁移到 PyJWT，claim 策略仍由 DDP 裁决 | ACCEPTED |
| [architecture/阶段7-全链路实施计划-Human1M.md](architecture/阶段7-全链路实施计划-Human1M.md) | NVIDIA Human-1M 全链路实施计划与诚实边界 | 已执行 |
| [testing/主链端到端与可追溯性验收-2026-11-17.md](testing/主链端到端与可追溯性验收-2026-11-17.md) | **主链端到端与可追溯性验收**：逐跳状态矩阵、血缘正反向核查、跨项目隔离、修掉的断点与修不了的原因 | **现行** |
| [testing/evidence/2026-11-15-human1m-chain/README.md](testing/evidence/2026-11-15-human1m-chain/README.md) | **真实数据全链路验收报告**：39 视频下载/解析/审看/审核/质量报告 | **已通过** |
| [architecture/outbox事件语义界定-v1.0.md](architecture/outbox事件语义界定-v1.0.md) | 投递类 vs 留痕类事件的判据与归类（R-13 规则） | 现行 |

## 运维

| 文档 | 内容 | 状态 |
|---|---|---|
| [operations/备份与恢复演练记录-2026-11-17.md](operations/备份与恢复演练记录-2026-11-17.md) | **最新**：迁移 0043 结构（83 表 / 188 触发器）的真实备份恢复演练 | 已执行 |
| [operations/备份与恢复演练记录-2026-11-14.md](operations/备份与恢复演练记录-2026-11-14.md) | 真实 PG 16 备份恢复演练：71 表 / 177 触发器一致；对象存储缺口如实登记 | 已执行 |
| [operations/main-分支保护与评审纪律.md](operations/main-分支保护与评审纪律.md) | 私有仓无平台强制力时的替代方案与重新决策触发条件 | 现行 |
| [operations/docker-compose-minio变更说明.md](operations/docker-compose-minio变更说明.md) | MinIO 镜像固定与卷路径变更的数据影响说明 | 现行 |
| [operations/Prometheus指标抓取与metrics访问控制.md](operations/Prometheus指标抓取与metrics访问控制.md) | `/metrics` 静态 Bearer 令牌 + 来源网段白名单、未配置时的失败关闭口径、抓取配置示例与排查表 | 现行 |

> ⚠️ 本文件下方「阶段 0 交付」一节为 2026-11-08 的历史记录，其中「当前工程为空」
> 等表述已过期；**当前事实以 `N0-N9-项目最终报告-2026-11-17.md` 为准**。

## 阶段 0 交付（历史记录，2026-11-08）

| 文档 | 内容 | 状态 |
|---|---|---|
| [01-现状审计报告-2026-11-08.md](01-现状审计报告-2026-11-08.md) | 只读审计：目录、代码、GitHub、平台、工具链、部署目标、风险 | 待用户审阅 |
| [02-差距分析与实施计划-2026-11-08.md](02-差距分析与实施计划-2026-11-08.md) | 16 领域差距矩阵、目标架构、阶段 0–6 计划、质量门禁、平台映射、决策清单 | 待用户批准 |
| [03-1+6团队与平台纳管方案-2026-11-08.md](03-1+6团队与平台纳管方案-2026-11-08.md) | 1+6 角色、协作协议、平台纳管步骤、执行器策略、启动检查清单 | 待用户批准 |
| [04-阶段0平台纳管与并行开工记录-2026-11-08.md](04-阶段0平台纳管与并行开工记录-2026-11-08.md) | 平台纳管、团队、任务、知识库、评测套件与并行执行记录 | 进行中 |
| [architecture/阶段1-设备域契约冻结-v1.md](architecture/阶段1-设备域契约冻结-v1.md) | 设备域契约冻结 v1：字段、版本、状态、错误码、参数 Schema | 已批准（2026-11-08） |
| [architecture/阶段1-设备域冻结字段实现差距与排期.md](architecture/阶段1-设备域冻结字段实现差距与排期.md) | 冻结字段实现差距 `G-01`–`G-20`、建议批次与关闭门禁 | 跟踪中 |
| [adr/0009-device-contract-freeze.md](adr/0009-device-contract-freeze.md) | ADR-0009 设备域契约冻结与不可变决策 | 已批准 |
| [product/阶段2-采集与上传PRD.md](product/阶段2-采集与上传PRD.md) | 阶段 2 需求、流程、角色、页面状态与验收基线 | 已实现（QA Accepted） |
| [architecture/阶段2-契约索引.md](architecture/阶段2-契约索引.md) | 阶段 2 契约索引：任务/批次、上传、RawAsset、Episode、血缘 | 实现完成，待冻结 |
| [adr/0010-stage2-collection-ingestion-contract.md](adr/0010-stage2-collection-ingestion-contract.md) | ADR-0010 阶段 2 采集接入契约决策 | 已提议（待冻结） |
| [testing/阶段2测试策略与验收矩阵.md](testing/阶段2测试策略与验收矩阵.md) | 阶段 2 采集/上传/Episode 测试策略、S2-T01–T30 验收矩阵 | 已执行（DDP-311 Accepted） |
| [testing/阶段2采集样例包设计与清单.md](testing/阶段2采集样例包设计与清单.md) | 多相机+机器人状态合成样例、负向文件与元数据用例 | 现行（T2） |
| [testing/阶段2采集上传验收报告-2026-11-09-v1.1.md](testing/阶段2采集上传验收报告-2026-11-09-v1.1.md) | DDP-311 复验：真实 PG + 真实 MinIO，D01/D02 关闭，QA Accepted | 现行 |
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
