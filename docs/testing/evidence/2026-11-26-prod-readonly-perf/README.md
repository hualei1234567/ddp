# G10 生产只读压测基线（2026-11-26）

> 任务：G10 / `v52-034`。用户明确要求**不使用 staging、全面在 prod**。
> 证据文件（本目录）：
> - `readonly-load-raw.json` —— 正式基线第 1 次（本文 §4.1 主表）
> - `readonly-load-raw-repeat.json` —— 同参数重复第 2 次（抖动评估）
> - `nplus1-scaling-raw.json` —— 行数梯度实验（N+1 证据）
>
> 工具：`scripts/perf/readonly_load.py`（用法与红线见 `scripts/perf/README.md`）

## 1. 结论摘要

**单机生产环境的短时只读基线跑通：2100 个 GET 请求、0 错误、0 写操作，
PostgreSQL 连接峰值 18/100（18%），未触发 80% 守卫。**

| 检查项 | 结果 |
|---|---|
| 只读契约 | 3 次运行全部仅 GET；端点解析从输入侧拒绝非 GET 语义 |
| 错误率 | 0.00%（2100/2100 成功，两次重复运行一致） |
| 整体 p50 / p95 / p99（第 1 次） | 15.24ms / **75.66ms** / 89.33ms |
| 整体 p50 / p95 / p99（第 2 次） | 15.24ms / **93.41ms** / 105.82ms |
| 最慢端点 | `collection-tasks` 列表：p95 87.93ms（第 2 次 110.73ms） |
| p95 > 500ms 的端点 | **0 个** |
| PG 连接守卫 | 峰值 18/100（18%），阈值 80%，未中止 |
| 数据写入 | 0（无 POST/PATCH/PUT/DELETE） |
| 门禁 | `scripts/check.sh` 10/10 通过（§8） |

**主要发现**：`GET /collection-tasks` 存在结构性 N+1（列表每行额外 2 次
分配查询），是本机最慢只读端点，且延迟随页大小线性增长（limit 1→20→50：
8.6→38.9→87.7ms）。当前 p95 未超 500ms，不属于门禁级瓶颈，但页大小或数据量
上来后会放大；已给出批量查询建议（§5.1），**按任务边界本任务未改业务代码**。

## 2. 环境事实（“生产”即本机 docker compose）

| 项 | 值 | 证据 |
|---|---|---|
| 部署形态 | 本机 docker compose；PG / MinIO / 各类 worker 容器 `Up` | `docker ps` |
| PostgreSQL | 16.16，`127.0.0.1:55432/ddp`，`max_connections=100` | `select version(); show max_connections;` |
| MinIO | `http://127.0.0.1:9000` | compose 端口映射 |
| 被测 API | **本次新建**单 worker uvicorn `127.0.0.1:18000`，只连生产库 | 启动命令 §6；`/health`=200 |
| 生产 API 容器 | `ddp-api` 已 `Exited (255)` 3 天（compose profile `app` 不常驻） | `docker ps -a` |
| 数据规模 | projects 48 / users 126 / episodes 206 / raw_assets 283 / datasets 83 / collection_tasks 98 | psql 只读 `count(*)` |
| 压测项目 | `1bca073d-c9af-4601-9b0f-7888d0747db0`（98 tasks / 137 episodes / 65 datasets / 26 raw_assets） | psql |
| 压测身份 | 该项目 owner 用户（`project_members.role=owner`） | psql；JWT 用生产 `.env` 的 `DDP_JWT_SECRET` 按 HS256 签发，TTL 2h，未入库 |
| 机器 | arm64 / 10 核 / 16 GiB / macOS；PG 16.16 | 报告 `environment.hardware` |
| 运行负载 | load average 1m=3.09 / 5m=2.99 / 15m=2.43（压测开始时，后台 worker 在跑） | 报告 `environment.load_average_start` |
| 代码版本 | `8b0abdca91`（分支 `feat/v52-perf-baseline`，工作区含本任务未提交改动） | 报告 `environment.git` |

> 说明：生产 API 进程为本次按任务授权**新建的只读用途实例**（同 commit、连生产
> 库），未修改任何生产配置；`ddp-api` 容器保持原 `Exited` 状态未启动。

## 3. 安全合规（红线逐条）

| 红线 | 执行情况 | 证据 |
|---|---|---|
| 只压只读 GET，禁止写方法 | 3 次运行仅 GET；脚本无写方法；解析器拒绝含空白/绝对 URL 的端点 | `readonly_load.py`、`core.parse_readonly_endpoints` |
| 并发 ≤32 / 每端点 ≤2000 / 总时长 ≤180s | 实际 concurrency 8、300 请求/端点、duration 120s；越限用例 33/2001/181 均退出码 2 | §6 越限验证 |
| 默认 dry-run，显式 `--execute` 才打 | 默认 dry-run 仅打印计划，未发请求；正式运行显式 `--execute` | dry-run 输出 |
| PG 连接数守卫 >80% 立即中止 | 峰值 18/100=18%；每秒采样；守卫连续 3 次查询失败也中止 | 报告 `connection_guard` |
| 单机短时基线 ≠ 容量规划 | 报告 `disclaimer` 字段 + 本文 §7 | 原始 JSON |

补充：端点解析拒绝 `POST /api/v1/...` 这类描述（含空白），也拒绝 `http://` 绝对
URL；目标默认只允许 loopback，非本机目标需 `--allow-non-loopback`。守卫使用**只读**
SQL（`pg_stat_activity` / `current_setting`），不写 PG。

## 4. 实测数字

口径：最近秩百分位；RPS=阶段内 `请求数/墙钟`；端点逐段串行压测，整体 RPS 不等于
系统并发上限。环境负载见 §2。

### 4.1 正式基线（第 1 次，`readonly-load-raw.json`）

总请求 2100，墙钟 5.8965s，整体 **RPS 356.15**，错误率 0.00%。
整体 p50=15.237ms，p95=75.655ms，p99=89.329ms。

| 端点 | n | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) | RPS | 错误率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `GET /health` | 300 | 2.39 | 2.98 | 3.97 | 4.04 | 3076.43 | 0.00% |
| `GET /api/v1/projects?limit=20` | 300 | 8.32 | 9.66 | 41.90 | 42.16 | 866.50 | 0.00% |
| `GET /api/v1/projects/{id}` | 300 | 11.29 | 13.33 | 92.01 | 93.20 | 584.69 | 0.00% |
| `GET .../episodes?limit=20` | 300 | 20.73 | 28.32 | 34.02 | 34.37 | 371.14 | 0.00% |
| `GET .../raw-assets?limit=20` | 300 | 19.04 | 23.13 | 85.95 | 89.33 | 377.07 | 0.00% |
| `GET .../datasets?limit=20` | 300 | 15.12 | 18.31 | 21.60 | 24.13 | 515.27 | 0.00% |
| `GET .../collection-tasks?limit=20` | 300 | **72.64** | **87.93** | **118.84** | **124.54** | 108.98 | 0.00% |

### 4.2 重复运行（第 2 次，`readonly-load-raw-repeat.json`）

同机同参数连续第二次，整体 RPS 312.19，p50=15.242ms、p95=93.413ms、
p99=105.823ms，错误率 0.00%。

| 端点 | p50 (ms) | p95 (ms) | 两次 p95 偏差 |
|---|---:|---:|---:|
| `/health` | 3.07 | 3.81 | +27.9% |
| `/api/v1/projects?limit=20` | 10.66 | 12.52 | +29.6% |
| `/api/v1/projects/{id}` | 12.06 | 13.33 | 0.0% |
| `.../episodes?limit=20` | 23.98 | 37.76 | +33.3% |
| `.../raw-assets?limit=20` | 23.68 | 26.94 | +16.5% |
| `.../datasets?limit=20` | 15.16 | 19.53 | +6.7% |
| `.../collection-tasks?limit=20` | 87.93 | 110.73 | +25.9% |

两次运行 p95 偏差约 0–33%，属于本机（后台 worker、GC、CPU 调度）正常抖动。
**不要把单次 p95 当硬指标**；回归对比必须同机同参数并重复取中位数。

### 4.3 行数梯度实验（N+1 直接证据，`nplus1-scaling-raw.json`）

固定并发 4、每端点 100 请求，只改变 `limit`：

| 端点阶段 | p50 (ms) | p95 (ms) | RPS | 每行边际 |
|---|---:|---:|---:|---:|
| collection-tasks `limit=1` | 8.57 | 12.14 | 427.58 | — |
| collection-tasks `limit=20` | 38.90 | 45.18 | 95.82 | ≈ +1.60ms/行 |
| collection-tasks `limit=50` | 87.70 | 95.91 | 45.12 | ≈ +1.62ms/行 |
| episodes `limit=1`（对照） | 9.70 | 11.13 | 405.62 | — |
| episodes `limit=50`（对照） | 19.39 | 22.57 | 176.65 | ≈ +0.20ms/行 |

`collection-tasks` 随行数线性增长（≈1.6ms/行），`episodes` 近似平坦（≈0.2ms/行）：
这是“每行固定次数额外查询”的典型形状，而非数据量导致的扫描变慢。

## 5. 瓶颈与具体优化建议（本任务不改业务代码）

### 5.1 结构性 N+1：`GET /collection-tasks` 列表（主要发现）

**代码事实**（只读排查，未修改）：

- 路由：`backend/app/api/v1/collection.py:42` → 用例
  `backend/app/application/collection/tasks.py:122` `list_tasks`。
- 列表仓储 `backend/app/infrastructure/repositories/collection_tasks.py:102`
  一次查询取 21 行；随后 `_from_row`（同文件 `:253`）对每一行调用
  `_assignment_ids`（同文件 `:301`）**两次**：
  - `:270` 查 `collection_task_devices`
  - `:276` 查 `collection_task_sensors`
- 即 `limit=20` 时约 **1（列表）+ 1（episode 计数）+ 40（分配）= 42 条 SQL**，
  40 条为固定 round-trip 开销。
- 索引不是问题：两张关联表 PK 为 `(project_id, task_id, device_id|sensor_id)`，
  逐行查询命中 PK 前缀；`episodes` 已有 `(project_id, task_id)` 索引
  （`ix_episodes_project_task`），计数查询无缺索引。
- 当前分配数据极少（全库 `collection_task_devices=1`、`collection_task_sensors=1`），
  说明**瓶颈是往返次数，不是返回数据量**。

**量化**：梯度实验中 `limit=20` 为 38.90ms、`limit=50` 为 87.70ms，边际 ≈1.61ms/行
≈ 2 次往返 × 0.80ms；与代码事实一致。

**建议（后续任务实施，建议单开任务）**：

1. **批量装配分配**（首选，改动集中在仓储）：仿照已有的
   `EpisodeRepository.counts_by_task`（`backend/app/infrastructure/repositories/episodes.py:147`）
   的批量做法，对当前页的 `task_ids` 一次查两张关联表：
   `WHERE project_id=? AND task_id IN (...) ORDER BY task_id, device_id`
   （sensors 同），在内存里 `GROUP BY task_id` 后塞回实体。
   预期 SQL 从约 42 条降到 4 条；按梯度外推 `limit=20` p50 38.9→约 10–15ms、
   `limit=50` 87.7→约 15ms。**这是外推估算，修复后必须同机同脚本复测确认。**
2. **只为最终页装配**：`list_tasks` 先取 `limit+1` 再 `build_page` 丢掉 1 行；
   若保留该行参与 `_from_row`，会多算 2 次查询。批量后此项自然消失，但若暂不改
   批量，至少把计数/装配放到 `build_page` 之后。
3. **页面变大时优先做 1**：默认页 20 尚可接受；若前端/客户端把默认页提到 50+，
   N+1 会线性放大（实测 50 行 p50 已是 87.7ms）。

**为何不在本任务改**：任务边界明确“不要在本任务里改业务代码，改动留后续任务”。
本任务只交付基线、证据与建议。

### 5.2 尾部抖动：`GET /api/v1/projects/{id}`

第 1 次 p99=92.0ms（p50=11.3ms、max=93.2ms），第 2 次 p99=15.3ms。属一次性尾部
抖动（GC/线程池/连接池争用一类），**不作为瓶颈结论**；建议后续用更长窗口或
`--requests 1000` 复测确认是否稳定存在。

### 5.3 其他端点与容量观察

- `episodes` / `raw_assets` / `datasets` 列表 p95 18–38ms，当前数据量下余量充足；
  但数据只有 206 episodes，**延迟以固定开销为主，不代表 10⁶ 行规模**。
- API 为单 uvicorn worker；同步 `def` 端点走 Starlette/AnyIO 线程池（默认 40）。
  本次并发 8 未接近线程池或连接池上限（`pool_size=10 + max_overflow=20`）。
- PG 连接峰值 18/100：被测 API 连接池 + 守卫连接 + 后台 worker，未见泄漏迹象。
- 未发现缺失索引导致的慢查询；本次不建议为性能加迁移（`collection_tasks` 的
  列表排序用 `(project_id, id)` 唯一索引，已有）。

## 6. 复现命令（真实执行过的版本）

```bash
# 0) 让被测 API 起来（只连生产库，单 worker）
set -a; . <repo>/.env; set +a
export DDP_DATABASE_URL='postgresql+psycopg://ddp:ddp@localhost:55432/ddp'
export DDP_S3_ENDPOINT='http://127.0.0.1:9000'
export PYTHONPATH='<repo>/data-platform'
cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 18000 --workers 1

# 1) 铸造 token（生产 .env 的 DDP_JWT_SECRET；写入 /tmp/perf-token，不入库）
#    见 scripts/perf/README.md §3，sub=541eb26e-...（项目 owner），TTL 2h

# 2) 默认 dry-run（不发请求）
backend/.venv/bin/python scripts/perf/readonly_load.py \
  --base-url http://127.0.0.1:18000 --project-id 1bca073d-c9af-4601-9b0f-7888d0747db0 \
  --endpoints 'health=/health,projects=/api/v1/projects?limit=20,
    project_detail=/api/v1/projects/{project_id},
    episodes=/api/v1/projects/{project_id}/episodes?limit=20,
    raw_assets=/api/v1/projects/{project_id}/raw-assets?limit=20,
    datasets=/api/v1/projects/{project_id}/datasets?limit=20,
    collection_tasks=/api/v1/projects/{project_id}/collection-tasks?limit=20' \
  --concurrency 8 --requests 300 --duration 120

# 3) 正式基线（显式 --execute）
DDP_LOAD_TOKEN=$(cat /tmp/perf-token) backend/.venv/bin/python \
  scripts/perf/readonly_load.py --base-url http://127.0.0.1:18000 \
  --project-id 1bca073d-c9af-4601-9b0f-7888d0747db0 --endpoints '<同上>' \
  --concurrency 8 --requests 300 --duration 120 --execute \
  --json-out docs/testing/evidence/2026-11-26-prod-readonly-perf/readonly-load-raw.json

# 4) 红线拒绝验证（全部退出码 2）
... --concurrency 33 / --requests 2001 / --duration 181
... --base-url https://prod.example.com（默认拒绝非本机）
... --endpoints 'POST /api/v1/projects'（含空白，拒绝）
... --execute（无 token，拒绝）
```

三次运行退出码均为 `0`（完成、0 错误），PG 连接守卫 `aborted=false`。

## 7. 已知边界（诚实声明）

1. **这是单机生产环境的短时只读基线，不等于容量规划结论。** 并发、数据分布、
   后台 worker 负载任一变化都会显著改变数字；跨机器、跨时间不可直接比较。
2. **数据量小**：全库仅 206 episodes / 283 raw_assets / 98 collection_tasks；
   列表延迟以固定开销为主，不代表大数据量下的表现。
3. **API 实例为本次新建的单 worker 进程**（非 `ddp-api` 容器；容器保持 Exited）。
   与生产代码同 commit，连同一生产库/MinIO；未改生产配置。
4. **压测期间后台 worker 容器在运行**（load avg 1m≈3.09），存在环境噪声。
5. **端点逐段串行压测**，整体 RPS 是“各阶段平均”，不是系统并发上限。
6. 只覆盖 7 个 GET 端点，未覆盖全部只读端点（全量 5xx 巡检另有
   `scripts/qa/readonly_endpoint_smoke.py`）。
7. 单次仅 5.9–6.7s，**未测长稳、内存增长、连接泄漏或限流行为**（GET 不受限流）。
8. 无 staging 对照（用户明确要求全部在 prod）。
9. 修复 N+1 后的收益（38.9→10–15ms 等）为**基于梯度的外推估算**，`[未验证]`。
10. 未验证鉴权失败/越权路径的延迟；token 使用 owner 身份。

## 8. 门禁结果

`scripts/check.sh` 持锁串行跑一次（日志 `/tmp/check-perf.log`），待填实跑结果。
本任务改动仅 `scripts/perf/**`、`backend/tests/unit/test_perf_readonly_core.py`、
`docs/testing/evidence/**`，不涉及业务代码/迁移/契约。

## 9. 变更文件

| 文件 | 说明 |
|---|---|
| `scripts/perf/readonly_load.py` | 新增（475 行）：生产只读压测执行环、守卫、报告；函数均 ≤50 行 |
| `scripts/perf/core.py` | 扩展：新增只读纯函数层（端点解析/占位符/红线/脱敏/目标校验/分端点汇总）+ 1 个回归修复（查询串 `=` 误判） |
| `scripts/perf/README.md` | 新增：用法、造 token、红线、退出码、结果解读 |
| `backend/tests/unit/test_perf_readonly_core.py` | 新增：12 个纯函数单测（红线边界、写方法不可表达、脱敏、统计口径） |
| `docs/testing/evidence/2026-11-26-prod-readonly-perf/*` | 本报告 + 3 份原始 JSON |
