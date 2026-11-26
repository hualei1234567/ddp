"""数据库索引审计（审计 R6：408 个索引里可能有零使用索引，造成写放大）。

## 为什么需要这个脚本

每次 INSERT/UPDATE 都要维护该表的所有索引。零使用索引 = 纯写成本。但
"零使用" 的判定很容易误判，本脚本把误判来源显式化：

1. **统计样本不足**：库刚创建或 `pg_stat_reset` 后，所有 `idx_scan` 都是 0，
   此时任何结论都无效。脚本先检查总扫描量，样本不足直接拒绝给结论。
2. **约束背书索引**：主键/唯一/排他约束背后的索引**不能删**（删了约束就没了），
   即使 `idx_scan=0`。脚本按 `pg_index.indisprimary/indisunique` 区分。
3. **小表 seq scan 更优**：表只有几十行时 PG 一定走 seq scan，索引永远不被使用，
   但表长大后索引会变有用。脚本输出表行数供人判断。

## 用法

```bash
python scripts/db/index_audit.py --dsn postgresql+psycopg://ddp:ddp@localhost:55432/ddp
python scripts/db/index_audit.py --dsn ... --min-total-scans 100000 --format json
```

**只读**：本脚本只 SELECT 系统表，不改任何东西。删索引必须人工评审 + 迁移。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import create_engine, text

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.perf.core import parse_pg_dsn  # noqa: E402

# 样本充分性门槛：总索引扫描数低于此值时不建议下任何结论
DEFAULT_MIN_TOTAL_SCANS = 100_000
# 小表阈值：低于此行数时 seq scan 通常更优，零使用索引不一定是问题
SMALL_TABLE_ROWS = 1_000


def classify_index(
    *,
    is_constraint: bool,
    sample_sufficient: bool,
    table_rows: int,
) -> str:
    """判定一个零使用索引该怎么处理（纯函数，便于单测）。

    判定顺序即优先级：
    1. 约束背书 → 无论如何都不能删（`keep_constraint`）
    2. 统计样本不足 → 不得下结论（`review_after_growth`）
    3. 小表 → seq scan 更优属正常，表长大后复查（`review_after_growth`）
    4. 其余 → 可删候选（`drop_candidate`）
    """
    if is_constraint:
        return "keep_constraint"
    if not sample_sufficient:
        return "review_after_growth"
    if table_rows < SMALL_TABLE_ROWS:
        return "review_after_growth"
    return "drop_candidate"


@dataclass(frozen=True)
class IndexFinding:
    """一条零使用索引的判定。"""

    table: str
    index: str
    size_bytes: int
    table_rows: int
    kind: str  # constraint / performance
    verdict: str  # drop_candidate / keep_constraint / review_after_growth


_QUERY = """
select
    s.relname                as table_name,
    s.indexrelname           as index_name,
    s.idx_scan               as idx_scan,
    pg_relation_size(s.indexrelid) as size_bytes,
    coalesce(t.n_live_tup, 0) as table_rows,
    i.indisprimary           as is_primary,
    i.indisunique            as is_unique
from pg_stat_user_indexes s
join pg_index i on i.indexrelid = s.indexrelid
left join pg_stat_user_tables t
       on t.relname = s.relname and t.schemaname = s.schemaname
where s.schemaname = 'public'
  and s.idx_scan = 0
order by pg_relation_size(s.indexrelid) desc
"""


def _sample_sufficient(engine, min_total: int) -> tuple[bool, dict[str, int]]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "select coalesce(sum(idx_scan), 0) as idx, coalesce(sum(seq_scan), 0) as seq"
                " from pg_stat_user_tables where schemaname = 'public'"
            )
        ).one()
    stats = {"total_index_scans": int(row[0]), "total_seq_scans": int(row[1])}
    return stats["total_index_scans"] >= min_total, stats


def audit(dsn: str, *, min_total_scans: int = DEFAULT_MIN_TOTAL_SCANS) -> dict:
    """执行审计，返回结构化报告（只读）。"""
    engine = create_engine(dsn)
    try:
        sufficient, stats = _sample_sufficient(engine, min_total_scans)
        with engine.connect() as conn:
            rows = conn.execute(text(_QUERY)).all()
        findings: list[IndexFinding] = []
        for row in rows:
            is_constraint = bool(row.is_primary or row.is_unique)
            kind = "constraint" if is_constraint else "performance"
            verdict = classify_index(
                is_constraint=is_constraint,
                sample_sufficient=sufficient,
                table_rows=int(row.table_rows),
            )
            findings.append(
                IndexFinding(
                    table=row.table_name,
                    index=row.index_name,
                    size_bytes=int(row.size_bytes),
                    table_rows=int(row.table_rows),
                    kind=kind,
                    verdict=verdict,
                )
            )
    finally:
        engine.dispose()

    by_verdict: dict[str, int] = {}
    for f in findings:
        by_verdict[f.verdict] = by_verdict.get(f.verdict, 0) + 1
    return {
        "sample_sufficient": sufficient,
        "statistics": stats,
        "min_total_scans": min_total_scans,
        "small_table_rows_threshold": SMALL_TABLE_ROWS,
        "counts": {
            "total_zero_scan_indexes": len(findings),
            "by_verdict": by_verdict,
            "wasted_bytes_on_drop_candidates": sum(
                f.size_bytes for f in findings if f.verdict == "drop_candidate"
            ),
        },
        "findings": [asdict(f) for f in findings],
        "notes": [
            "只读审计：不改任何索引；删索引必须人工评审并通过迁移落地",
            "sample_sufficient=false 时不得下任何删除结论（统计样本不足）",
            "review_after_growth：表行数小于阈值，seq scan 更优是正常现象，"
            "表长大后需复查",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="数据库索引审计（只读）")
    parser.add_argument("--dsn", required=True, help="目标库 DSN")
    parser.add_argument("--min-total-scans", type=int, default=DEFAULT_MIN_TOTAL_SCANS)
    parser.add_argument("--format", choices=("json", "table"), default="table")
    parser.add_argument("--out", default="", help="JSON 输出路径")
    args = parser.parse_args(argv)

    target = parse_pg_dsn(args.dsn)
    report = audit(args.dsn, min_total_scans=args.min_total_scans)
    report["target"] = f"{target.host}:{target.port}/{target.database}"

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        s = report["statistics"]
        print(f"目标: {report['target']}")
        print(
            f"统计样本: 索引扫描 {s['total_index_scans']:,} / 顺序扫描 {s['total_seq_scans']:,}"
            f" → {'充分' if report['sample_sufficient'] else '不足（结论无效）'}"
        )
        print(
            f"零使用索引: {report['counts']['total_zero_scan_indexes']} 个 "
            f"{report['counts']['by_verdict']}"
        )
        print()
        print(f"{'verdict':22s} {'table':34s} {'index':46s} {'rows':>7s} {'size':>10s}")
        for f in sorted(report["findings"], key=lambda x: -x["size_bytes"]):
            print(
                f"{f['verdict']:22s} {f['table'][:34]:34s} {f['index'][:46]:46s} "
                f"{f['table_rows']:>7d} {f['size_bytes']:>10d}"
            )
        wasted = report["counts"]["wasted_bytes_on_drop_candidates"]
        print(f"\n可删候选占用空间合计: {wasted:,} 字节")

    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报告已写入 {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
