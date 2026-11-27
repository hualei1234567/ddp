"""大表增长/体积只读审计（审计 R?：大表分区/归档仅评估未实施）。

## 为什么需要这个脚本

审计里有一条「大表分区/归档仅评估未实施（生产数据量百级）」。这句话有两种
可能的读法：

1. 「迟早要做」——先写个方案占位，等数据涨上来再实现；
2. 「现在到底需不需要，要用事实回答」——本脚本走第二条路。

**本脚本不实施分区，也不建议现在实施。** 它只回答一个可验证的问题：
**当前每张表多大、按什么速率增长、按什么阈值该启动分区/归档评估。**
如果结论是"现在不需要"，那就写"暂不需要"，并把触发阈值钉死成数字，
让未来有据可依，而不是靠感觉。

## 三条防误判硬规则（不可违反）

本脚本的价值完全取决于它**敢不敢说"数据不可信"**。三条硬规则：

### 规则一：`n_live_tup` 不可作为行数依据 —— 必须用精确 COUNT

`pg_stat_user_tables.n_live_tup` 是**估算值**，且在两种情况下会严重偏小：

1. **PG 容器重启后统计归零**：PG 的统计信息存在共享内存里，容器重启即清零，
   而表数据在磁盘上活着。本项目实测就踩到了：`outbox_events` 的
   `n_live_tup=35`，而真实行数是 `12518`——**偏差 358 倍**。若据此判定
   "表很小，无需分区"，结论完全错误。
2. **表从未被 ANALYZE/VACUUM 过**：新表 + 未 autovacuum 的表 `n_live_tup=0`。

因此本脚本对每张表执行**精确 `COUNT(*)`**（只读 SELECT），并把
`n_live_tup` 仅作为「统计新鲜度」旁证输出。精确 COUNT 的代价是对表做全表
扫描——本项目单库 102 张表、最大表 1.2 万行，全量扫描是毫秒级；表长到亿级
时再考虑采样，届时 `--exact-counts` 应可关闭（届时必须同时标注估算口径）。

### 规则二：统计新鲜度不足时，不得给增长率结论

`pg_stat_database.stats_reset` 为空不代表统计新鲜——**容器重启也会清零
统计，但不会写 `stats_reset`**。本脚本用「精确行数 vs `n_live_tup` 的偏差」
反推统计是否可信：偏差超过 `--stats-drift-ratio`（默认 10 倍）即判定
**统计不可信**，该表的膨胀率与 `n_live_tup` 一律标注为不可信，
增长率改用**表内时间戳列的按日分布**（若该表有可靠时间列）。

### 规则三：只有"有机增长"能用于外推，突发流量不能

实测发现 `outbox_events` 的日增量呈强烈双峰：11-17 写了 7968 行、11-18 写了
3375 行，而其余 7 天合计仅 318 行。这些尖峰来自**压测/演练**而非业务流量。
把尖峰当基线外推，会得出"30 天内到 100 万行"的荒谬结论。

因此脚本对每张表同时输出**两套口径**：
- `daily_all`：含尖峰（观测总量）
- `daily_organic`：剔除超过 `p95` 日增量的尖峰日（用于外推的基线）

判定与阈值推导**只用 `daily_organic`**，并在报告里同时给出两套数字，
让人看得见尖峰的存在与被剔除的事实。

## 用法

```bash
# 默认 dry-run：只打印将要执行的查询与判定计划，不连库
python scripts/db/table_growth_audit.py --dsn postgresql+psycopg://ddp:ddp@localhost:55432/ddp

# 真实执行（只读 SELECT）
python scripts/db/table_growth_audit.py --dsn ... --execute --format json --out /tmp/growth.json
```

**只读保证（硬约束）**：

- 本文件内**所有** SQL 均为 `SELECT`，且统一通过 `text("SELECT ...")` 包装。
  脚本内置 `assert_select_only()`，在 `--execute` 前**逐条断言**语句以
  `SELECT` / `WITH` 开头且不含任何 DDL/DML 关键字——写错一条就直接崩，
  不会带着写语句连上生产库。
- 连接使用 `create_engine(dsn)` 且连接串不含写权限需求；脚本不执行任何
  `commit()` / `rollback()` 之外的写操作，也不调用 `pg_dump`。
- **目标护栏**：默认只允许连**本机 loopback**。非 loopback 目标必须显式
  `--allow-non-loopback`，且该开关**只放开"连上去读"，绝不会放开写**——
  写语句在本脚本里根本不存在（规则一）。这是与 `scripts/perf/core.py`
  中 `ensure_safe_base_dsn()`（白名单库名）的区别：审计脚本只读，
  连错库的代价仅是"读到不该读的数据"，不是"改坏数据"。

## 阈值判定

阈值不是拍脑袋，是**从实测增长率 × 保留期外推**出来的：

```
预期行数 = 日增基线(organic) × 保留天数
分区触发 = 预期行数 ≥ 分区触发行数 或 单表体积 ≥ 分区触发体积
```

默认阈值见 `PARTITION_TRIGGER_ROWS` / `PARTITION_TRIGGER_BYTES`，
依据与推导过程见 `docs/ops/table-growth-and-partitioning.md`。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.perf.core import mask_dsn, parse_pg_dsn  # noqa: E402

# ---------------------------------------------------------------------------
# 阈值常量（代码常量，不是 CLI 参数：放宽必须改代码走评审）
# ---------------------------------------------------------------------------

#: 单表行数达到该值时，**启动分区评估**（不是"立即分区"）。
#: 依据见 ``docs/ops/table-growth-and-partitioning.md``：与备份子进程超时
#: （``maintenance_poll_interval_seconds * 30``，默认 1800s）以及维护 worker
#: 单条 DELETE 的可承受规模共同推导。
PARTITION_TRIGGER_ROWS = 50_000_000
#: 单表（含索引）总体积达到该值时启动分区评估。
PARTITION_TRIGGER_BYTES = 50 * 1024**3  # 50 GiB
#: 归档触发：表内可归档数据超过该值时启动归档评估。
#: 依据：``maintenance.py`` 的清扫是**单条 DELETE**（无分批），
#: 千万行级单语句会制造巨量 WAL 与膨胀，需要改为分区整体摘除或分批归档。
ARCHIVE_TRIGGER_ROWS = 10_000_000
#: 增长预警：按当前速率，保留期（默认 30 天）内的预计行数达到该值时，
#: 即使绝对行数尚未到阈值，也要复查。提前量用于给运维留出决策窗口。
GROWTH_REVIEW_TRIGGER_ROWS = 1_000_000
#: 统计可信度：精确行数与 n_live_tup 超过该倍数即判定统计不可信。
DEFAULT_STATS_DRIFT_RATIO = 10.0
#: 大表候选：行数达到该值即进入"大表候选"清单。
LARGE_TABLE_CANDIDATE_ROWS = 100_000
#: 增长速率外推时默认使用的保留天数（与 outbox_retention_days 默认值一致）。
DEFAULT_RETENTION_DAYS = 30
#: 尖峰识别倍数：日增量超过「中位数 × 该倍数」即判为尖峰日（压测/演练）。
#:
#: 选「中位数 × 倍数」而不是「p95」：本库实测每张表只有 4~10 个有数据的日期，
#: p95 在小样本下几乎等于最大值，等于什么都剔不掉（实测已验证：outbox_events
#: 的 p95 = 7968 = 最大值）。中位数对离群值稳健，×10 是保守阈值——
#: 只剔除 10 倍以上的明显离群，真实业务增长不会被误删。
DEFAULT_BURST_MULTIPLIER = 10.0

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: 禁止出现在任何 SQL 里的写关键字（按**词边界**匹配）。
#:
#: 之所以要用词边界而不是裸子串：`pg_stat_user_tables` 的列名
#: ``last_autovacuum`` / ``last_autoanalyze`` 里含有 "VACUUM" / "ANALYZE"
#: 子串，裸子串匹配会把合法的只读查询误判成写操作——**误报会让护栏失效**，
#: 因为人只会习惯性地忽略一直报错的检查。词边界 ``\bVACUUM\b`` 不会匹配
#: ``last_autovacuum``（"auto" 与 "vacuum" 之间没有词边界）。
_FORBIDDEN_SQL_TOKENS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "VACUUM",
    "ANALYZE",
    "REINDEX",
    "SET",
    "COPY",
    "CALL",
    "DO",
    "LOCK",
    "COMMIT",
    "ROLLBACK",
)


# ---------------------------------------------------------------------------
# 只读断言（规则一的运行期实现）
# ---------------------------------------------------------------------------


def assert_select_only(sql: str, *, where: str) -> str:
    """断言一条 SQL 是纯只读查询；不满足直接抛异常。

    允许的语句前缀：``SELECT`` / ``WITH``（CTE 最终仍是 SELECT）。
    额外做关键字黑名单检查，防止 ``WITH x AS (DELETE ... RETURNING *)`` 这类
    伪装成只读的语句——CTE 里的写操作同样必须被拦住。
    """
    normalized = sql.strip()
    upper = normalized.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        raise ValueError(
            f"{where}：只允许 SELECT/WITH 查询，实际为 {normalized[:60]!r}"
        )
    # 去掉字符串字面量再扫关键字，避免把 'DELETE' 这种枚举值误判为写操作。
    stripped = _strip_string_literals(normalized)
    for token in _FORBIDDEN_SQL_TOKENS:
        # 词边界匹配：``last_autovacuum`` 里的 "VACUUM" 不算写操作，
        # 而真正的 ``VACUUM tbl`` 会被抓住（见 _FORBIDDEN_SQL_TOKENS 注释）。
        if re.search(rf"\b{token}\b", stripped, flags=re.IGNORECASE):
            raise ValueError(
                f"{where}：SQL 含写操作关键字 {token!r}，违反只读约束\n{normalized[:200]}"
            )
    return normalized


def _strip_string_literals(sql: str) -> str:
    """去掉单引号字符串字面量（'' 转义），避免关键字扫描误报枚举值。"""
    out: list[str] = []
    in_string = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if in_string:
            if char == "'":
                if index + 1 < len(sql) and sql[index + 1] == "'":
                    index += 2
                    continue
                in_string = False
        elif char == "'":
            in_string = True
        else:
            out.append(char)
        index += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# 纯函数层：判定、排序、阈值（可直接单测，不连库）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartitionVerdict:
    """单表的分区/归档判定。"""

    table: str
    verdict: str  # no_partition_needed / archive_needed / partition_review / insufficient_data
    reasons: tuple[str, ...]
    rows: int
    total_bytes: int


def classify_partition(
    *,
    table: str,
    rows: int,
    total_bytes: int,
    projected_rows_at_retention: int | None,
    stats_trustworthy: bool,
    trigger_rows: int = PARTITION_TRIGGER_ROWS,
    trigger_bytes: int = PARTITION_TRIGGER_BYTES,
    archive_trigger_rows: int = ARCHIVE_TRIGGER_ROWS,
    growth_review_rows: int = GROWTH_REVIEW_TRIGGER_ROWS,
    large_candidate_rows: int = LARGE_TABLE_CANDIDATE_ROWS,
) -> PartitionVerdict:
    """判定单表现在是否需要分区/归档（纯函数）。

    判定顺序即优先级：

    1. **已过分区阈值**（行数或体积） → ``partition_review``（启动评估）
    2. **按当前速率在保留期内就会过阈值** → ``partition_review``
    3. **已过归档阈值** → ``archive_needed``
    4. **保留期内预计行数已超增长预警线** → ``growth_review``（提前复查）
    5. **规模已接近阈值但增长率不可知** → ``insufficient_data``（需人工复查）
    6. 其余 → ``no_partition_needed``（**暂不需要**，并给出触发阈值）

    关于第 5 条与第 6 条的分界：**「小表」不需要增长数据就能下结论**。
    一张 1044 行的表距离 5000 万行的阈值差 4 万倍，无论它怎么增长，
    今天都不需要分区——把这种表标成 ``insufficient_data`` 是把
    「我不知道增长率」误当成「我不知道答案」，反而淹没真正需要关注的表。
    因此只有**规模已达大表候选线**（默认 10 万行）却算不出增长率时，
    才判 ``insufficient_data``。

    关键点：``no_partition_needed`` **不等于永远不需要**，它附带
    ``trigger_rows`` / ``trigger_bytes`` 作为可执行的触发条件。
    """
    reasons: list[str] = []

    if rows >= trigger_rows:
        reasons.append(f"行数 {rows:,} 已达分区阈值 {trigger_rows:,}")
    if total_bytes >= trigger_bytes:
        reasons.append(f"体积 {total_bytes:,} 字节已达分区阈值 {trigger_bytes:,}")
    if reasons:
        return PartitionVerdict(
            table=table,
            verdict="partition_review",
            reasons=tuple(reasons),
            rows=rows,
            total_bytes=total_bytes,
        )

    if (
        projected_rows_at_retention is not None
        and projected_rows_at_retention >= trigger_rows
    ):
        return PartitionVerdict(
            table=table,
            verdict="partition_review",
            reasons=(
                f"按当前速率，保留期（{projected_rows_at_retention:,} 行）内将触及分区阈值",
            ),
            rows=rows,
            total_bytes=total_bytes,
        )

    if rows >= archive_trigger_rows:
        return PartitionVerdict(
            table=table,
            verdict="archive_needed",
            reasons=(f"行数 {rows:,} 已达归档阈值 {archive_trigger_rows:,}",),
            rows=rows,
            total_bytes=total_bytes,
        )

    if (
        projected_rows_at_retention is not None
        and projected_rows_at_retention >= growth_review_rows
    ):
        return PartitionVerdict(
            table=table,
            verdict="growth_review",
            reasons=(
                f"按当前速率，保留期内预计 {projected_rows_at_retention:,} 行，"
                f"已超增长预警线 {growth_review_rows:,}，需复查",
            ),
            rows=rows,
            total_bytes=total_bytes,
        )

    # 规模已接近阈值却算不出增长率——这才是真正需要人工介入的未知。
    if rows >= large_candidate_rows and projected_rows_at_retention is None:
        return PartitionVerdict(
            table=table,
            verdict="insufficient_data",
            reasons=(
                f"行数 {rows:,} 已达大表候选线 {large_candidate_rows:,}，"
                "但无可用时间列/样本不足，无法给出增长率结论",
            ),
            rows=rows,
            total_bytes=total_bytes,
        )

    reasons.append(
        f"行数 {rows:,} 与体积 {total_bytes:,} 字节均远低于阈值"
        f"（{trigger_rows:,} 行 / {trigger_bytes:,} 字节）"
    )
    if projected_rows_at_retention is not None:
        reasons.append(
            f"按当前速率保留期内预计 {projected_rows_at_retention:,} 行，不触阈"
        )
    else:
        reasons.append("无可用时间列，增长率未知（但规模远低于阈值，不影响当前判定）")
    if not stats_trustworthy:
        reasons.append("PG 统计值与精确行数偏差大，统计不可信（已改用精确 COUNT）")
    return PartitionVerdict(
        table=table,
        verdict="no_partition_needed",
        reasons=tuple(reasons),
        rows=rows,
        total_bytes=total_bytes,
    )


def sort_tables_by_size(tables: list[dict]) -> list[dict]:
    """按「总体积降序 → 行数降序 → 表名升序」排序。

    排序键**必须确定性**：同体积同行数的表按表名排，否则两次运行
    的 JSON 不可 diff，证据文件会每次都变。
    """
    return sorted(
        tables,
        key=lambda item: (
            -int(item["total_bytes"]),
            -int(item["rows"]),
            str(item["table"]),
        ),
    )


def sort_tables_by_rows(tables: list[dict]) -> list[dict]:
    """按「行数降序 → 总体积降序 → 表名升序」排序（同样要求确定性）。"""
    return sorted(
        tables,
        key=lambda item: (
            -int(item["rows"]),
            -int(item["total_bytes"]),
            str(item["table"]),
        ),
    )


def compute_organic_baseline(
    daily_counts: dict[str, int],
    *,
    burst_multiplier: float = DEFAULT_BURST_MULTIPLIER,
) -> dict:
    """从按日行数分布里剔除尖峰，算出"有机基线"。

    规则三的实现：**中位数 × 倍数** 阈值法。

    - 阈值 = ``median(daily) * burst_multiplier``；
    - 日增量超过阈值的日子判为**尖峰日**（压测/演练/批量导入）；
    - 其余日子算**有机基线** = 有机日行数之和 / 有机日数。

    为什么不用 p95：本库实测每张表只有 4~10 个有数据的日期，小样本下
    p95 几乎等于最大值（实测 outbox_events 的 p95 = 7968 = 最大值），
    什么尖峰都剔不掉——护栏形同虚设。中位数对离群值稳健。

    边界：样本极少（< 3 天）时中位数本身不稳，此时返回 ``baseline_per_day=0``
    并标记 ``insufficient_samples=True``，让上层拒绝据此下结论。
    """
    if not daily_counts:
        return {
            "baseline_per_day": 0.0,
            "organic_days": 0,
            "burst_days": 0,
            "burst_rows": 0,
            "burst_threshold": 0,
            "median_daily": 0,
            "insufficient_samples": True,
        }
    ordered = sorted(daily_counts.values())
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        median = float(ordered[middle])
    else:
        median = (ordered[middle - 1] + ordered[middle]) / 2
    threshold = median * burst_multiplier
    # 用 > 而非 >=：恰好等于阈值不算尖峰，避免边界抖动。
    organic = [value for value in ordered if value <= threshold]
    burst = [value for value in ordered if value > threshold]
    baseline = sum(organic) / len(organic) if organic else 0.0
    return {
        "baseline_per_day": round(baseline, 3),
        "organic_days": len(organic),
        "burst_days": len(burst),
        "burst_rows": sum(burst),
        "burst_threshold": round(threshold, 3),
        "median_daily": round(median, 3),
        "insufficient_samples": len(organic) < 3,
    }


def project_rows(baseline_per_day: float, retention_days: int) -> int:
    """按有机基线外推保留期内的行数。"""
    return int(round(baseline_per_day * retention_days))


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TableStat:
    """单表的增长/体积事实（全部来自只读查询）。"""

    table: str
    rows: int
    table_bytes: int
    index_bytes: int
    total_bytes: int
    n_live_tup: int
    n_dead_tup: int
    last_vacuum: str | None
    last_autovacuum: str | None
    last_analyze: str | None
    last_autoanalyze: str | None
    index_ratio: float
    bloat_ratio: float
    stats_trustworthy: bool
    time_column: str | None
    daily_counts: dict[str, int] = field(default_factory=dict)
    growth: dict = field(default_factory=dict)
    verdict: PartitionVerdict | None = None


# ---------------------------------------------------------------------------
# SQL（全部为 SELECT；运行期由 assert_select_only 逐条断言）
# ---------------------------------------------------------------------------

_SQL_TABLE_SIZES = """
select
    c.relname                                        as table_name,
    pg_table_size(c.oid)                            as table_bytes,
    pg_indexes_size(c.oid)                          as index_bytes,
    pg_total_relation_size(c.oid)                   as total_bytes,
    coalesce(t.n_live_tup, 0)                       as n_live_tup,
    coalesce(t.n_dead_tup, 0)                       as n_dead_tup,
    t.last_vacuum, t.last_autovacuum,
    t.last_analyze, t.last_autoanalyze
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
left join pg_stat_user_tables t
       on t.relname = c.relname and t.schemaname = n.nspname
where n.nspname = 'public'
  and c.relkind = 'r'
order by pg_total_relation_size(c.oid) desc
"""

_SQL_TIME_COLUMNS = """
select c.relname as table_name, a.attname as column_name
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
join pg_attribute a on a.attrelid = c.oid
where n.nspname = 'public'
  and c.relkind = 'r'
  and a.attnum > 0
  and not a.attisdropped
  and a.attname in ('created_at', 'occurred_at', 'recorded_at', 'created_on')
order by c.relname, a.attname
"""

_SQL_STATS_RESET = """
select stats_reset from pg_stat_database where datname = current_database()
"""


def _sql_count(table: str) -> str:
    """构造精确 COUNT 的 SQL。表名来自系统目录，先做标识符白名单校验。"""
    if not table.replace("_", "").isalnum() or not table[0].isalpha():
        raise ValueError(f"非法表名，拒绝拼进 SQL：{table!r}")
    return f"select count(*) from public.{table}"


def _sql_daily_distribution(table: str, column: str) -> str:
    """构造按日行数分布的 SQL。"""
    if not table.replace("_", "").isalnum() or not table[0].isalpha():
        raise ValueError(f"非法表名，拒绝拼进 SQL：{table!r}")
    if not column.replace("_", "").isalnum() or not column[0].isalpha():
        raise ValueError(f"非法列名，拒绝拼进 SQL：{column!r}")
    return (
        "select date_trunc('day', "
        f"public.{table}.{column})::date as day, count(*) as rows_count "
        f"from public.{table} "
        f"where public.{table}.{column} is not null "
        "group by 1 order by 1"
    )


# ---------------------------------------------------------------------------
# 审计执行
# ---------------------------------------------------------------------------


def _pick_time_column(available: dict[str, list[str]]) -> str | None:
    """为一张表挑一个可靠的时间列。

    优先级：``occurred_at``（事件发生时刻，最贴近真实增长） >
    ``created_at``（写入时刻） > ``recorded_at`` > ``created_on``。
    """
    for candidate in ("occurred_at", "created_at", "recorded_at", "created_on"):
        if candidate in available:
            return candidate
    return None


def audit(
    dsn: str,
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    stats_drift_ratio: float = DEFAULT_STATS_DRIFT_RATIO,
    large_candidate_rows: int = LARGE_TABLE_CANDIDATE_ROWS,
    burst_multiplier: float = DEFAULT_BURST_MULTIPLIER,
) -> dict:
    """执行只读审计，返回结构化报告（全部为 SELECT 查询）。"""
    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            size_rows = conn.execute(
                text(assert_select_only(_SQL_TABLE_SIZES, where="表体积"))
            ).all()
            time_rows = conn.execute(
                text(assert_select_only(_SQL_TIME_COLUMNS, where="时间列发现"))
            ).all()
            stats_reset_row = conn.execute(
                text(assert_select_only(_SQL_STATS_RESET, where="统计重置时间"))
            ).first()
    finally:
        engine.dispose()

    time_columns: dict[str, list[str]] = {}
    for row in time_rows:
        time_columns.setdefault(str(row.table_name), []).append(str(row.column_name))

    tables: list[TableStat] = []
    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            for row in size_rows:
                table = str(row.table_name)
                exact_rows = int(
                    conn.execute(
                        text(
                            assert_select_only(
                                _sql_count(table), where=f"{table}.count"
                            )
                        )
                    ).scalar()
                    or 0
                )
                time_column = _pick_time_column(time_columns.get(table, []))
                daily_counts: dict[str, int] = {}
                if time_column is not None and exact_rows > 0:
                    dist = conn.execute(
                        text(
                            assert_select_only(
                                _sql_daily_distribution(table, time_column),
                                where=f"{table}.日分布",
                            )
                        )
                    ).all()
                    daily_counts = {str(d.day): int(d.rows_count) for d in dist}

                growth = compute_organic_baseline(
                    daily_counts, burst_multiplier=burst_multiplier
                )
                n_live_tup = int(row.n_live_tup)
                # 规则二：精确行数与统计值偏差过大 → 统计不可信。
                if exact_rows > 0 and n_live_tup > 0:
                    drift = max(exact_rows, n_live_tup) / min(exact_rows, n_live_tup)
                    stats_trustworthy = drift <= stats_drift_ratio
                else:
                    stats_trustworthy = n_live_tup == exact_rows
                # 样本不足（有机日 < 3 天）时不给外推值——宁可不结论，
                # 也不要用 2 个样本点编出一条增长曲线。
                if (
                    growth["baseline_per_day"] > 0
                    and not growth["insufficient_samples"]
                ):
                    projected: int | None = project_rows(
                        growth["baseline_per_day"], retention_days
                    )
                else:
                    projected = None
                table_bytes = int(row.table_bytes)
                index_bytes = int(row.index_bytes)
                total_bytes = int(row.total_bytes)
                verdict = classify_partition(
                    table=table,
                    rows=exact_rows,
                    total_bytes=total_bytes,
                    projected_rows_at_retention=projected,
                    stats_trustworthy=stats_trustworthy,
                    large_candidate_rows=large_candidate_rows,
                )
                tables.append(
                    TableStat(
                        table=table,
                        rows=exact_rows,
                        table_bytes=table_bytes,
                        index_bytes=index_bytes,
                        total_bytes=total_bytes,
                        n_live_tup=n_live_tup,
                        n_dead_tup=int(row.n_dead_tup),
                        last_vacuum=_iso(row.last_vacuum),
                        last_autovacuum=_iso(row.last_autovacuum),
                        last_analyze=_iso(row.last_analyze),
                        last_autoanalyze=_iso(row.last_autoanalyze),
                        index_ratio=round(index_bytes / total_bytes, 4)
                        if total_bytes
                        else 0.0,
                        bloat_ratio=(
                            round(int(row.n_dead_tup) / exact_rows, 4)
                            if exact_rows
                            else 0.0
                        ),
                        stats_trustworthy=stats_trustworthy,
                        time_column=time_column,
                        daily_counts=daily_counts,
                        growth=growth,
                        verdict=verdict,
                    )
                )
    finally:
        engine.dispose()

    table_dicts = [asdict(t) for t in tables]
    by_size = sort_tables_by_size(table_dicts)
    by_rows = sort_tables_by_rows(table_dicts)
    large_candidates = [
        item for item in by_rows if int(item["rows"]) >= large_candidate_rows
    ]
    verdict_counts: dict[str, int] = {}
    for item in table_dicts:
        verdict = (item.get("verdict") or {}).get("verdict", "unknown")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    return {
        "target": mask_dsn(dsn),
        "readonly": True,
        "retention_days": retention_days,
        "thresholds": {
            "partition_trigger_rows": PARTITION_TRIGGER_ROWS,
            "partition_trigger_bytes": PARTITION_TRIGGER_BYTES,
            "archive_trigger_rows": ARCHIVE_TRIGGER_ROWS,
            "growth_review_trigger_rows": GROWTH_REVIEW_TRIGGER_ROWS,
            "large_table_candidate_rows": LARGE_TABLE_CANDIDATE_ROWS,
            "stats_drift_ratio": stats_drift_ratio,
            "burst_multiplier": burst_multiplier,
        },
        "statistics_reset": _iso(stats_reset_row[0]) if stats_reset_row else None,
        "totals": {
            "table_count": len(tables),
            "non_empty_tables": sum(1 for t in tables if t.rows > 0),
            "total_rows": sum(t.rows for t in tables),
            "total_bytes": sum(t.total_bytes for t in tables),
            "stats_untrustworthy_tables": sum(
                1 for t in tables if not t.stats_trustworthy
            ),
        },
        "verdict_counts": verdict_counts,
        "top_by_size": by_size[:20],
        "top_by_rows": by_rows[:20],
        "large_table_candidates": large_candidates,
        "tables": table_dicts,
        "notes": [
            "只读审计：全部 SQL 为 SELECT，未执行任何 DDL/DML",
            "行数为精确 COUNT(*)，不使用 n_live_tup 估算值（统计可能因容器重启归零）",
            "增长率只用剔除尖峰后的有机基线外推；尖峰来自压测/演练，不代表业务流量",
            "统计不可信的表（精确行数与 n_live_tup 偏差过大）增长率以时间列分布为准",
        ],
    }


def _iso(value: object) -> str | None:
    """把 DBAPI 时间值转成 ISO 字符串；None 原样返回。"""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _plan(dsn: str) -> dict:
    """dry-run 计划：说明将要查什么、判定规则是什么，但不连库。"""
    target = parse_pg_dsn(dsn)
    if target.host.lower() not in _LOOPBACK_HOSTS:
        raise ValueError(
            f"拒绝在非本机数据库上执行只读审计：host={target.host!r}；"
            f"只允许 {sorted(_LOOPBACK_HOSTS)}。确需读取远端只读副本请显式 "
            "--allow-non-loopback"
        )
    return {
        "mode": "dry-run",
        "readonly": True,
        "target": mask_dsn(dsn),
        "resolved_target": f"{target.host}:{target.port}/{target.database}",
        "planned_queries": [
            {
                "purpose": "每表体积/索引体积/统计新鲜度",
                "sql": _SQL_TABLE_SIZES.strip(),
            },
            {
                "purpose": "发现可用时间列（决定增长率口径）",
                "sql": _SQL_TIME_COLUMNS.strip(),
            },
            {
                "purpose": "统计重置时间（新鲜度旁证）",
                "sql": _SQL_STATS_RESET.strip(),
            },
            {
                "purpose": "逐表精确行数（COUNT(*)，只读）",
                "sql": "select count(*) from public.<table>",
            },
            {
                "purpose": "逐表按日行数分布（时间列口径，只读）",
                "sql": "select date_trunc('day', <ts_col>)::date, count(*) from public.<table> group by 1",
            },
        ],
        "thresholds": {
            "partition_trigger_rows": PARTITION_TRIGGER_ROWS,
            "partition_trigger_bytes": PARTITION_TRIGGER_BYTES,
            "archive_trigger_rows": ARCHIVE_TRIGGER_ROWS,
            "growth_review_trigger_rows": GROWTH_REVIEW_TRIGGER_ROWS,
            "large_table_candidate_rows": LARGE_TABLE_CANDIDATE_ROWS,
            "stats_drift_ratio": DEFAULT_STATS_DRIFT_RATIO,
            "burst_multiplier": DEFAULT_BURST_MULTIPLIER,
        },
        "notes": [
            "dry-run：本次未连接数据库，未执行任何查询",
            "--execute 才真正连库；即便如此，全部 SQL 仍只有 SELECT",
            "写操作在本脚本中不存在（无 INSERT/UPDATE/DELETE/DDL），只读由 assert_select_only 强制",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="大表增长/体积只读审计（默认 dry-run）"
    )
    parser.add_argument("--dsn", required=True, help="目标库 DSN")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="真正连库执行只读查询（默认 dry-run，只打印计划）",
    )
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="允许连接非本机主机（只放开读；写操作在本脚本中不存在）",
    )
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument(
        "--stats-drift-ratio", type=float, default=DEFAULT_STATS_DRIFT_RATIO
    )
    parser.add_argument(
        "--large-candidate-rows", type=int, default=LARGE_TABLE_CANDIDATE_ROWS
    )
    parser.add_argument(
        "--burst-multiplier",
        type=float,
        default=DEFAULT_BURST_MULTIPLIER,
        help="日增量超过「中位数 × 该倍数」判为尖峰日（压测/演练）",
    )
    parser.add_argument("--format", choices=("json", "table"), default="table")
    parser.add_argument("--out", default="", help="JSON 输出路径")
    args = parser.parse_args(argv)

    if not args.execute:
        plan = _plan(args.dsn)
        if args.format == "json":
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            print("模式: dry-run（未连接数据库）")
            print(f"目标: {plan['resolved_target']}  （脱敏 DSN: {plan['target']}）")
            print("只读保证: 全部 SQL 均为 SELECT；无任何 DDL/DML")
            print()
            print("将要执行的查询:")
            for item in plan["planned_queries"]:
                print(f"  - {item['purpose']}")
            print()
            print("阈值:")
            for key, value in plan["thresholds"].items():
                print(f"  {key}: {value}")
            print()
            print("加 --execute 真正执行。")
        return 0

    target = parse_pg_dsn(args.dsn)
    if target.host.lower() not in _LOOPBACK_HOSTS and not args.allow_non_loopback:
        raise ValueError(
            f"拒绝在非本机数据库上执行只读审计：host={target.host!r}；"
            f"只允许 {sorted(_LOOPBACK_HOSTS)}。确需读取远端只读副本请显式 "
            "--allow-non-loopback"
        )

    report = audit(
        args.dsn,
        retention_days=args.retention_days,
        stats_drift_ratio=args.stats_drift_ratio,
        large_candidate_rows=args.large_candidate_rows,
        burst_multiplier=args.burst_multiplier,
    )
    report["mode"] = "execute"

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_table(report)

    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报告已写入 {args.out}", file=sys.stderr)
    return 0


def _print_table(report: dict) -> None:
    """终端表格输出。"""
    totals = report["totals"]
    print(f"目标: {report['target']}")
    print(
        f"表总数 {totals['table_count']}（非空 {totals['non_empty_tables']}）| "
        f"总行数 {totals['total_rows']:,} | 总体积 {totals['total_bytes']:,} 字节"
    )
    print(f"统计不可信表: {totals['stats_untrustworthy_tables']}")
    print(f"判定分布: {report['verdict_counts']}")
    print()
    header = (
        f"{'table':32s} {'rows':>8s} {'total':>12s} {'idx%':>6s} {'bloat':>7s} "
        f"{'trust':>6s} {'base/d':>9s} {'verdict':20s}"
    )
    print(header)
    print("-" * len(header))
    for item in report["top_by_rows"][:20]:
        growth = item.get("growth") or {}
        verdict = (item.get("verdict") or {}).get("verdict", "-")
        print(
            f"{item['table'][:32]:32s} {item['rows']:>8d} {item['total_bytes']:>12d} "
            f"{item['index_ratio'] * 100:>5.1f}% {item['bloat_ratio'] * 100:>6.2f}% "
            f"{'Y' if item['stats_trustworthy'] else 'N':>6s} "
            f"{growth.get('baseline_per_day', 0):>9.1f} {verdict:20s}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
