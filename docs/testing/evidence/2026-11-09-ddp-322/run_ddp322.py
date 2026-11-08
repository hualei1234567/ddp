#!/usr/bin/env python3
"""DDP-322 阶段 3 缺陷整改复验探针（基线 26cef24）。

约束：
- 只读业务代码，不修改 backend/app、migrations、packages/contracts；
- 在本地真实 PostgreSQL 16 创建一次性数据库；
- 通过 Alembic 逐级迁移 0011 → 0016，并在真实迁移库上验证 7 个守卫；
- 为验证 0016 对“已处于 0015 的历史库”生效：先把 7 个函数体替换为
  历史缺陷版本（OLD.col <> NEW.col），再执行 0016，确认函数被重装；
- D1 证据全部来自原始迁移库，未把 json/jsonb 列改写；
- D2–D8 使用真实 uvicorn + 真实 HTTP + 同一迁移库；
- 所有结果写入本目录 run.json / summary.log / key-http-evidence.md。

运行：
    backend/.venv/bin/python docs/testing/evidence/2026-11-09-ddp-322/run_ddp322.py

环境变量（均有本地默认值）：
    DDP322_PG_ADMIN  默认 postgresql://ddp:ddp@127.0.0.1:55432/postgres
    DDP322_PORT      默认 8022
    DDP322_DROP_DB   设为 1 时结束后删除一次性数据库（默认保留）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

os.environ["DDP_ENV"] = "local"
os.environ["DDP_LOCAL_HEADER_AUTH"] = "true"
os.environ.pop("DDP_DATABASE_URL", None)

import httpx  # noqa: E402
import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select, text, update  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.application.annotation.commands import (  # noqa: E402
    NewAnnotationArbitration,
    NewAnnotationReview,
    NewGoldSet,
    NewQualitySample,
)
from app.domain.identity.models import ProjectRole  # noqa: E402
from app.infrastructure.db.models.episodes import EpisodeModel  # noqa: E402
from app.infrastructure.db.models.identity import (  # noqa: E402
    Project,
    ProjectMember,
    User,
)

PG_ADMIN = os.environ.get(
    "DDP322_PG_ADMIN",
    "postgresql://ddp:ddp@127.0.0.1:55432/postgres",
)
APP_PORT = int(os.environ.get("DDP322_PORT", "8022"))
DROP_DB = os.environ.get("DDP322_DROP_DB", "0") == "1"
DB_NAME = f"ddp_qa_d322_{os.getpid()}_{int(time.time())}"
PG_DSN = f"postgresql://ddp:ddp@127.0.0.1:55432/{DB_NAME}"
PG_URL = f"postgresql+psycopg://ddp:ddp@127.0.0.1:55432/{DB_NAME}"
BASE_URL = f"http://127.0.0.1:{APP_PORT}"
SHA_ZERO = "sha256:" + "0" * 64

CHECKED_AT = datetime.now(UTC)
checks: list[dict[str, Any]] = []
http_log: list[dict[str, Any]] = []
observations: list[str] = []
migration_steps: list[dict[str, Any]] = []
d1_results: list[dict[str, Any]] = []
ids: dict[str, UUID] = {}

client: httpx.Client | None = None
server_process: subprocess.Popen[str] | None = None


# ---------------------------------------------------------------------------
# 记录
# ---------------------------------------------------------------------------
def check(
    area: str,
    step: str,
    ok: bool,
    expected: object,
    actual: object,
    *,
    severity: str = "blocker",
    note: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> bool:
    item: dict[str, Any] = {
        "area": area,
        "step": step,
        "status": "PASS" if ok else "FAIL",
        "expected": expected,
        "actual": actual,
        "severity": severity,
        "at": datetime.now(UTC).isoformat(),
    }
    if note:
        item["note"] = note
    if evidence:
        item["evidence"] = evidence
    checks.append(item)
    return ok


def observe(message: str) -> None:
    observations.append(message)


def expect_status(
    area: str,
    step: str,
    response: httpx.Response,
    expected: int | tuple[int, ...],
    *,
    severity: str = "blocker",
) -> bool:
    allowed = (expected,) if isinstance(expected, int) else expected
    body = _safe_json(response)
    return check(
        area,
        step,
        response.status_code in allowed,
        list(allowed),
        response.status_code,
        severity=severity,
        evidence={"body": body, "request_id": response.headers.get("X-Request-Id")},
    )


def expect_code(
    area: str,
    step: str,
    response: httpx.Response,
    code: str,
    *,
    severity: str = "blocker",
) -> bool:
    body = _safe_json(response)
    actual = ((body or {}).get("error") or {}).get("code")
    return check(
        area,
        step,
        actual == code,
        code,
        actual,
        severity=severity,
        evidence={"body": body, "request_id": response.headers.get("X-Request-Id")},
    )


def expect_eq(
    area: str,
    step: str,
    expected: object,
    actual: object,
    *,
    severity: str = "blocker",
) -> bool:
    return check(area, step, expected == actual, expected, actual, severity=severity)


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return {"_non_json_body": response.text[:2000]}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def request(
    area: str,
    step: str,
    user: str,
    method: str,
    path: str,
    *,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    global client
    assert client is not None
    merged: dict[str, str] = {"X-User-Id": str(ids[user])}
    if headers:
        merged.update(headers)
    started = time.perf_counter()
    response = client.request(method, path, json=json_body, headers=merged)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    http_log.append(
        {
            "area": area,
            "step": step,
            "user": user,
            "method": method,
            "path": path,
            "status": response.status_code,
            "elapsed_ms": elapsed_ms,
            "request_id": response.headers.get("X-Request-Id"),
            "request": json_body,
            "response": _safe_json(response),
        }
    )
    return response


def ifmatch(version: int) -> dict[str, str]:
    return {"If-Match": f'"{version}"'}


def url(project: str, suffix: str) -> str:
    return f"/api/v1/projects/{ids[project]}{suffix}"


def pid(name: str) -> str:
    return str(ids[name])


# ---------------------------------------------------------------------------
# PostgreSQL / Alembic
# ---------------------------------------------------------------------------
def pg_admin_execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    with psycopg.connect(PG_ADMIN, autocommit=True) as connection:
        connection.execute(sql, params)


def create_database() -> None:
    pg_admin_execute(f'CREATE DATABASE "{DB_NAME}"')


def drop_database() -> None:
    try:
        pg_admin_execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (DB_NAME,),
        )
        pg_admin_execute(f'DROP DATABASE IF EXISTS "{DB_NAME}"')
    except Exception as exc:  # noqa: BLE001
        observe(f"删除一次性数据库失败：{exc}")


def alembic_config() -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", PG_URL)
    return config


def current_revision() -> str | None:
    with psycopg.connect(PG_DSN) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None


def annotation_table_count() -> int:
    with psycopg.connect(PG_DSN) as connection:
        row = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name LIKE 'annotation_%'"
        ).fetchone()
        return int(row[0])


def trigger_inventory() -> list[dict[str, str]]:
    with psycopg.connect(PG_DSN) as connection:
        rows = connection.execute(
            """
            SELECT c.relname, t.tgname
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE NOT t.tgisinternal AND c.relname LIKE 'annotation_%'
            ORDER BY c.relname, t.tgname
            """
        ).fetchall()
    return [{"table": row[0], "trigger": row[1]} for row in rows]


def annotation_json_columns() -> list[dict[str, str]]:
    with psycopg.connect(PG_DSN) as connection:
        rows = connection.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name LIKE 'annotation_%'
              AND data_type IN ('json', 'jsonb')
            ORDER BY table_name, column_name
            """
        ).fetchall()
    return [{"table": row[0], "column": row[1], "type": row[2]} for row in rows]


GUARD_FUNCTIONS = (
    "ddp_annotation_submission_guard",
    "ddp_annotation_record_guard",
    "ddp_annotation_review_guard",
    "ddp_annotation_arbitration_guard",
    "ddp_annotation_gold_set_guard",
    "ddp_annotation_quality_sample_guard",
    "ddp_annotation_iaa_report_guard",
)


def guard_function_sources() -> dict[str, str]:
    with psycopg.connect(PG_DSN) as connection:
        rows = connection.execute(
            "SELECT proname, prosrc FROM pg_proc WHERE proname = ANY(%s) ORDER BY proname",
            (list(GUARD_FUNCTIONS),),
        ).fetchall()
    return {row[0]: row[1] or "" for row in rows}


def guard_source_summary() -> dict[str, Any]:
    sources = guard_function_sources()
    return {
        name: {
            "has_cast": "CAST(OLD." in source and "AS TEXT" in source,
            "legacy_json_compare_lines": [
                line.strip()
                for line in source.splitlines()
                if re.search(r"OLD\.[a-z_]+ <> NEW\.[a-z_]+", line)
            ],
            "length": len(source),
        }
        for name, source in sources.items()
    }


ANNOTATION_TRIGGER_COUNTS = {"0012": 9, "0013": 23, "0014": 42, "0015": 58, "0016": 58}


def inject_legacy_guard_functions() -> list[str]:
    """模拟“已处于 0015 的历史库”：把 7 个守卫函数体替换为旧缺陷版本。"""
    from app.infrastructure.db import annotation_quality_guards as quality
    from app.infrastructure.db import annotation_result_guards as result

    sqls = {
        "ddp_annotation_submission_guard": result._SUBMISSION_FUNCTION_SQL,
        "ddp_annotation_record_guard": result._RECORD_FUNCTION_SQL,
        "ddp_annotation_review_guard": result._REVIEW_FUNCTION_SQL,
        "ddp_annotation_arbitration_guard": result._ARBITRATION_FUNCTION_SQL,
        "ddp_annotation_gold_set_guard": quality._GOLD_SET_FUNCTION_SQL,
        "ddp_annotation_quality_sample_guard": quality._QUALITY_SAMPLE_FUNCTION_SQL,
        "ddp_annotation_iaa_report_guard": quality._IAA_REPORT_FUNCTION_SQL,
    }
    pattern = re.compile(r"CAST\(OLD\.(\w+) AS TEXT\) <> CAST\(NEW\.(\w+) AS TEXT\)")
    injected: list[str] = []
    with psycopg.connect(PG_DSN) as connection:
        for name, sql in sqls.items():
            legacy = pattern.sub(lambda m: f"OLD.{m.group(1)} <> NEW.{m.group(2)}", sql)
            if legacy == sql:
                raise RuntimeError(f"未能构造 {name} 的历史缺陷函数体")
            connection.execute(legacy)
            injected.append(name)
        connection.commit()
    return injected


def migration_probe() -> None:
    config = alembic_config()
    command.upgrade(config, "0011")
    migration_steps.append(
        {
            "action": "upgrade",
            "revision": "0011",
            "actual": current_revision(),
            "tables": annotation_table_count(),
            "triggers": len(trigger_inventory()),
        }
    )
    for revision in ("0012", "0013", "0014", "0015"):
        command.upgrade(config, revision)
        inventory = trigger_inventory()
        expected = ANNOTATION_TRIGGER_COUNTS[revision]
        check(
            "A9",
            f"迁移升级到 {revision}",
            current_revision() == revision and len(inventory) == expected,
            {"revision": revision, "triggers": expected},
            {
                "revision": current_revision(),
                "triggers": len(inventory),
                "tables": annotation_table_count(),
            },
        )
        migration_steps.append(
            {
                "action": "upgrade",
                "revision": revision,
                "actual": current_revision(),
                "tables": annotation_table_count(),
                "triggers": inventory,
            }
        )

    json_columns = annotation_json_columns()
    json_types = sorted({item["type"] for item in json_columns})
    check(
        "D1",
        "迁移库 JSON 列保持原始 json 类型（未临时改 jsonb）",
        json_types == ["json"],
        ["json"],
        {"types": json_types, "columns": json_columns},
    )
    observe(f"迁移库 annotation_* JSON 列共 {len(json_columns)} 个，类型 {json_types}。")

    # 全新迁移时 0014/0015 用当前代码安装守卫，函数体已是 CAST 版本。
    before = guard_source_summary()
    migration_steps.append(
        {"action": "guard_source_after_0015", "functions": before}
    )

    # 模拟历史库：替换为旧缺陷函数体，再执行 0016 重装。
    injected = inject_legacy_guard_functions()
    legacy_sources = guard_source_summary()
    legacy_lines = sum(
        len(item["legacy_json_compare_lines"]) for item in legacy_sources.values()
    )
    check(
        "D1",
        "模拟 0015 历史库的守卫函数体存在 OLD!=NEW JSON 比较",
        legacy_lines > 0,
        ">0 处旧式 JSON <> 比较",
        legacy_lines,
        note="用于证明 0016 会对历史库重装函数体",
    )
    migration_steps.append(
        {
            "action": "legacy_guards_injected",
            "functions": injected,
            "source": legacy_sources,
        }
    )

    command.upgrade(config, "0016")
    after = guard_source_summary()
    all_cast = all(item["has_cast"] for item in after.values())
    remaining_legacy = sum(
        len(item["legacy_json_compare_lines"]) for item in after.values()
    )
    check(
        "D1",
        "0016 重装后 7 个守卫函数均使用 CAST AS TEXT",
        all_cast,
        "7/7 含 CAST(OLD ... AS TEXT)",
        {name: item["has_cast"] for name, item in after.items()},
    )
    check(
        "D1",
        "0016 重装后不再存在 OLD!=NEW JSON 比较",
        remaining_legacy == 0,
        "0 处",
        remaining_legacy,
    )
    migration_steps.append(
        {
            "action": "guard_source_after_0016",
            "functions": after,
            "revision": current_revision(),
        }
    )

    # 0016 downgrade 为 no-op：回退到 0015 后函数体仍为修复版。
    command.downgrade(config, "0015")
    downgraded = guard_source_summary()
    check(
        "A9",
        "0016 降级到 0015 后守卫函数保持修复版（no-op）",
        current_revision() == "0015"
        and all(item["has_cast"] for item in downgraded.values()),
        "revision=0015 且 7/7 含 CAST",
        {
            "revision": current_revision(),
            "has_cast": {name: item["has_cast"] for name, item in downgraded.items()},
        },
    )
    migration_steps.append(
        {
            "action": "downgrade",
            "revision": "0015",
            "actual": current_revision(),
            "triggers": len(trigger_inventory()),
            "functions": downgraded,
        }
    )
    command.upgrade(config, "0016")
    check(
        "A9",
        "重新升级到 head(0016)",
        current_revision() == "0016" and len(trigger_inventory()) == 58,
        {"revision": "0016", "triggers": 58},
        {"revision": current_revision(), "triggers": len(trigger_inventory())},
    )
    migration_steps.append(
        {
            "action": "upgrade",
            "revision": "head",
            "actual": current_revision(),
            "tables": annotation_table_count(),
            "triggers": trigger_inventory(),
        }
    )


# ---------------------------------------------------------------------------
# D1：真实迁移库上的 7 个守卫 JSON UPDATE
# ---------------------------------------------------------------------------
def build_d1_objects() -> dict[str, UUID]:
    """用应用仓储在迁移库中构造 7 类终态对象。"""
    from tests.annotation_results_support import (
        build_stacks,
        make_submission,
        prepare_ready_task,
        seed_identity,
    )

    engine = create_engine(PG_URL)
    factory: sessionmaker[Session] = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )
    row_ids: dict[str, UUID] = {}
    with factory() as session:
        seed = seed_identity(session)
        stacks = build_stacks(session, seed)
        prepare_ready_task(stacks, two_annotators=False)
        submission = make_submission(stacks, stacks.scope_a, stacks.assignment_a)

        quality = stacks.quality_samples.create_one(
            stacks.scope_b,
            NewQualitySample(
                task_id=stacks.task_id,
                submission_id=submission.submission_id,
                annotator_id=seed.user_a,
                sampling_policy_version="quality/1",
                sample_type="RANDOM",
                sample_reason="D1 守卫探针",
            ),
            path="/projects/p/quality-samples",
        )
        quality = stacks.quality_samples.apply_action(
            stacks.scope_b,
            quality.quality_sample_id,
            "cancel",
            expected_version=quality.version,
            reason="D1 探针取消",
            path="/projects/p/quality-samples/x/actions/cancel",
        )

        iaa = stacks.iaa.compute(
            stacks.scope_a,
            stacks.task_id,
            metric_definitions_version="ddp.stage3.iaa/1",
            path="/projects/p/iaa-reports",
        )

        review = stacks.reviews.create(
            stacks.scope_c,
            NewAnnotationReview(
                submission_id=submission.submission_id,
                reviewer_id=seed.user_c,
            ),
            path="/projects/p/annotation-submissions/x/reviews",
        )
        review = stacks.reviews.apply_action(
            stacks.scope_c,
            review.review_id,
            "claim",
            expected_version=review.version,
            path="/projects/p/annotation-reviews/x/actions/claim",
        )
        review = stacks.reviews.apply_action(
            stacks.scope_c,
            review.review_id,
            "escalate",
            expected_version=review.version,
            reason="标注人与复核人意见不一致",
            path="/projects/p/annotation-reviews/x/actions/escalate",
        )

        arbitration = stacks.arbitrations.create(
            stacks.scope_b,
            NewAnnotationArbitration(
                submission_id=submission.submission_id,
                arbiter_ids=(seed.user_b,),
                quorum=1,
                trigger="REVIEWER_DISAGREEMENT",
                review_id=review.review_id,
            ),
            path="/projects/p/annotation-arbitrations",
        )
        arbitration = stacks.arbitrations.apply_action(
            stacks.scope_b,
            arbitration.arbitration_id,
            "cancel",
            expected_version=arbitration.version,
            reason="D1 探针取消",
            path="/projects/p/annotation-arbitrations/x/actions/cancel",
        )

        gold_set = stacks.gold_sets.create(
            stacks.scope_a,
            NewGoldSet(
                code=f"d1-gold-{uuid4().hex[:8]}",
                schema_id=stacks.schema_id,
                items=(
                    {
                        "gold_item_id": str(uuid4()),
                        "episode_id": str(seed.episode_a),
                        "expected_labels": [{"label_key": "action", "value": "PICK"}],
                    },
                ),
            ),
            path="/projects/p/gold-sets",
        )
        gold_set = stacks.gold_sets.apply_action(
            stacks.scope_a,
            gold_set.gold_set_id,
            "revoke",
            expected_version=gold_set.version,
            reason="D1 探针撤销",
            path="/projects/p/gold-sets/x/actions/revoke",
        )

        record_row = session.execute(
            text(
                "SELECT id FROM annotation_records "
                "WHERE submission_id = :sid LIMIT 1"
            ),
            {"sid": submission.submission_id},
        ).fetchone()

        row_ids = {
            "submission": submission.submission_id,
            "record": record_row[0] if record_row else UUID(int=0),
            "review": review.review_id,
            "arbitration": arbitration.arbitration_id,
            "gold_set": gold_set.gold_set_id,
            "quality_sample": quality.quality_sample_id,
            "iaa_report": iaa.report_id,
        }
        check(
            "D1",
            "迁移库中成功构造 7 类标注对象",
            all(row_ids.values()),
            "7 类对象均存在",
            {name: str(value) for name, value in row_ids.items()},
        )
        session.commit()
    engine.dispose()
    return row_ids


_D1_GUARDS: tuple[tuple[str, str, str, str, str], ...] = (
    ("submission", "annotation_submissions", "record_refs", "id", "annotation_submission_immutable"),
    ("record", "annotation_records", "labels", "id", "annotation_record_immutable"),
    ("review", "annotation_reviews", "issues", "id", "annotation_review_immutable"),
    ("arbitration", "annotation_arbitrations", "votes", "id", "annotation_arbitration_immutable"),
    ("gold_set", "annotation_gold_sets", "items", "id", "annotation_gold_set_immutable"),
    ("quality_sample", "annotation_quality_samples", "item_refs", "id", "annotation_quality_sample_immutable"),
    ("iaa_report", "annotation_iaa_reports", "metrics", "id", "annotation_iaa_report_immutable"),
)
_D1_VALUES = {
    "submission": '["tampered"]',
    "record": '[{"label_key": "action", "value": "PLACE"}]',
    "review": '[{"code": "tampered"}]',
    "arbitration": '[{"arbiter_id": "tampered", "decision": "APPROVE"}]',
    "gold_set": "[]",
    "quality_sample": '["tampered"]',
    "iaa_report": '[{"metric": "COHEN_KAPPA", "value": 1.0}]',
}


def d1_json_update_probe(row_ids: dict[str, UUID]) -> None:
    for name, table, column, id_column, expected_message in _D1_GUARDS:
        row_id = row_ids[name]
        sql = (
            f'UPDATE {table} SET "{column}" = %s::json '
            f'WHERE "{id_column}" = %s'
        )
        result: dict[str, Any] = {
            "name": name,
            "table": table,
            "column": column,
            "row_id": str(row_id),
            "expected_message": expected_message,
        }
        try:
            with psycopg.connect(PG_DSN) as connection:
                connection.execute(sql, (_D1_VALUES[name], row_id))
                connection.commit()
            result.update(
                {
                    "status": "FAIL",
                    "actual": "UPDATE 未报错（不可变守卫未触发）",
                }
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            operator_error = "operator does not exist" in message
            business_error = expected_message in message
            result.update(
                {
                    "status": "PASS" if business_error and not operator_error else "FAIL",
                    "exception": type(exc).__name__,
                    "actual": message.splitlines()[0] if message else "",
                    "operator_error": operator_error,
                    "business_error": business_error,
                }
            )
        d1_results.append(result)
        check(
            "D1",
            f"{name} 守卫对 JSON 列 {table}.{column} UPDATE 返回业务错误",
            result["status"] == "PASS",
            f"业务错误 {expected_message}，且无 operator does not exist",
            result["actual"],
            evidence={
                "table": table,
                "column": column,
                "operator_error": result.get("operator_error"),
                "business_error": result.get("business_error"),
            },
        )


# ---------------------------------------------------------------------------
# HTTP 数据种子
# ---------------------------------------------------------------------------
def seed_http_data() -> None:
    engine = create_engine(PG_URL)
    factory: sessionmaker[Session] = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )
    now = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    for name in (
        "project_a",
        "project_b",
        "owner_a",
        "an1",
        "an2",
        "viewer_a",
        "arbiter_a",
        "outsider",
        "owner_b",
    ):
        ids[name] = uuid4()

    with factory() as session:
        session.add_all(
            [
                Project(id=ids["project_a"], code=f"qa322-a-{uuid4().hex[:8]}", name="QA322 项目 A"),
                Project(id=ids["project_b"], code=f"qa322-b-{uuid4().hex[:8]}", name="QA322 项目 B"),
                User(id=ids["owner_a"], display_name="项目 A 负责人"),
                User(id=ids["an1"], display_name="标注员 1"),
                User(id=ids["an2"], display_name="标注员 2（兼 AE）"),
                User(id=ids["viewer_a"], display_name="只读用户 RO"),
                User(id=ids["arbiter_a"], display_name="仲裁员"),
                User(id=ids["outsider"], display_name="项目外用户"),
                User(id=ids["owner_b"], display_name="项目 B 负责人"),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMember(project_id=ids["project_a"], user_id=ids["owner_a"], role=ProjectRole.OWNER.value),
                ProjectMember(project_id=ids["project_a"], user_id=ids["an1"], role=ProjectRole.EDITOR.value),
                ProjectMember(project_id=ids["project_a"], user_id=ids["an2"], role=ProjectRole.EDITOR.value),
                ProjectMember(project_id=ids["project_a"], user_id=ids["viewer_a"], role=ProjectRole.VIEWER.value),
                ProjectMember(project_id=ids["project_a"], user_id=ids["arbiter_a"], role=ProjectRole.EDITOR.value),
                ProjectMember(project_id=ids["project_b"], user_id=ids["owner_b"], role=ProjectRole.OWNER.value),
            ]
        )
        session.commit()

    episodes = {
        "ep_pass": ("EP-A-PASS", "ACCEPTED", "PASS"),
        "ep_pass2": ("EP-A-PASS2", "ACCEPTED", "PASS"),
        "ep_warn": ("EP-A-WARN", "COMPLETED", "WARN"),
        "ep_rejected": ("EP-A-REJECTED", "REJECTED", "FAIL"),
        "ep_invalid": ("EP-A-INVALID", "INVALIDATED", "NOT_CHECKED"),
        "ep_archived": ("EP-A-ARCHIVED", "ARCHIVED", "PASS"),
        "ep_b": ("EP-B-PASS", "ACCEPTED", "PASS"),
    }
    for name, (code, status, quality) in episodes.items():
        project = ids["project_b"] if name == "ep_b" else ids["project_a"]
        actor = ids["owner_b"] if name == "ep_b" else ids["owner_a"]
        episode_id = uuid4()
        ids[name] = episode_id
        with factory() as session:
            session.add(
                EpisodeModel(
                    id=episode_id,
                    project_id=project,
                    episode_lineage_id=uuid4(),
                    episode_version=1,
                    code=code,
                    status="PENDING",
                    quality_status="NOT_CHECKED",
                    annotation_status="NOT_STARTED",
                    scene_labels=["warehouse"] if name == "ep_b" else ["kitchen"],
                    start_time_ns=1_000,
                    end_time_ns=10_000,
                    duration_ns=9_000,
                    time_domain="UTC",
                    time_base={},
                    canonical_schema_version="ddp.canonical/1.0.0-draft",
                    pipeline_id="qa322-probe",
                    pipeline_version="1.0.0",
                    segmentation_rule_version="seg-v1",
                    calibration_snapshot=[],
                    manifest_key=f"canonical/{project}/{episode_id}/1/manifest.json",
                    manifest_sha256=f"sha256:{uuid4().hex * 2}",
                    frame_count=10,
                    attempt=1,
                    max_attempts=3,
                    created_at=now,
                    updated_at=now,
                    created_by=actor,
                    updated_by=actor,
                )
            )
            session.commit()

        def step(values: dict[str, Any]) -> None:
            with factory() as session:
                session.execute(
                    update(EpisodeModel)
                    .where(EpisodeModel.id == episode_id)
                    .values(**values)
                )
                session.commit()

        if status == "INVALIDATED":
            step({"status": "INVALIDATED"})
            continue
        step({"status": "PROCESSING"})
        step({"status": "READY"})
        step({"status": "QUALITY_CHECKING"})
        step({"quality_status": quality})
        if status == "REJECTED":
            step({"status": "REJECTED"})
            continue
        step({"status": "ACCEPTED"})
        if status in {"COMPLETED", "ARCHIVED"}:
            step({"status": "COMPLETED"})
        if status == "ARCHIVED":
            step({"status": "ARCHIVED"})
    engine.dispose()


# ---------------------------------------------------------------------------
# 服务启动
# ---------------------------------------------------------------------------
def start_server() -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.update(
        {
            "DDP_ENV": "local",
            "DDP_LOCAL_HEADER_AUTH": "true",
            "DDP_DATABASE_URL": PG_URL,
            "DDP_RATE_LIMIT_ENABLED": "false",
            "DDP_LOG_LEVEL": "WARNING",
            "DDP_JWT_SECRET": "qa322-local-secret-not-for-production",
        }
    )
    log_path = EVIDENCE / "server.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(APP_PORT),
            "--log-level",
            "warning",
        ],
        cwd=str(BACKEND),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 40
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"uvicorn 提前退出，见 {log_path}")
        try:
            response = httpx.get(f"{BASE_URL}/health", timeout=1.0)
            if response.status_code == 200:
                return process
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    process.terminate()
    raise RuntimeError(f"uvicorn 未能启动，见 {log_path}")


# ---------------------------------------------------------------------------
# HTTP 场景
# ---------------------------------------------------------------------------
SCHEMA_LABELS = [
    {
        "label_key": "action",
        "label_type": "ENUM",
        "required": True,
        "level": "EPISODE",
        "value_spec": {"enum_values": ["PICK", "PLACE"]},
    },
    {
        "label_key": "segment",
        "label_type": "TIME_RANGE",
        "required": False,
        "level": "EPISODE",
        "value_spec": {"min_duration_ns": 1},
    },
]


def schema_payload(code: str, *, name: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "name": name or f"标签 {code}",
        "annotation_levels": ["EPISODE"],
        "labels": SCHEMA_LABELS,
    }


def record_payload(episode_name: str) -> dict[str, Any]:
    return {
        "episode_id": pid(episode_name),
        "episode_version": 1,
        "labels": [
            {
                "label_key": "action",
                "value": "PICK",
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            },
            {
                "label_key": "segment",
                "value": {
                    "start_time_ns": 1_100,
                    "end_time_ns": 1_500,
                    "time_domain": "UTC",
                },
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            },
        ],
    }


def run_http_scenarios() -> None:
    assert client is not None
    health = client.get(f"{BASE_URL}/health")
    expect_status("A10", "健康检查", health, 200)

    # ------------------------------------------------------------------
    # A1 / D7 Schema
    # ------------------------------------------------------------------
    created = request(
        "A1", "创建 Schema", "an1", "POST", url("project_a", "/annotation-schemas"),
        json_body=schema_payload("qa322-schema"),
    )
    expect_status("A1", "创建 Schema", created, 201)
    schema_v1 = created.json()
    ids["schema_main"] = UUID(schema_v1["schema_id"])
    expect_eq("A1", "初始状态 DRAFT", "DRAFT", schema_v1["status"])

    patched = request(
        "A1", "DRAFT 修改定义", "an1", "PATCH",
        f"/api/v1/projects/{ids['project_a']}/annotation-schemas/{ids['schema_main']}",
        json_body={"name": "标签 qa322-schema（修订）"},
        headers=ifmatch(schema_v1["version"]),
    )
    expect_status("A1", "DRAFT 修改定义", patched, 200)
    schema_patched = patched.json()

    submitted = request(
        "A1", "提交 Schema 审批", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-schemas/{ids['schema_main']}/actions/submit-review",
        json_body={}, headers=ifmatch(schema_patched["version"]),
    )
    expect_status("A1", "提交 Schema 审批", submitted, 200)
    schema_pending = submitted.json()

    published = request(
        "A1", "负责人发布 Schema", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-schemas/{ids['schema_main']}/actions/publish",
        json_body={}, headers=ifmatch(schema_pending["version"]),
    )
    expect_status("A1", "负责人发布 Schema", published, 200)
    schema_published = published.json()
    expect_eq("A1", "发布后状态 PUBLISHED", "PUBLISHED", schema_published["status"])

    patch_published = request(
        "D7", "发布后修改 Schema 定义", "an1", "PATCH",
        f"/api/v1/projects/{ids['project_a']}/annotation-schemas/{ids['schema_main']}",
        json_body={"name": "不应允许"},
        headers=ifmatch(schema_published["version"]),
    )
    expect_status("D7", "发布后修改被拒", patch_published, 409)
    expect_code("D7", "发布后错误码 annotation_schema_immutable", patch_published, "annotation_schema_immutable")

    no_if_match = request(
        "A1", "缺少 If-Match", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-schemas/{ids['schema_main']}/actions/submit-review",
        json_body={},
    )
    expect_status("A1", "缺少 If-Match → 428", no_if_match, 428)

    bad_if_match = request(
        "A1", "非法 If-Match", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-schemas/{ids['schema_main']}/actions/deprecate",
        json_body={"reason": "非法头"}, headers={"If-Match": "abc"},
    )
    expect_status("A1", "非法 If-Match → 422", bad_if_match, 422)

    stale = request(
        "A1", "过期 If-Match", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-schemas/{ids['schema_main']}/actions/deprecate",
        json_body={"reason": "版本冲突"}, headers=ifmatch(1),
    )
    expect_status("A1", "过期 If-Match → 409", stale, 409)
    expect_code("A1", "过期 If-Match 错误码", stale, "version_conflict")

    # 幂等创建
    idem_body = schema_payload("qa322-idem-schema")
    first = request(
        "A10", "Schema 幂等首次创建", "an1", "POST", url("project_a", "/annotation-schemas"),
        json_body=idem_body, headers={"Idempotency-Key": "qa322-idem-schema-0001"},
    )
    expect_status("A10", "Schema 幂等首次创建", first, 201)
    replay = request(
        "A10", "Schema 幂等重放", "an1", "POST", url("project_a", "/annotation-schemas"),
        json_body=idem_body, headers={"Idempotency-Key": "qa322-idem-schema-0001"},
    )
    expect_status("A10", "Schema 幂等重放", replay, 201)
    expect_eq("A10", "相同幂等键返回同一 Schema", first.json()["schema_id"], replay.json()["schema_id"])
    conflict = request(
        "A10", "Schema 同键异体", "an1", "POST", url("project_a", "/annotation-schemas"),
        json_body=schema_payload("qa322-idem-schema", name="不同请求体"),
        headers={"Idempotency-Key": "qa322-idem-schema-0001"},
    )
    expect_status("A10", "Schema 同键异体 → 409", conflict, 409)
    expect_code("A10", "Schema 同键异体错误码", conflict, "idempotency_key_reused")

    # ------------------------------------------------------------------
    # D5 样本集预览排除原因
    # ------------------------------------------------------------------
    sample_created = request(
        "D5", "创建样本集", "an1", "POST", url("project_a", "/annotation-sample-sets"),
        json_body={"code": f"qa322-sample-{uuid4().hex[:6]}", "filters": {"scene_labels": ["kitchen"]}},
    )
    expect_status("D5", "创建样本集", sample_created, 201)
    sample = sample_created.json()
    ids["sample_main"] = UUID(sample["sample_set_id"])
    sample_url = f"/api/v1/projects/{ids['project_a']}/annotation-sample-sets/{ids['sample_main']}"

    preview = request(
        "D5", "样本预览", "an1", "POST", f"{sample_url}/actions/preview",
        json_body={"filters": {"scene_labels": ["kitchen"]}},
        headers=ifmatch(sample["version"]),
    )
    expect_status("D5", "样本预览", preview, 200)
    preview_json = preview.json()
    summary = preview_json.get("exclusion_summary") or {}
    for reason in (
        "episode_status:REJECTED",
        "episode_status:INVALIDATED",
        "episode_status:ARCHIVED",
    ):
        expect_eq("D5", f"exclusion_summary 含 {reason}", 1, summary.get(reason))
    expect_eq("D5", "exclusion_summary 含 WARN 未确认", 1, summary.get("warn_not_confirmed"))
    selected_ids = {item["episode_id"] for item in preview_json["episode_refs"]}
    expect_true_d5 = not (
        {pid("ep_rejected"), pid("ep_invalid"), pid("ep_archived")} & selected_ids
    )
    check(
        "D5", "不合格 Episode 不入选",
        expect_true_d5,
        "REJECTED/INVALIDATED/ARCHIVED 均不入选",
        sorted(selected_ids),
    )

    snapshot = request(
        "D5", "生成样本集快照", "an1", "POST", f"{sample_url}/actions/snapshot",
        json_body={}, headers=ifmatch(preview_json["version"]),
    )
    expect_status("D5", "生成样本集快照", snapshot, 200)
    expect_eq("D5", "快照状态 SNAPSHOTTED", "SNAPSHOTTED", snapshot.json()["status"])

    # ------------------------------------------------------------------
    # A3 任务/批次/分配 + D4 容量
    # ------------------------------------------------------------------
    task_created = request(
        "A3", "创建任务", "an1", "POST", url("project_a", "/annotation-tasks"),
        json_body={
            "code": f"qa322-task-{uuid4().hex[:6]}",
            "name": "QA322 标注任务",
            "sample_set_id": str(ids["sample_main"]),
            "schema_id": str(ids["schema_main"]),
            "schema_version": schema_published["schema_version"],
            "target_item_count": 1,
            "target_episode_count": 1,
            "quality_policy": {"max_rework_rounds": 1},
        },
    )
    expect_status("A3", "创建任务", task_created, 201)
    task = task_created.json()
    ids["task_main"] = UUID(task["task_id"])
    task_url = f"/api/v1/projects/{ids['project_a']}/annotation-tasks/{ids['task_main']}"

    task_submitted = request(
        "A3", "任务提交审批", "an1", "POST", f"{task_url}/actions/submit-review",
        json_body={}, headers=ifmatch(task["version"]),
    )
    expect_status("A3", "任务提交审批", task_submitted, 200)
    task_approved = request(
        "A3", "负责人审批任务", "owner_a", "POST", f"{task_url}/actions/approve",
        json_body={}, headers=ifmatch(task_submitted.json()["version"]),
    )
    expect_status("A3", "负责人审批任务", task_approved, 200)
    task_ready = request(
        "A3", "任务就绪", "an1", "POST", f"{task_url}/actions/ready",
        json_body={}, headers=ifmatch(task_approved.json()["version"]),
    )
    expect_status("A3", "任务就绪", task_ready, 200)
    task_ready_json = task_ready.json()

    batch_created = request(
        "A3", "创建批次", "an1", "POST", f"{task_url}/batches",
        json_body={"planned_item_count": 1, "item_refs": [pid("ep_pass")]},
    )
    expect_status("A3", "创建批次", batch_created, 201)
    batch = batch_created.json()
    ids["batch_main"] = UUID(batch["batch_id"])
    batch_url = f"/api/v1/projects/{ids['project_a']}/annotation-batches/{ids['batch_main']}"
    batch_ready = request(
        "A3", "批次就绪", "an1", "POST", f"{batch_url}/actions/ready",
        json_body={}, headers=ifmatch(batch["version"]),
    )
    expect_status("A3", "批次就绪", batch_ready, 200)
    batch_ready_json = batch_ready.json()

    over = request(
        "D4", "超容量分配", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-batches/{ids['batch_main']}/annotation-assignments",
        json_body={"assignee_id": pid("an2"), "planned_item_count": 51, "planned_minutes": 30},
    )
    expect_status("D4", "超容量分配 → 409", over, 409)
    expect_code("D4", "超容量错误码", over, "annotation_assignment_capacity_exceeded")

    assignment1 = request(
        "A3", "分配标注员 1", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-batches/{ids['batch_main']}/annotation-assignments",
        json_body={"assignee_id": pid("an1"), "planned_item_count": 1, "planned_minutes": 30},
    )
    expect_status("A3", "分配标注员 1", assignment1, 201)
    ids["assignment_an1"] = UUID(assignment1.json()["assignment_id"])
    assignment1_json = assignment1.json()

    task_assigned = request(
        "A3", "任务分配", "an1", "POST", f"{task_url}/actions/assign",
        json_body={}, headers=ifmatch(task_ready_json["version"]),
    )
    expect_status("A3", "任务分配", task_assigned, 200)
    batch_assigned = request(
        "A3", "批次分配", "an1", "POST", f"{batch_url}/actions/assign",
        json_body={}, headers=ifmatch(batch_ready_json["version"]),
    )
    expect_status("A3", "批次分配", batch_assigned, 200)
    claim1 = request(
        "A3", "标注员 1 领取", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-assignments/{ids['assignment_an1']}/actions/claim",
        json_body={}, headers=ifmatch(assignment1_json["version"]),
    )
    expect_status("A3", "标注员 1 领取", claim1, 200)
    task_started = request(
        "A3", "任务开始", "an1", "POST", f"{task_url}/actions/start",
        json_body={}, headers=ifmatch(task_assigned.json()["version"]),
    )
    expect_status("A3", "任务开始", task_started, 200)
    batch_started = request(
        "A3", "批次开始", "an1", "POST", f"{batch_url}/actions/start",
        json_body={}, headers=ifmatch(batch_assigned.json()["version"]),
    )
    expect_status("A3", "批次开始", batch_started, 200)
    start1 = request(
        "A3", "标注员 1 开始", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-assignments/{ids['assignment_an1']}/actions/start",
        json_body={}, headers=ifmatch(claim1.json()["version"]),
    )
    expect_status("A3", "标注员 1 开始", start1, 200)


    # ------------------------------------------------------------------
    # A4 v1 提交 + D8 IAA 单标注者
    # ------------------------------------------------------------------
    sub1_created = request(
        "A4", "创建提交 v1", "an1", "POST", url("project_a", "/annotation-submissions"),
        json_body={
            "assignment_id": str(ids["assignment_an1"]),
            "records": [record_payload("ep_pass")],
            "data_source": "REAL",
        },
    )
    expect_status("A4", "创建提交 v1", sub1_created, 201)
    sub1 = sub1_created.json()
    ids["sub1_v1"] = UUID(sub1["submission_id"])
    expect_eq("A4", "提交版本 1", 1, sub1["submission_version"])
    sub1_submitted = request(
        "A4", "提交 v1", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v1']}/actions/submit",
        json_body={}, headers=ifmatch(sub1["version"]),
    )
    expect_status("A4", "提交 v1", sub1_submitted, 200)
    expect_eq("A4", "提交 v1 状态 SUBMITTED", "SUBMITTED", sub1_submitted.json()["status"])

    iaa_insufficient = request(
        "D8", "IAA 单标注者样本不足", "an1", "POST", url("project_a", "/iaa-reports"),
        json_body={"task_id": str(ids["task_main"]), "metric_definitions_version": "ddp.stage3.iaa/1"},
    )
    expect_status("D8", "IAA 单标注者 → 201", iaa_insufficient, 201)
    iaa_json = iaa_insufficient.json()
    expect_eq("D8", "IAA 状态 INSUFFICIENT_DATA", "INSUFFICIENT_DATA", iaa_json["status"])
    metrics = iaa_json.get("metrics") or []
    check(
        "D8", "IAA 样本不足返回指标 N/A 行",
        len(metrics) > 0,
        ">0 行",
        len(metrics),
        evidence={"metrics": metrics[:6]},
    )
    metric_names = {item.get("metric") for item in metrics}
    expected_metrics = {
        "PERCENT_AGREEMENT",
        "COHEN_KAPPA",
        "FLEISS_KAPPA",
        "KRIPPENDORFF_ALPHA",
        "SPAN_F1",
        "BOUNDARY_MAE",
    }
    check(
        "D8", "N/A 行覆盖全部 6 类指标",
        expected_metrics <= metric_names,
        sorted(expected_metrics),
        sorted(metric_names),
    )
    check(
        "D8", "N/A 行 value 为 null 且状态 INSUFFICIENT_DATA",
        all(item.get("value") is None for item in metrics)
        and all(item.get("status") == "INSUFFICIENT_DATA" for item in metrics),
        "全部 value=null, status=INSUFFICIENT_DATA",
        {
            "values": [item.get("value") for item in metrics],
            "statuses": sorted({item.get("status") for item in metrics}),
        },
    )

    # ------------------------------------------------------------------
    # D2 复核驳回 → require-rework → v2 → 复检
    # ------------------------------------------------------------------
    task_current_for_submit = request("D2", "读取任务当前版本", "an1", "GET", task_url)
    batch_current_for_submit = request("D2", "读取批次当前版本", "an1", "GET", batch_url)
    expect_status("D2", "读取任务当前版本", task_current_for_submit, 200)
    expect_status("D2", "读取批次当前版本", batch_current_for_submit, 200)
    batch_submitted = request(
        "D2", "批次提交反馈审", "an1", "POST", f"{batch_url}/actions/submit",
        json_body={}, headers=ifmatch(batch_current_for_submit.json()["version"]),
    )
    expect_status("D2", "批次提交反馈审", batch_submitted, 200)
    task_submitted2 = request(
        "D2", "任务提交反馈审", "an1", "POST", f"{task_url}/actions/submit",
        json_body={}, headers=ifmatch(task_current_for_submit.json()["version"]),
    )
    expect_status("D2", "任务提交反馈审", task_submitted2, 200)
    batch_reviewing = request(
        "D2", "批次进入 REVIEWING", "an1", "POST", f"{batch_url}/actions/system-review",
        json_body={}, headers=ifmatch(batch_submitted.json()["version"]),
    )
    expect_status("D2", "批次进入 REVIEWING", batch_reviewing, 200)
    expect_eq("D2", "批次状态 REVIEWING", "REVIEWING", batch_reviewing.json()["status"])
    task_reviewing = request(
        "D2", "任务进入 REVIEWING", "an1", "POST", f"{task_url}/actions/system-review",
        json_body={}, headers=ifmatch(task_submitted2.json()["version"]),
    )
    expect_status("D2", "任务进入 REVIEWING", task_reviewing, 200)
    expect_eq("D2", "任务状态 REVIEWING", "REVIEWING", task_reviewing.json()["status"])

    self_review = request(
        "A5", "标注员自审", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v1']}/reviews",
        json_body={"reviewer_id": pid("an1")},
    )
    expect_status("A5", "标注员自审 → 403", self_review, 403)
    expect_code("A5", "自审错误码", self_review, "annotation_review_self_forbidden")

    review1_created = request(
        "D2", "创建复核 round1", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v1']}/reviews",
        json_body={"reviewer_id": pid("owner_a")},
    )
    expect_status("D2", "创建复核 round1", review1_created, 201)
    review1 = review1_created.json()
    ids["review1"] = UUID(review1["review_id"])

    non_reviewer = request(
        "A5", "非复核人领取复核", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-reviews/{ids['review1']}/actions/claim",
        json_body={}, headers=ifmatch(review1["version"]),
    )
    expect_status("A5", "非复核人领取复核 → 403", non_reviewer, 403)

    review1_claimed = request(
        "D2", "复核 round1 领取", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-reviews/{ids['review1']}/actions/claim",
        json_body={}, headers=ifmatch(review1["version"]),
    )
    expect_status("D2", "复核 round1 领取", review1_claimed, 200)
    review1_in = review1_claimed.json()

    reject_no_reason = request(
        "D2", "驳回缺原因", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-reviews/{ids['review1']}/actions/reject",
        json_body={}, headers=ifmatch(review1_in["version"]),
    )
    expect_status("D2", "驳回缺原因 → 422", reject_no_reason, 422)

    rejected = request(
        "D2", "合法驳回", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-reviews/{ids['review1']}/actions/reject",
        json_body={
            "reason": "标签不一致",
            "issues": [
                {
                    "code": "LABEL_MISMATCH",
                    "severity": "MAJOR",
                    "message": "action 标签需修正",
                    "label_key": "action",
                }
            ],
        },
        headers=ifmatch(review1_in["version"]),
    )
    expect_status("D2", "合法驳回", rejected, 200)
    expect_eq("D2", "复核状态 REJECTED", "REJECTED", rejected.json()["status"])

    sub1_current = request(
        "D2", "读取被驳回提交", "an1", "GET",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v1']}",
    )
    expect_status("D2", "读取被驳回提交", sub1_current, 200)
    expect_eq("D2", "提交状态 REJECTED", "REJECTED", sub1_current.json()["status"])

    wrong_rework = request(
        "D3", "非复核人/非 OWNER 发起 require-rework", "an2", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v1']}/actions/require-rework",
        json_body={"reason": "越权返工"},
        headers=ifmatch(sub1_current.json()["version"]),
    )
    expect_status("D3", "非复核人/非 OWNER require-rework → 403", wrong_rework, 403)

    rework = request(
        "D2", "OWNER 发起 require-rework", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v1']}/actions/require-rework",
        json_body={"reason": "复核驳回，进入返工"},
        headers=ifmatch(sub1_current.json()["version"]),
    )
    expect_status("D2", "OWNER 发起 require-rework", rework, 200)
    expect_eq("D2", "提交状态 REWORK_REQUIRED", "REWORK_REQUIRED", rework.json()["status"])

    task_after = request("D2", "读取任务返工状态", "an1", "GET", task_url)
    batch_after = request("D2", "读取批次返工状态", "an1", "GET", batch_url)
    assignment_after = request(
        "D2", "读取分配返工状态", "an1", "GET",
        f"/api/v1/projects/{ids['project_a']}/annotation-assignments/{ids['assignment_an1']}",
    )
    expect_eq("D2", "任务从 REVIEWING 退回 REWORK", "REWORK", task_after.json()["status"])
    expect_eq("D2", "批次从 REVIEWING 退回 REWORK", "REWORK", batch_after.json()["status"])
    expect_eq("D2", "分配退回 IN_PROGRESS", "IN_PROGRESS", assignment_after.json()["status"])
    expect_eq("D2", "批次 rework_count 递增为 1", 1, batch_after.json()["rework_count"])

    sub2_created = request(
        "D2", "创建提交 v2", "an1", "POST", url("project_a", "/annotation-submissions"),
        json_body={
            "assignment_id": str(ids["assignment_an1"]),
            "records": [record_payload("ep_pass")],
            "data_source": "REAL",
        },
    )
    expect_status("D2", "创建提交 v2", sub2_created, 201)
    sub2 = sub2_created.json()
    ids["sub1_v2"] = UUID(sub2["submission_id"])
    expect_eq("D2", "v2 版本号", 2, sub2["submission_version"])
    expect_eq("D2", "v2 supersedes v1", str(ids["sub1_v1"]), sub2["supersedes_id"])
    old_v1 = request(
        "D2", "读取旧版 v1", "an1", "GET",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v1']}",
    )
    expect_eq("D2", "旧版 v1 保留且状态 SUPERSEDED", "SUPERSEDED", old_v1.json()["status"])

    task_resumed = request(
        "D2", "任务 REWORK 恢复", "an1", "POST", f"{task_url}/actions/resume-work",
        json_body={}, headers=ifmatch(task_after.json()["version"]),
    )
    expect_status("D2", "任务 REWORK 恢复", task_resumed, 200)
    batch_resumed = request(
        "D2", "批次 REWORK 恢复", "an1", "POST", f"{batch_url}/actions/resume-work",
        json_body={}, headers=ifmatch(batch_after.json()["version"]),
    )
    expect_status("D2", "批次 REWORK 恢复", batch_resumed, 200)

    sub2_submitted = request(
        "D2", "提交 v2", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v2']}/actions/submit",
        json_body={}, headers=ifmatch(sub2["version"]),
    )
    expect_status("D2", "提交 v2", sub2_submitted, 200)

    review2_created = request(
        "D2", "创建复核 round2", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v2']}/reviews",
        json_body={"reviewer_id": pid("owner_a")},
    )
    expect_status("D2", "创建复核 round2", review2_created, 201)
    review2 = review2_created.json()
    ids["review2"] = UUID(review2["review_id"])
    review2_claimed = request(
        "D2", "复核 round2 领取", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-reviews/{ids['review2']}/actions/claim",
        json_body={}, headers=ifmatch(review2["version"]),
    )
    expect_status("D2", "复核 round2 领取", review2_claimed, 200)
    review2_in = review2_claimed.json()
    approved = request(
        "D2", "复检通过 v2", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-reviews/{ids['review2']}/actions/approve",
        json_body={"checklist": [{"key": "labels", "passed": True}]},
        headers=ifmatch(review2_in["version"]),
    )
    expect_status("D2", "复检通过 v2", approved, 200)
    expect_eq("D2", "复核状态 APPROVED", "APPROVED", approved.json()["status"])
    v2_final = request(
        "D2", "读取 v2 最终状态", "an1", "GET",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v2']}",
    )
    expect_eq("D2", "v2 最终 APPROVED", "APPROVED", v2_final.json()["status"])

    assignment2 = request(
        "A3", "分配标注员 2", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-batches/{ids['batch_main']}/annotation-assignments",
        json_body={"assignee_id": pid("an2"), "planned_item_count": 1, "planned_minutes": 30},
    )
    expect_status("A3", "分配标注员 2", assignment2, 201)
    ids["assignment_an2"] = UUID(assignment2.json()["assignment_id"])
    assignment2_json = assignment2.json()
    claim2 = request(
        "A3", "标注员 2 领取", "an2", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-assignments/{ids['assignment_an2']}/actions/claim",
        json_body={}, headers=ifmatch(assignment2_json["version"]),
    )
    expect_status("A3", "标注员 2 领取", claim2, 200)
    start2 = request(
        "A3", "标注员 2 开始", "an2", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-assignments/{ids['assignment_an2']}/actions/start",
        json_body={}, headers=ifmatch(claim2.json()["version"]),
    )
    expect_status("A3", "标注员 2 开始", start2, 200)

    # ------------------------------------------------------------------
    # D6 抽检 plan 三种策略
    # ------------------------------------------------------------------
    sub_an2_created = request(
        "D6", "标注员 2 创建提交", "an2", "POST", url("project_a", "/annotation-submissions"),
        json_body={
            "assignment_id": str(ids["assignment_an2"]),
            "records": [record_payload("ep_pass")],
            "data_source": "REAL",
        },
    )
    expect_status("D6", "标注员 2 创建提交", sub_an2_created, 201)
    sub_an2 = sub_an2_created.json()
    ids["sub_an2"] = UUID(sub_an2["submission_id"])
    sub_an2_submitted = request(
        "D6", "标注员 2 提交", "an2", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub_an2']}/actions/submit",
        json_body={}, headers=ifmatch(sub_an2["version"]),
    )
    expect_status("D6", "标注员 2 提交", sub_an2_submitted, 200)

    plan_base = {
        "task_id": str(ids["task_main"]),
        "sampling_policy_version": "quality/1",
        "sample_reason": "DDP-322 策略化抽检",
    }
    candidates = [
        {"submission_id": str(ids["sub1_v2"]), "stratum": "high", "risk_score": 0.9},
        {"submission_id": str(ids["sub_an2"]), "stratum": "low", "risk_score": 0.1},
    ]

    plan_random = request(
        "D6", "RANDOM 抽检计划", "owner_a", "POST", url("project_a", "/quality-samples/plan"),
        json_body={**plan_base, "candidates": candidates, "sample_type": "RANDOM", "sample_size": 1},
    )
    expect_status("D6", "RANDOM 抽检计划", plan_random, 201)
    random_items = plan_random.json()
    check(
        "D6", "RANDOM 返回 1 条策略化样本",
        isinstance(random_items, list) and len(random_items) == 1,
        "长度 1",
        len(random_items) if isinstance(random_items, list) else random_items,
    )
    if isinstance(random_items, list) and random_items:
        item = random_items[0]
        expect_eq("D6", "RANDOM 策略落库", "RANDOM", item["sample_type"])
        expect_eq("D6", "RANDOM 策略版本落库", "quality/1", item["sampling_policy_version"])
        check(
            "D6", "RANDOM 样本携带候选 risk_score",
            item.get("metadata", {}).get("risk_score") in (0.9, 0.1),
            "0.9 或 0.1",
            item.get("metadata"),
        )

    plan_stratified = request(
        "D6", "STRATIFIED 抽检计划", "owner_a", "POST", url("project_a", "/quality-samples/plan"),
        json_body={
            **plan_base,
            "candidates": candidates,
            "sample_type": "STRATIFIED",
            "sample_size": 1,
            "stratum": "high",
        },
    )
    expect_status("D6", "STRATIFIED 抽检计划", plan_stratified, 201)
    strat_items = plan_stratified.json()
    check(
        "D6", "STRATIFIED 选中 high 层",
        isinstance(strat_items, list)
        and len(strat_items) == 1
        and strat_items[0]["submission_id"] == str(ids["sub1_v2"]),
        str(ids["sub1_v2"]),
        strat_items[0]["submission_id"] if isinstance(strat_items, list) and strat_items else strat_items,
    )
    if isinstance(strat_items, list) and strat_items:
        expect_eq("D6", "STRATIFIED 逐候选 stratum 落库", "high", strat_items[0]["stratum"])
        expect_eq("D6", "STRATIFIED risk_score 落库", 0.9, strat_items[0]["metadata"]["risk_score"])

    plan_risk = request(
        "D6", "RISK_BASED 抽检计划", "owner_a", "POST", url("project_a", "/quality-samples/plan"),
        json_body={
            **plan_base,
            "candidates": candidates,
            "sample_type": "RISK_BASED",
            "sample_size": 1,
            "risk_threshold": 0.5,
        },
    )
    expect_status("D6", "RISK_BASED 抽检计划", plan_risk, 201)
    risk_items = plan_risk.json()
    check(
        "D6", "RISK_BASED 选中高风险候选",
        isinstance(risk_items, list)
        and len(risk_items) == 1
        and risk_items[0]["submission_id"] == str(ids["sub1_v2"]),
        str(ids["sub1_v2"]),
        risk_items[0]["submission_id"] if isinstance(risk_items, list) and risk_items else risk_items,
    )
    if isinstance(risk_items, list) and risk_items:
        expect_eq("D6", "RISK_BASED 高风险分落库", 0.9, risk_items[0]["metadata"]["risk_score"])

    duplicated = request(
        "D6", "重复候选", "owner_a", "POST", url("project_a", "/quality-samples/plan"),
        json_body={
            **plan_base,
            "candidates": [candidates[0], candidates[0]],
            "sample_type": "RANDOM",
            "sample_size": 1,
        },
    )
    expect_status("D6", "重复候选 → 422", duplicated, 422)
    expect_code("D6", "重复候选错误码", duplicated, "validation_failed")

    idem_plan_body = {
        **plan_base,
        "candidates": candidates,
        "sample_type": "RANDOM",
        "sample_size": 1,
    }
    plan_idem_first = request(
        "D6", "plan 幂等首次", "owner_a", "POST", url("project_a", "/quality-samples/plan"),
        json_body=idem_plan_body, headers={"Idempotency-Key": "qa322-plan-idem-0001"},
    )
    expect_status("D6", "plan 幂等首次", plan_idem_first, 201)
    plan_idem_replay = request(
        "D6", "plan 幂等重放", "owner_a", "POST", url("project_a", "/quality-samples/plan"),
        json_body=idem_plan_body, headers={"Idempotency-Key": "qa322-plan-idem-0001"},
    )
    expect_status("D6", "plan 幂等重放", plan_idem_replay, 201)
    first_ids = [item["quality_sample_id"] for item in plan_idem_first.json()]
    replay_ids = [item["quality_sample_id"] for item in plan_idem_replay.json()]
    expect_eq("D6", "plan 幂等重放返回同一批样本", first_ids, replay_ids)

    plan_idem_conflict = request(
        "D6", "plan 同键异体", "owner_a", "POST", url("project_a", "/quality-samples/plan"),
        json_body={**idem_plan_body, "sample_type": "STRATIFIED", "stratum": "high"},
        headers={"Idempotency-Key": "qa322-plan-idem-0001"},
    )
    expect_status("D6", "plan 同键异体 → 409", plan_idem_conflict, 409)
    expect_code("D6", "plan 同键异体错误码", plan_idem_conflict, "idempotency_key_reused")

    # ------------------------------------------------------------------
    # D2 超限 409（v3 返工）
    # ------------------------------------------------------------------
    assignment_current = request(
        "D2", "读取分配当前版本", "an1", "GET",
        f"/api/v1/projects/{ids['project_a']}/annotation-assignments/{ids['assignment_an1']}",
    )
    assignment_reworked = request(
        "D2", "分配重新进入进行中", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-assignments/{ids['assignment_an1']}/actions/rework",
        json_body={"reason": "准备第三次提交"}, headers=ifmatch(assignment_current.json()["version"]),
    )
    expect_status("D2", "分配重新进入进行中", assignment_reworked, 200)

    sub3_created = request(
        "D2", "创建提交 v3", "an1", "POST", url("project_a", "/annotation-submissions"),
        json_body={
            "assignment_id": str(ids["assignment_an1"]),
            "records": [record_payload("ep_pass")],
            "data_source": "REAL",
        },
    )
    expect_status("D2", "创建提交 v3", sub3_created, 201)
    sub3 = sub3_created.json()
    ids["sub1_v3"] = UUID(sub3["submission_id"])
    expect_eq("D2", "v3 版本号", 3, sub3["submission_version"])
    sub3_submitted = request(
        "D2", "提交 v3", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v3']}/actions/submit",
        json_body={}, headers=ifmatch(sub3["version"]),
    )
    expect_status("D2", "提交 v3", sub3_submitted, 200)

    task_current_for_submit2 = request("D2", "读取任务当前版本（第二轮）", "an1", "GET", task_url)
    batch_current_for_submit2 = request("D2", "读取批次当前版本（第二轮）", "an1", "GET", batch_url)
    expect_status("D2", "读取任务当前版本（第二轮）", task_current_for_submit2, 200)
    expect_status("D2", "读取批次当前版本（第二轮）", batch_current_for_submit2, 200)
    batch_submitted2 = request(
        "D2", "批次再次提交反馈审", "an1", "POST", f"{batch_url}/actions/submit",
        json_body={}, headers=ifmatch(batch_current_for_submit2.json()["version"]),
    )
    expect_status("D2", "批次再次提交反馈审", batch_submitted2, 200)
    task_submitted3 = request(
        "D2", "任务再次提交反馈审", "an1", "POST", f"{task_url}/actions/submit",
        json_body={}, headers=ifmatch(task_current_for_submit2.json()["version"]),
    )
    expect_status("D2", "任务再次提交反馈审", task_submitted3, 200)
    batch_reviewing2 = request(
        "D2", "批次再次 REVIEWING", "an1", "POST", f"{batch_url}/actions/system-review",
        json_body={}, headers=ifmatch(batch_submitted2.json()["version"]),
    )
    expect_status("D2", "批次再次 REVIEWING", batch_reviewing2, 200)
    task_reviewing2 = request(
        "D2", "任务再次 REVIEWING", "an1", "POST", f"{task_url}/actions/system-review",
        json_body={}, headers=ifmatch(task_submitted3.json()["version"]),
    )
    expect_status("D2", "任务再次 REVIEWING", task_reviewing2, 200)

    review3_created = request(
        "D2", "创建复核 round3", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v3']}/reviews",
        json_body={"reviewer_id": pid("owner_a")},
    )
    expect_status("D2", "创建复核 round3", review3_created, 201)
    review3 = review3_created.json()
    review3_claimed = request(
        "D2", "复核 round3 领取", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-reviews/{review3['review_id']}/actions/claim",
        json_body={}, headers=ifmatch(review3["version"]),
    )
    expect_status("D2", "复核 round3 领取", review3_claimed, 200)
    review3_rejected = request(
        "D2", "复核 round3 驳回", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-reviews/{review3['review_id']}/actions/reject",
        json_body={
            "reason": "第三次返工",
            "issues": [{"code": "E3", "severity": "MAJOR", "message": "第三次"}],
        },
        headers=ifmatch(review3_claimed.json()["version"]),
    )
    expect_status("D2", "复核 round3 驳回", review3_rejected, 200)
    current_v3 = request(
        "D2", "读取 v3 当前版本", "an1", "GET",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v3']}",
    )
    over_limit = request(
        "D2", "返工次数超过上限", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v3']}/actions/require-rework",
        json_body={"reason": "超过上限"}, headers=ifmatch(current_v3.json()["version"]),
    )
    expect_status("D2", "返工次数超过上限 → 409", over_limit, 409)
    expect_code("D2", "返工超限错误码", over_limit, "annotation_task_rework_limit_exceeded")
    batch_final = request("D2", "读取批次最终计数", "an1", "GET", batch_url)
    expect_eq("D2", "超限失败不递增 rework_count", 1, batch_final.json()["rework_count"])

    # ------------------------------------------------------------------
    # D3 黄金集答案授权 + FAILURE 审计/outbox
    # ------------------------------------------------------------------
    gold_created = request(
        "D3", "创建黄金集", "an1", "POST", url("project_a", "/gold-sets"),
        json_body={
            "code": f"qa322-gold-{uuid4().hex[:6]}",
            "schema_id": str(ids["schema_main"]),
            "items": [
                {
                    "gold_item_id": str(uuid4()),
                    "episode_id": pid("ep_pass"),
                    "expected_labels": [{"label_key": "action", "value": "PICK"}],
                }
            ],
            "exposure_policy": "HIDDEN",
            "max_exposure_per_annotator": 1,
            "rotation_policy": "NONE",
        },
    )
    expect_status("D3", "创建黄金集", gold_created, 201)
    gold = gold_created.json()
    ids["gold_main"] = UUID(gold["gold_set_id"])
    gold_url = f"/api/v1/projects/{ids['project_a']}/gold-sets/{ids['gold_main']}"

    for actor, label in (("an1", "AN"), ("an2", "AE"), ("viewer_a", "RO")):
        denied = request(
            "D3", f"{label} 读取黄金集答案", actor, "GET", f"{gold_url}/answers"
        )
        expect_status("D3", f"{label} 读取答案 → 403", denied, 403)
        expect_code("D3", f"{label} 读取答案错误码", denied, "gold_set_access_denied")

    owner_answers = request("D3", "OWNER 读取黄金集答案", "owner_a", "GET", f"{gold_url}/answers")
    expect_status("D3", "OWNER 读取答案 → 200", owner_answers, 200)
    check(
        "D3", "OWNER 响应包含 expected_labels",
        "expected_labels" in owner_answers.text,
        "包含 expected_labels",
        owner_answers.json().get("items", [{}])[0],
    )

    # ------------------------------------------------------------------
    # A10 权限 / 跨项目 / 分页
    # ------------------------------------------------------------------
    outsider_tasks = request(
        "A10", "项目外用户访问任务", "outsider", "GET", url("project_a", "/annotation-tasks")
    )
    expect_status("A10", "项目外用户访问任务 → 403", outsider_tasks, 403)
    expect_code("A10", "项目外用户错误码", outsider_tasks, "forbidden")

    cross_project = request(
        "A10", "跨项目访问黄金集", "owner_b", "GET",
        f"/api/v1/projects/{ids['project_b']}/gold-sets/{ids['gold_main']}",
    )
    expect_status("A10", "跨项目访问黄金集 → 404", cross_project, 404)
    expect_code("A10", "跨项目错误码", cross_project, "resource_not_found")

    first_page = request(
        "A10", "Schema 分页第一页", "an1", "GET",
        url("project_a", "/annotation-schemas?limit=1"),
    )
    expect_status("A10", "Schema 分页第一页", first_page, 200)
    first_page_json = first_page.json()
    check(
        "A10", "分页 limit 生效",
        len(first_page_json.get("items", [])) <= 1,
        "≤1",
        len(first_page_json.get("items", [])),
    )
    cursor = first_page_json.get("next_cursor")
    check("A10", "分页返回 next_cursor", bool(cursor), "非空 cursor", cursor)
    if cursor:
        second_page = request(
            "A10", "Schema 分页第二页", "an1", "GET",
            url("project_a", f"/annotation-schemas?limit=1&cursor={cursor}"),
        )
        expect_status("A10", "Schema 分页第二页", second_page, 200)
        first_ids_page = {item["schema_id"] for item in first_page_json["items"]}
        second_ids_page = {item["schema_id"] for item in second_page.json().get("items", [])}
        check(
            "A10", "分页游标不重复",
            not (first_ids_page & second_ids_page),
            "两页 ID 不相交",
            {"first": sorted(first_ids_page), "second": sorted(second_ids_page)},
        )


# ---------------------------------------------------------------------------
# 审计查询
# ---------------------------------------------------------------------------
def audit_queries() -> dict[str, Any]:
    with psycopg.connect(PG_DSN) as connection:
        failure_rows = connection.execute(
            "SELECT actor_user_id, action, result, details FROM audit_logs "
            "WHERE project_id = %s AND action = 'view-answers' AND result = 'FAILURE'",
            (ids["project_a"],),
        ).fetchall()
        success_rows = connection.execute(
            "SELECT actor_user_id, action, result FROM audit_logs "
            "WHERE project_id = %s AND action = 'view-answers' AND result = 'SUCCESS'",
            (ids["project_a"],),
        ).fetchall()
        denied_events = connection.execute(
            "SELECT event_type, payload FROM outbox_events "
            "WHERE project_id = %s AND event_type = 'annotation.gold_set.answers_denied'",
            (ids["project_a"],),
        ).fetchall()
        accessed_events = connection.execute(
            "SELECT event_type, payload FROM outbox_events "
            "WHERE project_id = %s AND event_type = 'annotation.gold_set.answers_accessed'",
            (ids["project_a"],),
        ).fetchall()
    failure_actors = {row[0] for row in failure_rows}
    expected_actors = {ids["an1"], ids["an2"], ids["viewer_a"]}
    check(
        "D3", "403 拒绝写入 FAILURE 审计",
        expected_actors <= failure_actors and len(failure_rows) >= 3,
        "AN/AE/RO 各一条 FAILURE",
        {
            "count": len(failure_rows),
            "actors": sorted(str(item) for item in failure_actors),
        },
    )
    check(
        "D3", "403 拒绝写入 answers_denied outbox",
        len(denied_events) >= 3,
        "≥3 条",
        len(denied_events),
    )
    check(
        "D3", "OWNER 成功访问写入 SUCCESS 审计与 outbox",
        len(success_rows) >= 1 and len(accessed_events) >= 1,
        "≥1 条 SUCCESS + ≥1 条 accessed",
        {"success": len(success_rows), "accessed": len(accessed_events)},
    )
    return {
        "failure_audits": [
            {"actor": str(row[0]), "action": row[1], "result": row[2], "details": row[3]}
            for row in failure_rows
        ],
        "success_audits": [
            {"actor": str(row[0]), "action": row[1], "result": row[2]}
            for row in success_rows
        ],
        "denied_events": [
            {"event_type": row[0], "payload": row[1]} for row in denied_events
        ],
        "accessed_events": [
            {"event_type": row[0], "payload": row[1]} for row in accessed_events
        ],
    }


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def summarize_areas() -> dict[str, Any]:
    areas: dict[str, dict[str, int]] = {}
    for item in checks:
        bucket = areas.setdefault(item["area"], {"PASS": 0, "FAIL": 0})
        bucket[item["status"]] += 1
    return areas


def write_outputs(extra: dict[str, Any]) -> None:
    areas = summarize_areas()
    total_pass = sum(item["status"] == "PASS" for item in checks)
    total_fail = sum(item["status"] == "FAIL" for item in checks)
    payload = {
        "task": "DDP-322",
        "baseline": os.environ.get("DDP322_BASELINE", "26cef24"),
        "checked_at": CHECKED_AT.isoformat(),
        "database": DB_NAME,
        "pg_dsn": PG_DSN,
        "server": BASE_URL,
        "summary": {
            "total": len(checks),
            "pass": total_pass,
            "fail": total_fail,
            "areas": areas,
        },
        "checks": checks,
        "observations": observations,
        "migration_steps": migration_steps,
        "d1_results": d1_results,
        "http_log": http_log,
        "extra": extra,
    }
    (EVIDENCE / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (EVIDENCE / "d1-raw-guard-results.json").write_text(
        json.dumps(d1_results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (EVIDENCE / "migration-evidence.json").write_text(
        json.dumps(migration_steps, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    lines = [
        "# DDP-322 运行摘要",
        "",
        f"- 基线 HEAD：{payload['baseline']}",
        f"- 检查时间：{payload['checked_at']}",
        f"- 一次性数据库：{DB_NAME}",
        f"- 总检查：{len(checks)}，PASS {total_pass}，FAIL {total_fail}",
        "",
        "## 分区",
    ]
    for area in sorted(areas):
        lines.append(
            f"- {area}: PASS {areas[area]['PASS']} / FAIL {areas[area]['FAIL']}"
        )
    lines.append("")
    lines.append("## 失败项")
    failures = [item for item in checks if item["status"] == "FAIL"]
    if not failures:
        lines.append("- 无")
    else:
        for item in failures:
            lines.append(
                f"- [{item['area']}] {item['step']}：期望 {item['expected']}，实际 {item['actual']}"
            )
    lines.append("")
    lines.append("## 观察")
    for message in observations:
        lines.append(f"- {message}")
    (EVIDENCE / "summary.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    key_lines = ["# DDP-322 关键 HTTP/DB 证据", ""]
    for area in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "A9", "A10"):
        items = [item for item in checks if item["area"] == area]
        if not items:
            continue
        key_lines.append(f"## {area}")
        for item in items:
            key_lines.append(
                f"- {item['status']} | {item['step']} | 期望 {item['expected']} | 实际 {item['actual']}"
            )
        key_lines.append("")
    (EVIDENCE / "key-http-evidence.md").write_text(
        "\n".join(key_lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    global client, server_process
    extra: dict[str, Any] = {}
    try:
        create_database()
        observe(f"一次性 PostgreSQL 数据库：{DB_NAME}")
        migration_probe()
        observe("迁移 0011→0016 完成；0016 对历史缺陷函数体完成重装。")
        row_ids = build_d1_objects()
        d1_json_update_probe(row_ids)

        seed_http_data()
        server_process = start_server()
        client = httpx.Client(base_url=BASE_URL, timeout=30.0)
        run_http_scenarios()
        extra["audit"] = audit_queries()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        check("INFRA", "探针执行未抛出未处理异常", False, "正常结束", f"{type(exc).__name__}: {exc}")
        extra["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if client is not None:
            client.close()
        if server_process is not None:
            server_process.terminate()
            try:
                server_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server_process.kill()
        write_outputs(extra)
        if DROP_DB:
            drop_database()

    failures = [item for item in checks if item["status"] == "FAIL"]
    print(
        f"DDP-322 完成：{len(checks)} 检查，"
        f"PASS {len(checks) - len(failures)}，FAIL {len(failures)}"
    )
    print(f"证据目录：{EVIDENCE.relative_to(ROOT)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
