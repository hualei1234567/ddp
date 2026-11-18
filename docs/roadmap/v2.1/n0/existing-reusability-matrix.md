# DDP V2.1 N0 Existing / Reusability Matrix

- 日期：2026-11-18
- 基线提交：`7a9bb4b`
- 用法：**任何新任务开工前先查本表**。能在表中找到复用点的，禁止新建平行实现；
  找不到的，必须在任务卡写明「为什么现有能力不能扩展」。
- 判定口径：复用度 = 高（可直接用）/ 中（需小扩展）/ 低（只能借鉴模式）。

## 1. 后端横切能力

| 能力 | 现有实现 | 规模 | 复用度 | 约束 / 使用方式 | 缺口 |
|---|---|---|---|---|---|
| 审计 + outbox 同事务 | `backend/app/application/audit.py` | 211 行 | 高 | `record_change(audit, outbox, scope, ...)`；禁止各域自写一份 | 设备域 PATCH 未接入（TD-03） |
| 幂等键 | `backend/app/application/idempotency.py` | 156 行 | 高 | `validate_key` / `lookup` / `store`；配 `Idempotency-Key` 头 | 4 份适配层已收敛，勿再复制 |
| 乐观锁 | `backend/app/api/v1/preconditions.py` + `_optimistic_lock` | 60 行 | 高 | `If-Match` + `expected_version`；比较用 `row_version` | — |
| 游标分页 | `backend/app/api/v1/dataset_pagination.py` | 54 行 | 高 | cursor=id 比较，禁 offset | 两处私有副本待收敛（TD-09） |
| 项目隔离 / 权限 | `backend/app/api/deps.py`、`domain/identity/models.py`、`application/identity/access.py` | — | 极高 | 一律走 `ProjectScope` / `WriteScope`；禁止手写 project_id 过滤 | — |
| 分布式限流 | `backend/app/infrastructure/rate_limit.py` | 311 行 | 高 | 配 Redis 后端；fail-open（ADR-0031） | 单机内存后端无全局配额 |
| 对象存储 | `backend/app/infrastructure/storage/s3.py` | 320 行 / 15 方法 | 高 | 预签名、分片、流式、错误分级；**不建议改** | 无对象列目录核对 |
| Outbox 投递 | `backend/app/workers/{datasources,ingestion_jobs,exports}.py` + `claim_next` | — | 高 | 新消费者照此写：`claim_next(event_type)` → 处理 → `mark_published/retry/dead_letter` | 无通用分发器；事件归类有盲区（TD-13） |
| 维护清扫 | `backend/app/workers/maintenance.py` | — | 高 | 过期幂等记录、上传会话可复用 | 上传会话只做惰性过期（TD-15） |
| 领域巡检 | `backend/app/workers/domain_sweeps.py` | — | 高 | 租约过期 / 采集任务数据检查 / 数据集有效性 | 只有三类巡检 |
| 用量快照 | `backend/app/workers/usage_snapshots.py` | — | 高 | 定时写 `usage_snapshots` | — |
| 迁移 + 守卫模式 | `backend/app/infrastructure/db/*_guards.py`、Alembic 单头链 | 47 revisions | 高 | 新表守卫必须 PG + SQLite 双路径 | json/jsonb 不一致（TD-08） |
| 契约冻结 | `packages/contracts/baselines/`（stage2–7）+ `scripts/check_contract_parity.py --strict` | — | 高 | 新阶段契约照此冻结并纳入 CI SHA 校验 | parity 基线已接受 61 项（含 9 条生效契约超前错误码） |
| 错误映射 | `backend/app/api/errors.py` `_mapped` + `details_codes` | 877 行（>800 债务） | 中 | 新错误码必须同时有产生点、映射表、契约 enum | 拆分待做（TD-01） |
| E2E 脚手架 | `backend/tests/e2e/{conftest,support}.py` + `test_main_chain_e2e.py` | — | 高 | 真实 PG 下跑主链 | 只有 1 条主链 |
| 真实栈 smoke | `scripts/qa/{full_chain_smoke,readonly_endpoint_smoke,raw_asset_lifecycle_smoke,collection_asset_lifecycle_smoke}.py` | — | 高 | 每次交付跑只读巡检 | 无性能压测（MISSING） |
| 种子脚本 | `scripts/seed/`（13 模块 + 公共 client） | — | 高 | 新模块加 `modules/<name>.py`，复用 `client.upload_object` | 部分降级逻辑（配额） |
| 审计查询 | `backend/app/api/v1/audit_log_routes.py` | — | 高 | 跨资源 + 单资源时间线，下推 SQL | 过滤只能文本（TD-10） |

## 2. 领域能力

| 能力 | 现有实现 | 复用度 | 约束 | 缺口 |
|---|---|---|---|---|
| 状态机模式 | `domain/suppliers/state_machines.py`、`domain/collection/*` | 高 | 显式迁移表 + `allowed_actions()`；`system-*` / `worker-*` 不对外 | 个别域仍用字符串分派 |
| 数据集版本 | `domain/datasets/models.py`、`state_machines.py`、`version_actions.py` | 高 | 生命周期轴 + `validity_status` 轴分离；冻结后 DB 触发器不可变 | `UNKNOWN` 无写入点（TD-04） |
| 血缘 | `application/datasets/lineage.py`、`application/episodes/provenance.py`、`api/v1/lineage.py` | 中 | 写边 + 读时投影；`DATA_SOURCE`/`DATA_SOURCE_IMPORT` 节点 | Annotation/Quality/TrainingRun 无边；无通用图查询（agent 结论） |
| 数据集授权 | `domain/datasets/dataset_grants.py` + `dataset_grant_routes.py` | 高 | 范围/有效期/usage_policy | 无独立 requester/purpose 字段 |
| 标注 Schema | `domain/annotation/schema.py`（10 种 label type / 6 层 level） | 高 | 项目自建标签；禁止另建第二套标签模型 | 无七类预设注册表 |
| 标注 AI 建议 | `domain/annotation/ai_suggestion.py` + `api/v1/annotation_ai.py` | 高 | AI 值单独存储、必须人工确认；不得当 ground truth | — |
| 标注 QA | `quality_sample.py`、`gold_set.py`、`iaa.py`、`arbitration.py` | 高 | 抽检/盲测/IAA/仲裁都已实现 | 无独立双标任务与一致性门槛；普通复核非盲审 |
| 标注返工 | `state_rules.py` + `submissions.py` | 高 | Reject→Reason→Rework→Review→Pass，含轮次上限 | — |
| 数据源 / 导入 | `domain/datasources/*`、`application/datasources/*`、`workers/datasources.py` | 中 | 状态机已定；导入由 outbox 驱动 | 无进度、无日志端点、`retry` 系统动作未在 HTTP/应用层拦截 |
| Canonical Adapter | `data-platform/ddp_data/adapters/{registry,builtin}.py`、`lerobot/parser.py` | 中 | Adapter 是解析器的声明式外壳；只读优先 | 只有 LeRobot preview + JSONL；无 Parquet/MCAP/ROS/HDF5 字段级解析 |
| 质量规则 | `domain/annotation/quality_rules.py`（标注域）、`application/datasources/quality.py`（导入域） | 低-中 | 两套规则引擎已验证，可作为 N4 的模板 | 无统一 Quality Engine、无六类分层、无 Episode 级自动质检 |
| 模型评测 | `domain/modelops/*`、`evaluation_routes.py`、`adapter_callback_routes.py` | 高 | 平台算 verdict，HMAC 回调 | 无 failure_cases 结构 |
| 反馈 / 缺口 / 需求 | `application/modelops/feedback.py`、`gaps.py`、`requirements.py` | 中 | 五轴覆盖已接真实落点 | 缺口来源少，无 status/priority |
| 设备 / 机器人 / 标定 | `domain/devices/*`、`api/v1/{devices,sensors,robots,calibrations,calibration_profiles}.py` | 高 | 版本分离与快照已落地 | G-16/G-11 PATCH 审计 |
| 场地 / 供应商 | `domain/locations/*`、`domain/suppliers/*` | 高 | 复合外键 + 项目隔离 | 无跨基地调度/计费 |
| 项目 / 成员 | `api/v1/project_routes.py`、`domain/identity` | 高 | 创建仅平台管理员；不硬删 | 无项目归档阻断（已知限制） |

## 3. 前端能力

| 能力 | 现有实现 | 复用度 | 约束 | 缺口 |
|---|---|---|---|---|
| 页面骨架 / 状态 | `components/ui/{PageContainer,PageState,StatusTag,ApiErrorState,ProjectScopeRequired}` | 高 | Loading/Empty/Error/Forbidden/Partial/Stale/Disabled 全覆盖 | `not-found` variant 缺失 |
| 请求 hook | `hooks/useRequest.ts`、`hooks/useCursorPage.ts` | 高 | 统一 Loading/Error/Abort | — |
| API Client | `services/api.ts`（368 行）+ `validation.ts`（359 行）+ 严格解析器 | 高 | 超时/Abort/X-Request-Id/ApiError/validate | 204 空响应分支需收紧 |
| 上传队列 | `services/uploadQueue.ts`、`uploadTransfer.ts`、`hooks/useUploadQueue.ts`（758 行） | 高 | 断点续传、分片重试、弱网；**禁止第二套** | 文件超 500 行需评审说明 |
| 视频播放 | `pages/datasources/AssetReviewPage.tsx` + `hls.js`（ADR-0017） | 中 | 预签名 URL + 原生 `<video>`；206 Range 实测 | 无倍速、无时间轴、无多视角同屏 |
| 动作按钮 | `components/rawAssets/RawAssetActionBar.tsx`、`components/dataset/ActionButtons.tsx` | 高 | 只按服务端 `allowed_actions` 渲染 | 各域动作条尚未完全统一 |
| 标注工作台 | `hooks/useAnnotationWorkbench.ts`、`components/annotation/*` | 高 | 复用草稿/提交/复核/仲裁组件 | — |
| 导航与路由 | `app/navigation.ts` + `routes.tsx` | 高 | 菜单项必须可真实进入；禁止占位项回归（已有测试） | — |
| 前端测试 | vitest 101 文件 / 739 passed（2026-11-18 实测） | 高 | 组件静态渲染 + 解析器测试 | 无 jsdom 交互测试、无 E2E |

## 4. 不可复用 / 需新建（明确边界）

| 需求 | 为什么不复用 | 新建位置建议 |
|---|---|---|
| Processing Run（N4-01） | 现有 `ingestion_jobs` 只覆盖接入任务，语义与状态机不同 | 新领域 `domain/processing/`，复用 outbox/worker 模式 |
| Canonical Frame / SensorStream / Action / Observation | 当前 Canonical 只有 Episode 级 | `data-platform/ddp_data/canonical/`，版本化 schema |
| Parquet / MCAP / ROS Bag / HDF5 解析 | 只有格式识别与魔数校验 | `data-platform/ddp_data/adapters/` 下按格式新增只读 Adapter |
| 六类质量报告 | 导入质量是扁平 rule findings | 统一 Quality Engine（N4-04），标注/导入规则作为规则源 |
| Capture Session | 无逐设备采集执行态 | `domain/collection/sessions` 或在现有 collection 扩展，先做 ADR |
| SSRF 防护 | 训练 Adapter endpoint 由部署配置，当前无内网地址拦截 | 在 `training_adapter_http.py` 加解析后 IP 白名单/黑名单 |

## 5. 使用纪律

1. 新文件出现前，先在本表找到「扩展点」；找不到才允许新建。
2. 扩展既有模块时，补测试而不是复制断言。
3. 公共能力修改必须评估全部调用方（至少 grep 一次），并在 PR 写明影响面。

