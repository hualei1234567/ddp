"""数据库兜底约束：目标投影完整性与已校验标定不可变。

设计依据：契约冻结 v1 §8.4、§10。生产环境由 Alembic 0004 安装这些对象；
测试使用 ``Base.metadata.create_all`` 时由 ``after_create`` 事件安装同一份 DDL，
避免测试库与迁移后的库出现行为漂移。

方言差异：
- PostgreSQL 给 ``calibration_records`` 添加真实复合外键，并用 plpgsql 触发器
  拦截已校验记录的溯源字段/非法状态直写；
- SQLite 不支持给既有表追加外键，因此用等价触发器校验目标投影行；不可变
  规则同样使用触发器实现。SQLite 仅用于测试，不作为 PostgreSQL 的验收替代。

任何规则调整都必须新增迁移；禁止原地修改本文件来改变已发布历史的行为。
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

_FROZEN_STATES = "'VALIDATED', 'ACTIVE', 'SUPERSEDED', 'REVOKED', 'EXPIRED'"

# 已校验记录冻结的溯源字段。valid_to 是有效期，现有仓储允许在提交后调整，
# 因此不在此列表内（与契约 §5.4 的差异需由架构评审另行收敛）。
_FROZEN_COLUMNS = (
    "id",
    "project_id",
    "profile_id",
    "scope",
    "target_type",
    "target_id",
    "calibration_type",
    "method",
    "parameters",
    "parent_frame",
    "child_frame",
    "valid_from",
    "evidence_asset_ids",
    "source",
    "checksum",
    "uncertainty",
    "performed_by",
    "created_at",
    "created_by",
    "validated_by",
    "validated_at",
)
# PostgreSQL 的 json 类型没有等号运算符；这些列必须转 text 后再比较。
_PG_JSON_COLUMNS = frozenset({"parameters", "evidence_asset_ids", "uncertainty"})

_SQLITE_TARGET_GUARD_STATEMENTS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_calibration_records_target_guard_insert
    BEFORE INSERT ON calibration_records
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1 FROM calibration_target_refs ref
        WHERE ref.project_id = NEW.project_id
          AND ref.target_type = NEW.target_type
          AND ref.target_id = NEW.target_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'calibration_target_invalid');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_calibration_records_target_guard_update
    BEFORE UPDATE ON calibration_records
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1 FROM calibration_target_refs ref
        WHERE ref.project_id = NEW.project_id
          AND ref.target_type = NEW.target_type
          AND ref.target_id = NEW.target_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'calibration_target_invalid');
    END
    """,
)

_SQLITE_IMMUTABLE_STATEMENTS = (
    f"""
    CREATE TRIGGER IF NOT EXISTS trg_calibration_records_immutable_update
    BEFORE UPDATE ON calibration_records
    FOR EACH ROW
    WHEN OLD.status IN ({_FROZEN_STATES})
      AND (
        {" OR ".join(f"OLD.{column} IS NOT NEW.{column}" for column in _FROZEN_COLUMNS)}
        OR (
            OLD.status <> NEW.status
            AND NOT (
                (OLD.status = 'VALIDATED' AND NEW.status = 'ACTIVE')
                OR (
                    OLD.status = 'ACTIVE'
                    AND NEW.status IN ('SUPERSEDED', 'REVOKED', 'EXPIRED')
                )
            )
        )
        OR (
            OLD.supersedes_id IS NOT NEW.supersedes_id
            AND NOT (
                OLD.status = 'VALIDATED'
                AND NEW.status = 'ACTIVE'
                AND OLD.supersedes_id IS NULL
            )
        )
      )
    BEGIN
        SELECT RAISE(ABORT, 'calibration_record_immutable');
    END
    """,
)


def _postgres_function_sql() -> str:
    comparisons = []
    for column in _FROZEN_COLUMNS:
        if column in _PG_JSON_COLUMNS:
            comparisons.append(f"OLD.{column}::text IS DISTINCT FROM NEW.{column}::text")
        else:
            comparisons.append(f"OLD.{column} IS DISTINCT FROM NEW.{column}")
    frozen = " OR ".join(comparisons)
    return f"""
    CREATE OR REPLACE FUNCTION ddp_calibration_records_immutable_guard()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF OLD.status IN ({_FROZEN_STATES}) THEN
            IF {frozen} THEN
                RAISE EXCEPTION 'calibration_record_immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.status IS DISTINCT FROM NEW.status
               AND NOT (
                    (OLD.status = 'VALIDATED' AND NEW.status = 'ACTIVE')
                    OR (
                        OLD.status = 'ACTIVE'
                        AND NEW.status IN ('SUPERSEDED', 'REVOKED', 'EXPIRED')
                    )
               ) THEN
                RAISE EXCEPTION 'calibration_record_state_transition_invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.supersedes_id IS DISTINCT FROM NEW.supersedes_id
               AND NOT (
                    OLD.status = 'VALIDATED'
                    AND NEW.status = 'ACTIVE'
                    AND OLD.supersedes_id IS NULL
               ) THEN
                RAISE EXCEPTION 'calibration_record_immutable'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$;
    """


# --- ADR-0020 / G-07：标定 Profile 行不可变 -----------------------------------
# Profile 是"配置的某一版本"，一旦写入就不该被改：改配置的正确做法是插入
# 新版本行。没有这条约束时，直接 SQL/ORM 改一行就能让历史记录的
# profile_version 指向一段已经不同的配置，谱系不可信。
_SQLITE_PROFILE_IMMUTABLE = """
    CREATE TRIGGER IF NOT EXISTS trg_calibration_profiles_immutable_update
    BEFORE UPDATE ON calibration_profiles
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'calibration_profile_immutable');
    END
"""

_PG_PROFILE_FUNCTION = """
    CREATE OR REPLACE FUNCTION ddp_calibration_profiles_immutable_guard()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION 'calibration_profile_immutable' USING ERRCODE = '23514';
    END;
    $$;
"""


_PG_PROFILE_DELETE_FUNCTION = """
    CREATE OR REPLACE FUNCTION ddp_calibration_profiles_delete_guard()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION 'calibration_profile_delete_forbidden' USING ERRCODE = '23514';
    END;
    $$;
"""


def install_calibration_profile_guards(connection: Connection) -> None:
    """禁止 UPDATE/DELETE ``calibration_profiles``。

    改配置的正确做法是插入新版本行；删除会让历史记录的 ``profile_id`` 悬空。
    两个动作都必须在数据库层拦——应用层仓储已经只做 insert，
    但直接 SQL 或其它服务不会遵守这个约定。
    """
    if not _has_table(connection, "calibration_profiles"):
        return
    dialect = connection.dialect.name
    if dialect == "sqlite":
        connection.execute(text(_SQLITE_PROFILE_IMMUTABLE))
        connection.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS trg_calibration_profiles_no_delete
                BEFORE DELETE ON calibration_profiles
                FOR EACH ROW
                BEGIN
                    SELECT RAISE(ABORT, 'calibration_profile_delete_forbidden');
                END
                """
            )
        )
    elif dialect == "postgresql":
        connection.execute(text(_PG_PROFILE_FUNCTION))
        connection.execute(
            text(
                "CREATE TRIGGER trg_calibration_profiles_immutable_update "
                "BEFORE UPDATE ON calibration_profiles "
                "FOR EACH ROW EXECUTE FUNCTION ddp_calibration_profiles_immutable_guard()"
            )
        )
        connection.execute(text(_PG_PROFILE_DELETE_FUNCTION))
        connection.execute(
            text(
                "CREATE TRIGGER trg_calibration_profiles_no_delete "
                "BEFORE DELETE ON calibration_profiles "
                "FOR EACH ROW EXECUTE FUNCTION ddp_calibration_profiles_delete_guard()"
            )
        )


def drop_calibration_profile_guards(connection: Connection) -> None:
    dialect = connection.dialect.name
    if dialect == "sqlite":
        for name in (
            "trg_calibration_profiles_immutable_update",
            "trg_calibration_profiles_no_delete",
        ):
            connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
    elif dialect == "postgresql":
        connection.execute(
            text(
                "DROP TRIGGER IF EXISTS trg_calibration_profiles_immutable_update "
                "ON calibration_profiles"
            )
        )
        connection.execute(
            text(
                "DROP TRIGGER IF EXISTS trg_calibration_profiles_no_delete "
                "ON calibration_profiles"
            )
        )
        connection.execute(
            text("DROP FUNCTION IF EXISTS ddp_calibration_profiles_immutable_guard()")
        )
        connection.execute(text("DROP FUNCTION IF EXISTS ddp_calibration_profiles_delete_guard()"))


def _has_table(connection: Connection, table_name: str) -> bool:
    return inspect(connection).has_table(table_name)


def install_calibration_target_guards(connection: Connection) -> None:
    """安装目标投影校验；PostgreSQL 由真实复合外键承担，无需触发器。"""
    if connection.dialect.name != "sqlite":
        return
    if not (
        _has_table(connection, "calibration_records")
        and _has_table(connection, "calibration_target_refs")
    ):
        return
    for statement in _SQLITE_TARGET_GUARD_STATEMENTS:
        connection.exec_driver_sql(statement)


def drop_calibration_target_guards(connection: Connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    connection.exec_driver_sql("DROP TRIGGER IF EXISTS trg_calibration_records_target_guard_update")
    connection.exec_driver_sql("DROP TRIGGER IF EXISTS trg_calibration_records_target_guard_insert")


def install_calibration_record_guards(connection: Connection) -> None:
    """安装已校验标定不可变触发器。"""
    if not _has_table(connection, "calibration_records"):
        return
    if connection.dialect.name == "sqlite":
        for statement in _SQLITE_IMMUTABLE_STATEMENTS:
            connection.exec_driver_sql(statement)
        return
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(_postgres_function_sql())
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS trg_calibration_records_immutable_update "
            "ON calibration_records"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER trg_calibration_records_immutable_update "
            "BEFORE UPDATE ON calibration_records "
            "FOR EACH ROW EXECUTE FUNCTION ddp_calibration_records_immutable_guard()"
        )
        return
    raise RuntimeError(f"未支持的数据库方言：{connection.dialect.name}")


def drop_calibration_record_guards(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS trg_calibration_records_immutable_update"
        )
        return
    if connection.dialect.name == "postgresql":
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS trg_calibration_records_immutable_update "
            "ON calibration_records"
        )
        connection.exec_driver_sql(
            "DROP FUNCTION IF EXISTS ddp_calibration_records_immutable_guard()"
        )
        return
    raise RuntimeError(f"未支持的数据库方言：{connection.dialect.name}")
