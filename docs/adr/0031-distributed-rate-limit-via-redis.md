# ADR-0031 分布式限流：引入 Redis 后端

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：ADR-0001、ADR-0006、ADR-0014；`CLAUDE.md` §二/§三/§五；
  `docs/05-现状审计报告-2026-11-14.md` §6.1 R-7、§7「分布式限流」
- 影响范围：`backend/app/infrastructure/rate_limit.py`、
  `backend/app/api/middleware/rate_limit.py`、`backend/app/core/config.py`、
  `backend/app/main.py`、`backend/requirements.txt`、`docker-compose.yml`
- 不涉及：数据库结构（无迁移）、契约、权限模型

## 1. 背景与问题

审计 §7 把「分布式限流」标为 **BLOCKED**：`rate_limit_backend` 的唯一合法值是
`"memory"`，实现是进程内滑动窗口（`InMemoryRateLimiter`）。多副本部署时每个
进程各持一份计数，**N 个副本等于 N 倍配额**，全局限流形同不存在。

R-7 的处理是「先移除 redis 依赖与 compose 容器，实现时再重新引入并做依赖评估」。
本次即该实现，按 `CLAUDE.md` §三 补齐依赖评估。

## 2. 现有抽象（复用以外的探查结论）

- 协议已存在：`app.infrastructure.rate_limit.RateLimiter`（`typing.Protocol`），
  只有 `check(key, *, limit, window_seconds) -> RateLimitDecision` 一个方法。
- `RateLimitDecision` 已含 `allowed/limit/remaining/retry_after_seconds`，
  中间件已据此填写 `Retry-After` / `X-RateLimit-Limit` / `X-RateLimit-Remaining`。
- 中间件按「认证桶 / 写桶 × 客户端 IP」组键，`X-Forwarded-For` 默认不信任。

结论：**扩展现有协议，不新造抽象**。新增 `RedisRateLimiter` 实现同一 Protocol，
`build_rate_limiter` 按枚举分派。协议补一个 `close()`（释放连接池），
`InMemoryRateLimiter` 提供对应空实现以保持契约统一。

## 3. 决策

1. 直接依赖官方 SDK `redis`，用**单条 Lua 脚本**实现精确滑动窗口。
2. `memory` 保持默认；`redis` 为显式可选后端。
3. Redis 不可用时 **fail-open**（放行 + error 日志 + `degraded` 标记）。
4. 连接超时与重试策略显式收紧，保证限流最坏耗时有界。
5. `docker-compose.yml` 恢复 `redis` 服务，镜像固定 tag + digest。

## 4. 依赖评估（`CLAUDE.md` §三）

| 项 | 结论 |
|---|---|
| **用途** | 分布式限流计数存储：跨副本共享同一份滑动窗口计数 |
| **版本固定** | `redis==8.1.0`（`backend/requirements.txt` 精确固定，非 `latest`）。PyPI 当前稳定版；8.2.0b1 为 beta，不采用 |
| **替代方案 A：`limits` + redis storage** | **实测否决**，见下方「为什么不用 limits」 |
| **替代方案 B：手写 `INCR`+`EXPIRE`** | 固定窗口而非滑动窗口，边界处可放行 2× 配额；且 `INCR` 与 `EXPIRE` 两次往返存在原子性缺口（`INCR` 成功、`EXPIRE` 失败即产生永不过期的键）。若要精确滑动窗口仍需自写 Lua，不省事 |
| **替代方案 C：继续只用 memory** | 多副本无全局配额，即本次要闭环的问题本身 |
| **体积/性能** | wheel 560 KB（纯 Python，无 C 扩展）；可选 `hiredis` 加速未引入。运行期每桶一个 ZSET，键存活一个窗口 |
| **传递依赖** | 无（`async-timeout` 仅 Python < 3.11.3 需要，本项目 3.11.17 不触发；其余均为 extras） |
| **许可证** | MIT（PyPI `license_expression=MIT`） |
| **requires-python** | `>=3.10`，本项目 3.11.17 满足 |
| **安全公告复核** | 查询 OSV（`api.osv.dev`，包 `redis`/PyPI/版本 8.1.0）返回**空结果**，无已知漏洞。历史 `redis-py` 相关 CVE 集中在 4.x 及更早，8.1.0 不在影响范围 |
| **与现有技术栈冲突** | 无。项目其余组件（httpx/Pydantic/SQLAlchemy/Alembic）不依赖 redis；阶段 8 移除后 `grep -rn redis backend/` 只剩说明注释 |

### 为什么不用 `limits`（实测记录）

在隔离 venv 中安装 `limits[redis]==5.8.0` 后实测（连接本机真实 Redis）：

```
limits 5.8.0 / Deprecated 1.3.1 / packaging 26.3 / redis 7.4.1
Requires-Dist: redis!=4.5.2,!=4.5.3,<8.0.0,>3
```

1. **锁死 `redis<8.0.0`**：与「固定到当前稳定版 8.1.0」直接冲突，且把后续
   升级路径交给 limits 的发布节奏。
2. **`hit()` 只返回 `bool`**：拿不到 `remaining` 与 `retry_after`。
   而本项目中间件要写 `X-RateLimit-Remaining` / `Retry-After`（现有 API 测试
   已断言这些响应头）。要补齐必须再调 `get_window_stats()`，**多一次网络往返**，
   反而破坏「单次原子往返」的目标。
3. **时钟取客户端 `time.time()`**（`RedisStorage.acquire_entry` 源码：
   `timestamp = time.time()`）。多副本时钟漂移直接进入判定；而 Redis 服务端
   `TIME` 是天然全局时钟。
4. 附带 3 个非必需依赖（`Deprecated`、`packaging`、`typing-extensions`）。

结论：`limits` 是成熟库，但其抽象面向「布尔命中」，与本项目的
「决策 + 余量 + 重试建议」契约不匹配；引入后仍需自写适配层并接受版本锁定。
**直接用官方 SDK + 一段受测 Lua 更小、更直接、约束更少**，符合 §二
「不手写已知有标准解法的底层协议」——这里没有手写协议，协议仍是 RESP，
只是把一段注册脚本交给官方 SDK 的 `register_script`。

## 5. 原子性与键设计

脚本一次 `EVALSHA` 往返内完成「裁剪过期 → 计数 → 判定 → 记账」四步：

```lua
local server_time = redis.call('TIME')
local now_ms = tonumber(server_time[1]) * 1000 + math.floor(tonumber(server_time[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local used = redis.call('ZCARD', key)
-- used >= limit → 计算 retry 并返回拒绝；否则 ZADD + PEXPIRE 并返回放行
```

要点：

| 关注点 | 做法 |
|---|---|
| 原子性 | 单脚本执行，Redis 单线程语义保证；**不存在 read-then-write 的两往返竞态** |
| 时钟 | Redis 服务端 `TIME`，副本间无漂移 |
| 跨窗口累积 | ZSET 成员为命中时间戳，`ZREMRANGEBYSCORE` 每次裁掉窗口外的；`PEXPIRE` 保证键最多存活一个窗口 |
| 键名 | `{prefix}:{window_seconds}s:{bucket}:{来源}`，如 `ddp:rl:60s:auth:203.0.113.7`。含窗口后不同窗口配置不会共用计数 |
| 成员唯一性 | `uuid4().hex`。ZSET 按成员去重，同毫秒命中若成员相同会被少计 |
| 拒绝时的过期 | 拒绝分支同样 `PEXPIRE`，避免窗口内计数提前清零导致突发放行 |

## 6. fail-open vs fail-closed（显式决策）

**决策：fail-open。**

核实既有行为：改造前 `rate_limit_backend` 只有 `"memory"`，从未存在可运行的
redis 后端，因此文档里「redis 后端显式未实现并 fail-closed」指的是**配置层
拒绝启动**（`Literal["memory"]` 校验 + 启动自检），不是运行时降级策略。
换句话说，本 ADR 是这条语义的首次真实定义。

选择 fail-open 的理由：

1. **限流是保护措施，不是授权措施。** 授权（JWT 校验、项目隔离）保持
   fail-closed，不因本决策改变。
2. **fail-closed 会造成自伤式拒绝服务。** 所有写接口共享限流前置，Redis 抖动
   会直接把整站写操作变成 503/429——一个缓存组件故障升级为业务不可用。
3. **代价对称且可控。** fail-open 的最坏结果是「短时限流失效」，而非
   「服务不可用」。限流失效期间仍受授权与项目域校验保护。
4. **可观测补偿。** 降级不是静默的：基础设施层记 `error` 并附异常堆栈，
   中间件记 `error` 级「降级放行」并带桶名/窗口/额度，`RateLimitDecision.degraded`
   为 `True`，便于接入指标告警。
5. **配置错误仍 fail-closed。** 「启用 redis 却不给 URL / URL 非法」在启动自检
   就拒绝（不是运行期降级），避免"以为开了全局限流"。

已知取舍：Redis 故障期间暴力破解防护减弱。缓解是故障窗口短（超时 0.5s）+
降级日志可告警 + 授权层不依赖 Redis。

## 7. 超时与线程边界（`CLAUDE.md` §五）

实测记录（本机，`socket_timeout=socket_connect_timeout=0.3`，指向关闭的 6390 端口）：

| 配置 | 首次 `PING` 失败耗时 |
|---|---|
| redis-py **默认** retry | **4.46 s** |
| `Retry(NoBackoff(), 0)` | **0.001 s** |

默认重试策略会把一次连接失败放大到数秒——如果限流在请求路径上同步执行，
这就是**每个请求挂住数秒**。因此：

- `retry=Retry(NoBackoff(), 0)`：禁用重试，失败立即上抛；
- `socket_timeout` / `socket_connect_timeout` 默认 0.5 s（配置项可调，上限 5 s，
  `gt=0` 强制非零）；
- `max_connections` 默认 32，避免连接池成为瓶颈；
- 中间件用 `run_in_threadpool` 调用同步 `check()`，**不阻塞事件循环**
  （asyncio 版本虽可用，但协议是同步的，改协议会波及 memory 实现与现有测试；
  线程池是 Starlette 的既有做法，复用现有能力）。

单次最坏耗时上界 ≈ `socket_connect_timeout`（0.5 s）+ `socket_timeout`（0.5 s）。

## 8. 配置与可观测性

新增配置（`DDP_` 前缀）：

| 配置 | 默认 | 说明 |
|---|---|---|
| `DDP_RATE_LIMIT_BACKEND` | `memory` | `memory` \| `redis` |
| `DDP_RATE_LIMIT_REDIS_URL` | `""` | `redis://` 或 `rediss://`；`redis` 后端必填 |
| `DDP_RATE_LIMIT_REDIS_SOCKET_TIMEOUT_SECONDS` | `0.5` | 单次往返超时，`(0, 5]` |
| `DDP_RATE_LIMIT_REDIS_CONNECT_TIMEOUT_SECONDS` | `0.5` | 连接超时，`(0, 5]` |
| `DDP_RATE_LIMIT_REDIS_MAX_CONNECTIONS` | `32` | 连接池上限 |
| `DDP_RATE_LIMIT_REDIS_KEY_PREFIX` | `ddp:rl` | 键前缀 |

启动自检（`validate_runtime_security` → `_rate_limit_config_problems`）：

- `backend=redis` 且 URL 为空 → 拒绝启动；
- URL 含首尾空白、protoco 非 `redis/rediss`、缺主机名、端口非法或越界 → 拒绝启动；
- `backend=memory` 时 URL 被忽略（**不报错**）：切回 memory 是回滚路径，
  env 里残留 URL 不得阻止重启。

日志（`logging.getLogger("ddp.rate_limit")`，复用现有 JSON formatter 与脱敏器）：

- 命中：`warning`，含 `bucket/backend/window_seconds/limit/remaining/
  retry_after_seconds/method/path/client`；
- 放行：`debug`（避免正常流量刷爆日志）；
- 降级：`error`，含「fail-open」；
- 来源标识：`sha256(...)[:16]` 摘要（`client_fingerprint`）。同一来源可追踪，
  但**无法从日志反推 IP**，且与内存键哈希同源。

## 9. 容器镜像固定

`docker-compose.yml` 恢复 `redis` 服务，参照 MinIO 的写法固定
**tag + 多架构 OCI index digest**：

```
image: redis:7.4.12-alpine@sha256:7f7d30b7fdd09467bac58198a1f166dd69789d348d4fe859cccb20e0cf287fbe
```

- `7.4.12` 为本地已验证运行版本（`redis_version:7.4.12`），digest 经
  `docker buildx imagetools inspect` 核对为单个 index digest（非某一架构的
  manifest digest），amd64/arm64 均可拉取。
- 不挂持久卷、关闭 RDB/AOF（`--save "" --appendonly no`）：计数可在重启后
  重建，没必要写盘。
- `--maxmemory 256mb --maxmemory-policy allkeys-lru`：内存吃紧时淘汰旧键，
  表现为配额提前恢复（宽松），不会误拒；**不用 `noeviction`**，否则限流写失败。
- `api` 服务注入 `DDP_RATE_LIMIT_REDIS_URL: redis://redis:6379/0`
  （容器内必须用服务名）+ `depends_on: redis (service_healthy)`。

## 10. 测试

| 层 | 文件 | 覆盖 |
|---|---|---|
| 单元 | `tests/unit/test_rate_limit.py` | memory 行为回归（放行/拒绝/窗口滚动/键隔离/桶上限/非法策略）、`degraded` 不得误置、`close()`、配置校验（缺 URL、非法 URL 参数化、`rediss`、memory+URL 回滚）、超时上界、后端分派 |
| 集成（真实 Redis） | `tests/infrastructure/test_rate_limit_redis.py` | 配额内放行、超限拒绝 + retry_after、窗口滚动恢复、**两个独立实例共享配额**、**8 线程并发不超发**（原子性）、键隔离（来源/窗口）、键 TTL、键名含窗口与桶、不可达 fail-open、**不可达耗时有界** |
| 接口 | `tests/api/test_rate_limit_middleware.py` | 原有 8 条用例全部保留；新增命中日志（含字段、不含原始 IP）、放行 debug 日志、`client_fingerprint` 稳定性、shutdown 释放、redis 后端端到端 429、**跨 app 实例共享配额**、不可达 fail-open |

Redis 集成测试由 `DDP_TEST_REDIS_URL` 控制；未设置时整模块 skip 并标注 BLOCKED
（不做 mock 替代——原子性、跨客户端共享、键过期无法用假客户端可靠验证）。

已做反向验证：同一「跨 app 实例」用例在 `memory` 后端下两个实例各放行一次
（共 2 次 200），在 `redis` 后端下只放行一次（第 2 次 429），证明该用例
确实区分「进程内」与「全局」。

## 11. 影响与回滚

- 对外行为：`memory` 默认路径行为不变（现有 8 条中间件用例与 8 条单测全绿）。
  新增可选后端，不改既有 API 形状、错误体、响应头。
- 数据库：**无变更、无迁移**。
- 配置：新增 6 项，均有默认值；`.env.example` 已补说明。
- 回滚：把 `DDP_RATE_LIMIT_BACKEND` 改回 `memory`（或删除该变量）即可，
  URL 可留在 env 中不影响启动；`redis` 服务可保留（闲置）或从 compose 移除。
  无数据迁移、无不可逆状态。

## 12. 未做的事

- 不做按主体（用户/令牌）限流：协议已留口子，但需等认证完成后由应用层依赖接入。
- 不做 Redis Sentinel / Cluster 拓扑：单实例足够当前规模；切换需另立 ADR。
- 不引入 `hiredis`：当前 QPS 下纯 Python 解析不是瓶颈，避免多一个 C 扩展依赖。
- 不把限流计数做成 Prometheus 指标：指标/追踪是独立未决项（审计 P2-⑭），
  本 ADR 只保证日志可观测与 `degraded` 信号可用。
