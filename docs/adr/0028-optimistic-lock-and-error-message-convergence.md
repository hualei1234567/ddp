# ADR-0028 乐观锁判定收敛与错误文案不回显（R-8 / R-12）

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：审计 R-8、R-12；`CLAUDE.md`《复用优先》第五节（禁止复制业务规则）
- 影响范围：`app/infrastructure/repositories/_optimistic_lock.py`（新增）、
  四个 `_*_sql.py` 包装、`app/api/errors.py`

## 1. R-8：`ensure_expected_version` 四份实现

### 1.1 审计结论复核

审计写的是「4 份实现且行为不一致，数据集域允许 `{expected, expected+1}`」。
复核后确认这是**入参约定不同**，不是单纯的写法重复：

| 域 | 调用方传给仓储的实体 | 正确判定 |
|---|---|---|
| collection / annotation / devices | 加载时的版本（未自增） | `entity_version == expected` |
| datasets | **已自增的候选版本** | `entity_version in {expected, expected+1}` |

数据集域为什么会传自增后的版本：`update_dataset()` 与 `version_actions`
在构造候选实体时就写 `row_version=current.row_version + 1`，
仓储拿到的是这个自增后的对象。其他域则传未自增的实体。

**如果直接合并成严格相等，数据集域所有写操作都会 409**；
**如果统一放宽成差一容忍，其余三域会放过"版本未刷新"的调用**。
两种"简化"都是错的。

### 1.2 真正的收敛方式

把判定逻辑收成一份（`_optimistic_lock.ensure_expected_version`），
把**入参约定显式化**为 `accept_candidate` 参数：

```python
ensure_expected_version(
    entity_version, expected_version,
    invalid=..., conflict=..., accept_candidate=True,  # 仅 datasets
)
```

好处：

- 只有一处 `if` 判断，不再有四份靠肉眼对比的实现；
- 差异变成显式参数，新增域必须主动选一种，不能"顺手复制一份"；
- `tests/unit/test_optimistic_lock.py` 有一条用例用 `inspect.getsource`
  断言四个包装函数里不再出现 `entity_version ==` / `entity_version in`——
  谁把判定写回去，CI 立刻失败。

### 1.3 安全性说明

本函数只是**前置校验**。真正防并发的是 SQL 的
`WHERE version = expected_version` 与未命中时的 `raise_update_failure`
（它重新读行并按严格相等判定冲突）。所以两种约定都不会放行并发写，
差别只在"何时报错"。

审计 §6.3 已复核过"差一容忍会放行并发写"的怀疑**不成立**，本次结论与之相同。

### 1.4 一个险些引入的缺陷

第一版实现里 `conflict=VersionConflictError`（直接传类）。
集合域与标注域的 `VersionConflictError.__init__(self, resource)` 需要参数，
于是运行期 `TypeError`。全量测试抓到（`test_task_optimistic_lock_and_legal_transition`）。

修复后补了一条用例 `test_domain_wrappers_raise_real_conflict_errors`：
**真实调用四个包装函数**，而不是只测传假工厂的路径——
只测后者的话，"工厂签名不对"永远测不到。

## 2. R-12：错误文案回显内部标识

### 2.1 现状

`app/api/errors.py` 里各域的策略不一致：

| 域 | 缺文案表项时 |
|---|---|
| collection / annotation / dataset | 固定兜底文案 |
| **modelops** | `str(exc)` 回显 |
| **supplier** | `str(exc)` 回显 |

领域异常的 `str()` 通常含实体名、字段名、状态枚举值，有时含客户端输入。
原样回显既暴露内部结构，也与其余三域策略冲突。

### 2.2 决策

两个域都改为**固定文案表 + 固定兜底**，与其余三域一致：

- 新增 `_MODELOPS_MESSAGES`（18 条），覆盖 `_MODELOPS_STATUS_BY_CODE` 的全部 code；
- supplier 去掉 `str(exc)` 兜底，未登记 code 用 `_SUPPLIER_FALLBACK`；
- 领域异常原文仍进日志（中间件记录），只是不出现在响应体。

固定文案不等于"千篇一律"：`test_known_codes_keep_their_specific_message`
断言已知 code 的文案与未知 code 的兜底不同，避免把所有 code 都塌成一个字符串。

## 3. 顺带清掉的 R-7 残留

`redis` 依赖在 batch 0 已从 `requirements.txt` 移除、代码零 import，
但 `docker-compose.yml` 仍在启动 `redis:7-alpine` 容器、向三个服务注入
`DDP_REDIS_URL`、并保留 `ddp-redisdata` 卷。

保留一个零使用的容器不是"以后可能用得上"，而是：
占端口、占内存、扩大攻击面，并让人误以为系统依赖 Redis。
`rate_limit_backend` 的唯一合法值是 `memory`（见 `app/core/config.py`），
多副本全局配额确实还没做——但那属于**未实现**，不该用空转容器表达。

清理后 `grep -c redis docker-compose.yml` 只剩一条说明注释。

## 4. 验收

| 项 | 结果 |
|---|---|
| 乐观锁单元测试 | 15 passed（含严格/候选两种约定、非正数、包装函数真实调用） |
| 判定实现份数 | 4 个包装函数里 `if entity_version` 出现次数：**0** |
| 错误文案测试 | 6 passed（敏感串、内部表名不出现在 message；已知 code 文案仍具体） |
| compose redis 残留 | `grep -c redis docker-compose.yml` → 1（仅说明注释）；`docker compose config` 有效 |
| 真实 PostgreSQL 全量 | 见交付汇报 |
| `make check` | 通过 |
