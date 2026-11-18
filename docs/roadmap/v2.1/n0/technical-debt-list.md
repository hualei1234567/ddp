# DDP V2.1 N0 Technical Debt List

- 日期：2026-11-18
- 基线提交：`7a9bb4b`（main）
- 口径：只登记**当前代码/数据库可复核**的债务；已闭环项不重复登记，历史项在证据栏注明。
- 证据来源：`docs/05-现状审计报告-2026-11-14.md`（含 §12 闭环与 §12.7 勘误）、
  `docs/roadmap/next-stage-v2/audit/N0-N9-项目最终报告-2026-11-17.md`、
  本轮只读复核命令（见每行证据）。

## 1. P1（阻断阶段出口，或违反硬约束）

| ID | 债务 | 位置 | 证据 / 复核结论 | 影响 | 修复建议 |
|---|---|---|---|---|---|
| TD-01 | **6 个文件 >800 行**：`annotation/submissions.py` 908、`api/errors.py` 877、`episodes/episodes.py` 850、`repositories/calibration_records.py` 821、`annotation/ports.py` 809、`datasets/version_actions.py` 803 | 见左 | `find backend/app apps/web/src (...)-exec wc -l {} + \| awk '$1>800'` | 违反 CLAUDE.md §五与 V2.1 门禁 §3；修改成本与缺陷概率上升 | 按职责拆分并补测试；不得为拆而拆，需先补请求层/仓储层测试锁定语义 |
| TD-04 | **`validity_status = UNKNOWN` 无写入点** | `domain/datasets/enums.py`、`application/datasets/validity.py` | N0 复核：巡检只写 VALID/STALE；UNKNOWN 仅在枚举 | 来源不可达时无法表达「不知道是否有效」，只能维持 VALID | 增加来源可达性探测，探测失败写 UNKNOWN；需产品确认语义 |
| TD-05 | **Episode 级自动质检缺失** | `application/datasources/*`、`domain/episodes/*` | 报告 §5 第 2 项 MISSING；导入级规则有，逐 Episode 结论由外部登记 | 质量中心无法自动回答「哪些 Episode 不合格」 | 在 N4 建设 Quality Engine 时落到 Episode 粒度，规则版本化 |
| TD-06 | **Processing Pipeline 八阶段无独立实体** | `backend/app/domain` 无 `ProcessingRun` | 复核：仅 `ingestion_jobs`；无 CREATED…RETRYING 状态机 | N4 的「可观察、可重试、可追踪」无法满足 | N4-01 新建 ProcessingRun（复用 ingestion job 与 outbox 模式） |
| TD-07 | **staging 部署 BLOCKED** | `docs/operations/*` | 报告 §6.3：主机名无法解析；本轮未重试绕过 | N8-05 出口不成立；发布链路未验证 | 用户提供可达主机后补；在此之前 staging 相关任务全部 BLOCKED |

## 2. P2（正确性/可维护性）

| ID | 债务 | 位置 | 证据 | 影响 | 修复建议 |
|---|---|---|---|---|---|
| TD-08 | `json` 与 `jsonb` 列类型在迁移间不统一 | 0012/0014/0040 用裸 `sa.JSON()`；0009/0036/0038 用 `with_variant` | 报告 §9 #1；已用 SQL cast 兼容 | 查询退化、跨方言行为差异 | 新增迁移统一列类型；先补 `EXPLAIN` 证据 |
| TD-09 | 两份私有 keyset 分页副本 | `annotation_quality_checks`、`modelops_rows` | 报告 §9 #2 | 分页语义漂移 | 收敛到 `_sql_support.keyset_after_desc` |
| TD-10 | 写入侧 `resource_type` / `action` 大小写不统一 | 16 种取值中 `calibration`、`data_source_review` 为小写 | 报告 §9 #3 | 前端只能文本框过滤，无法枚举下拉 | 统一常量表 + 迁移回填 + 契约 enum |
| TD-11 | `/metrics` 的 `status` 列无独立索引 | 治理/指标查询 | 报告 §9 #4 | 数据量大后每次抓取 2 次 GROUP BY | 预聚合或补索引 |
| TD-12 | 迁移编号缺口：0037 / 0039 / 0045–0047 未使用 | `backend/migrations/versions/` | `alembic history` 链完整（47 个 revision，单头 0052）；编号不连续 | 易误判「迁移丢失」 | 在 README 说明；新迁移继续用 0053+ |
| TD-13 | outbox 事件归类盲区：设备域 4 处 event_type 为 f-string，AST 脚本提取不到；`robot.*` / `calibration_profile.*` 未登记 | `api/v1/devices.py`、`sensors.py`、`robots.py`、`profiles.py` | 审计 §12.2 原文 | 自检通过≠全覆盖 | 改为常量枚举；补齐归类表 |
| TD-14 | ~~异常原文未落日志~~ **已闭环**（`main.py:60-79` `_log_domain_error`），编号保留不复用 | — | — | — | — |
| TD-15 | 上传会话只做惰性过期 | `application/ingestion` | 操作文档 2026-11-18 技术债表 | 孤儿会话长期占位 | 维护 worker 定期清理（复用 maintenance 模式） |
| TD-16 | `RawAssetLink` 缺独立实体/关系表 | `ingestion` | 同上 | 原始资产与 Episode 的多对多只能靠间接推断 | 先确认业务需求；需要时新增关系表与迁移 |
| TD-17 | `app/shared/` 为空壳 | `backend/app/shared/__init__.py` | `ls` 仅 `__init__.py` | 有演变为万能 utils 的风险 | 删除或声明用途 |
| TD-18 | ADR-0001–0009 状态仍标「已提议（待用户批准）」，但内容已落地（含与实现不符的 Celery/Arq、Playwright） | `docs/adr/0001..0009` | `grep -l 已提议 docs/adr/000*.md` | 决策链断档 | 批量改为 ACCEPTED 并注明实现差异（ADR 修订记录） |
| TD-19 | `_dataset_sql.ensure_expected_version` docstring 与函数体矛盾（写「严格相等」，实传 `accept_candidate=True`） | `_dataset_sql.py` | 审计 §12.7 C.1 | 后续维护者按注释理解会错 | 改注释或统一语义；补回归用例 |
| TD-20 | 镜像未推 registry / 无 HTTPS | `docker-compose.yml`、部署文档 | 报告 §9 #5 | 生产部署前置不成立 | N8-05 一起解决 |
| TD-21 | `annotation_quality_checks` 等表增长到百万级需评估分区 | — | 报告 §9 #6 | 长期运维风险 | 数据量到阈值再评估，先留监控 |
| TD-23 | `infrastructure/repositories/calibration_records.py` 869 行（>800），本次 N1-002 继续增长 | 文件规模 |
| 违反 CLAUDE.md §五；activate 链路与本文件职责混杂 | 拆出 activate/状态迁移到独立模块，先补仓储测试锁定语义 |
| TD-22 | V2.1 蓝图与仓库文档并存，缺少单一入口 | `docs/roadmap/next-stage-v2/`、`docs/roadmap/v2.1/` | 本轮新建 | 新人/Agent 读错基线 | 本目录 README 声明：V2.1 为实施基线，V2.0 为历史蓝图 |

## 3. 已闭环但需保留证据的项（避免重复登记）

| 原债务 | 结论 | 证据 |
|---|---|---|
| R-9 幂等 4 份重复 | 已收敛 | `application/idempotency.py` |
| R-10 `record_change` 3–4 份 | 已收敛 | `application/audit.py` |
| R-11 错误映射重复 | 已收敛 | `api/errors.py` `_mapped` |
| R-14 幂等清扫 | 已实现 | `workers/maintenance.py` |
| R-7 redis 未使用 | 已由 ADR-0031 重新引入并有真实使用 | `infrastructure/rate_limit.py` |
| R-15 platform phase 硬编码 | 已改配置 | `api/v1/platform.py` |
| R-12 异常原文未落日志 | 已闭环 | `main.py:60-79` `_log_domain_error`（4xx warning / 5xx error+堆栈，响应体仍不回显） |
| TD-02 G-16 记录侧修订链 | 已闭环（N1-002，迁移 0053） | 部分唯一索引 + `validate_supersedes_link`；全量 2255 passed；真实 PG 升级成功 |
| TD-03 设备 PATCH 无审计 | 已闭环（N1-001） | `patch_audit.py`；devices/sensors/robots 接入；真实栈 PATCH 后审计 +1 |
| G-04/G-05/G-08/G-09/G-03 | 已闭环 | ADR-0020/0032；`sensors.py:70-72,192-193`；`schemas.py:408-413`；`calibrations.py:264-267` |
| G-19 字段路径 | 已按白名单透传 details | `api/errors.py:113` `details_codes` |
| G-20 错误码可达 | `calibration_*` 均有产生点 | `grep -rl` 命中 domain/application |

> 说明：上表只用于防止重复登记。若后续代码改动使其失效，必须重新打开并给出新证据。

