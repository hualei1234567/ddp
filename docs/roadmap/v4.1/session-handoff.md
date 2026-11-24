# DDP V4.1 会话交接摘要（2026-11-24 09:10）

> 用途：上下文压缩后继续工作的唯一事实入口。与 `task-board.md`、`delivery-report-2026-11-24.md` 配套阅读。

## 1. 当前基线

| 项 | 值 |
|---|---|
| 仓库 | `github.com/hualei1234567/ddp` |
| main | `8a3ba8d`（PR #100 合并后） |
| OpenAPI | **1.33.0** |
| Alembic head | **0072** |
| 本地栈 | docker compose（postgres 16 @55432 + minio @9000），健康 |
| 仓库卫生 | 单分支 main、零 worktree、无遗留开放 PR（仅 dependabot） |

## 2. 本轮（V4.1）已交付的 PR

| PR | 内容 | 状态 |
|---|---|---|
| #85 | V4.1 蓝图包入库（18 文件 SHA256 校验 0 差异） | ✅ |
| #86 | FR-034/035 Loader 实测证据（9 类结构缺口，BLOCKED 有据） | ✅ |
| #88 | FR-016/017/018 三级语义层级包含 + OUTCOME_SPAN 提交校验 | ✅ |
| #87 | FR-026/027/028 QC 批量重跑/差异/重试 UI | ✅ |
| #89/#96/#98/#99 | 看板、交付报告等文档 | ✅ |
| #90 | ADR-0047 Edge Agent 纯拉取模式（D2 裁决落盘） | ✅ |
| #92 | V31-012a 端侧 Agent 本地核心（Spool/Manifest/Adapter/Preflight/上传驱动） | ✅ |
| #93 | V31-011 Edge Agent 服务端（迁移 0071、配对/心跳/Lease/鉴权/API） | ✅ |
| #94 | V31-012b 真实 DDP HTTP 网关 + 端到端联调 | ✅ |
| #95 | FR-030 Split 防泄漏新增 Session/Subject 分组 | ✅ |
| #97 | FR-042 投递队列健康快照端点 | ✅ |
| #91 | LeRobot v2.1 9 类缺口修复 + 官方 Loader 实测通过（**VERIFIED_BY_LOADER**） | ✅ |
| #100 | 内容重复校验降级为墓碑 + 历史 outbox 积压归正（本轮运维修复） | ✅ |

## 3. 关键状态：M5 里程碑已解除阻塞

`LE_ROBOT` 导出的 `verification_status` = **`VERIFIED_BY_LOADER`**，依据 `lerobot==0.3.3` 官方 `LeRobotDataset` 对零手工修补产物的**双路构造通过**（`episodes=None` 与 `episodes=[0,1,2]`）。

**诚实边界不变**：`loadable_scope="METADATA_ONLY"`、`frame_level_read="NOT_CLAIMED_METADATA_ONLY"`、`fps_source="NOMINAL_METADATA_ONLY"`。
证据：`docs/testing/evidence/2026-11-23-lerobot-loader-verify-pass.md`。

## 4. 运维状态（本轮修复后）

| 指标 | 值 |
|---|---|
| 投递类 PENDING（真待办） | **0** |
| 留痕类误置 PENDING | **0** |
| FAILED 事件 | 10 条（分布 7 个项目），均为**业务语义正确的拒绝** |

10 条 FAILED 明细（有意不重放，需能力或数据修正而非重试）：
- `datasource.import.succeed` 6 条：该导入无媒体资产，无法生成 Episode
- `ingestion.job.created` 2 条：帧级切片能力未实现（N3 多模态 Pipeline）
- `datasource.import_requested` 1 条：任务状态 RUNNING 不允许执行
- `processing.run.requested` 1 条：输入引用不属于当前项目

## 5. 本轮修复的两类问题（PR #100，务必了解）

### 5.1 内容重复被误报为校验失败（真缺陷，三层根因）

1. 上传完成时内容去重查一次，**并发**时双双查不到对方 → 各留一个 VALIDATING 资产；
2. 校验转 VALID 时第二个撞**部分唯一索引**（VALIDATING→VALID 是*进入*索引条件）；
3. 仓储 `save()` 的 **UPDATE 分支没捕获 IntegrityError**（只有 `add()` 有）→ 异常冒到 Worker 被当可重试 → **无限重试，永不可能成功**。

修复：仓储 `_flush_or_conflict` 翻译冲突 + 用例捕获后**墓碑化并指向已有资产**（outcome=`DUPLICATE`）。

**关键陷阱**：不能用 `begin_nested` 包正常路径 —— savepoint 释放会破坏「审计失败→整体回滚」的原子性（既有测试会挂）。只在异常路径恢复事务。

### 5.2 23 条事件积压 9 天（历史数据）

早期 `enqueue` 无条件写 `PENDING`；迁移 **0072** 把「PENDING 且不在 `DISPATCH_EVENTS` 白名单」的行归正为 `dispatch=false + RECORDED`。

**约束陷阱**：数据库有 `ck_outbox_events_dispatch_status_consistent`（`dispatch=false ⟺ status=RECORDED`），**两个字段必须同时改**，只改 status 会被拒绝。

## 6. 未完成项与阻塞（全部需外部输入，非代码可解）

| 项 | 阻塞条件 |
|---|---|
| S3 真实传感器物化（FR-009~015） | 需**真实传感器样本** |
| S4 AI 真实推理（FR-021/022） | 需 **GPU + 模型权重**（GroundingDINO/SAM2/VLM） |
| FR-036/037 训练连接器 | 需**外部训练平台 API** |
| FR-038/039 Staging / 硬件 Pilot | 需 **staging 主机 / 采集硬件** |
| 端侧真实硬件采集 | `HARDWARE_CAPTURE_NOT_TESTED`（协议与本地核心已验证） |

## 7. 常用命令

```bash
# 后端测试（worktree 需软链 backend/.venv 与 apps/web/node_modules）
cd backend && PYTHONPATH=".:../data-platform" .venv/bin/python -m pytest tests -q

# 全量门禁（口径不得放宽）
bash scripts/check.sh          # 必须看到「全部检查通过」

# 真实栈
export DDP_DATABASE_URL="postgresql+psycopg://ddp:ddp@localhost:55432/ddp"
cd backend && .venv/bin/alembic upgrade head
.venv/bin/python scripts/e2e/v31-minio-upload.py   # V31-002 四要素验收

# 队列健康
curl -s "http://127.0.0.1:8000/api/v1/projects/<pid>/ops/dispatch-queue"

# outbox 自检（含运行时数据校验）
DDP_OUTBOX_DB_CHECK=1 DDP_DATABASE_URL=... python3 scripts/check_outbox_events.py
```

## 8. 工作方式约定（沿用）

- 一任务一 PR，CI 五项全绿后合并；`check.sh` 口径**不得放宽**
- 并行任务用独立 git worktree，共享文件（迁移/契约/权限矩阵）不并行改
- 多智能体：主控审查 + subagent 执行，subagent 只建 PR 不合并
- 诚实口径：`BLOCKED` 必须附解除条件，不用总体验收掩盖缺项
- 涉及不可逆/高风险操作（删数据、对外发布）先列影响与回滚，等确认
