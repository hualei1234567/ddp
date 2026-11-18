# DDP V2.1 N0-07 Risk List

- 日期：2026-11-18
- 基线提交：`7a9bb4b`
- 概率：高/中/低；影响：P0（数据损坏/不可恢复）、P1（阶段出口阻断）、P2（可维护性）。
- 「状态」区分已发生（历史证据）、现存、可预见。

## 1. 环境与外部依赖

| ID | 风险 | 证据 | 概率 | 影响 | 缓解 | 责任 |
|---|---|---|---|---|---|---|
| RK-01 | **staging 不可达**，发布链路（CI→镜像→staging→迁移→smoke）未验证 | `docs/operations/部署与验证记录-2026-11-17.md:118`；`ssh your-staging-host` 解析失败 | 高（已发生） | P1 | 用户提供 SSH 别名；在此之前所有 staging 任务标 BLOCKED，不用本地结果冒充 | 用户 / QA |
| RK-02 | **无真实机器人硬件**，N9 设备格式端到端无法验证 | 报告 §5 #1 | 高（已发生） | P1 | 用 Mock/回放 + 格式 Adapter 单测；不宣称 VERIFIED | 用户 / 后端 |
| RK-03 | **外部训练平台未配置**，Training Adapter 走 503 占位 | `infrastructure/training_adapters.py:17` | 高 | P2 | 保持显式 503；增加 `execution_mode` 区分 LOCAL/MOCK/DRY_RUN/REMOTE | 后端 |
| RK-04 | 本地栈依赖 13 个容器，域名/端口/镜像版本漂移导致「看起来 Up 但跑旧代码」 | 报告 §6.1 两次实测踩中 | 中 | P1 | 发布后四件事检查清单（迁移版本/新端点/静态产物/只读巡检） | DevOps |

## 2. 数据与一致性

| ID | 风险 | 证据 | 概率 | 影响 | 缓解 | 责任 |
|---|---|---|---|---|---|---|
| RK-05 | ~~`calibration_records.supersedes_id` 可分叉~~ **已修复**（N1-002：迁移 0053 部分唯一索引 + `validate_supersedes_link` + 存量冲突逐行打印后失败退出） | 领域/仓储/迁移 16 例；全量 2313 passed | 低 | P1 | 剩余：长度 ≥2 的环未检测（Backlog N1-009） | 后端 |
| RK-06 | ~~设备域 PATCH 不写审计~~ **已修复**（N1-001：`patch_audit.py` + devices/sensors/robots 接入；profiles 无 PATCH） | `tests/api/test_*_api.py` 新增 4 例；定向 49 passed | 低 | P1 | 无 | 后端 |
| RK-07 | **`validity_status=UNKNOWN` 无写入点**，来源不可达时仍显示有效 | TD-04 | 中 | P1 | 可达性探测；探测失败写 UNKNOWN 并阻断交付 | 后端 / 产品 |
| RK-08 | ~~导入 `retry` 系统动作未拦截~~ **已修复**（N2-001：`validate_import_action` 在 HTTP 用例入口拒绝系统动作；Worker 走 `ImportRunner.release_for_retry` 不受影响） | API 422 用例 + Worker 重试测试通过 | 低 | P1 | 无 | 后端 |
| RK-09 | **Atomic 语义缺口**：Episode→Annotation/Quality、DatasetVersion→TrainingRequest 血缘无边 | agent 复核：`ANNOTATED_BY`/`TRAINING_RUN` 只有枚举无写入方 | 高（现存） | P1 | 在 N5-04 补齐写边；缺边期间血缘图不得宣称完整 | 后端 |
| RK-10 | **json vs jsonb 列类型不一致**，历史已在真实 PG 导致 500 | 报告 §4 #1；TD-08 | 中 | P1 | 统一列类型迁移 + 等价性测试 | 后端 |
| RK-11 | **上传配额并发窗口**：按声明字节预扣，多会话可各自通过 | 报告 §5 #9 | 中 | P2 | 原子预扣/事务锁；或明确接受并监控 | 后端 |
| RK-12 | **对象存储用量以 DB 元数据为准**，与 MinIO 实际对象可能漂移 | 报告 §5 #10 | 中 | P2 | 定期对账任务；漂移时以 MinIO 为准并告警 | 运维 |

## 3. 安全

| ID | 风险 | 证据 | 概率 | 影响 | 缓解 | 责任 |
|---|---|---|---|---|---|---|
| RK-13 | **无分支保护**（私有仓 API 403），可直推 main | 审计 §12.1 R-3；`docs/operations/main-分支保护与评审纪律.md` | 中 | P1 | CI 必过 + 人工评审纪律；所有变更走 PR（本会话已执行） | 用户 |
| RK-14 | ~~Adapter SSRF 未防护~~ **已修复**（N8-001 训练侧 + N8-008 模型侧：同一 `adapter_url_guard.py`、禁重定向、默认拒绝私网；本地例外按出站面分别显式开关） | `test_adapter_url_guard.py` 模型/训练双侧用例；`test_modelops_gateway_chain.py` API 回归；单元 1294 passed | 低 | P1 | 残余：DNS 重绑定 TOCTOU（守卫 docstring 已记录，仅接受部署配置来源前提） | 后端 |
| RK-15 | 限流 fail-open：Redis 不可用时退化为单机内存限流 | ADR-0031 | 中 | P2 | 监控降级事件；关键写接口另有配额 | 后端 |
| RK-16 | 未脱敏历史/文档可能含真实邮箱、内网 IP | 用户约束 | 低 | P1 | 提交前扫描；证据只留摘要 | 全体 |
| RK-17 | 依赖告警积压：14 个 Dependabot PR 未处理（fastapi/alembic/pytest 等大版本） | `gh pr list` | 高 | P2 | 按季度集中升级；先补兼容性测试再合并 | 后端 / 用户 |

| RK-29 | 并行 worktree 共用同一个 PostgreSQL 测试库，互相推进 `alembic_version`，导致迁移测试假失败 | 本轮 3 个任务均命中 `Can't locate revision` | 高 | P2 | 每个并行任务使用独立测试库或隔离 schema；CI 用一次性实例 | QA |

## 4. 工程与交付

| ID | 风险 | 证据 | 概率 | 影响 | 缓解 | 责任 |
|---|---|---|---|---|---|---|
| RK-18 | **6 个文件 >800 行**，继续增长将违反门禁 | TD-01 | 高（现存） | P2 | 拆分专项；新代码不得再增 | 后端 |
| RK-19 | **契约 parity 基线已接受 61 项差异**（其中 9 条生效契约超前错误码），基线可能被当成「已一致」 | `check_contract_parity.py --strict` 输出 | 中 | P1 | 每次变更先看差异清单；新差异零容忍 | 架构 / 后端 |
| RK-20 | **迁移编号缺口**（0037/0039/0045–0047），多人并行易再撞号 | `alembic history` | 中 | P2 | 迁移编号单一 Owner；合并前检查 `alembic heads` 单头 | 后端 |
| RK-21 | 多会话/多 worktree 并行时，同一测试文件语义冲突（历史上已发生） | 报告 §4 #7 | 中 | P2 | 任务卡声明文件所有权；合并后跑全量 | 全体 |
| RK-22 | **前端无 jsdom 交互测试 / 无 E2E**，页面行为回归靠静态断言 | 前端 101 文件均为静态/纯函数测试 | 高 | P2 | 引入 Testing Library + 关键路径 Playwright；先补上传/审核/动作条 | 前端 |
| RK-23 | **无性能基线**（100/1K/10K episodes），规模化能力未知 | TD/agent：无压测脚本 | 高 | P1 | N8-03 建脚本与阈值；先测只读列表与视频 Range | QA |
| RK-24 | 备份只做本机 PG+MinIO，无异地/加密/RTO 指标 | `docs/operations/备份与恢复演练记录-2026-11-17.md` | 中 | P1 | 增加异地副本与恢复时长目标；每季度演练 | 运维 |

## 5. 产品与验收口径

| ID | 风险 | 证据 | 概率 | 影响 | 缓解 | 责任 |
|---|---|---|---|---|---|---|
| RK-25 | 把 PARTIAL/MISSING 说成已完成，误导后续排期 | 历史多次出现（报告 §4 与 §5） | 中 | P1 | 状态六态唯一口径；交付必须附命令/证据 | 全体 |
| RK-26 | ~~Mock/Local 训练被误认为真实训练~~ **已修复**（N7-001：`ExecutionMode` + 迁移 0054 + 契约 1.10.0 + 前端显著标注；未配置模式提交 422） | 领域/API/迁移/前端解析器用例通过 | 低 | P1 | 剩余：MOCK/DRY_RUN 是标记而非独立执行器（Backlog N7-007） | 后端 / 前端 |
| RK-27 | 长链（导入→标注→数据集→训练→评测）任一步失败时，用户看不到「下一步做什么」 | UI 蓝图 §3；当前各页独立 | 中 | P2 | 工作台聚合队列；失败项带 retry/logs 入口 | 前端 / 产品 |
| RK-28 | 质量报告六类分层缺失，业务无法按维度定位问题 | N2-06 MISSING | 高 | P2 | N4-04 统一 Quality Engine 后重做报告 UI | 产品 / 后端 |

## 6. 风险处置节奏

- **P0/P1 风险**：进入 `v2.1-backlog.md` 的 P0/P1 任务，未关闭前对应阶段不得标记 DONE。
- **已发生风险**（RK-01/02/13/18/23）：保留在清单顶部，直到证据更新。
- 每次阶段验收更新本表：新增风险、关闭风险、变更概率/影响都要留痕。

