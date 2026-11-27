# DDP 运营 SOP 索引（V5.2 阶段 4-5 运营闭环）

- 版本：v1.0 · 2026-11-26
- 分支基线：`feat/v52-ops-playbook`
- 定位：本目录是**运营操作**的唯一入口。领域规则、阈值、状态机以代码为准；
  本文只写「谁、在什么条件下、调哪个端点、按什么标准判定、出问题怎么处置」。
- 与规划文档的关系：`docs/roadmap/v5.2/phase4-production-plan.md` §4-5 定义
  「要有什么 SOP/看板」，本目录是它的落地版本。规划能力未上线时，本文如实
  标注 `MISSING` / `UNVERIFIED`，不把规划写成现状。

---

## 1. SOP 文档索引

| 文档 | 解决什么问题 | 主要责任角色 | 状态 |
|---|---|---|---|
| [honeypot-appeal.md](honeypot-appeal.md) | 黄金题熔断（PAUSED）误伤申诉：立案 → 取证 → 判定 → 解除 | 标注员（申诉）/ QA lead（取证判定）/ 项目 owner 或平台管理员（解除） | VERIFIED（端点与开关已 grep 核对） |
| [backflow-intake.md](backflow-intake.md) | 评测失败案例回流：受理、限流处置、重发幂等、转换时机 | QA lead / 项目 owner | VERIFIED（端点与参数已 grep 核对） |
| [dashboard-spec.md](dashboard-spec.md) | 四类看板（吞吐 / 质量 / 成本 / 数据飞轮）的指标来源、口径、刷新频率、异常阈值 | 项目 owner / 平台管理员 | 引用 VERIFIED（指标名与端点已 grep 核对）；阈值多为「建议/待定稿」，非 SLO 承诺 |
| [backup-restore-runbook.md](backup-restore-runbook.md) | 备份与恢复演练（FR-043，已有文档，不在本次新增范围） | 平台管理员 | VERIFIED（有演练证据） |
| [table-growth-and-partitioning.md](table-growth-and-partitioning.md) | 大表增长/体积只读审计：当前是否需要分区/归档、触发阈值、归档策略（复用 maintenance.py） | 平台管理员 | 判定 VERIFIED（2026-11-27 生产库实测）；分区/归档实施 PLANNED 且**当前不应实施** |

> 标注排期、质检抽检、成本月报在阶段 4 计划中同属运营 SOP 集，但本轮只交付
> 上述 4 份，避免文档铺开而无真实实现支撑。
> `table-growth-and-partitioning.md` 为后续批次新增（V5.2 审计 R?）。

---

## 2. 角色与权限

### 2.1 系统里真实存在的角色（不是运营称谓）

| 层级 | 枚举 | 取值 | 代码位置 |
|---|---|---|---|
| 平台角色 | `PlatformRole` | `member` / `admin` | `backend/app/domain/identity/models.py:18` |
| 项目角色 | `ProjectRole` | `viewer` / `editor` / `owner` | `backend/app/domain/identity/models.py:25` |
| 标注任务内角色 | `AssignmentRole` | `ANNOTATOR` / `REVIEWER` / `ARBITER` | `backend/app/domain/annotation/enums.py:225` |

权限矩阵是**显式白名单**，未列出的动作一律拒绝
（`ROLE_PERMISSIONS`，`backend/app/domain/identity/models.py:46`）：

| 项目角色 | READ | WRITE | VALIDATE | DELETE | MANAGE_MEMBERS | MANAGE_EDGE_AGENTS |
|---|---|---|---|---|---|---|
| `viewer` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `editor` | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| `owner` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

平台管理员（`PlatformRole.admin`）在 `ProjectContext.can()` 中**恒真**，即跨项目
拥有 owner 级动作权限（`backend/app/domain/identity/models.py:112`）。

接口上的三个权限闸口（`backend/app/api/deps.py`）：

| 依赖 | 语义 | 拒绝结果 |
|---|---|---|
| `get_project_scope`（默认） | READ：任何项目成员 | 403 `forbidden` |
| `get_write_scope` | WRITE：`editor` 及以上 | 403 `forbidden` |
| `get_owner_scope` | **仅 `owner` 或平台管理员**（按角色显式判定，不靠动作粒度） | 403 `forbidden` |

### 2.2 运营角色 → 系统角色映射

| 运营角色 | 映射到的系统身份 | 能做什么（真实端点/能力） | 不能做什么 |
|---|---|---|---|
| **项目 owner** | `ProjectRole.owner`（或 `PlatformRole.admin`） | 解除熔断 `POST /api/v1/projects/{project_id}/annotation-honeypot/{annotator_id}/resume`；读审计 `GET .../audit-logs`、成本台账 `GET .../ops/token-ledger`、投递队列 `GET .../ops/dispatch-queue`；读回流案例 `GET .../annotation-backflow`；建标注任务/批次/分配 | 改代码、跑迁移、改生产配置（须单独审批）；不能替标注员提交 |
| **QA lead** | `ProjectRole.editor` + 被指派 `AssignmentRole.REVIEWER` 或 `ARBITER` | 提交复核结论 `POST .../annotation-reviews`、`.../annotation-reviews/{review_id}/actions/{action}`；创建 IAA 报告 `POST .../iaa-reports`；质检抽检 `POST .../quality-samples/plan`、`.../quality-samples/{quality_sample_id}/actions/{action}`；评估黄金集 `POST .../gold-sets/{gold_set_id}/evaluations` | **不能解除熔断**（`get_owner_scope` 拒绝）；不能复核自己提交的提交（`reviewer_id` 不得等于提交人，`backend/app/application/annotation/reviews.py:148`）；不能读审计与成本台账 |
| **标注员** | `ProjectRole.editor` + `AssignmentRole.ANNOTATOR` | 处理分配给自己的 assignment：`POST .../annotation-assignments/{assignment_id}/actions/{action}`；提交草稿/提交 `POST .../annotation-submissions`、`.../annotation-submissions/{submission_id}/actions/{action}`；看自己的工作量 `GET .../annotation-workload` | 不能解除熔断、不能改黄金集、不能复核自己的提交、不能读审计/成本（403 `forbidden`） |
| **平台管理员** | `PlatformRole.admin` | 上述 owner 的全部端点 + 运维面：`GET /health`、`GET /metrics`（非 local 环境需 `DDP_METRICS_ACCESS_TOKEN`）；跨项目处置 | 不应用管理员身份替代 owner 做业务判定；`/metrics` 是全局聚合、**无 project_id 标签**，不能按项目下钻 |

> 注意：QA lead / 标注员是**运营称谓**，系统没有同名枚举。判断某人是否有权限，
> 只看其 `ProjectRole` + 被指派的 `AssignmentRole`，不看群昵称。

---

## 3. 通用操作约定

### 3.1 认证（curl 通用写法）

- 生产/常规：HS256 Bearer JWT，请求头 `Authorization: Bearer <token>`；
  签发密钥 `DDP_JWT_SECRET`（issuer `DDP_JWT_ISSUER` 默认 `ddp`，
  audience `DDP_JWT_AUDIENCE` 默认 `ddp-api`）。
- 本地联调：`DDP_LOCAL_HEADER_AUTH=true` 且 `DDP_ENV=local` 且来源为 loopback 时，
  可用 `X-User-Id: <uuid>` 代替 JWT；**平台角色不会来自请求头**
  （`backend/app/api/deps.py:95`）。

```bash
BASE=http://127.0.0.1:8000/api/v1
AUTH="Authorization: Bearer ${BEARER_JWT}"
```

### 3.2 端点前缀与错误体

- 业务端点统一前缀 `/api/v1`（`backend/app/main.py:468`）。
- 错误体统一 `{"error": {"code": "...", "message": "...", "request_id": "...", "details": {...}}}`
  （`backend/app/main.py:93`）。**申诉与工单必须记录 `request_id`**。
- 幂等：部分创建类写端点（标注任务/分配/提交/黄金集/IAA 报告/抽检计划）支持
  `Idempotency-Key` 头，**以端点签名为准**；回流受理靠 `(source, case_ref)` 天然幂等，
  熔断 `resume` 天然幂等（重复调用返回 `resumed=false`），二者都不读该头。
- 更新类端点支持 `If-Match`（版本不符 409）。
- 常见码：`forbidden`=403，`duplicate_resource`=409，`annotation_annotator_paused_honeypot`=422。

### 3.3 只读优先与留证

1. 先只读核对（审计、列表、状态行），再执行写动作；
2. 每个写动作记录：操作人、UTC 时间、`request_id`、变更前后状态；
3. 不擅自执行迁移 / 部署 / 删除数据 / 改生产开关——这些需用户单独批准
   （见 `CLAUDE.md`「数据与安全」）。

---

## 4. 文档变更纪律

- 只改 `docs/ops/**`；不碰 `backend/`、`apps/`、`packages/`、`scripts/`、`docs/memory/`。
- 文档里出现的每个端点、环境变量、Prometheus 指标名，必须能在仓库中 grep 到；
  引用不到的只能写 `PLANNED` / `MISSING`。
- 代码与文档冲突时以代码为准，并在同批 PR 修正文档。

---

## 5. 已知缺口（诚实声明，勿当已具备）

| 缺口 | 影响 | 现状 |
|---|---|---|
| 熔断状态没有读端点 | 被暂停的标注员无法自助查询；运营只能查库或从 422 错误反推 | `backend/app/api/v1/annotation_honeypot_routes.py` 只有 `POST .../annotation-honeypot/{annotator_id}/resume` |
| `resume` 不写审计 | 解除动作无 `audit_logs` 记录，必须人工留证 | `backend/app/application/annotation/honeypot_wiring.py` 无审计写入 |
| 回流没有「事后转换」HTTP 端点 | 未带 `conversion` 受理的案例停在 `RECEIVED`，Web 面无法再转 | 应用层 `BackflowUseCases.convert()` 存在，但路由未暴露（grep 无 convert 路由） |
| 标注域无独立 Prometheus 指标 | 标注吞吐/质量/成本只能走 API 与数据库 | `backend/app/core/metrics.py` 只有 HTTP 指标 + 平台业务指标 |
| 成本 ROI 口径未实现 | 看板只有 Token 成本，没有「人工时薪 / ROI」 | 配置层无时薪项（grep `config.py` 无 ROI/时薪） |
| 无 staging | 所有验证在 prod 做，回滚依赖开关 + 迁移 downgrade | `docs/roadmap/v5.2/phase4-production-plan.md` 开头约束 |

---

## 6. 术语

| 术语 | 含义 |
|---|---|
| 黄金题 / gold item | 已有权威答案、混入标注队列用于盲测质量的题；黄金集在 ACTIVE 态内容不可原地改，只能 `retire` 后出新版本 |
| 熔断（PAUSED） | 标注员黄金题滑窗准确率连续 2 个满窗 < 85%，系统自动暂停派单 |
| 回流（backflow） | 评测平台把失败案例回调给标注平台，转化为新标注任务的数据飞轮入口 |
| IAA | 标注者间一致性：`PERCENT_AGREEMENT` / `COHEN_KAPPA` / `FLEISS_KAPPA` / `KRIPPENDORFF_ALPHA` / `SPAN_F1` / `BOUNDARY_MAE` / `GOLD_ACCURACY` |
| outbox | 平台内部事件投递表（PostgreSQL outbox），`/metrics` 暴露积压与死信 |
