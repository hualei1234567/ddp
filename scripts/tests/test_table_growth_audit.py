"""大表增长审计脚本的纯函数层测试。

**为什么测这些**：这个脚本的结论会驱动「要不要分区/归档」这种**不可逆的
架构决策**。判错一次，代价是引入分区却没带来收益（写放大、迁移复杂度、
约束语义变化）。因此必须钉死：

1. **只读护栏不能漏**：任何含写关键字的 SQL 必须被拒绝；
2. **DSN 护栏不能松**：非 loopback 目标必须被拒（除非显式放开）；
3. **DSN 脱敏不能漏**：口令不得出现在报告/终端输出里；
4. **行数不能信统计值**：`n_live_tup` 偏差大时统计必须被判为不可信；
5. **增长外推不能被尖峰污染**：压测尖峰不得当作业务基线；
6. **判定优先级不能乱**：已达阈值必须先于"暂不需要"被命中；
7. **排序必须确定**：同体积同行数的表按表名排，否则证据文件每次都变。

这些都不需要连库——全部是纯函数，因此 CI 无需 docker 即可跑。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 被审计脚本本体路径——结构性断言（AST 扫描）需要读它的源码。
SCRIPT_PATH = _ROOT / "scripts" / "db" / "table_growth_audit.py"

from scripts.db.table_growth_audit import (  # noqa: E402
    ARCHIVE_TRIGGER_ROWS,
    DEFAULT_BURST_MULTIPLIER,
    GROWTH_REVIEW_TRIGGER_ROWS,
    LARGE_TABLE_CANDIDATE_ROWS,
    PARTITION_TRIGGER_BYTES,
    PARTITION_TRIGGER_ROWS,
    _plan,
    assert_select_only,
    classify_partition,
    compute_organic_baseline,
    project_rows,
    sort_tables_by_rows,
    sort_tables_by_size,
)
from scripts.perf.core import mask_dsn  # noqa: E402

_LOOPBACK_DSN = "postgresql+psycopg://ddp:ddp@localhost:55432/ddp"


# ---------------------------------------------------------------------------
# 只读护栏
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM public.outbox_events",
        "UPDATE public.audit_logs SET result = 'FAILURE'",
        "INSERT INTO public.projects VALUES (1)",
        "DROP TABLE public.outbox_events",
        "CREATE TABLE public.t (id int)",
        "ALTER TABLE public.t ADD COLUMN x int",
        "TRUNCATE public.outbox_events",
        "GRANT ALL ON public.t TO ddp",
        "VACUUM public.outbox_events",
        "ANALYZE public.outbox_events",
        "REINDEX TABLE public.t",
        "COPY public.t FROM '/tmp/x.csv'",
        "LOCK TABLE public.t",
        "COMMIT",
        "ROLLBACK",
    ],
)
def test_write_statements_are_rejected(sql: str) -> None:
    """任何写语句都必须被拒绝——这是"只读"承诺的执行点。"""
    with pytest.raises(ValueError, match="只允许 SELECT/WITH|违反只读约束"):
        assert_select_only(sql, where="测试")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select count(*) from public.outbox_events",
        "WITH x AS (SELECT 1 AS n) SELECT n FROM x",
        "SELECT c.relname, pg_total_relation_size(c.oid) FROM pg_class c",
    ],
)
def test_read_statements_are_accepted(sql: str) -> None:
    """正常只读查询必须放行（护栏不能误报到没法用）。"""
    assert assert_select_only(sql, where="测试") == sql.strip()


def test_write_keyword_hidden_in_cte_is_rejected() -> None:
    """CTE 里藏写操作必须被抓住——`WITH` 开头不等于只读。"""
    sql = "WITH gone AS (DELETE FROM public.outbox_events RETURNING id) SELECT * FROM gone"
    with pytest.raises(ValueError, match="只读"):
        assert_select_only(sql, where="测试")


def test_write_keyword_inside_string_literal_is_not_a_false_positive() -> None:
    """字符串字面量里的 'DELETE' 不该误报——误报会让护栏被人习惯性忽略。

    同时验证 `last_autovacuum` 这类列名不会被当成 VACUUM 操作：
    裸子串匹配会误判，词边界匹配才正确。
    """
    sql = (
        "SELECT relname, last_autovacuum, last_autoanalyze FROM pg_stat_user_tables "
        "WHERE status = 'DELETE_PENDING'"
    )
    assert assert_select_only(sql, where="测试") == sql.strip()


def test_every_sql_executed_by_script_is_select_only() -> None:
    """**结构性断言**：脚本里所有真正送进数据库的 SQL 都必须是只读。

    用 AST 静态分析（不连库）找出所有 ``text(...)`` 调用与 SQL 常量，
    逐条走一遍脚本自己的护栏。这样即使以后有人新增一条写查询，
    单测立刻变红——**而不是等到它在生产库上执行**。

    只读承诺不能只靠"我记得没写写操作"，必须有机器守卫。
    """
    import ast

    source = SCRIPT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # 收集所有看起来像 SQL 的字符串常量（模块级 SQL 与函数内拼接片段）。
    sql_like: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if not value:
                continue
            upper = value.upper()
            # 只取"以 SQL 关键字开头"或"含 SQL 关键字"的候选。
            # 关键字黑名单常量本身是单词列表（'INSERT'/'UPDATE'/…），
            # 会被下面的 startswith 过滤掉，不会误当成 SQL。
            if upper.startswith(
                ("SELECT", "WITH", "INSERT", "UPDATE", "DELETE")
            ) or upper in {
                "DROP",
                "CREATE",
                "ALTER",
                "TRUNCATE",
                "VACUUM",
                "ANALYZE",
                "COMMIT",
                "ROLLBACK",
            }:
                sql_like.append(value)

    assert sql_like, "未发现任何 SQL 字符串——脚本结构变了？需同步更新本断言"

    for value in sql_like:
        # 黑名单常量（单词列表）不是可执行 SQL，跳过。
        if value.upper() in {
            "INSERT",
            "UPDATE",
            "DELETE",
            "DROP",
            "CREATE",
            "ALTER",
            "TRUNCATE",
            "VACUUM",
            "ANALYZE",
            "COMMIT",
            "ROLLBACK",
        }:
            continue
        assert_select_only(value, where=f"AST 扫描 L{sql_like.index(value)}")

    # 额外确认：不存在任何把非 SELECT 语句交给 DBAPI 的入口。
    # SQLAlchemy 的 execute/exec_driver_sql 对写语句没有区别对待，
    # 所以"只走 assert_select_only"是唯一可靠的保证方式。
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
    ]
    assert calls, "未发现 execute 调用——脚本结构变了？需同步更新本断言"
    for call in calls:
        source_text = ast.unparse(call)
        assert "assert_select_only" in source_text, (
            "存在未经 assert_select_only 断言的 execute 调用："
            f"{source_text[:120]}\n"
            "只读承诺要求每条 SQL 执行前都过一遍护栏。"
        )


# ---------------------------------------------------------------------------
# DSN 护栏与脱敏
# ---------------------------------------------------------------------------


def test_dry_run_rejects_non_loopback_host() -> None:
    """非本机目标必须被拒绝——审计脚本只读，但连错库仍会读到不该读的数据。"""
    dsn = "postgresql+psycopg://ddp:ddp@db.internal.example.com:5432/ddp"
    with pytest.raises(ValueError, match="非本机"):
        _plan(dsn)


@pytest.mark.parametrize(
    "host",
    ["localhost", "127.0.0.1", "[::1]"],
)
def test_dry_run_accepts_loopback_hosts(host: str) -> None:
    """三个 loopback 写法都必须放行（IPv6 的 ::1 也要算本机）。

    IPv6 在 URL 里必须写成 ``[::1]``，这是 URL 规范要求的括号形式，
    裸 ``::1`` 会被 urlsplit 解析成空主机名——所以这里用带括号写法。
    """
    dsn = f"postgresql+psycopg://ddp:ddp@{host}:55432/ddp"
    plan = _plan(dsn)
    assert plan["mode"] == "dry-run"
    assert plan["readonly"] is True


def test_dry_run_does_not_connect() -> None:
    """dry-run 计划里必须只有 SELECT 类查询，且不含写关键字。"""
    plan = _plan(_LOOPBACK_DSN)
    for item in plan["planned_queries"]:
        sql = item["sql"]
        # 逐条走一遍脚本自己的护栏——比在测试里重写一遍关键字表更可靠：
        # 护栏改了，这里立刻跟着变；测试里复制一份关键字表则会产生漂移。
        assert_select_only(sql, where=item["purpose"])


def test_mask_dsn_hides_password() -> None:
    """口令不得出现在任何报告/终端输出里。"""
    masked = mask_dsn(_LOOPBACK_DSN)
    assert "ddp:ddp@" not in masked
    assert "***" in masked
    # 主机与库名要保留，否则没法核对审计的是哪个库。
    assert "localhost:55432" in masked
    assert masked.endswith("/ddp")


def test_dry_run_plan_masks_dsn() -> None:
    """dry-run 计划本身也必须脱敏（终端会直接打印）。"""
    plan = _plan(_LOOPBACK_DSN)
    assert "ddp:ddp@" not in plan["target"]
    assert "***" in plan["target"]


# ---------------------------------------------------------------------------
# 增长基线（规则三：尖峰不得污染外推）
# ---------------------------------------------------------------------------


def test_burst_days_are_excluded_from_organic_baseline() -> None:
    """压测尖峰日必须被剔除，否则会把演练流量当业务基线。

    这组数字取自生产库实测（2026-11-27 审计）：outbox_events 的按日分布为
    11-14:28 / 11-15:857 / 11-16:21 / 11-17:7968 / 11-18:3375 / 11-21:234 /
    11-23:18 / 11-24:1 / 11-25:14 / 11-26:2。

    中位数 = 24.5，尖峰阈值 = 24.5 × 10 = 245，因此 857 / 7968 / 3375 三天
    被判为尖峰（压测与演练），其余 7 天为有机日。
    若不剔除尖峰，基线会是 1251.8 行/天；剔除后是 45.4 行/天——差 27 倍。
    """
    daily = {
        "2026-11-14": 28,
        "2026-11-15": 857,
        "2026-11-16": 21,
        "2026-11-17": 7968,  # 压测尖峰
        "2026-11-18": 3375,  # 压测尖峰
        "2026-11-21": 234,
        "2026-11-23": 18,
        "2026-11-24": 1,
        "2026-11-25": 14,
        "2026-11-26": 2,
    }
    result = compute_organic_baseline(daily)
    assert result["burst_days"] == 3
    assert result["burst_rows"] == 857 + 7968 + 3375
    assert result["organic_days"] == 7
    # 有机基线 = (28+21+234+18+1+14+2) / 7 = 318 / 7 = 45.428571
    # 脚本把基线四舍五入到 3 位小数，断言按同一精度比对。
    assert result["baseline_per_day"] == pytest.approx(round(318 / 7, 3), abs=1e-9)
    # 未剔除尖峰的基线会高达 1251.8，剔除后必须显著更低。
    naive = sum(daily.values()) / len(daily)
    assert result["baseline_per_day"] < naive / 20


def test_steady_growth_is_not_mistaken_for_burst() -> None:
    """平稳增长的表不能被误判成"全是尖峰"——否则基线归零、结论失效。"""
    daily = {"d1": 10, "d2": 11, "d3": 12, "d4": 13, "d5": 14}
    result = compute_organic_baseline(daily)
    assert result["burst_days"] == 0
    assert result["organic_days"] == 5
    assert result["baseline_per_day"] == pytest.approx(12.0)
    assert result["insufficient_samples"] is False


def test_too_few_samples_is_flagged_insufficient() -> None:
    """只有 2 个有效日时不得据此下结论——两点连成一条"增长曲线"是自欺。"""
    result = compute_organic_baseline({"d1": 1, "d2": 100})
    assert result["insufficient_samples"] is True


def test_empty_distribution_yields_zero_baseline() -> None:
    """空表没有基线，且必须被标为样本不足。"""
    result = compute_organic_baseline({})
    assert result["baseline_per_day"] == 0.0
    assert result["insufficient_samples"] is True


def test_projection_uses_retention_days() -> None:
    """外推行数 = 日基线 × 保留天数。"""
    assert project_rows(45.4, 30) == 1362
    assert project_rows(0, 30) == 0


# ---------------------------------------------------------------------------
# 判定逻辑（规则二/三的落点）
# ---------------------------------------------------------------------------


def test_small_table_is_no_partition_needed() -> None:
    """当前生产规模（百级~万级）必须判为"暂不需要分区"。"""
    verdict = classify_partition(
        table="outbox_events",
        rows=12_518,
        total_bytes=7_020_544,
        projected_rows_at_retention=1_362,
        stats_trustworthy=False,
    )
    assert verdict.verdict == "no_partition_needed"
    assert any("暂" in reason or "远低于阈值" in reason for reason in verdict.reasons)


def test_table_over_row_trigger_is_partition_review() -> None:
    """超过 5000 万行必须判为需要评估分区。"""
    verdict = classify_partition(
        table="huge",
        rows=PARTITION_TRIGGER_ROWS + 1,
        total_bytes=1024,
        projected_rows_at_retention=10,
        stats_trustworthy=True,
    )
    assert verdict.verdict == "partition_review"


def test_table_over_byte_trigger_is_partition_review() -> None:
    """体积超 50GiB 也必须判为需要评估分区（行数不多但字节多同样危险）。"""
    verdict = classify_partition(
        table="fat",
        rows=1_000,
        total_bytes=PARTITION_TRIGGER_BYTES + 1,
        projected_rows_at_retention=10,
        stats_trustworthy=True,
    )
    assert verdict.verdict == "partition_review"


def test_projected_growth_reaching_trigger_is_partition_review() -> None:
    """按当前速率保留期内会触阈，必须提前判为需要评估。"""
    verdict = classify_partition(
        table="growing",
        rows=1_000,
        total_bytes=1024,
        projected_rows_at_retention=PARTITION_TRIGGER_ROWS + 1,
        stats_trustworthy=True,
    )
    assert verdict.verdict == "partition_review"


def test_over_archive_trigger_is_archive_needed() -> None:
    """超过归档阈值但未达分区阈值 → 归档优先（更轻的干预手段）。"""
    verdict = classify_partition(
        table="archivable",
        rows=ARCHIVE_TRIGGER_ROWS + 1,
        total_bytes=1024,
        projected_rows_at_retention=1,
        stats_trustworthy=True,
    )
    assert verdict.verdict == "archive_needed"


def test_projected_growth_over_review_line_is_growth_review() -> None:
    """保留期内预计行数超增长预警线 → 提前复查（不等到触阈才反应）。"""
    verdict = classify_partition(
        table="watch",
        rows=1_000,
        total_bytes=1024,
        projected_rows_at_retention=GROWTH_REVIEW_TRIGGER_ROWS + 1,
        stats_trustworthy=True,
    )
    assert verdict.verdict == "growth_review"


def test_large_table_without_growth_data_is_insufficient_data() -> None:
    """已达大表候选线却算不出增长率 → 结论不足以支撑判断，必须人工复查。"""
    verdict = classify_partition(
        table="big_unknown",
        rows=LARGE_TABLE_CANDIDATE_ROWS + 1,
        total_bytes=1024,
        projected_rows_at_retention=None,
        stats_trustworthy=False,
    )
    assert verdict.verdict == "insufficient_data"


def test_small_table_without_growth_data_still_has_verdict() -> None:
    """小表即使没有增长数据也能下结论。

    这是本脚本相对"到处标 insufficient_data"的关键差异：一张 1044 行的表
    距离 5000 万行差 4 万倍，增长率未知不影响当前判定。若把它标成
    insufficient_data，真正的"不知道"会被淹没。
    """
    verdict = classify_partition(
        table="small_unknown",
        rows=1_044,
        total_bytes=622_592,
        projected_rows_at_retention=None,
        stats_trustworthy=False,
    )
    assert verdict.verdict == "no_partition_needed"


def test_row_trigger_takes_priority_over_projection() -> None:
    """已达绝对阈值时，优先级高于任何投影结论。"""
    verdict = classify_partition(
        table="both",
        rows=PARTITION_TRIGGER_ROWS + 1,
        total_bytes=1024,
        projected_rows_at_retention=PARTITION_TRIGGER_ROWS + 1,
        stats_trustworthy=True,
    )
    assert verdict.verdict == "partition_review"
    assert any("已达分区阈值" in reason for reason in verdict.reasons)


# ---------------------------------------------------------------------------
# 排序（证据文件必须可 diff）
# ---------------------------------------------------------------------------


def _table(name: str, rows: int, total: int) -> dict:
    return {"table": name, "rows": rows, "total_bytes": total}


def test_sort_by_size_is_descending_and_deterministic() -> None:
    tables = [
        _table("b", 10, 300),
        _table("a", 10, 300),
        _table("c", 99, 100),
    ]
    result = sort_tables_by_size(tables)
    assert [item["table"] for item in result] == ["a", "b", "c"]
    # 体积相同 → 行数降序 → 表名升序，逐级确定。
    assert [item["table"] for item in sort_tables_by_rows(tables)] == ["c", "a", "b"]


def test_sort_by_rows_prefers_rows_then_size() -> None:
    tables = [
        _table("small_big", 10, 9999),
        _table("big_small", 100, 1),
    ]
    assert [item["table"] for item in sort_tables_by_rows(tables)] == [
        "big_small",
        "small_big",
    ]


def test_sort_does_not_mutate_input() -> None:
    """排序必须返回新列表，不能就地改调用方的数据。"""
    tables = [_table("b", 1, 2), _table("a", 3, 4)]
    sort_tables_by_size(tables)
    assert [item["table"] for item in tables] == ["b", "a"]


# ---------------------------------------------------------------------------
# 阈值常量（防止有人"放宽断言换绿"）
# ---------------------------------------------------------------------------


def test_thresholds_are_not_silently_lowered() -> None:
    """阈值是代码常量，任何人想放宽都必须改代码走评审——这里钉住当前值。

    放宽阈值来让某张表"看起来不需要分区"，是本任务最容易发生的作弊方式。
    """
    assert PARTITION_TRIGGER_ROWS == 50_000_000
    assert PARTITION_TRIGGER_BYTES == 50 * 1024**3
    assert ARCHIVE_TRIGGER_ROWS == 10_000_000
    assert GROWTH_REVIEW_TRIGGER_ROWS == 1_000_000
    assert LARGE_TABLE_CANDIDATE_ROWS == 100_000
    assert DEFAULT_BURST_MULTIPLIER == 10.0
