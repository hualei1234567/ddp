# DDP 性能基线脚本

| 脚本 | 目标环境 | 是否造数 | 用途 |
|---|---|---|---|
| `baseline.py` | staging / 本机临时库（`ddp_perf_*`） | 是（自建库 + 种子） | 不同数据规模下的列表分页与视频 Range 基线 |
| `readonly_load.py` | **生产**（本机 docker compose） | 否，**只发 GET** | G10 生产只读压测：给真实生产 API 的只读端点出短时基线 |

两者共用 `perf/core.py`（统计口径、DSN 脱敏、端解析、红线校验）与 `perf/report.py`
（硬件 / Git / PG 版本 / 负载元数据）。`core.py` 里声明的上限是**代码常量**而不是
CLI 参数：放宽必须改代码并走评审。

> **`readonly_load.py` 的结论只用于回归对比，不是容量规划。** 单机、单 API 进程、
> 小数据量、后台 worker 同时运行，任何一个条件变化，数字都会变。

## 1. 前置

```bash
# 依赖容器（PG 55432 / MinIO 9000）已在跑
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'ddp-postgres|ddp-minio'

# 后端依赖（httpx / psycopg / SQLAlchemy 走 backend/.venv）
backend/.venv/bin/python --version
```

## 2. 让被测 API 起来

优先用已在跑的生产 API。**注意本仓库的 compose 里 API 是 `profiles: ["app"]`，
不常驻**；`ddp-api` 容器可能处于 `Exited`。找不到监听端口时，按任务约定自起一个
**只连生产库**的单 worker 实例（不改生产配置、不写数据）：

```bash
# 从仓库根的 .env 读 JWT 密钥与 S3 凭据；DB 指向生产 PG
set -a; . /path/to/DataPlatform/.env; set +a
export DDP_DATABASE_URL='postgresql+psycopg://ddp:ddp@localhost:55432/ddp'
export DDP_S3_ENDPOINT='http://127.0.0.1:9000'
export PYTHONPATH='<repo>/data-platform'   # ddp_data 顶层包
cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 18000 --workers 1

curl -s -o /dev/null -w '%{http_code}\n' localhost:18000/health   # 期望 200
```

健康路径是 `/health`（不是 `/healthz`）。只读压测不要开 `--reload`、不要多 worker，
否则并发/抖动不可解释。

## 3. 造 token

Token 需要是**真实项目成员**的 JWT（否则列表端点返回 403/404，错误率 100%）。
用生产 `.env` 里的 `DDP_JWT_SECRET` 按 API 同一口径（HS256）签发，TTL 建议 ≤2h：

```bash
# 1) 取一个有数据的项目 + 该项目成员
export PGPASSWORD=ddp
PROJECT_ID=$(psql -h 127.0.0.1 -p 55432 -U ddp -d ddp -tAc \
  "select p.id from projects p join episodes e on e.project_id=p.id
   group by p.id order by count(*) desc limit 1")
USER_ID=$(psql -h 127.0.0.1 -p 55432 -U ddp -d ddp -tAc \
  "select user_id from project_members where project_id='$PROJECT_ID' limit 1")

# 2) 签发（读 .env，不把密钥写进命令行历史）
export DDP_LOAD_TOKEN=$(backend/.venv/bin/python - "$USER_ID" <<'PY'
import datetime, pathlib, sys, jwt
env = {}
for line in pathlib.Path("<repo>/.env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
now = datetime.datetime.now(datetime.timezone.utc)
print(jwt.encode({
    "sub": sys.argv[1], "iss": env.get("DDP_JWT_ISSUER", "ddp"),
    "aud": env.get("DDP_JWT_AUDIENCE", "ddp-api"),
    "iat": int(now.timestamp()),
    "exp": int((now + datetime.timedelta(hours=2)).timestamp()),
    "platform_role": "member",
}, env["DDP_JWT_SECRET"], algorithm="HS256"))
PY
)
```

安全要求：token 只放环境变量（`--token` 会出现在 `ps` 里）；**不要提交进仓库**；
报告与日志里不得出现 token 或 `DDP_JWT_SECRET`。

## 4. 跑压测（默认 dry-run）

```bash
# 默认 dry-run：只打印计划、做连接数预检，不发任何请求
backend/.venv/bin/python scripts/perf/readonly_load.py \
  --base-url http://127.0.0.1:18000 \
  --project-id "$PROJECT_ID" \
  --endpoints 'health=/health,
    projects=/api/v1/projects?limit=20,
    project_detail=/api/v1/projects/{project_id},
    episodes=/api/v1/projects/{project_id}/episodes?limit=20,
    raw_assets=/api/v1/projects/{project_id}/raw-assets?limit=20,
    datasets=/api/v1/projects/{project_id}/datasets?limit=20,
    collection_tasks=/api/v1/projects/{project_id}/collection-tasks?limit=20' \
  --concurrency 8 --requests 300 --duration 120

# 真正压测：必须显式 --execute
#   （追加 --json-out 落原始 JSON，报告要引用）
... --execute --json-out docs/testing/evidence/<date>/readonly-load-raw.json
```

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--base-url` | `http://127.0.0.1:8000` | 生产 API 地址；默认只允许 loopback |
| `--token` | env `DDP_LOAD_TOKEN` | JWT；推荐用环境变量 |
| `--endpoints` | 必填 | 逗号分隔；`name=path` 命名，`{project_id}` 会按 `--project-id` 替换 |
| `--project-id` | — | 端点含 `{project_id}` 时必填 |
| `--concurrency` | 4 | 上限 **32**，超限拒绝执行 |
| `--requests` | 200 | **每端点**请求数，上限 **2000** |
| `--duration` | 0 | 总时长预算秒（0=只按请求数），**硬上限 180** |
| `--timeout` | 10 | 单请求超时秒 |
| `--pg-dsn` | env `DDP_PERF_PG_DSN` 或生产 DSN | 连接数守卫用；报告里自动脱敏 |
| `--health-path` | `/health` | 执行前健康检查 |
| `--json-out` | — | 原始 JSON 落盘路径 |
| `--execute` | 关 | 显式确认；不加就是 dry-run |
| `--dry-run` | 开 | 显式 dry-run；与 `--execute` 同时给时按 dry-run 处理 |
| `--allow-non-loopback` | 关 | 允许非本机目标；压远程才加 |

退出码：`0` 完成且无错误；`1` 完成但有非 2xx/网络错误；`2` 参数/前置条件不满足
（含红线超限、守卫不可用）；`3` 被连接数守卫中止。

## 5. 红线（硬编码，必须遵守）

1. **只发 GET**：脚本内没有写方法；端点解析拒绝含空白的描述，写方法无法被表达。
2. **并发 ≤ 32、每端点 ≤ 2000、总时长 ≤ 180s**：超限直接拒绝（不自动降级）。
3. **默认 dry-run**：只有 `--execute` 才发请求。
4. **连接数守卫**：压测期间每秒查一次 `pg_stat_activity`，客户端后端数超过
   `max_connections` 的 **80%** 立即中止全部 worker；守卫连续 3 次查询失败也中止
   （无法确认安全时按不安全处理）。执行前预检已超阈值则直接拒绝开始。
5. **默认只压本机**：防误压其他环境；`--allow-non-loopback` 需人工确认。

## 6. 怎么解读结果

- `p50/p95/p99`：最近秩百分位（与 `baseline.py` 同口径）；小样本时 p99 跳动大。
- `RPS`：**该端点阶段**的吞吐（`请求数 / 阶段墙钟`）；整体 RPS = 总请求 / 总墙钟。
  端点按顺序逐段压测，所以整体 RPS 不是系统并发上限，不要当容量结论。
- `错误率`：非 2xx 与网络异常都计错误；先看 `status_counts` 与 `sample_errors`
  区分 401/403（token/权限）、404（数据）与 5xx（服务端）。
- `truncated_by_time`：该端点被时长预算截断，样本数少于计划值。
- `connection_guard`：报告记录起点/峰值/终点连接占用与是否中止；峰值应远低于 80%。
- 对比必须**同机、同参数、同数据量**；至少重复一次取中位数。两次运行 p95 差 20%
  属于本机正常抖动（后台 worker、GC、调度都会影响）。

## 7. 常见失败

| 现象 | 原因 | 处理 |
|---|---|---|
| `拒绝执行：连接数守卫不可用` | PG DSN/密码不对，或 PG 不可达 | 检查 `--pg-dsn`；生产默认 55432 |
| `拒绝执行：健康检查返回 HTTP 404` | 健康路径不对 | 用 `--health-path /health` |
| 错误率 100%、`401` | token 过期/密钥口径不一致 | 重新签 token，确认 iss/aud |
| 错误率 100%、`403`/`404` | 用户不是项目成员 / 项目无数据 | 换 `--project-id` 与成员用户 |
| `拒绝压测非本机目标` | base-url 指向远端 | 确认目标后加 `--allow-non-loopback` |
| 被守卫中止（退出码 3） | 生产连接占用 >80% | 立即停止，排查长事务/泄漏 |

## 8. 证据与测试

- 首次生产基线：`docs/testing/evidence/2026-11-26-prod-readonly-perf/README.md`
  （含原始 JSON、两次重复运行与 N+1 梯度实验）。
- 纯函数层单测：`backend/tests/unit/test_perf_readonly_core.py`
  （端点解析/红线/脱敏/汇总）。跑法：
  `cd backend && .venv/bin/python -m pytest tests/unit/test_perf_readonly_core.py -q`

---

## 9. 写路径端到端压测（`write_path_e2e.py`）

`readonly_load.py` 测的是**读**路径；`write_path_probe.py` 测的是**事务骨架**
（直接 INSERT 审计行 + outbox 行，绕过了 HTTP、鉴权与业务校验）。两者之间
还有一段空白：**真实写接口**（如标注提交）的端到端容量——这一段由本脚本补上。

### 与 `write_path_probe.py` 的差别

| 维度 | `write_path_probe.py` | `write_path_e2e.py` |
|---|---|---|
| 写入方式 | 直接 `INSERT`（SQL） | 真实 HTTP `POST /api/v1/.../annotation-submissions` |
| 鉴权 | 无 | 真实 HS256 Bearer JWT（复用 `tests/jwt_support.make_token`） |
| 业务校验 | 无 | 走完整校验链（Schema / 分配 / 状态机） |
| 附带写入 | 仅审计 + outbox | 审计 + outbox + 血缘 + 进度投影 + 幂等记录 |
| 前置数据 | 项目 + 用户 | 完整：项目 / 用户 / Episode / Schema / 样本集 / 任务 / 批次 / 分配 / 标注员 |
| 隔离方式 | 一次性临时库 | 一次性临时库（`ddp_perf_e2e_` 前缀） |

### 用法

```bash
# 先看计划（默认 dry-run：不建库、不发请求）
backend/.venv/bin/python scripts/perf/write_path_e2e.py \
  --dsn postgresql+psycopg://ddp:ddp@localhost:55432/postgres

# 真实执行
backend/.venv/bin/python scripts/perf/write_path_e2e.py \
  --dsn postgresql+psycopg://ddp:ddp@localhost:55432/postgres \
  --execute --workers 1,4,16 --iterations 10 \
  --out docs/testing/evidence/2026-11-27-write-path-e2e/result.json
```

### 安全护栏（硬编码，不可绕过）

1. 只允许本机回环 PG（`ensure_safe_base_dsn`）；
2. 临时库名必须带 `ddp_perf_e2e_` 前缀（`ensure_safe_perf_db_name`），
   用完 `DROP DATABASE`；
3. 默认 dry-run，必须显式 `--execute`；
4. 不写主库——所有数据只落在临时库。

### 关键口径

- **每次迭代 = 2 次真实写请求**（建草稿 201 + 提交 200），因此 `requests_sent`
  是请求数、TPS 是请求吞吐；
- **工作位在压测前造好**：领域规则不允许同一分配反复建草稿，所以每个
  「工作位」只够 1 次建草稿 + 1 次提交；
- **限流在脚本内关闭**：否则写请求会被 IP 桶挡掉，测到的不是业务写容量；
- **连接池参数取生产默认口径**（`pool_size=10` / `max_overflow=20`），
  压测期间**不放宽**——放宽会把「打满」这个结论掩盖掉；
- **连接峰值是 50ms 间隔的瞬时采样最大值**，尖峰可能被漏掉（README 里已注明）。

### 实测结论（2026-11-27，生产 PG）

| 并发 | 请求数 | TPS | p50 | p95 | p99 | 错误率 |
|---|---|---|---|---|---|---|
| 1 | 20 | 15.97 | 42.9ms | 104.3ms | 132.0ms | 0 |
| 4 | 80 | 26.46 | 148.1ms | 256.0ms | 281.5ms | 0 |
| 16 | 220 | 24.75 | 551.1ms | 1036.7ms | 1366.7ms | 0 |

连接池红线：**安全**（峰值 17 条连接 / PG 上限 100，占用 26%，余量 74）。

完整证据见 `docs/testing/evidence/2026-11-27-write-path-e2e/README.md`。

### 测试

`scripts/tests/test_write_path_e2e.py`（39 项）覆盖：dry-run 不建库、
临时库名校验、非 loopback DSN 被拒、汇总口径、连接池红线判定。

```bash
PYTHONPATH="backend:data-platform" backend/.venv/bin/python \
  -m pytest scripts/tests/test_write_path_e2e.py -q
```
