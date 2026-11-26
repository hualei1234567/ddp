# DDP 项目全方位审计报告

> 审计日期：2026-11-26｜审计对象：`DataPlatform` 仓库 `main@6f9395f`
> 审计方式：**代码与运行数据实测**（不依赖自述文档）。每条结论附可复现的采集命令或文件路径。
> 审计立场：独立、对抗性。**优先挖问题**，不为主观印象背书。

---

## 一、执行摘要

### 总体评级：**B+（工程底子扎实，生产化仍有缺口）**

| 维度 | 评级 | 一句话结论 |
|---|---|---|
| 架构与分层 | **A-** | DDD 依赖方向零违规，domain 层纯净（0 处基础设施/框架泄漏） |
| 代码规模与组织 | **B** | 16.2 万行后端 / 10.5 万行前端，12 个文件超 500 行（最大 1035 行） |
| 测试体系 | **A-** | 4126 后端用例 + 1279 前端用例，真实 DB 测试 195 文件，0 mock 库使用 |
| 契约治理 | **A** | 270 路径双向一致（parity strict 通过），68 项已接受差异有基线 |
| 数据库与迁移 | **A-** | 74 迁移单 head，47/49 迁移测试含 downgrade 往返 |
| 安全性 | **B+** | 写端点 100% 有权限依赖，密钥启动即校验；缺 SAST/依赖持续扫描 |
| 性能与容量 | **C+** | 只读 p95 75ms 可用，但**写路径零压测**、无容量模型 |
| 可观测性 | **C** | 有 Prometheus 指标与深健康检查，**但告警未接真实实例、无 trace** |
| 可运维性 | **B-** | 有 compose + Dockerfile + 发布纪律，**无灰度/无蓝绿/无自动回滚** |
| 文档与知识 | **A** | 49 ADR + 32 记忆卡 + 326 文档 + 25 证据目录，check_ref 全部可追溯 |
| 依赖与供应链 | **B** | 15 运行时依赖全 `==` 锁定，但**无 lock 文件、无 SBOM** |
| 工程效能 | **B** | 10 步门禁全绿，但**无覆盖率门槛、无静态类型检查、CI 单 job 34 分钟** |

### 三个最该马上做的事

1. **写路径压测 + 容量模型**（当前唯一的性能盲区，风险最高）
2. **告警接真实 Alertmanager**（指标有了，没人被叫醒等于没有）
3. **代码覆盖率门槛**（4126 个用例但没有覆盖率数字，不知道哪 20% 没测）

### 三个最值得肯定的地方

1. **契约与实现的双向校验**：270 条路径无漂移，这在同类项目里罕见
2. **坑点资产化**：32 张记忆卡 + `check_ref` 强制指向真实文件，知识不会腐化
3. **诚实边界文化**：代码与文档到处标注"未验证/降级/假设"，没有把 demo 说成生产

---

## 二、审计范围与方法

**范围**：`backend/`（FastAPI 后端）、`apps/web/`（React 前端）、`data-platform/`（纯库）、`packages/contracts/`（契约）、`scripts/`（门禁）、`docs/`（文档）、`infra/` + `docker-compose.yml`（部署）、`.github/workflows/`（CI）

**方法**：
- 静态分析：`ruff`、文件规模统计、import 方向扫描、正则安全扫描
- 运行时采集：pytest 收集、`alembic heads`、PG 系统表（`pg_tables`/`pg_indexes`/`pg_stat_user_tables`）
- 交叉验证：契约 `check_contract_parity.py --strict`、记忆库 `check_memory.py`
- 压测结论引用已有证据 `docs/testing/evidence/2026-11-26-prod-readonly-perf/`

**未覆盖**（诚实声明）：无 SAST 工具（Semgrep/CodeQL）、无渗透测试、无覆盖率工具（未安装 coverage.py）、无长期稳定性观测（无 7×24 运行数据）

---

## 三、架构与分层（A-）

### 3.1 实测结果

| 检查项 | 结果 |
|---|---|
| `domain/` 引入 `infrastructure/` 或 `application/` | **0 处** ✅ |
| `domain/` 引入 FastAPI / SQLAlchemy | **0 处** ✅ |
| `application/` 引入 FastAPI | **0 处** ✅ |
| `infrastructure/` 引入 `application/` | 3 处（`training_adapters.py`、`training_adapter_http.py`、`model_platform.py`），均为**实现 application 定义的 Port 协议**，属正当依赖倒置 ✅ |

**命令**：
```bash
grep -rln "from app.infrastructure\|from app.application" backend/app/domain/   # 空
grep -rln "from fastapi\|from sqlalchemy" backend/app/domain/                   # 空
grep -h "from app.application" backend/app/infrastructure/training_adapters.py  # 仅 Port 类型
```

### 3.2 评价

分层纪律是这份代码最扎实的部分。`domain` 层承载 848 个文件中最大的一块逻辑却零框架依赖，意味着领域逻辑可脱离 FastAPI/SQLAlchemy 单测——这也是 4126 个测试能在 34 分钟内跑完的结构性原因。

**建议（低优先级）**：`infrastructure` → `application` 的 3 处引用虽属正当 Port 实现，但建议在 `docs/adr/` 补一条说明，避免后续误判为违规而误改。

---

## 四、代码规模与组织（B）

### 4.1 规模实测

| 模块 | 文件 | 行数 |
|---|---|---|
| `backend/app` | 706 | **161,644** |
| `backend/tests` | 417 | 113,645 |
| `apps/web/src` | 556 | 105,329 |
| `data-platform` | 54 | 10,108 |
| 迁移 | 74 | — |
| 契约 | 106 文件 | — |

测试行数 / 生产行数 = **0.70**（后端），比例健康。

### 4.2 超大文件（>500 行，共 12 个后端 + 5 个前端）

| 文件 | 行数 | 风险 |
|---|---|---|
`application/annotation/ports.py` | 1035 | 中：纯 Protocol 集合，可接受但已到拆分线
`api/errors.py` | 1008 | 中：错误码映射表，建议按域分片
`application/annotation/submissions.py` | 948 | **高**：核心提交链路，改动风险集中
`domain/annotation/submission.py` | 906 | 中：领域聚合根
`application/episodes/episodes.py` | 870 | 中
`infrastructure/repositories/calibration_records.py` | 869 | 中
`apps/web/src/types/annotation.ts` | 810 | 中：类型定义集中
`apps/web/src/services/datasets.ts` | 767 | 中
`apps/web/src/hooks/useUploadQueue.ts` | 764 | **高**：上传队列是状态密集逻辑
`apps/web/src/pages/episodes/EpisodeListPage.tsx` | 760 | **高**：建议拆组件

**评价**：12/706 = 1.7% 的文件超 500 行，比例很低（多数项目 >10%）。但 `submissions.py`（948）与 `useUploadQueue.ts`（764）是高变更率 + 高复杂度叠加点。

**建议（中优先级）**：
1. `application/annotation/submissions.py` 按"提交校验 / 状态流转 / 审计记录"拆 3 文件（已有拆前例：`ai_replay.py`/`ai_usage.py` 从 `ai_suggestions.py` 拆出）
2. `useUploadQueue.ts` 拆为 hook（状态机）+ 工具函数（重试/分片）

### 4.3 技术债标记

| 指标 | 实测 |
|---|---|
| `TODO` / `FIXME` / `XXX` / `HACK` | **0** ✅ |
| `type: ignore` | 170 |
| `noqa` | 83 |
| 裸 `except:` | **0** ✅ |
| `except Exception` | 44（集中在 workers 与 processing pipeline，属"任务不得因单条失败中断"的正当用法） |

170 处 `type: ignore` 是**最大隐性技术债**：项目**没有 mypy/pyright**，所以这些忽略既无验证也无收益——要么上静态类型检查让它们有意义，要么说明它们只是噪音。

**建议（中优先级）**：引入 `mypy --strict` 到**新增代码**（增量而非全量），基线以外的 `type: ignore` 报错。预计首次可清掉 30–50% 的忽略。

---

## 五、测试体系（A-）

### 5.1 规模与分布

| 层 | 文件数 |
|---|---|
| `tests/unit` | 159 |
| `tests/api` | 105 |
| `tests/migrations` | 49 |
| `tests/infrastructure` | 40 |
| `tests/workers` | 10 |
| `tests/application` | 7 |
| `tests/e2e` | 4 |
| `tests/integration` | 1 |

**用例数**：后端 **4126**、前端 **1279 passed / 19 skipped**（163 文件）。

### 5.2 真实性检查（关键）

| 检查项 | 实测 | 评价 |
|---|---|---|
| `MagicMock` / `Mock(` 使用 | **0** | 极好：不用 mock 库，用真实 fixture / 手写 fake |
| `monkeypatch` | 219 | 合理：多为环境变量注入 |
| 真实 DB 测试（SQLite/PG）| 195 文件 | 极好：不是"假数据库"测试 |
| 跳过的测试 | 11（前端 skip）/ 19 skipped（前端总数） | 可接受 |

**评价**：这是本项目最强的维度之一。0 处 `MagicMock` 意味着测试覆盖的是真实 SQL、真实 HTTP 栈、真实迁移——2026-11-26 生产演练抓到的回流 409 缺陷、N+1 问题都是"真实环境才暴露"的类型，说明现有测试**确实**在补这些缺口而不是自我安慰。

### 5.3 缺口

| 缺口 | 影响 | 严重度 |
|---|---|---|
| **无覆盖率测量** | 4126 个用例但不知道行/分支覆盖率；不知道哪 20% 没测 | **高** |
| `tests/domain` 仅 3 文件 | 领域层 848 文件，纯领域测试偏少（部分逻辑在 `unit` 里） | 中 |
| `tests/integration` 仅 1 文件 | 跨模块集成测试薄弱（e2e 4 个文件承担了这部分） | 中 |
| 前端 collect 353s vs 执行 112s | 收集开销是执行的 3 倍，CI 时间浪费 | 低 |

**建议（高优先级）**：
```bash
# 加入 requirements-dev.txt 与 check.sh
pip install coverage
coverage run -m pytest && coverage report --fail-under=75
```
先测出真实基线再定门槛（不要拍脑袋定 90%）。

---

## 六、契约与 API 治理（A）

| 指标 | 实测 |
|---|---|
| 契约路径 | 270 |
| 运行时路径 | 270 |
| 双向漂移 | **0**（`--strict` 通过） |
| 已接受差异基线 | 68 项（有 `_why` 说明） |
| 契约版本 | 1.40.0 |
| 路由文件 | 118 |

**评价**：`check_contract_parity.py` 同时校验"契约超前"（契约有、实现无）与"实现超前"（实现有、契约无），且 `--strict` 会失败。这在实践中抓过真实缺陷（PR #134 的 Redocly 路径参数、PR #140 的裸容器字段），是**门禁有效**的直接证据。

**遗留（低优先级）**：68 项已接受差异中，部分属"响应字段实现必填但契约 optional"的临时宽松。建议按季度收敛，基线只减不增。

---

## 七、数据库与迁移（A-）

| 指标 | 实测 |
|---|---|
| 迁移总数 | 74 |
| heads | **1 个（0084）** ✅ |
| 生产库版本 | 0084（已实跑） |
| 表 / 索引（生产） | 101 / 408 |
| 迁移测试含 downgrade | **47 / 49** = 96% ✅ |
| 最大迁移文件 | 570 行（`0009_stage2_ingestion_episode_lineage.py`） |

**评价**：单 head + 96% downgrade 覆盖，意味着任何迁移都可回滚——这是"不设 staging 直接上生产"策略成立的前提。生产数据量小（`usage_snapshots` 240 行、`episodes` 206 行），说明**尚未经历真实数据量考验**。

**风险（高）**：数据量为百级，但索引已有 408 个（平均每表 4 个）。在真实数据量下，未使用的索引会成为写放大来源。建议用 `pg_stat_user_indexes.idx_scan` 识别零使用索引。

---

## 八、安全性（B+）

### 8.1 正面实测

| 检查项 | 结果 |
|---|---|
| 写端点鉴权覆盖 | **100%**（POST/PUT/PATCH/DELETE 全部依赖 Scope/Principal；13 个"疑似"经复检全部使用 `DeleteScope`/`ExportGrantScope`/`PlatformAdmin` 等自定义依赖） |
| 读端点鉴权 | 227 处 `ReadScope` |
| 密钥入库 | **无** `.env` 入库（`.gitignore` 已覆盖 `.env`、`.env.*`） |
| 硬编码密钥 | 无（唯一匹配是日志脱敏正则的注释） |
| 启动期安全校验 | **有**：非 local 环境拒绝占位符密钥，`jwt_secret` 长度 <32 直接拒绝启动 |
| CORS 默认 | `cors_allowed_origins = []`（白名单空 = 不放行），`allow_credentials=False` ✅ |
| 限流 | 默认开启（`rate_limit_enabled=True`），支持内存/Redis 双后端 |
| Secret 扫描 | CI 阻断（`scripts/check_secrets.py`） |
| 依赖漏洞扫描 | CI 有 `pip-audit`，但**仅告警不阻断** |

### 8.2 缺口

| 缺口 | 严重度 | 说明 |
|---|---|---|
| 无 SAST（Semgrep/CodeQL） | **高** | 注入、路径穿越、SSRF 类问题只能靠 review |
| 依赖漏洞仅告警 | 中 | 阶段 0 的临时宽松，应设"高危必须修"的阻断线 |
| 无 SBOM | 中 | 15 个直接依赖 + 传递依赖没有物料清单，供应链事件无法快速定位影响面 |
| 无 lock 文件 | 中 | `requirements.txt` 全是 `==` 精确版本，但**传递依赖未锁**——`pip install` 结果随上游漂移 |
| 无前端依赖审计 | 中 | 25 个前端依赖，`package-lock.json` 存在，但 CI 无 `npm audit` |

**建议（高优先级）**：
1. 加 Semgrep（开源，CI 5 分钟级）→ 阻断高危规则
2. `pip-audit` 从"仅告警"升级为"high/critical 阻断"
3. 用 `pip-compile` 生成 `requirements.lock`（含传递依赖哈希）

---

## 九、性能与容量（C+）

### 9.1 已有实测（只读）

| 指标 | 第 1 次 | 第 2 次 |
|---|---|---|
| 请求数 / 错误率 | 2100 / 0.00% | 2100 / 0.00% |
| p50 / p95 / p99 | 15.24 / **75.66** / 89.33 ms | 15.24 / 93.41 / 105.82 ms |
| PG 连接峰值 | 18/100 (18%) | 18/100 |
| p95 > 500ms 端点 | **0** | **0** |

N+1 缺陷已修复（`collection-tasks` 41 → 3 次查询，PR #148），性能预算表已建立（`docs/roadmap/v5.2/performance-budget.md`）。

### 9.2 缺口（本节是整份审计最严重的部分）

| 缺口 | 严重度 | 影响 |
|---|---|---|
| **写路径零压测** | **高** | 标注提交、任务创建、导出触发等写操作的真实延迟与锁竞争完全未知 |
| **无容量模型** | **高** | 不知道"多少并发用户/多少事件每秒"会打满，无法回答扩容问题 |
| 无长期稳定性数据 | **高** | 无 7×24 运行、无内存增长曲线、无连接泄漏观测 |
| 压测仅单机短时 | 中 | 180 秒 / 2100 请求，不足以暴露慢泄漏 |
| 前端 bundle 686 kB > 500 kB 告警 | 低 | 未做 manualChunks 拆分，首屏可优化 |

**建议（高优先级）**：
1. 用 `scripts/perf/` 扩展写路径压测（**必须**在隔离库上做，不能压生产写）
2. 建立容量模型：单请求 DB 查询数 × QPS → 连接池需求，形成"扩容决策表"
3. 起一个 24 小时 soak 测试（只读 + 低频写），观测 RSS 与连接数曲线

---

## 十、可观测性与可运维性（C / B-）

### 10.1 已有

| 能力 | 状态 |
|---|---|
| Prometheus 指标端点 | ✅ `/metrics`（含抓取令牌鉴权、非本地环境无令牌返回 503） |
| 业务指标 | ✅ 接入/Episode/导入/处理/质量状态分布 + outbox 积压 + 回流积压 + 熔断暂停 + 近 1 小时通过提交 |
| 深度健康检查 | ✅ `/api/v1/health/dependencies`（任一依赖异常返回 200 degraded，探活 ≤2s） |
| 结构化日志 | ✅ 含 trace_id、敏感字段脱敏 |
| 审计日志 | ✅ 39 行（生产），覆盖标注/项目/成员变更 |
| 部署载体 | ✅ docker-compose 13 服务 + `infra/docker/{api,web}.Dockerfile` + nginx 模板 |
| 发布纪律 | ✅ `docs/ops/release-discipline.md`（迁移五步法、开关清单、回滚演练记录） |
| 运营 SOP | ✅ 6 篇（索引/熔断申诉/回流受理/看板口径/发布纪律） |

### 10.2 缺口（评级低的原因）

| 缺口 | 严重度 |
|---|---|
| **告警规则只在文档里，未接真实 Alertmanager** | **高** |
| **无分布式 trace**（只有 request_id 串联日志，跨服务/跨 worker 不可追） | **高** |
| 无灰度/蓝绿发布 | 中 |
| 无自动回滚（靠人工 revert） | 中 |
| 无 SLO 燃烧率看板（有指标无告警表达式落地） | 中 |
| worker 无独立健康端点（13 服务里 8 个 worker 只能看进程存活） | 中 |

**建议（高优先级）**：
1. 部署 Prometheus + Alertmanager（compose 加 2 个服务即可），把 `docs/ops/dashboard-spec.md` 的 5 条规则真正加载
2. 加 OpenTelemetry SDK（FastAPI 有官方 instrumentor），trace 打通 API → 用例 → 仓储 → outbox

---

## 十一、文档与知识资产（A）

| 资产 | 数量 | 质量评价 |
|---|---|---|
| ADR | **49 篇** | 决策有背景/备选/取舍，非流水账 |
| 记忆卡 | **32 张** | 每张有 `check_ref` 强制指向真实文件，`check_memory.py` 阻断失效引用 |
| 测试证据 | **25 个目录** | 含原始 JSON/日志，可复现 |
| 运营 SOP | 6 篇 | 有触发条件/步骤/判定/时限 |
| roadmap 文档 | 117 篇 | — |
| 契约草案/映射 | `docs/architecture/` | 含兼容性基线与差异说明 |

**评价**：这是本项目第二个强项。`check_memory.py` 会**阻断** `check_ref` 指向不存在文件的卡片——这条规则把所有"文档写得漂亮但实际不存在"的腐化路径堵死了。实测 32 张卡片全过、6 张标记复发（说明复发被如实记录而非隐藏）。

**缺口（低）**：`docs/roadmap/` 117 篇中部分历史阶段文档未标注"已完成/已废弃"，检索时噪音较大。建议加 `status:` front-matter。

---

## 十二、依赖与供应链（B）

| 项 | 实测 |
|---|---|
| 后端运行时依赖 | 15 个，**全部 `==` 精确锁定** |
| 后端开发依赖 | 5 个，全部锁定 |
| 前端依赖 | 25 个，`package-lock.json` 存在 |
| 关键库版本 | FastAPI 0.115.6 / SQLAlchemy 2.0.36 / Alembic 1.14.0 / Pydantic Settings 2.7.1 |
| 依赖漏洞扫描 | `pip-audit`（仅告警） |
| SBOM | **无** |
| 传递依赖锁定 | **无**（无 lock 文件） |

**评价**：直接依赖锁定得很好，还在注释里写了"为什么需要这个依赖""替代方案是什么"——这是少见的严谨。但**传递依赖未锁**是真实风险：`fastapi==0.115.6` 的 30+ 个传递依赖会随上游漂移，同一天两次 `pip install` 可能装出不同环境。

**建议（中优先级）**：`pip-compile --generate-hashes` 生成 `requirements.lock`，CI 用 lock 安装。

---

## 十三、工程效能（B）

### 13.1 门禁（10 步，全绿）

```
[1] 后端 ruff check + format        [6] Agent Bash 守卫测试
[2] 后端 pytest（4126 用例）         [7] outbox 事件归类自检
[3] 前端 typecheck/ESLint/vitest     [8] 契约一致性（270 路径双向）
[4] 前端构建                          [9] Canonical Schema 一致性
[5] Secret 扫描（阻断）               [10] 记忆库校验 + 星云同步
```

**评价**：10 步门禁覆盖了多数团队会漏的点（契约漂移、outbox 消费者、记忆卡失效引用、Schema 手改）。**这是本项目能把 17 个 PR 在一天内安全合并的底层保障。**

### 13.2 缺口

| 缺口 | 严重度 | 影响 |
|---|---|---|
| 无覆盖率门槛 | **高** | 见 §5.3 |
| 无后端静态类型检查 | 中 | 170 处 `type: ignore` 无收益 |
| CI 单 job 34 分钟 | 中 | 可拆并行（后端 lint/test 与前端 lint/test 已并行，但后端 pytest 单进程） |
| 无 pre-commit | 低 | 本地问题推到 CI 才发现 |
| 前端 collect 353s | 低 | 收集比执行慢 3 倍 |

**建议（中优先级）**：pytest 加 `-n auto`（pytest-xdist）；CI 按 tests 目录切片并行。

---

## 十四、风险登记册（按严重度排序）

| # | 风险 | 严重度 | 概率 | 影响面 | 建议动作 | 时限 |
|---|---|---|---|---|---|---|
| R1 | 写路径无压测，生产写操作可能在高并发下锁竞争/超时 | **高** | 中 | 标注提交、任务创建 | 隔离库写压测 + 容量模型 | 1 周内 |
| R2 | 告警规则未加载到真实 Alertmanager，故障无人被叫醒 | **高** | 高 | 全系统可用性 | compose 加 Prometheus+Alertmanager | 1 周内 |
| R3 | 无覆盖率测量，4126 用例的盲区未知 | **高** | 高 | 变更安全性 | 接 coverage + 定基线门槛 | 1 周内 |
| R4 | 无 SAST，注入/SSRF 类问题靠人眼 | **高** | 中 | 安全 | 接 Semgrep 阻断高危 | 2 周内 |
| R5 | 传递依赖未锁，环境不可复现 | 中 | 高 | 构建/部署 | pip-compile 生成 lock | 2 周内 |
| R6 | 408 索引中可能有零使用索引，写放大 | 中 | 中 | 写性能 | `pg_stat_user_indexes` 审计 | 2 周内 |
| R7 | `submissions.py`(948)/`useUploadQueue.ts`(764) 高复杂度高热区 | 中 | 中 | 变更风险 | 拆分 + 补测试 | 1 月内 |
| R8 | 170 处 `type: ignore` 无静态检查背书 | 中 | 中 | 类型安全 | 增量 mypy | 1 月内 |
| R9 | 无分布式 trace，跨服务问题定位慢 | 中 | 中 | 可运维性 | OTel 接入 | 1 月内 |
| R10 | 前端 bundle 686 kB 超告警线 | 低 | 高 | 首屏 | manualChunks | 1 月内 |
| R11 | 数据量为百级，未经历真实量考验 | 中 | — | 全部性能结论 | 造 10× 数据做验证 | 待数据 |
| R12 | 无灰度/自动回滚 | 中 | 低 | 发布风险 | 保留人工审批 + 扩展开关 | 视需要 |

---

## 十五、改进路线图建议

### 第 1 周（补最危险的洞）
1. coverage 接入 + 基线测量（R3）
2. Prometheus + Alertmanager 真实部署，5 条规则加载（R2）
3. 写路径压测脚本（隔离库）+ 容量模型初版（R1）

### 第 2 周（安全与可复现）
4. Semgrep 接入，高危规则阻断（R4）
5. `pip-compile` 生成 lock + SBOM（R5）
6. `pg_stat_user_indexes` 索引审计，删零使用索引（R6）

### 第 3–4 周（结构与观测）
7. `submissions.py` / `useUploadQueue.ts` 拆分（R7）
8. 增量 mypy（新代码 strict）（R8）
9. OTel trace 接入（R9）
10. 前端 manualChunks 拆分（R10）

### 持续（每月）
- 契约差异基线只减不增
- 记忆卡复发项 ≥3 次的升级为自动化约束（已触发：MEM-INC-005/009/016/020/021/022）
- roadmap 文档补 `status:` front-matter

---

## 十六、审计局限（诚实边界）

1. **无覆盖率数字**：未安装 coverage，无法给出真实覆盖率——本报告只能评价测试"规模与真实性"，不能评价"充分性"
2. **无 SAST 扫描结果**：未运行 Semgrep/CodeQL，安全结论基于人工模式扫描 + 配置审阅
3. **无真实数据量**：生产库最大表 240 行，所有性能结论仅代表"小数据量下"
4. **无长期运行数据**：无 7×24 观测，内存泄漏/连接泄漏无法判断
5. **无渗透测试**：权限矩阵基于代码审阅，未做越权尝试
6. **未审计前端可访问性**（a11y）：未跑 axe/Lighthouse
7. 本报告的所有"评级"是**相对判断**，无行业基准对照

---

## 附录：审计命令清单（可复现）

```bash
# 规模
find backend/app -name '*.py' | wc -l; find backend/app -name '*.py' -exec cat {} + | wc -l

# 分层合规
grep -rln "from app.infrastructure\|from app.application" backend/app/domain/
grep -rln "from fastapi\|from sqlalchemy" backend/app/domain/

# 超大文件
find backend/app -name '*.py' -exec wc -l {} + | sort -rn | awk '$1>500'

# 技术债
grep -rn "TODO\|FIXME\|XXX\|HACK" backend/app apps/web/src data-platform
grep -rn "type: ignore" backend/app | wc -l

# 契约
PYTHONPATH=".:data-platform" backend/.venv/bin/python scripts/check_contract_parity.py --strict

# 迁移
cd backend && PYTHONPATH=".:../data-platform" .venv/bin/python -c \
  "from alembic.config import Config; from alembic.script import ScriptDirectory; \
   print(ScriptDirectory.from_config(Config('alembic.ini')).get_heads())"

# 数据库规模
PGPASSWORD=ddp psql -h localhost -p 55432 -U ddp -d ddp -c \
  "select count(*) from pg_tables where schemaname='public'; select count(*) from pg_indexes where schemaname='public';"

# 记忆库
python3 scripts/check_memory.py
```
