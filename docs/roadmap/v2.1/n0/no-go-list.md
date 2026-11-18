# DDP V2.1 N0-03 No-Go List

- 版本：V2.1 / N0
- 日期：2026-11-18
- 适用范围：N0–N9 全部阶段，所有 Agent 与人类贡献者
- 来源：`CLAUDE.md`、`docs/DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md`、
  `docs/roadmap/next-stage-v2/建设文档/05`、V2.1 建设包 `05_工程治理与质量/07`、
  `docs/roadmap/v2.1/n0/current-system-baseline.md`

> 本清单是**停止线**，不是建议。任何一条被触发，该变更不得合并；已合并的必须登记
> 技术债并按 `docs/ddp/06` 清理流程处理。

## 1. 资产保护类（触发即回滚或打回）

| ID | 禁止事项 | 判定方式 | 违反处置 |
|---|---|---|---|
| NG-01 | 推倒重建、重写已验证链路（HF → 下载校验 → LeRobot → MinIO → 视频 Range → 人工审核 → 质量报告） | diff 触及已验证模块的替换式重写；或删除既有测试 | 打回，恢复原实现 |
| NG-02 | 重复实现已有能力（平行 API client、第二套状态机、第二份幂等/审计/乐观锁） | 新增文件与既有实现职责重合且未说明复用评估 | 打回，登记重复实现 |
| NG-03 | 修改历史 Alembic migration（0001–0052 中已存在文件） | `git diff` 命中 `backend/migrations/versions/*.py` 既有文件 | 打回；只能新增下一个编号 |
| NG-04 | 删除既有业务数据、业务模块或已冻结契约字段/路径 | 迁移含 `drop_table` / `drop_column` 且无 ADR 与回滚方案；契约删除路径/字段 | 打回，需 ADR + 用户批准 |
| NG-05 | 未授权删除数据库记录，或用空过滤/模糊匹配批量删除 | 脚本无 dry-run、无 `--confirm-count`、无逐条 ID | 拒绝执行 |
| NG-06 | 用 Mock / 假数据冒充真实业务验收 | 交付声称 VERIFIED 但证据只有 mock | 降级为 UNVERIFIED 并打回 |

## 2. 环境与平台类

| ID | 禁止事项 | 说明 |
|---|---|---|
| NG-07 | 引入 GitLab、DSH、Codex 或任何第二代码托管/执行环境 | GitHub 是唯一托管；执行环境为 VS Code + Claude Code CLI + 原生 Agent |
| NG-08 | 未授权进行 staging / 生产迁移、部署、发布、强推、重写历史 | 每一项都需单独批准；`main` 只走 PR |
| NG-09 | 向真实机器人发送运动、复位或其他控制命令 | 未经明确授权一律禁止；测试只用 Mock/回放 |
| NG-10 | 把 `.env`、Token、密码、客户数据写入仓库/镜像/日志 | `scripts/check_secrets.py` 必须通过；日志脱敏 |
| NG-11 | 未经批准引入新的微服务、CQRS、Event Sourcing、消息队列集群、Kubernetes | 需 ADR + 收益论证 |
| NG-12 | 引入第二套同职责技术栈（HTTP 客户端、校验、ORM、迁移、UI、状态库、时间库） | 统一为 httpx / Pydantic v2 / SQLAlchemy 2 / Alembic / antd 5 / 现用 hooks / `datetime`+UTC |

## 3. 架构与代码质量类

| ID | 禁止事项 | 判定方式 |
|---|---|---|
| NG-13 | Domain 依赖 HTTP / ORM / 队列 / 云 SDK；Controller 写业务规则；Repository 承担编排 | 代码审查；跨层 import |
| NG-14 | 复制粘贴业务规则、重复状态机、重复校验、重复 API client | 同一规则出现两处权威实现 |
| NG-15 | 吞异常、空 `except`、未知状态默认成功、用空数组/空对象掩盖失败 | 代码审查 + 变异测试 |
| NG-16 | 硬编码型号、状态、阈值、路径、地址、密钥、项目专属规则 | grep 既有常量表；例外需 ADR |
| NG-17 | 新增文件 >800 行；500–800 行不说明原因 | CI/审查统计；现有 6 个 >800 行文件已登记债务，不得再增 |
| NG-18 | 万能 `utils/common/helpers/base_service`；无 Owner 的公共件 | 目录与命名审查 |
| NG-19 | 只改单层：API 改了而 OpenAPI/前端类型/Client/测试未同步（或反之） | `check_contract_parity.py --strict` + 审查清单 |
| NG-20 | 在普通 HTTP 线程处理大文件；无边界/无幂等/无超时/无失败记录的异步任务 | 代码审查 + worker 注册检查 |
| NG-21 | 迁移只做 upgrade 不做 downgrade 验证，或只在 SQLite 验证 | 新迁移必须 `upgrade → test → downgrade → test → upgrade → test`，最终以 PostgreSQL 为验收环境 |
| NG-22 | 为让测试变绿而放宽断言、删除用例、加 skip / xfail 掩盖失败 | diff 审查；新增 skip 需理由 |

## 4. 状态与交付口径类

| ID | 禁止事项 | 说明 |
|---|---|---|
| NG-23 | 把 `PLANNED` / `PARTIAL` / `UNVERIFIED` 描述成已上线能力 | 状态标记只认六态；文档与 UI 文案同口径 |
| NG-24 | 未跑测试声称通过；未执行标 VERIFIED | 交付必须附命令与真实输出；`NOT RUN` / `BLOCKED` 如实写 |
| NG-25 | 用 `git add -A`、`git stash`、覆盖其他会话改动 | 只按文件 `git add`；共享文件单一 Owner |
| NG-26 | 只交付代码不交付证据 | 每个阶段必须有：PRD/技术说明、ADR（如需）、契约、迁移、单测、集成测试、PG 测试、前端测试、E2E 证据、文档、CHANGELOG、已知风险、Next Backlog |

## 5. 数据与安全类

| ID | 禁止事项 | 说明 |
|---|---|---|
| NG-27 | 信任客户端传入的 `project_id` 或绕过服务端项目隔离 | 所有查询强制服务端注入 `project_id`；跨项目返回不泄露存在性 |
| NG-28 | 大文件写数据库；DB 存文件本体而非元数据/引用/Hash | 对象存储唯一权威 |
| NG-29 | 上传只校验扩展名，不校验类型白名单/大小/内容签名/Hash | 复用既有上传校验链路 |
| NG-30 | 把未脱敏的原始历史（真实邮箱、内网 IP、绝对路径）写入公开文档 | 提交前检查 |
| NG-31 | 在文档中泄露内部凭据、预签名 URL 长期有效链接 | 证据只保留请求/响应摘要 |

## 6. 本轮（N0–N1）附加停止线

| ID | 禁止事项 | 原因 |
|---|---|---|
| NG-32 | 在 N0 未完成前直接进入大规模编码 | 用户明确要求先冻结事实基线 |
| NG-33 | 用 V2.1 蓝图把「目标架构」当成「现有实现」推断功能已具备 | 蓝图 §二明示当前缺口；`docs/ddp/01` 是长期方向 |
| NG-34 | 为通过 N1 而顺手重构设备域或统一所有历史错误码（超出 G-04/05/08/09/11/16/19/20 范围） | 增量迭代；超范围需另立任务 |

## 7. 复核方式

- 自动闸口：`bash scripts/check.sh`（8 步）+ CI 4 job。
- 人工/Agent 闸口：按 `docs/DDP...总规范` §15.16 清单审查。
- 验收闸口：QA 独立复验，证据写入 `docs/testing/evidence/` 或 PR 描述。

