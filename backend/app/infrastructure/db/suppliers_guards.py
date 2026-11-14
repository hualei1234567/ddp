"""阶段 6 供应商域数据库兜底触发器。

- 状态列只允许状态机声明过的迁移（PostgreSQL 用 plpgsql 函数，SQLite 用触发器）；
- ``acceptance_ledger_entries`` 只追加：禁止 UPDATE 与 DELETE；
- 迁移与 ``Base.metadata`` 之后安装同一份 DDL，避免测试与验收环境漂移。
"""

# ruff: noqa: S608 - SQL 只由固定枚举常量拼接，不接收用户输入。

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

TRANSITIONS: dict[str, frozenset[tuple[str, str]]] = {
    "suppliers": frozenset(
        {
            ("DRAFT", "ACTIVE"),
            ("ACTIVE", "SUSPENDED"),
            ("ACTIVE", "TERMINATED"),
            ("SUSPENDED", "ACTIVE"),
            ("SUSPENDED", "TERMINATED"),
        }
    ),
    "supplier_delivery_batches": frozenset(
        {
            ("ANNOUNCED", "IN_TRANSIT"),
            ("IN_TRANSIT", "ARRIVED"),
            ("ARRIVED", "SCREENING"),
            ("SCREENING", "ACCEPTED"),
            ("SCREENING", "PARTIALLY_ACCEPTED"),
            ("SCREENING", "REJECTED"),
            ("REJECTED", "REWORK"),
            ("REWORK", "SCREENING"),
            ("ACCEPTED", "CLOSED"),
            ("PARTIALLY_ACCEPTED", "CLOSED"),
        }
    ),
    "inbound_segments": frozenset(
        {
            ("REGISTERED", "PENDING_REVIEW"),
            ("PENDING_REVIEW", "ACCEPTED"),
            ("PENDING_REVIEW", "REJECTED"),
            ("PENDING_REVIEW", "NEED_MORE"),
            ("NEED_MORE", "PENDING_REVIEW"),
        }
    ),
}

LEDGER_TABLE = "acceptance_ledger_entries"


def _pg_pairs(table: str) -> str:
    transitions = TRANSITIONS[table]
    return " OR ".join(
        f"(OLD.status = {old!r} AND NEW.status = {new!r})" for old, new in sorted(transitions)
    )


def _pg_install(conn: Connection) -> None:
    for table in TRANSITIONS:
        func = f"ddp_{table}_guard"
        pairs = _pg_pairs(table)
        conn.exec_driver_sql(
            f"""
            CREATE OR REPLACE FUNCTION {func}() RETURNS trigger AS $$
            BEGIN
                IF NEW.status = OLD.status THEN
                    RETURN NEW;
                END IF;
                IF {pairs} THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'invalid status transition on {table}'
                    USING ERRCODE = 'check_violation';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_{table}_guard ON {table}")
        conn.exec_driver_sql(
            f"CREATE TRIGGER trg_{table}_guard BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {func}()"
        )

    conn.exec_driver_sql(
        """
        CREATE OR REPLACE FUNCTION ddp_ledger_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'acceptance ledger is append-only'
                USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for event in ("UPDATE", "DELETE"):
        conn.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS trg_{LEDGER_TABLE}_{event.lower()} ON {LEDGER_TABLE}"
        )
        conn.exec_driver_sql(
            f"CREATE TRIGGER trg_{LEDGER_TABLE}_{event.lower()} BEFORE {event} "
            f"ON {LEDGER_TABLE} FOR EACH ROW EXECUTE FUNCTION ddp_ledger_append_only()"
        )


def _sqlite_install(conn: Connection) -> None:
    for table, transitions in TRANSITIONS.items():
        allowed = " OR ".join(
            f"(OLD.status = '{old}' AND NEW.status = '{new}')" for old, new in sorted(transitions)
        )
        conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_{table}_guard")
        conn.exec_driver_sql(
            f"""
            CREATE TRIGGER trg_{table}_guard
            BEFORE UPDATE OF status ON {table}
            FOR EACH ROW
            WHEN OLD.status <> NEW.status
             AND NOT ({allowed})
            BEGIN
                SELECT RAISE(ABORT, 'invalid status transition');
            END;
            """
        )
    for event in ("UPDATE", "DELETE"):
        conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_{LEDGER_TABLE}_{event.lower()}")
        conn.exec_driver_sql(
            f"""
            CREATE TRIGGER trg_{LEDGER_TABLE}_{event.lower()}
            BEFORE {event} ON {LEDGER_TABLE}
            FOR EACH ROW
            BEGIN
                SELECT RAISE(ABORT, 'acceptance ledger is append-only');
            END;
            """
        )


def install_suppliers_guards(conn: Connection) -> None:
    inspector = inspect(conn)
    existing = set(inspector.get_table_names())
    required = set(TRANSITIONS) | {LEDGER_TABLE}
    if required - existing:
        # 与既有守卫一致：未建表时跳过；迁移与 create_all 建表后再安装。
        return
    if conn.dialect.name == "postgresql":
        _pg_install(conn)
    else:
        _sqlite_install(conn)


def drop_suppliers_guards(conn: Connection) -> None:
    inspector = inspect(conn)
    existing = set(inspector.get_table_names())
    is_postgres = conn.dialect.name == "postgresql"
    for table in TRANSITIONS:
        if table in existing:
            suffix = f" ON {table}" if is_postgres else ""
            conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_{table}_guard{suffix}")
    if LEDGER_TABLE in existing:
        for event in ("update", "delete"):
            suffix = f" ON {LEDGER_TABLE}" if is_postgres else ""
            conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS trg_{LEDGER_TABLE}_{event}{suffix}")
    if is_postgres:
        for table in TRANSITIONS:
            conn.exec_driver_sql(f"DROP FUNCTION IF EXISTS ddp_{table}_guard() CASCADE")
        conn.exec_driver_sql("DROP FUNCTION IF EXISTS ddp_ledger_append_only() CASCADE")
