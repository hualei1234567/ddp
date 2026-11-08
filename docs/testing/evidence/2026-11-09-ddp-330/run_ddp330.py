#!/usr/bin/env python3
"""DDP-330 阶段 4 数据集/导出/训练/授权独立验收探针（基线 f4d32a4 / head 0020）。

约束与设计：
- 只读取业务代码，不修改 backend/app、migrations、packages/contracts；
- 在本地真实 PostgreSQL 16 创建一次性数据库，通过 Alembic 完成 0011→0019
  逐级升级、0019→0011 逐级降级、再升级到 0019；
- 记录每一级迁移后的表清单、触发器清单与 JSON 列类型，禁止任何 jsonb 临时改写；
- 使用真实 uvicorn（app.main:app）+ 真实 HTTP + 同一迁移库完成四域验收；
- 导出 Worker 使用真实 MinIO（独立 QA 桶），仅在故障注入场景使用
  显式包装器模拟写入失败/回读损坏，不触碰 staging 桶与数据；
- 真实训练平台尚未接入，ExternalReference 提交预期 503（BLOCKED）；
  回调验签使用 DB 播种的外部训练引用，但仍经真实 HTTP 回调端点。

运行：
    backend/.venv/bin/python docs/testing/evidence/2026-11-09-ddp-330/run_ddp330.py

环境变量：
    DDP330_PG_ADMIN        默认 postgresql://ddp:ddp@127.0.0.1:55432/postgres
    DDP330_PORT            默认 8033
    DDP330_DROP_DB         设为 1 时结束后删除一次性数据库（默认保留）
    DDP330_S3_ACCESS_KEY   必填，本地 MinIO 凭据（不得入库）
    DDP330_S3_SECRET_KEY   必填，本地 MinIO 凭据（不得入库）
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

os.environ["DDP_ENV"] = "local"
os.environ["DDP_LOCAL_HEADER_AUTH"] = "true"
os.environ.pop("DDP_DATABASE_URL", None)
os.environ.pop("DDP_S3_BUCKET", None)

import boto3  # noqa: E402
import httpx  # noqa: E402
import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from botocore.config import Config as BotoConfig  # noqa: E402
from sqlalchemy import create_engine, select, text, update  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.domain.datasets.canonical import canonical_json, sha256_text  # noqa: E402
from app.domain.identity.models import ProjectScope  # noqa: E402
from app.infrastructure.db.models.annotation_catalog import (  # noqa: E402
    AnnotationSampleSet,
    AnnotationSchema,
)
from app.infrastructure.db.models.annotation_results import (  # noqa: E402
    AnnotationRecord,
    AnnotationSubmission,
)
from app.infrastructure.db.models.annotation_work import (  # noqa: E402
    AnnotationAssignment,
    AnnotationBatch,
    AnnotationTask,
)
from app.infrastructure.db.models.episodes import EpisodeModel, EpisodeRawAssetModel  # noqa: E402
from app.infrastructure.db.models.identity import Project, ProjectMember, User  # noqa: E402
from app.infrastructure.db.models.ingestion import RawAssetModel  # noqa: E402
from app.infrastructure.db.models.training import ExternalTrainingReferenceModel  # noqa: E402
from app.infrastructure.db.session import create_session_factory  # noqa: E402
from app.infrastructure.repositories.export_jobs import SqlExportJobRepository  # noqa: E402
from app.infrastructure.storage.s3 import S3MultipartStorage  # noqa: E402
from app.workers.exports import ExportWorker  # noqa: E402

# ---------------------------------------------------------------------------
# 环境与常量
# ---------------------------------------------------------------------------
PG_ADMIN = os.environ.get(
    "DDP330_PG_ADMIN",
    "postgresql://ddp:ddp@127.0.0.1:55432/postgres",
)
APP_PORT = int(os.environ.get("DDP330_PORT", "8033"))
DROP_DB = os.environ.get("DDP330_DROP_DB", "0") == "1"
DB_NAME = f"ddp_qa_d330_{os.getpid()}_{int(time.time())}"
PG_DSN = f"postgresql://ddp:ddp@127.0.0.1:55432/{DB_NAME}"
PG_URL = f"postgresql+psycopg://ddp:ddp@127.0.0.1:55432/{DB_NAME}"
BASE_URL = f"http://127.0.0.1:{APP_PORT}"

S3_PORT = int(os.environ.get("DDP330_S3_PORT", "9010"))
S3_ENDPOINT = os.environ.get("DDP330_S3_ENDPOINT", f"http://127.0.0.1:{S3_PORT}")
S3_ACCESS_KEY = os.environ.get("DDP330_S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("DDP330_S3_SECRET_KEY", "")
MANAGED_MINIO = not (S3_ACCESS_KEY and S3_SECRET_KEY)
MINIO_DATA_DIR = Path(tempfile.gettempdir()) / (
    f"ddp-qa-minio-{os.getpid()}-{int(time.time())}"
)
if MANAGED_MINIO:
    S3_ACCESS_KEY = f"qa330{uuid4().hex[:12]}"
    S3_SECRET_KEY = uuid4().hex + uuid4().hex
QA_BUCKET = f"ddp-qa-ddp330-{int(time.time())}"
ADAPTER_SECRET = "qa-adapter-secret-0123456789abcdef"
LIMITED_SECRET = "qa-limited-secret-0123456789abcdef"
ROBOT_ID = UUID("11111111-1111-4111-8111-111111111111")
DEVICE_ID = UUID("22222222-2222-4222-8222-222222222222")
ADAPTER_ID = "qa-adapter"
LIMITED_ADAPTER_ID = "qa-limited"

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
CHECKED_AT = datetime.now(UTC)

checks: list[dict[str, Any]] = []
http_log: list[dict[str, Any]] = []
observations: list[str] = []
migration_steps: list[dict[str, Any]] = []
trigger_snapshots: dict[str, Any] = {}
direct_write_results: list[dict[str, Any]] = []
ids: dict[str, UUID] = {}
episodes: dict[str, list[UUID]] = {}

client: httpx.Client | None = None
server_process: subprocess.Popen[str] | None = None
minio_process: subprocess.Popen[str] | None = None


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
    evidence: dict[str, Any] | None = None,
) -> bool:
    return check(
        area,
        step,
        expected == actual,
        expected,
        actual,
        severity=severity,
        evidence=evidence,
    )


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
    try:
        response = client.request(method, path, json=json_body, headers=merged)
    except httpx.ReadError:
        time.sleep(0.2)
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


def raw_request(
    area: str,
    step: str,
    method: str,
    path: str,
    *,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    global client
    assert client is not None
    started = time.perf_counter()
    try:
        response = client.request(method, path, content=content, headers=headers)
    except httpx.ReadError:
        time.sleep(0.2)
        response = client.request(method, path, content=content, headers=headers)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    http_log.append(
        {
            "area": area,
            "step": step,
            "user": "adapter",
            "method": method,
            "path": path,
            "status": response.status_code,
            "elapsed_ms": elapsed_ms,
            "request_id": response.headers.get("X-Request-Id"),
            "request": {"raw_bytes": len(content or b"")},
            "response": _safe_json(response),
        }
    )
    return response


def ifmatch(version: int) -> dict[str, str]:
    return {"If-Match": f'"{version}"'}


def api(project: str, suffix: str) -> str:
    return f"/api/v1/projects/{ids[project]}{suffix}"


def api_root(suffix: str) -> str:
    return f"/api/v1{suffix}"


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


def public_tables() -> list[str]:
    with psycopg.connect(PG_DSN) as connection:
        rows = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY table_name"
        ).fetchall()
    return [row[0] for row in rows]


DATASET_TABLES = {
    "datasets",
    "dataset_versions",
    "dataset_items",
    "dataset_dedup_policies",
    "dataset_split_policies",
    "dataset_leakage_policies",
    "dataset_quality_policies",
    "dataset_leakage_reports",
    "dataset_quality_reports",
}
TRAINING_TABLES = {
    "training_requests",
    "training_request_revisions",
    "training_deliveries",
    "external_training_references",
    "dataset_grants",
    "training_adapter_callbacks",
}
STAGE4_TABLES = DATASET_TABLES | {"export_jobs"} | TRAINING_TABLES


def trigger_inventory() -> list[dict[str, str]]:
    with psycopg.connect(PG_DSN) as connection:
        rows = connection.execute(
            """
            SELECT c.relname, t.tgname
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace
            ORDER BY c.relname, t.tgname
            """
        ).fetchall()
    return [{"table": row[0], "trigger": row[1]} for row in rows]


def stage4_trigger_inventory() -> list[dict[str, str]]:
    return [item for item in trigger_inventory() if item["table"] in STAGE4_TABLES]


def json_columns(table_names: Iterable[str] | None = None) -> list[dict[str, str]]:
    with psycopg.connect(PG_DSN) as connection:
        rows = connection.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema='public' AND data_type IN ('json', 'jsonb')
            ORDER BY table_name, column_name
            """
        ).fetchall()
    items = [{"table": row[0], "column": row[1], "type": row[2]} for row in rows]
    if table_names is not None:
        allowed = set(table_names)
        items = [item for item in items if item["table"] in allowed]
    return items


EXPECTED_STAGE4_TRIGGERS = {
    "0017": {
        "trg_datasets_initial_status",
        "trg_datasets_transition",
        "trg_datasets_identity_immutable",
        "trg_datasets_no_delete",
        "trg_dataset_versions_initial_status",
        "trg_dataset_versions_transition",
        "trg_dataset_versions_validity_transition",
        "trg_dataset_versions_immutable",
        "trg_dataset_versions_no_delete",
        "trg_dataset_items_no_update",
        "trg_dataset_items_no_delete",
        "trg_dataset_dedup_policies_no_update",
        "trg_dataset_dedup_policies_no_delete",
        "trg_dataset_split_policies_no_update",
        "trg_dataset_split_policies_no_delete",
        "trg_dataset_leakage_policies_no_update",
        "trg_dataset_leakage_policies_no_delete",
        "trg_dataset_quality_policies_no_update",
        "trg_dataset_quality_policies_no_delete",
        "trg_dataset_leakage_reports_no_update",
        "trg_dataset_leakage_reports_no_delete",
        "trg_dataset_quality_reports_no_update",
        "trg_dataset_quality_reports_no_delete",
    },
    "0018": {
        "trg_export_jobs_initial_status",
        "trg_export_jobs_transition",
        "trg_export_jobs_input_immutable",
        "trg_export_jobs_artifacts_immutable",
        "trg_export_jobs_no_delete",
    },
    "0019": {
        "trg_training_requests_initial_status",
        "trg_training_requests_transition",
        "trg_training_requests_immutable",
        "trg_training_requests_terminal_immutable",
        "trg_training_requests_no_delete",
        "trg_training_request_revisions_initial_status",
        "trg_training_request_revisions_transition",
        "trg_training_request_revisions_immutable",
        "trg_training_request_revisions_decision_immutable",
        "trg_training_request_revisions_terminal_immutable",
        "trg_training_request_revisions_no_delete",
        "trg_training_deliveries_initial_status",
        "trg_training_deliveries_transition",
        "trg_training_deliveries_immutable",
        "trg_training_deliveries_terminal_immutable",
        "trg_training_deliveries_no_delete",
        "trg_external_training_references_initial_status",
        "trg_external_training_references_transition",
        "trg_external_training_references_immutable",
        "trg_external_training_references_terminal_immutable",
        "trg_external_training_references_no_delete",
        "trg_dataset_grants_initial_status",
        "trg_dataset_grants_transition",
        "trg_dataset_grants_immutable",
        "trg_dataset_grants_terminal_immutable",
        "trg_dataset_grants_no_delete",
    },
}
EXPECTED_FINAL_TRIGGERS = (
    EXPECTED_STAGE4_TRIGGERS["0017"]
    | EXPECTED_STAGE4_TRIGGERS["0018"]
    | EXPECTED_STAGE4_TRIGGERS["0019"]
)


def migration_probe() -> None:
    config = alembic_config()
    command.upgrade(config, "0011")
    migration_steps.append(
        {
            "action": "upgrade",
            "revision": "0011",
            "actual": current_revision(),
            "tables": len(public_tables()),
            "triggers": len(trigger_inventory()),
        }
    )

    cumulative: set[str] = set()
    for revision in ("0012", "0013", "0014", "0015", "0016", "0017", "0018", "0019"):
        command.upgrade(config, revision)
        inventory = trigger_inventory()
        stage4 = stage4_trigger_inventory()
        stage4_names = {item["trigger"] for item in stage4}
        if revision in EXPECTED_STAGE4_TRIGGERS:
            cumulative |= EXPECTED_STAGE4_TRIGGERS[revision]
        if revision >= "0017":
            check(
                "D7",
                f"迁移 {revision} 后阶段 4 触发器清单完全匹配",
                stage4_names == cumulative,
                sorted(cumulative),
                sorted(stage4_names),
                evidence={"revision": current_revision(), "triggers": stage4},
            )
        if revision == "0017":
            check(
                "D7",
                "0017 后数据集 9 张表全部存在且触发器 23 个",
                len(DATASET_TABLES & set(public_tables())) == 9 and len(stage4) == 23,
                9,
                {
                    "tables": sorted(DATASET_TABLES & set(public_tables())),
                    "triggers": len(stage4),
                },
            )
        migration_steps.append(
            {
                "action": "upgrade",
                "revision": revision,
                "actual": current_revision(),
                "stage4_tables": sorted(STAGE4_TABLES & set(public_tables())),
                "stage4_triggers": stage4,
                "all_trigger_count": len(inventory),
            }
        )

    # 模拟已部署的 0019 旧库：把外部训练引用守卫替换为错误使用 status 列的
    # stale 版本（真实旧缺陷），再执行 upgrade head(0020)，验证迁移重装守卫。
    with psycopg.connect(PG_DSN) as connection:
        connection.execute(
            """
            CREATE OR REPLACE FUNCTION ddp_external_training_reference_guard()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF OLD.status IS DISTINCT FROM NEW.status THEN
                    RAISE EXCEPTION 'stale_guard_status' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$;
            """
        )
        connection.commit()
        stale_source = connection.execute(
            "SELECT prosrc FROM pg_proc "
            "WHERE proname = 'ddp_external_training_reference_guard'"
        ).fetchone()[0]
    check(
        "D7",
        "0019 库已注入旧版 stale 守卫（使用 OLD.status）",
        "OLD.status" in stale_source,
        "含 OLD.status",
        {"has_wrong_status": "OLD.status" in stale_source},
    )

    command.upgrade(config, "0020")
    with psycopg.connect(PG_DSN) as connection:
        repaired_source = connection.execute(
            "SELECT prosrc FROM pg_proc "
            "WHERE proname = 'ddp_external_training_reference_guard'"
        ).fetchone()[0]
        trigger_state = connection.execute(
            "SELECT tgenabled FROM pg_trigger "
            "WHERE tgname = 'trg_external_training_references_transition' "
            "AND NOT tgisinternal"
        ).fetchone()
    check(
        "D7",
        "0019 库执行 upgrade head 到 0020 后 stale 守卫被重装为 external_status",
        "OLD.external_status" in repaired_source and "OLD.status" not in repaired_source,
        "含 OLD.external_status 且不含 OLD.status",
        {
            "has_external_status": "OLD.external_status" in repaired_source,
            "has_wrong_status": "OLD.status" in repaired_source,
            "revision": current_revision(),
        },
    )
    check(
        "D7",
        "0020 后外部训练引用转换触发器处于启用状态",
        bool(trigger_state) and trigger_state[0] == "O",
        "O",
        trigger_state[0] if trigger_state else None,
    )
    migration_steps.append(
        {
            "action": "upgrade",
            "revision": "0020",
            "actual": current_revision(),
            "stage4_triggers": stage4_trigger_inventory(),
            "guard_source_has_external_status": "OLD.external_status" in repaired_source,
            "transition_trigger_enabled": trigger_state[0] if trigger_state else None,
        }
    )

    final_stage4 = {item["trigger"] for item in stage4_trigger_inventory()}
    check(
        "D7",
        "0020(head) 后阶段 4 触发器共 54 个且名单精确匹配",
        final_stage4 == EXPECTED_FINAL_TRIGGERS and len(final_stage4) == 54,
        {"count": 54},
        {"count": len(final_stage4), "triggers": sorted(final_stage4)},
    )
    check(
        "D7",
        "0020 后阶段 4 表全部存在",
        STAGE4_TABLES <= set(public_tables()),
        sorted(STAGE4_TABLES),
        sorted(STAGE4_TABLES & set(public_tables())),
    )
    stage4_json = json_columns(STAGE4_TABLES)
    types = sorted({item["type"] for item in stage4_json})
    check(
        "D7",
        "迁移库阶段 4 JSON 列类型来自迁移定义，QA 未执行任何 ALTER 临时改写",
        types == ["jsonb"],
        ["jsonb"],
        {"types": types, "columns": stage4_json},
    )
    observe(f"阶段 4 JSON 列共 {len(stage4_json)} 个，类型 {types}。")
    trigger_snapshots["head"] = stage4_trigger_inventory()

    downgrade_expectations = {
        "0019": {"tables": STAGE4_TABLES, "triggers": 54},
        "0018": {"tables": DATASET_TABLES | {"export_jobs"}, "triggers": 28},
        "0017": {"tables": DATASET_TABLES, "triggers": 23},
        "0016": {"tables": set(), "triggers": 0},
        "0015": {"tables": set(), "triggers": 0},
        "0014": {"tables": set(), "triggers": 0},
        "0013": {"tables": set(), "triggers": 0},
        "0012": {"tables": set(), "triggers": 0},
        "0011": {"tables": set(), "triggers": 0},
    }
    for revision in ("0019", "0018", "0017", "0016", "0015", "0014", "0013", "0012", "0011"):
        command.downgrade(config, revision)
        present = STAGE4_TABLES & set(public_tables())
        stage4 = stage4_trigger_inventory()
        expected = downgrade_expectations[revision]
        check(
            "D7",
            f"降级到 {revision} 后阶段 4 表与触发器符合预期",
            present == expected["tables"] and len(stage4) == expected["triggers"],
            {
                "tables": sorted(expected["tables"]),
                "triggers": expected["triggers"],
            },
            {"tables": sorted(present), "triggers": len(stage4)},
        )
        migration_steps.append(
            {
                "action": "downgrade",
                "revision": revision,
                "actual": current_revision(),
                "stage4_tables_present": sorted(present),
                "stage4_triggers": stage4,
                "all_trigger_count": len(trigger_inventory()),
            }
        )

    check(
        "D7",
        "完整降级链回到 0011",
        current_revision() == "0011",
        "0011",
        current_revision(),
    )
    command.upgrade(config, "head")
    check(
        "D7",
        "降级后重新升级到 head(0020)",
        current_revision() == "0020" and STAGE4_TABLES <= set(public_tables()),
        {"revision": "0020", "tables": sorted(STAGE4_TABLES)},
        {
            "revision": current_revision(),
            "tables": sorted(STAGE4_TABLES & set(public_tables())),
            "stage4_triggers": len(stage4_trigger_inventory()),
        },
    )
    migration_steps.append(
        {
            "action": "upgrade",
            "revision": "head",
            "actual": current_revision(),
            "stage4_tables": sorted(STAGE4_TABLES & set(public_tables())),
            "stage4_triggers": stage4_trigger_inventory(),
        }
    )
    with psycopg.connect(PG_DSN) as connection:
        head_source = connection.execute(
            "SELECT prosrc FROM pg_proc "
            "WHERE proname = 'ddp_external_training_reference_guard'"
        ).fetchone()[0]
    check(
        "D7",
        "重新升级到 head 后外部训练引用守卫使用 external_status",
        "OLD.external_status" in head_source and "OLD.status" not in head_source,
        "含 OLD.external_status 且不含 OLD.status",
        {
            "has_external_status": "OLD.external_status" in head_source,
            "has_wrong_status": "OLD.status" in head_source,
        },
    )


# ---------------------------------------------------------------------------
# 测试数据播种
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EpisodeSpec:
    idx: int
    status: str = "ACCEPTED"
    quality: str = "PASS"
    annotation_approved: bool = True
    lineage: UUID | None = None
    manifest_sha: str | None = None
    raw_sha: str | None = None
    scene: str = "kitchen"
    time_sync: str = "VERIFIED"
    calibration_valid: bool = True


def _digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def seed_identity(factory: sessionmaker[Session]) -> None:
    for name in (
        "project_a",
        "project_b",
        "owner_a",
        "approver_a",
        "editor_a",
        "viewer_a",
        "outsider",
        "owner_b",
        "grantee_b",
    ):
        ids[name] = uuid4()

    with factory() as session:
        session.add_all(
            [
                Project(id=ids["project_a"], code=f"qa330-a-{uuid4().hex[:8]}", name="QA330 项目 A"),
                Project(id=ids["project_b"], code=f"qa330-b-{uuid4().hex[:8]}", name="QA330 项目 B"),
                User(id=ids["owner_a"], display_name="项目 A 负责人"),
                User(id=ids["approver_a"], display_name="项目 A 第二负责人"),
                User(id=ids["editor_a"], display_name="项目 A 数据工程师"),
                User(id=ids["viewer_a"], display_name="项目 A 只读用户"),
                User(id=ids["outsider"], display_name="项目外用户"),
                User(id=ids["owner_b"], display_name="项目 B 负责人"),
                User(id=ids["grantee_b"], display_name="项目 B 算法工程师"),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMember(
                    project_id=ids["project_a"],
                    user_id=ids["owner_a"],
                    role="owner",
                ),
                ProjectMember(
                    project_id=ids["project_a"],
                    user_id=ids["approver_a"],
                    role="owner",
                ),
                ProjectMember(
                    project_id=ids["project_a"],
                    user_id=ids["editor_a"],
                    role="editor",
                ),
                ProjectMember(
                    project_id=ids["project_a"],
                    user_id=ids["viewer_a"],
                    role="viewer",
                ),
                ProjectMember(
                    project_id=ids["project_b"],
                    user_id=ids["owner_b"],
                    role="owner",
                ),
                ProjectMember(
                    project_id=ids["project_b"],
                    user_id=ids["grantee_b"],
                    role="editor",
                ),
            ]
        )
        session.commit()


def seed_episodes(
    factory: sessionmaker[Session],
    project_id: UUID,
    actor_id: UUID,
    specs: list[EpisodeSpec],
    *,
    prefix: str,
    scene: str = "kitchen",
) -> list[UUID]:
    """播种完整 Episode + RawAsset + 已批准标注链；所有状态迁移真实经过触发器。"""
    episode_ids: list[UUID] = []
    with factory() as session:
        schema_id = uuid4()
        sample_set_id = uuid4()
        task_id = uuid4()
        batch_id = uuid4()
        assignment_id = uuid4()
        submission_id = uuid4()
        session.add(
            AnnotationSchema(
                id=schema_id,
                project_id=project_id,
                schema_lineage_id=uuid4(),
                schema_version=1,
                code=f"schema-{prefix}-{uuid4().hex[:8]}",
                name="阶段 4 验收 Schema",
                description=None,
                status="DRAFT",
                annotation_levels=["EPISODE"],
                labels=[
                    {
                        "label_key": "action",
                        "label_type": "ENUM",
                        "required": False,
                        "level": "EPISODE",
                        "visibility": "ANNOTATOR",
                        "value_spec": {"enum_values": ["PICK"]},
                    }
                ],
                compatibility="BACKWARD_COMPATIBLE",
                schema_sha256=_digest(f"{prefix}-schema"),
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        session.execute(
            update(AnnotationSchema)
            .where(AnnotationSchema.id == schema_id)
            .values(status="PENDING_REVIEW", version=2)
        )
        session.execute(
            update(AnnotationSchema)
            .where(AnnotationSchema.id == schema_id)
            .values(status="PUBLISHED", published_at=NOW, version=3)
        )
        session.add(
            AnnotationSampleSet(
                id=sample_set_id,
                project_id=project_id,
                code=f"sample-{prefix}-{uuid4().hex[:8]}",
                name=None,
                status="DRAFT",
                selection_rule_version="stage3-sample-selection/1",
                strategy="FILTER",
                filters={"code_prefix": prefix},
                strata=[],
                dedup_key="EPISODE_LINEAGE",
                candidate_count=len(specs),
                selected_count=len(specs),
                excluded_count=0,
                deduplicated_count=0,
                snapshot_sha256=None,
                snapshot_at=None,
                cancelled_reason=None,
                data_source="REAL",
                exclusion_summary={},
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        session.execute(
            update(AnnotationSampleSet)
            .where(AnnotationSampleSet.id == sample_set_id)
            .values(status="PREVIEW", version=2)
        )
        session.execute(
            update(AnnotationSampleSet)
            .where(AnnotationSampleSet.id == sample_set_id)
            .values(
                status="SNAPSHOTTED",
                snapshot_sha256=_digest(f"{prefix}-snapshot"),
                snapshot_at=NOW,
                version=3,
            )
        )
        session.add(
            AnnotationTask(
                id=task_id,
                project_id=project_id,
                code=f"task-{prefix}-{uuid4().hex[:8]}",
                name="阶段 4 验收任务",
                description=None,
                status="DRAFT",
                sample_set_id=sample_set_id,
                schema_id=schema_id,
                schema_version=1,
                schema_sha256=_digest(f"{prefix}-schema"),
                gold_set_id=None,
                iaa_policy={},
                quality_policy={},
                assignment_mode="MANUAL",
                target_item_count=len(specs),
                target_episode_count=len(specs),
                due_at=None,
                priority="P1",
                progress={},
                quality_summary={},
                iaa_report_id=None,
                accepted_at=None,
                completed_at=None,
                status_reason=None,
                blocked_reason=None,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        session.add(
            AnnotationBatch(
                id=batch_id,
                project_id=project_id,
                task_id=task_id,
                code=f"batch-{prefix}-{uuid4().hex[:8]}",
                name=None,
                sequence_no=1,
                status="PLANNED",
                stratum=None,
                item_refs=[],
                planned_item_count=len(specs),
                assignee_ids=[str(actor_id)],
                reviewer_pool_ids=[],
                due_at=None,
                submitted_item_count=0,
                approved_item_count=0,
                rejected_item_count=0,
                rework_count=0,
                quality_summary={},
                status_reason=None,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        session.add(
            AnnotationAssignment(
                id=assignment_id,
                project_id=project_id,
                task_id=task_id,
                batch_id=batch_id,
                assignee_id=actor_id,
                assignment_role="ANNOTATOR",
                status="ASSIGNED",
                planned_item_count=len(specs),
                claimed_item_count=0,
                submitted_item_count=0,
                approved_item_count=0,
                planned_minutes=30,
                actual_minutes=None,
                lease_expires_at=None,
                claimed_at=None,
                submitted_at=None,
                completed_at=None,
                revoked_reason=None,
                status_reason=None,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        session.add(
            AnnotationSubmission(
                id=submission_id,
                project_id=project_id,
                task_id=task_id,
                batch_id=batch_id,
                assignment_id=assignment_id,
                annotator_id=actor_id,
                submission_version=1,
                status="DRAFT",
                schema_id=schema_id,
                schema_version=1,
                schema_sha256=_digest(f"{prefix}-schema"),
                item_count=0,
                records_digest=_digest(f"{prefix}-records"),
                data_source="REAL",
                record_refs=[],
                review_id=None,
                arbitration_id=None,
                supersedes_id=None,
                submitted_at=None,
                decided_at=None,
                reason=None,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()

        lineage_occurrences: dict[UUID, int] = {}
        approved_episode_ids: list[UUID] = []
        record_ids: list[UUID] = []
        for spec in specs:
            episode_id = uuid4()
            asset_id = uuid4()
            lineage_id = spec.lineage or uuid4()
            occurrence = lineage_occurrences.get(lineage_id, 0) + 1
            lineage_occurrences[lineage_id] = occurrence
            manifest_sha = spec.manifest_sha or _digest(f"{prefix}:{spec.idx:02d}:manifest")
            raw_sha = spec.raw_sha or _digest(f"{prefix}:{spec.idx:02d}:raw")
            start_ns = 1_000_000 + spec.idx * 10_000
            end_ns = start_ns + 10_000
            calibration = (
                [{"calibration_id": str(uuid4()), "status": "ACTIVE"}]
                if spec.calibration_valid
                else [{"status": "INVALID"}]
            )
            episode_ids.append(episode_id)
            session.add(
                RawAssetModel(
                    id=asset_id,
                    project_id=project_id,
                    code=f"raw-{prefix}-{spec.idx}",
                    source_type="UPLOAD",
                    collection_task_id=None,
                    batch_id=None,
                    upload_session_id=None,
                    original_filename=f"{prefix}-{spec.idx}.mcap",
                    object_key=f"raw/{project_id}/{asset_id}/content.mcap",
                    size_bytes=1024 + spec.idx,
                    content_type="application/x-mcap",
                    format="MCAP",
                    sha256=raw_sha,
                    status="VALIDATING",
                    captured_at=NOW,
                    ingested_at=NOW,
                    storage_class="STANDARD",
                    retention_policy_id=None,
                    quarantine_reason=None,
                    quarantine_codes=[],
                    scan_report_key=None,
                    verified_at=NOW,
                    archived_at=None,
                    tombstoned_at=None,
                    duplicate_of_asset_id=None,
                    uploaded_by=actor_id,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            session.flush()
            session.execute(
                update(RawAssetModel)
                .where(RawAssetModel.id == asset_id)
                .values(status="VALID", version=2)
            )
            session.add(
                EpisodeModel(
                    id=episode_id,
                    project_id=project_id,
                    episode_lineage_id=lineage_id,
                    episode_version=occurrence,
                    code=f"{prefix}-{spec.idx}",
                    task_id=None,
                    batch_id=None,
                    robot_id=ROBOT_ID,
                    device_ids=[str(DEVICE_ID)],
                    sensor_ids=[],
                    status="PENDING",
                    quality_status="NOT_CHECKED",
                    annotation_status="NOT_STARTED",
                    success=True,
                    failure_reason=None,
                    scene_labels=[spec.scene or scene],
                    language_instruction=None,
                    start_time_ns=start_ns,
                    end_time_ns=end_ns,
                    duration_ns=10_000,
                    time_domain="UTC",
                    time_base={
                        "time_domain": "UTC",
                        "clock_source": "host",
                        "sync_method": "ptp",
                        "sync_status": spec.time_sync,
                    },
                    canonical_schema_version="ddp.canonical/1.0.0-draft",
                    pipeline_id="qa330-probe",
                    pipeline_version="1.0.0",
                    segmentation_rule_version="seg-v1",
                    frame_count=10,
                    calibration_snapshot=calibration,
                    manifest_key=f"canonical/{project_id}/{episode_id}/1/manifest.json",
                    manifest_sha256=manifest_sha,
                    preview_refs=[],
                    supersedes_id=None,
                    attempt=1,
                    max_attempts=3,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            session.flush()
            session.add(
                EpisodeRawAssetModel(
                    project_id=project_id,
                    episode_id=episode_id,
                    asset_id=asset_id,
                    role="PRIMARY",
                    start_time_ns=start_ns,
                    end_time_ns=end_ns,
                    sha256_snapshot=raw_sha,
                    size_bytes_snapshot=1024 + spec.idx,
                )
            )
            session.flush()
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode_id)
                .values(status="PROCESSING")
            )
            session.execute(
                update(EpisodeModel).where(EpisodeModel.id == episode_id).values(status="READY")
            )
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode_id)
                .values(status="QUALITY_CHECKING", quality_status=spec.quality)
            )
            if spec.status == "REJECTED":
                session.execute(
                    update(EpisodeModel)
                    .where(EpisodeModel.id == episode_id)
                    .values(status="REJECTED", failure_reason="QA330 验收拒绝")
                )
                continue
            if spec.status == "INVALIDATED":
                session.execute(
                    update(EpisodeModel)
                    .where(EpisodeModel.id == episode_id)
                    .values(status="INVALIDATED")
                )
                continue
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode_id)
                .values(status="ACCEPTED")
            )
            if spec.status in {"COMPLETED", "ARCHIVED"}:
                session.execute(
                    update(EpisodeModel)
                    .where(EpisodeModel.id == episode_id)
                    .values(status="COMPLETED")
                )
            if spec.status == "ARCHIVED":
                session.execute(
                    update(EpisodeModel)
                    .where(EpisodeModel.id == episode_id)
                    .values(status="ARCHIVED")
                )
            if spec.annotation_approved:
                record_id = uuid4()
                session.add(
                    AnnotationRecord(
                        id=record_id,
                        project_id=project_id,
                        submission_id=submission_id,
                        task_id=task_id,
                        episode_id=episode_id,
                        episode_version=occurrence,
                        annotation_lineage_id=uuid4(),
                        annotation_version=1,
                        schema_id=schema_id,
                        schema_version=1,
                        status="DRAFT",
                        time_range=None,
                        labels=[{"label_key": "action", "value": "PICK"}],
                        evidence_refs=[],
                        record_sha256=_digest(f"{prefix}:{spec.idx}:record"),
                        supersedes_id=None,
                        approved_by=None,
                        approved_at=None,
                        version=1,
                        created_at=NOW,
                        updated_at=NOW,
                        created_by=actor_id,
                        updated_by=actor_id,
                    )
                )
                session.flush()
                record_ids.append(record_id)
                approved_episode_ids.append(episode_id)

        if record_ids:
            session.execute(
                update(AnnotationSubmission)
                .where(AnnotationSubmission.id == submission_id)
                .values(
                    status="SUBMITTED",
                    submitted_at=NOW,
                    item_count=len(record_ids),
                    record_refs=[str(item) for item in record_ids],
                    version=2,
                )
            )
            session.execute(
                update(AnnotationRecord)
                .where(AnnotationRecord.id.in_(record_ids))
                .values(status="SUBMITTED", version=2)
            )
            session.execute(
                update(AnnotationSubmission)
                .where(AnnotationSubmission.id == submission_id)
                .values(status="IN_REVIEW", version=3)
            )
            session.execute(
                update(AnnotationSubmission)
                .where(AnnotationSubmission.id == submission_id)
                .values(status="APPROVED", decided_at=NOW, version=4)
            )
            session.execute(
                update(AnnotationRecord)
                .where(AnnotationRecord.id.in_(record_ids))
                .values(status="APPROVED", approved_by=actor_id, approved_at=NOW, version=3)
            )
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id.in_(approved_episode_ids))
                .values(annotation_status="APPROVED")
            )
        session.commit()
    return episode_ids


def seed_all_episodes(factory: sessionmaker[Session]) -> None:
    # 基线：9 ACCEPTED + 1 COMPLETED，全部 PASS 且标注已批准。
    base_specs = [EpisodeSpec(idx=i) for i in range(10)]
    base_specs[9] = EpisodeSpec(idx=9, status="COMPLETED")
    episodes["base"] = seed_episodes(
        factory, ids["project_a"], ids["editor_a"], base_specs, prefix="base"
    )

    # 选择门禁：状态、标注、时间同步、标定、WARN。
    gate_specs = [
        EpisodeSpec(idx=0, status="REJECTED"),
        EpisodeSpec(idx=1, status="INVALIDATED"),
        EpisodeSpec(idx=2, status="ARCHIVED"),
        EpisodeSpec(idx=3, annotation_approved=False),
        EpisodeSpec(idx=4, time_sync="UNVERIFIED"),
        EpisodeSpec(idx=5, calibration_valid=False),
        EpisodeSpec(idx=6, quality="WARN"),
        EpisodeSpec(idx=7),
    ]
    episodes["gate"] = seed_episodes(
        factory, ids["project_a"], ids["editor_a"], gate_specs, prefix="gate"
    )

    # 精确去重：血缘、Manifest、RawAsset 各一对重复，另两个被门禁排除。
    shared_lineage = uuid4()
    shared_manifest = _digest("dup:shared:manifest")
    shared_raw = _digest("dup:shared:raw")
    dup_specs = []
    for i in range(10):
        dup_specs.append(
            EpisodeSpec(
                idx=i,
                lineage=shared_lineage if i in {0, 1} else None,
                manifest_sha=shared_manifest if i in {2, 3} else None,
                raw_sha=shared_raw if i in {4, 5} else None,
                status="INVALIDATED" if i == 8 else "ACCEPTED",
                annotation_approved=False if i == 9 else True,
            )
        )
    episodes["dup"] = seed_episodes(
        factory, ids["project_a"], ids["editor_a"], dup_specs, prefix="dup"
    )

    # 泄漏：10 个不同血缘；其中两个共享 Manifest，用于制造跨划分硬泄漏。
    leak_manifest = _digest("leak:shared:manifest")
    leak_specs = [
        EpisodeSpec(
            idx=i,
            manifest_sha=leak_manifest if i in {0, 1} else None,
            scene="warehouse",
        )
        for i in range(10)
    ]
    episodes["leak"] = seed_episodes(
        factory, ids["project_a"], ids["editor_a"], leak_specs, prefix="leak"
    )

    # WARN 确认：三条 WARN，均标注批准。
    warn_specs = [EpisodeSpec(idx=i, quality="WARN", scene="warn-scene") for i in range(3)]
    episodes["warn"] = seed_episodes(
        factory, ids["project_a"], ids["editor_a"], warn_specs, prefix="warn"
    )

    # 跨项目：项目 B 也播种 3 条 Episode，用于跨项目隔离负向控制。
    episodes["project_b"] = seed_episodes(
        factory,
        ids["project_b"],
        ids["owner_b"],
        [EpisodeSpec(idx=i, scene="office") for i in range(3)],
        prefix="pb",
    )


# ---------------------------------------------------------------------------
# 数据集 HTTP 辅助
# ---------------------------------------------------------------------------
def create_dataset(
    user: str,
    project: str,
    code: str,
    *,
    owner_id: UUID | None = None,
    usage_policy: str = "TRAINING_ALLOWED",
    idempotency_key: str | None = None,
    area: str = "D1",
) -> dict[str, Any]:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    response = request(
        area,
        f"创建数据集 {code}",
        user,
        "POST",
        api(project, "/datasets"),
        json_body={
            "code": code,
            "name": f"数据集 {code}",
            "purpose": "TRAINING",
            "owner_id": str(owner_id or ids["owner_a"]),
            "usage_policy": usage_policy,
        },
        headers=headers,
    )
    expect_status(area, f"创建数据集 {code}", response, 201)
    return response.json()


def create_policies(
    user: str,
    project: str,
    dataset_id: str,
    *,
    dedup_keys: list[str] | None = None,
    split: dict[str, Any] | None = None,
    leakage: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    area: str = "D2",
) -> dict[str, Any]:
    dedup_payload = {
        "exact_keys": dedup_keys or ["EPISODE_LINEAGE", "MANIFEST_SHA256", "RAW_ASSET_SHA256"],
        "near_duplicate_enabled": False,
        "near_duplicate_enforcement": "WARN_ONLY",
        "retain_rule": "NEWEST_ACCEPTED",
    }
    split_payload = split or {
        "strategy": "RATIO",
        "train_ratio": 0.8,
        "validation_ratio": 0.1,
        "test_ratio": 0.1,
        "seed": 42,
        "group_by": ["EPISODE_LINEAGE"],
        "empty_split_policy": "FAIL",
        "min_item_count": 1,
        "min_duration_ns": 0,
        "rounding_rule": "LARGEST_REMAINDER",
    }
    leakage_payload = leakage or {
        "hard_rules": [
            "EPISODE_LINEAGE_CROSS_SPLIT",
            "MANIFEST_SHA256_CROSS_SPLIT",
            "RAW_ASSET_SHA256_CROSS_SPLIT",
        ],
        "soft_rules": ["SCENE_GROUP_CROSS_SPLIT"],
        "waiver_allowed_rules": ["SCENE_GROUP_CROSS_SPLIT"],
        "scene_grouping_enabled": True,
        "temporal_overlap_enabled": False,
    }
    quality_payload = quality or {
        "min_selected_count": 1,
        "allow_warn_quality": True,
        "require_leakage_pass": False,
    }
    endpoints = {
        "dedup": ("/dedup-policies", dedup_payload),
        "split": ("/split-policies", split_payload),
        "leakage": ("/leakage-policies", leakage_payload),
        "quality": ("/quality-policies", quality_payload),
    }
    result: dict[str, Any] = {}
    for name, (suffix, payload) in endpoints.items():
        response = request(
            area,
            f"创建 {name} 策略",
            user,
            "POST",
            api(project, f"/datasets/{dataset_id}{suffix}"),
            json_body=payload,
        )
        expect_status(area, f"创建 {name} 策略", response, 201)
        result[name] = response.json()
    return result


def create_version(
    user: str,
    project: str,
    dataset_id: str,
    policies: dict[str, Any],
    *,
    selection_filters: dict[str, Any],
    idempotency_key: str | None = None,
    area: str = "D2",
) -> httpx.Response:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    return request(
        area,
        "创建数据集版本",
        user,
        "POST",
        api(project, f"/datasets/{dataset_id}/versions"),
        json_body={
            "selection_rule_version": "dataset-selection/1",
            "selection_filters": selection_filters,
            "dedup_policy_id": policies["dedup"]["dedup_policy_id"],
            "dedup_policy_version": policies["dedup"]["version"],
            "split_policy_id": policies["split"]["split_policy_id"],
            "split_policy_version": policies["split"]["version"],
            "leakage_policy_id": policies["leakage"]["leakage_policy_id"],
            "leakage_policy_version": policies["leakage"]["version"],
            "quality_policy_id": policies["quality"]["quality_policy_id"],
            "quality_policy_version": policies["quality"]["version"],
        },
        headers=headers,
    )


def preview_version(
    user: str,
    project: str,
    dataset_id: str,
    version_id: str,
    policies: dict[str, Any],
    *,
    selection_filters: dict[str, Any],
    sample_limit: int = 100,
    area: str = "D2",
) -> httpx.Response:
    return request(
        area,
        "数据集选择预览",
        user,
        "POST",
        api(project, f"/datasets/{dataset_id}/versions/{version_id}/selection-preview"),
        json_body={
            "selection_filters": selection_filters,
            "dedup_policy_id": policies["dedup"]["dedup_policy_id"],
            "dedup_policy_version": policies["dedup"]["version"],
            "split_policy_id": policies["split"]["split_policy_id"],
            "split_policy_version": policies["split"]["version"],
            "leakage_policy_id": policies["leakage"]["leakage_policy_id"],
            "leakage_policy_version": policies["leakage"]["version"],
            "quality_policy_id": policies["quality"]["quality_policy_id"],
            "quality_policy_version": policies["quality"]["version"],
            "sample_limit": sample_limit,
        },
    )


def version_action(
    user: str,
    project: str,
    dataset_id: str,
    version_id: str,
    action: str,
    *,
    expected_version: int | None,
    json_body: dict[str, Any] | None = None,
    area: str = "D2",
    step: str | None = None,
) -> httpx.Response:
    headers = ifmatch(expected_version) if expected_version is not None else None
    return request(
        area,
        step or f"版本动作 {action}",
        user,
        "POST",
        api(project, f"/datasets/{dataset_id}/versions/{version_id}/actions/{action}"),
        json_body=json_body or {},
        headers=headers,
    )


def build_validate_freeze(
    user_builder: str,
    user_approver: str,
    project: str,
    dataset_id: str,
    policies: dict[str, Any],
    *,
    selection_filters: dict[str, Any],
    build_body: dict[str, Any] | None = None,
    expect_ready: bool = True,
    freeze: bool = True,
    area: str = "D1",
) -> dict[str, Any]:
    created = create_version(
        user_builder,
        project,
        dataset_id,
        policies,
        selection_filters=selection_filters,
        area=area,
    )
    expect_status(area, "创建版本返回 201", created, 201)
    version = created.json()
    built = version_action(
        user_builder,
        project,
        dataset_id,
        version["dataset_version_id"],
        "build",
        expected_version=version["version"],
        json_body=build_body or {},
        area=area,
        step="构建数据集版本",
    )
    expect_status(area, "构建数据集版本返回 200", built, 200)
    built_body = built.json()
    validated = version_action(
        user_builder,
        project,
        dataset_id,
        version["dataset_version_id"],
        "validate",
        expected_version=built_body["version"],
        area=area,
        step="校验数据集版本",
    )
    expect_status(area, "校验数据集版本返回 200", validated, 200)
    ready_body = validated.json()
    if expect_ready:
        expect_eq(area, "校验后版本为 READY", "READY", ready_body.get("status"))
    if freeze:
        frozen = version_action(
            user_approver,
            project,
            dataset_id,
            version["dataset_version_id"],
            "freeze",
            expected_version=ready_body["version"],
            json_body={
                "reason": "QA 独立核验通过",
                "payload": {"qa_verified": True, "qa_evidence": "ddp-330"},
            },
            area=area,
            step="冻结数据集版本",
        )
        expect_status(area, "冻结数据集版本返回 200", frozen, 200)
        ready_body = frozen.json()
        expect_eq(area, "冻结后版本为 FROZEN", "FROZEN", ready_body.get("status"))
        expect_eq(area, "冻结后 validity_status 保持 VALID", "VALID", ready_body.get("validity_status"))
    return ready_body


def _episode_lineages(episode_ids: list[UUID]) -> dict[UUID, UUID]:
    engine = create_engine(PG_URL)
    factory = create_session_factory(engine)
    with factory() as session:
        rows = session.execute(
            select(EpisodeModel.id, EpisodeModel.episode_lineage_id).where(
                EpisodeModel.id.in_(episode_ids)
            )
        ).all()
    engine.dispose()
    return {row[0]: row[1] for row in rows}


def _choose_leak_seed(episode_ids: list[UUID]) -> int:
    lineages = _episode_lineages(episode_ids)
    pair = (episode_ids[0], episode_ids[1])
    for seed in range(0, 5000):
        ordered = sorted(
            episode_ids,
            key=lambda episode_id: sha256_text(
                f"{seed}:{lineages[episode_id]}:{episode_id}"
            ),
        )
        positions = {episode_id: index for index, episode_id in enumerate(ordered)}
        first = positions[pair[0]]
        second = positions[pair[1]]
        # 10 个单元：0-7 TRAIN、8 VALIDATION、9 TEST。
        if (first == 9) != (second == 9):
            return seed
        if (first == 8) != (second == 8):
            return seed
    return 42


# ---------------------------------------------------------------------------
# D1：Dataset / DatasetVersion 状态机与冻结
# ---------------------------------------------------------------------------
def scenario_dataset_state_machine() -> dict[str, Any]:
    dataset = create_dataset("editor_a", "project_a", "state-ds")
    dataset_id = dataset["dataset_id"]
    expect_eq("D1", "新建数据集初始状态 DRAFT", "DRAFT", dataset.get("status"))
    policies = create_policies("editor_a", "project_a", dataset_id)
    version = create_version(
        "editor_a",
        "project_a",
        dataset_id,
        policies,
        selection_filters={"code_prefix": "base-"},
        area="D1",
    )
    expect_status("D1", "创建版本返回 201", version, 201)
    version_body = version.json()
    expect_eq("D1", "新建版本初始状态 DRAFT", "DRAFT", version_body.get("status"))
    expect_eq("D1", "新建版本 validity_status 为 VALID", "VALID", version_body.get("validity_status"))

    built = version_action(
        "editor_a",
        "project_a",
        dataset_id,
        version_body["dataset_version_id"],
        "build",
        expected_version=version_body["version"],
        area="D1",
        step="构建后进入 VALIDATING",
    )
    expect_status("D1", "构建返回 200", built, 200)
    built_body = built.json()
    expect_eq("D1", "构建后状态 VALIDATING", "VALIDATING", built_body.get("status"))

    validated = version_action(
        "editor_a",
        "project_a",
        dataset_id,
        version_body["dataset_version_id"],
        "validate",
        expected_version=built_body["version"],
        area="D1",
        step="校验后进入 READY",
    )
    expect_status("D1", "校验返回 200", validated, 200)
    ready_body = validated.json()
    expect_eq("D1", "校验后状态 READY", "READY", ready_body.get("status"))
    expect_eq(
        "D1",
        "校验后 80/10/10 划分计数",
        {"train": 8, "validation": 1, "test": 1},
        {
            "train": ready_body.get("train_count"),
            "validation": ready_body.get("validation_count"),
            "test": ready_body.get("test_count"),
        },
    )

    self_freeze = version_action(
        "editor_a",
        "project_a",
        dataset_id,
        version_body["dataset_version_id"],
        "freeze",
        expected_version=ready_body["version"],
        json_body={"payload": {"qa_verified": True}},
        area="D1",
        step="构建人自审冻结",
    )
    expect_status("D1", "构建人自审冻结返回 403", self_freeze, 403)
    expect_code("D1", "自审冻结错误码", self_freeze, "dataset_freeze_approval_forbidden")

    frozen = version_action(
        "owner_a",
        "project_a",
        dataset_id,
        version_body["dataset_version_id"],
        "freeze",
        expected_version=ready_body["version"],
        json_body={
            "reason": "QA 独立核验通过",
            "payload": {"qa_verified": True, "qa_evidence": "ddp-330"},
        },
        area="D1",
        step="独立审批人冻结",
    )
    expect_status("D1", "独立审批人冻结返回 200", frozen, 200)
    frozen_body = frozen.json()
    expect_eq("D1", "冻结后状态 FROZEN", "FROZEN", frozen_body.get("status"))
    expect_eq("D1", "冻结后 validity_status 仍为 VALID", "VALID", frozen_body.get("validity_status"))
    expect_eq("D1", "冻结后 manifest_sha256 存在", True, bool(frozen_body.get("manifest_sha256")))

    illegal = version_action(
        "editor_a",
        "project_a",
        dataset_id,
        version_body["dataset_version_id"],
        "build",
        expected_version=frozen_body["version"],
        area="D1",
        step="FROZEN 后再次构建",
    )
    expect_status("D1", "FROZEN 后非法构建返回 409", illegal, 409)
    expect_code("D1", "FROZEN 后非法构建错误码", illegal, "state_transition_invalid")

    stale = version_action(
        "owner_a",
        "project_a",
        dataset_id,
        version_body["dataset_version_id"],
        "archive",
        expected_version=frozen_body["version"] + 99,
        area="D1",
        step="陈旧 If-Match 动作",
    )
    expect_status("D1", "陈旧 If-Match 返回 409", stale, 409)
    expect_code("D1", "陈旧 If-Match 错误码", stale, "version_conflict")

    missing = version_action(
        "owner_a",
        "project_a",
        dataset_id,
        version_body["dataset_version_id"],
        "archive",
        expected_version=None,
        area="D1",
        step="缺少 If-Match 动作",
    )
    expect_status("D1", "缺少 If-Match 返回 428", missing, 428)

    dataset_after = request(
        "D1", "读取冻结后的数据集", "editor_a", "GET", api("project_a", f"/datasets/{dataset_id}")
    )
    expect_status("D1", "读取数据集返回 200", dataset_after, 200)
    dataset_body = dataset_after.json()
    expect_eq("D1", "冻结后数据集状态 ACTIVE", "ACTIVE", dataset_body.get("status"))
    expect_eq(
        "D1",
        "冻结后 current_version_id 指向该版本",
        version_body["dataset_version_id"],
        dataset_body.get("current_version_id"),
    )

    return {
        "dataset": dataset_body,
        "dataset_id": dataset_id,
        "policies": policies,
        "version": frozen_body,
        "version_id": version_body["dataset_version_id"],
    }


def scenario_dataset_permissions_and_pagination() -> None:
    for code in ("page-ds-1", "page-ds-2", "page-ds-3"):
        create_dataset("editor_a", "project_a", code, area="D8")

    page1 = request(
        "D8",
        "数据集分页第一页",
        "editor_a",
        "GET",
        api("project_a", "/datasets?limit=1"),
    )
    expect_status("D8", "分页第一页返回 200", page1, 200)
    body1 = page1.json()
    expect_eq("D8", "分页第一页仅 1 条", 1, len(body1.get("items", [])))
    expect_eq("D8", "分页第一页存在 next_cursor", True, bool(body1.get("next_cursor")))
    cursor = body1.get("next_cursor")
    page2 = request(
        "D8",
        "数据集分页第二页",
        "editor_a",
        "GET",
        api("project_a", f"/datasets?limit=1&cursor={cursor}"),
    )
    expect_status("D8", "分页第二页返回 200", page2, 200)
    body2 = page2.json()
    expect_eq("D8", "分页第二页仅 1 条", 1, len(body2.get("items", [])))
    if body1.get("items") and body2.get("items"):
        expect_eq(
            "D8",
            "分页两页 ID 不重复",
            False,
            body1["items"][0]["dataset_id"] == body2["items"][0]["dataset_id"],
        )

    viewer_create = request(
        "D8",
        "VIEWER 创建数据集",
        "viewer_a",
        "POST",
        api("project_a", "/datasets"),
        json_body={
            "code": "viewer-denied",
            "name": "拒绝",
            "purpose": "TRAINING",
            "owner_id": str(ids["owner_a"]),
            "usage_policy": "TRAINING_ALLOWED",
        },
    )
    expect_status("D8", "VIEWER 创建数据集返回 403", viewer_create, 403)
    outsider_list = request(
        "D8", "项目外用户列出数据集", "outsider", "GET", api("project_a", "/datasets")
    )
    expect_status("D8", "项目外用户列出数据集返回 403", outsider_list, 403)

    foreign = create_dataset(
        "owner_b", "project_b", "foreign-ds", owner_id=ids["owner_b"], area="D8"
    )
    cross = request(
        "D8",
        "跨项目读取数据集",
        "editor_a",
        "GET",
        api("project_a", f"/datasets/{foreign['dataset_id']}"),
    )
    expect_status("D8", "跨项目读取数据集返回 404", cross, 404)
    expect_code("D8", "跨项目读取数据集错误码", cross, "resource_not_found")

    payload = {
        "code": "idem-ds-330",
        "name": "幂等数据集",
        "purpose": "TRAINING",
        "owner_id": str(ids["owner_a"]),
        "usage_policy": "TRAINING_ALLOWED",
    }
    first = request(
        "D8",
        "幂等创建数据集",
        "editor_a",
        "POST",
        api("project_a", "/datasets"),
        json_body=payload,
        headers={"Idempotency-Key": "idem-dataset-330-0001"},
    )
    expect_status("D8", "首次幂等创建返回 201", first, 201)
    replay = request(
        "D8",
        "幂等重放创建数据集",
        "editor_a",
        "POST",
        api("project_a", "/datasets"),
        json_body=payload,
        headers={"Idempotency-Key": "idem-dataset-330-0001"},
    )
    expect_status("D8", "幂等重放返回 201", replay, 201)
    expect_eq(
        "D8",
        "幂等重放返回同一 dataset_id",
        first.json().get("dataset_id"),
        replay.json().get("dataset_id"),
    )
    reused = request(
        "D8",
        "同键异体创建数据集",
        "editor_a",
        "POST",
        api("project_a", "/datasets"),
        json_body={**payload, "name": "不同请求体"},
        headers={"Idempotency-Key": "idem-dataset-330-0001"},
    )
    expect_status("D8", "同键异体返回 409", reused, 409)
    expect_code("D8", "同键异体错误码", reused, "idempotency_key_reused")

    duplicate = request(
        "D8",
        "重复编码创建数据集",
        "editor_a",
        "POST",
        api("project_a", "/datasets"),
        json_body=payload,
    )
    expect_status("D8", "重复编码返回 409", duplicate, 409)
    extra = request(
        "D8",
        "携带权威字段创建数据集",
        "editor_a",
        "POST",
        api("project_a", "/datasets"),
        json_body={**payload, "status": "ACTIVE", "code": "extra-field-330"},
    )
    expect_status("D8", "携带权威字段返回 422", extra, 422)
    expect_code("D8", "携带权威字段错误码", extra, "validation_failed")

    dataset_id = first.json()["dataset_id"]
    missing_patch = request(
        "D8",
        "PATCH 缺少 If-Match",
        "editor_a",
        "PATCH",
        api("project_a", f"/datasets/{dataset_id}"),
        json_body={"name": "缺少 If-Match"},
    )
    expect_status("D8", "PATCH 缺少 If-Match 返回 428", missing_patch, 428)
    patched = request(
        "D8",
        "PATCH 数据集",
        "editor_a",
        "PATCH",
        api("project_a", f"/datasets/{dataset_id}"),
        json_body={"name": "新名称"},
        headers=ifmatch(1),
    )
    expect_status("D8", "PATCH 数据集返回 200", patched, 200)
    stale_patch = request(
        "D8",
        "PATCH 陈旧 If-Match",
        "editor_a",
        "PATCH",
        api("project_a", f"/datasets/{dataset_id}"),
        json_body={"name": "陈旧版本"},
        headers=ifmatch(1),
    )
    expect_status("D8", "PATCH 陈旧 If-Match 返回 409", stale_patch, 409)
    expect_code("D8", "PATCH 陈旧 If-Match 错误码", stale_patch, "version_conflict")


# ---------------------------------------------------------------------------
# D2：选择门禁、去重、划分、泄漏
# ---------------------------------------------------------------------------
def scenario_selection_gates_and_split() -> dict[str, Any]:
    dataset = create_dataset("editor_a", "project_a", "gate-ds")
    dataset_id = dataset["dataset_id"]
    policies = create_policies("editor_a", "project_a", dataset_id)
    version = create_version(
        "editor_a",
        "project_a",
        dataset_id,
        policies,
        selection_filters={"code_prefix": "gate-"},
    )
    expect_status("D2", "门禁数据集版本创建成功", version, 201)
    version_id = version.json()["dataset_version_id"]

    gate_preview = preview_version(
        "editor_a",
        "project_a",
        dataset_id,
        version_id,
        policies,
        selection_filters={"code_prefix": "gate-"},
    )
    expect_status("D2", "门禁预览返回 200", gate_preview, 200)
    gate_body = gate_preview.json()
    codes = {item["code"] for item in gate_body.get("exclusion_summary", [])}
    expect_eq("D2", "门禁候选数", 8, gate_body.get("candidate_count"))
    expect_eq("D2", "仅 1 条合格样本入选", 1, gate_body.get("selected_count"))
    expect_eq("D2", "门禁排除 7 条", 7, gate_body.get("excluded_count"))
    for code in (
        "EPISODE_STATUS_INELIGIBLE",
        "ANNOTATION_NOT_APPROVED",
        "TIME_SYNC_UNKNOWN",
        "CALIBRATION_INVALID",
        "QUALITY_WARN_UNCONFIRMED",
    ):
        expect_eq("D2", f"排除原因包含 {code}", True, code in codes)

    warn_preview = preview_version(
        "editor_a",
        "project_a",
        dataset_id,
        version_id,
        policies,
        selection_filters={"code_prefix": "warn-"},
    )
    expect_status("D2", "WARN 未确认预览返回 200", warn_preview, 200)
    warn_body = warn_preview.json()
    expect_eq("D2", "WARN 未确认时全部排除", 0, warn_body.get("selected_count"))
    warn_codes = {item["code"] for item in warn_body.get("exclusion_summary", [])}
    expect_eq("D2", "WARN 未确认排除原因", True, "QUALITY_WARN_UNCONFIRMED" in warn_codes)

    warn_confirmed = preview_version(
        "editor_a",
        "project_a",
        dataset_id,
        version_id,
        policies,
        selection_filters={
            "code_prefix": "warn-",
            "allow_warn": True,
            "warn_confirmation_reason": "QA330 质检确认",
        },
    )
    expect_status("D2", "WARN 已确认预览返回 200", warn_confirmed, 200)
    warn_confirmed_body = warn_confirmed.json()
    expect_eq("D2", "WARN 已确认后 3 条入选", 3, warn_confirmed_body.get("selected_count"))
    expect_eq(
        "D2",
        "WARN 确认产生 warning",
        True,
        any(
            str(item).startswith("QUALITY_WARN_CONFIRMED")
            for item in warn_confirmed_body.get("warnings", [])
        ),
    )

    # 选择过滤的 UUID 字段是契约字段；HTTP JSON 传入字符串 UUID 应可用。
    for field_name, values in (
        ("episode_ids", [str(episodes["base"][0])]),
        ("robot_ids", [str(ROBOT_ID)]),
        ("device_ids", [str(DEVICE_ID)]),
    ):
        filtered = preview_version(
            "editor_a",
            "project_a",
            dataset_id,
            version_id,
            policies,
            selection_filters={field_name: values},
            area="D2",
        )
        filtered_body = _safe_json(filtered)
        check(
            "D2",
            f"selection_filters.{field_name} 字符串 UUID 可通过 HTTP 使用",
            filtered.status_code == 200
            and int(filtered_body.get("selected_count", 0)) >= 1,
            200,
            {
                "status": filtered.status_code,
                "selected_count": filtered_body.get("selected_count"),
            },
            severity="major",
            note="字符串 UUID 应被解析为 UUID，而不是 422",
            evidence={"body": filtered_body},
        )
    for field_name in ("episode_ids", "robot_ids", "device_ids"):
        invalid_uuid = preview_version(
            "editor_a",
            "project_a",
            dataset_id,
            version_id,
            policies,
            selection_filters={field_name: ["not-a-uuid"]},
        )
        expect_status(
            "D2",
            f"selection_filters.{field_name} 非法 UUID 返回 422",
            invalid_uuid,
            422,
        )
        expect_code(
            "D2",
            f"selection_filters.{field_name} 非法 UUID 错误码",
            invalid_uuid,
            "validation_failed",
        )

    # 80/10/10 基线构建：保证 Episode 级划分。
    baseline = build_validate_freeze(
        "editor_a",
        "owner_a",
        "project_a",
        dataset_id,
        policies,
        selection_filters={"code_prefix": "base-"},
        area="D2",
        freeze=True,
    )
    expect_eq("D2", "基线版本 train_count", 8, baseline.get("train_count"))
    expect_eq("D2", "基线版本 validation_count", 1, baseline.get("validation_count"))
    expect_eq("D2", "基线版本 test_count", 1, baseline.get("test_count"))
    expect_eq("D2", "基线版本 item_count", 10, baseline.get("item_count"))
    expect_eq(
        "D2",
        "基线软泄漏规则为 WARN",
        "WARN",
        baseline.get("leakage_status"),
    )

    items = request(
        "D2",
        "读取基线版本条目",
        "editor_a",
        "GET",
        api(
            "project_a",
            f"/datasets/{dataset_id}/versions/{baseline['dataset_version_id']}/items?limit=100",
        ),
    )
    expect_status("D2", "读取基线条目返回 200", items, 200)
    splits = [item["split"] for item in items.json().get("items", [])]
    expect_eq("D2", "条目 TRAIN 数", 8, splits.count("TRAIN"))
    expect_eq("D2", "条目 VALIDATION 数", 1, splits.count("VALIDATION"))
    expect_eq("D2", "条目 TEST 数", 1, splits.count("TEST"))

    leakage_report = request(
        "D2",
        "读取基线泄漏报告",
        "editor_a",
        "GET",
        api(
            "project_a",
            f"/datasets/{dataset_id}/versions/{baseline['dataset_version_id']}/leakage-report",
        ),
    )
    expect_status("D2", "读取基线泄漏报告返回 200", leakage_report, 200)
    expect_eq("D2", "基线泄漏报告状态 WARN", "WARN", leakage_report.json().get("leakage_status"))

    # 空测试集：比例 0.9/0.1/0.0 且 FAIL。
    empty_dataset = create_dataset("editor_a", "project_a", "empty-test-ds")
    empty_policies = create_policies(
        "editor_a",
        "project_a",
        empty_dataset["dataset_id"],
        split={
            "strategy": "RATIO",
            "train_ratio": 0.9,
            "validation_ratio": 0.1,
            "test_ratio": 0.0,
            "seed": 42,
            "group_by": ["EPISODE_LINEAGE"],
            "empty_split_policy": "FAIL",
            "min_item_count": 1,
            "min_duration_ns": 0,
            "rounding_rule": "LARGEST_REMAINDER",
        },
    )
    empty_version = create_version(
        "editor_a",
        "project_a",
        empty_dataset["dataset_id"],
        empty_policies,
        selection_filters={"code_prefix": "base-"},
    )
    expect_status("D2", "空测试集版本创建成功", empty_version, 201)
    empty_build = version_action(
        "editor_a",
        "project_a",
        empty_dataset["dataset_id"],
        empty_version.json()["dataset_version_id"],
        "build",
        expected_version=empty_version.json()["version"],
        area="D2",
        step="空测试集构建",
    )
    expect_status("D2", "空测试集构建返回 422", empty_build, 422)
    expect_code("D2", "空测试集错误码", empty_build, "dataset_insufficient_data")

    # 显式 ALLOW_EMPTY_TEST：允许构建并记录 warning。
    allow_dataset = create_dataset("editor_a", "project_a", "allow-empty-ds")
    allow_policies = create_policies(
        "editor_a",
        "project_a",
        allow_dataset["dataset_id"],
        split={
            "strategy": "RATIO",
            "train_ratio": 0.9,
            "validation_ratio": 0.1,
            "test_ratio": 0.0,
            "seed": 42,
            "group_by": ["EPISODE_LINEAGE"],
            "empty_split_policy": "ALLOW_EMPTY_TEST",
            "min_item_count": 1,
            "min_duration_ns": 0,
            "rounding_rule": "LARGEST_REMAINDER",
        },
    )
    allow_version = create_version(
        "editor_a",
        "project_a",
        allow_dataset["dataset_id"],
        allow_policies,
        selection_filters={"code_prefix": "base-"},
    )
    expect_status("D2", "允许空测试集版本创建成功", allow_version, 201)
    allow_build = version_action(
        "editor_a",
        "project_a",
        allow_dataset["dataset_id"],
        allow_version.json()["dataset_version_id"],
        "build",
        expected_version=allow_version.json()["version"],
        area="D2",
        step="允许空测试集构建",
    )
    expect_status("D2", "允许空测试集构建返回 200", allow_build, 200)
    allow_body = allow_build.json()
    expect_eq("D2", "允许空测试集 test_count 为 0", 0, allow_body.get("test_count"))
    expect_eq(
        "D2",
        "允许空测试集记录 warning",
        True,
        "EMPTY_TEST_SPLIT_ALLOWED" in allow_body.get("warnings", []),
    )

    return {
        "dataset_id": dataset_id,
        "policies": policies,
        "version_id": baseline["dataset_version_id"],
        "version": baseline,
    }


def scenario_dedup_and_leakage() -> dict[str, Any]:
    dedup_dataset = create_dataset("editor_a", "project_a", "dedup-ds")
    dedup_policies = create_policies(
        "editor_a",
        "project_a",
        dedup_dataset["dataset_id"],
        dedup_keys=["EPISODE_LINEAGE", "MANIFEST_SHA256", "RAW_ASSET_SHA256"],
    )
    dedup_version = create_version(
        "editor_a",
        "project_a",
        dedup_dataset["dataset_id"],
        dedup_policies,
        selection_filters={"code_prefix": "dup-"},
    )
    expect_status("D2", "去重数据集版本创建成功", dedup_version, 201)
    dedup_preview = preview_version(
        "editor_a",
        "project_a",
        dedup_dataset["dataset_id"],
        dedup_version.json()["dataset_version_id"],
        dedup_policies,
        selection_filters={"code_prefix": "dup-"},
    )
    expect_status("D2", "去重预览返回 200", dedup_preview, 200)
    dedup_body = dedup_preview.json()
    expect_eq("D2", "去重候选数", 10, dedup_body.get("candidate_count"))
    expect_eq("D2", "去重前门禁排除数", 2, dedup_body.get("excluded_count"))
    expect_eq("D2", "精确去重排除数", 3, dedup_body.get("duplicated_count"))
    expect_eq("D2", "去重后入选数", 5, dedup_body.get("selected_count"))
    dedup_codes = {item["code"] for item in dedup_body.get("exclusion_summary", [])}
    for code in ("DUPLICATE", "EPISODE_STATUS_INELIGIBLE", "ANNOTATION_NOT_APPROVED"):
        expect_eq("D2", f"去重排除摘要包含 {code}", True, code in dedup_codes)

    # 硬泄漏：两个 Episode 共享 Manifest，且被确定性划分到不同 split。
    leak_seed = _choose_leak_seed(episodes["leak"])
    observe(f"硬泄漏测试选择 split seed={leak_seed}，使共享 Manifest 的 Episode 跨划分。")
    leak_dataset = create_dataset("editor_a", "project_a", "leak-ds")
    leak_policies = create_policies(
        "editor_a",
        "project_a",
        leak_dataset["dataset_id"],
        dedup_keys=["EPISODE_LINEAGE"],
        split={
            "strategy": "RATIO",
            "train_ratio": 0.8,
            "validation_ratio": 0.1,
            "test_ratio": 0.1,
            "seed": leak_seed,
            "group_by": ["EPISODE_LINEAGE"],
            "empty_split_policy": "FAIL",
            "min_item_count": 1,
            "min_duration_ns": 0,
            "rounding_rule": "LARGEST_REMAINDER",
        },
        leakage={
            "hard_rules": [
                "EPISODE_LINEAGE_CROSS_SPLIT",
                "MANIFEST_SHA256_CROSS_SPLIT",
            ],
            "soft_rules": [],
            "waiver_allowed_rules": [],
            "scene_grouping_enabled": False,
            "temporal_overlap_enabled": False,
        },
    )
    leak_version = create_version(
        "editor_a",
        "project_a",
        leak_dataset["dataset_id"],
        leak_policies,
        selection_filters={"code_prefix": "leak-"},
    )
    expect_status("D2", "硬泄漏版本创建成功", leak_version, 201)
    leak_build = version_action(
        "editor_a",
        "project_a",
        leak_dataset["dataset_id"],
        leak_version.json()["dataset_version_id"],
        "build",
        expected_version=leak_version.json()["version"],
        area="D2",
        step="硬泄漏构建",
    )
    expect_status("D2", "硬泄漏构建返回 200（进入 VALIDATING）", leak_build, 200)
    leak_build_body = leak_build.json()
    expect_eq("D2", "硬泄漏状态 FAIL", "FAIL", leak_build_body.get("leakage_status"))
    expect_eq(
        "D2",
        "硬泄漏记录 LEAKAGE_FAIL 阻断项",
        True,
        "LEAKAGE_FAIL" in leak_build_body.get("blocking_issues", []),
    )
    leak_report = request(
        "D2",
        "读取硬泄漏报告",
        "editor_a",
        "GET",
        api(
            "project_a",
            f"/datasets/{leak_dataset['dataset_id']}/versions/"
            f"{leak_version.json()['dataset_version_id']}/leakage-report",
        ),
    )
    expect_status("D2", "读取硬泄漏报告返回 200", leak_report, 200)
    leak_report_body = leak_report.json()
    expect_eq("D2", "硬泄漏报告 FAIL", "FAIL", leak_report_body.get("leakage_status"))
    expect_eq(
        "D2",
        "硬泄漏 hard_violation_count >= 1",
        True,
        (leak_report_body.get("hard_violation_count") or 0) >= 1,
    )
    leak_validate = version_action(
        "editor_a",
        "project_a",
        leak_dataset["dataset_id"],
        leak_version.json()["dataset_version_id"],
        "validate",
        expected_version=leak_build_body["version"],
        area="D2",
        step="硬泄漏版本校验",
    )
    expect_status("D2", "硬泄漏版本校验返回 200", leak_validate, 200)
    expect_eq(
        "D2",
        "硬泄漏版本校验后 VALIDATION_FAILED",
        "VALIDATION_FAILED",
        leak_validate.json().get("status"),
    )
    leak_freeze = version_action(
        "owner_a",
        "project_a",
        leak_dataset["dataset_id"],
        leak_version.json()["dataset_version_id"],
        "freeze",
        expected_version=leak_validate.json()["version"],
        json_body={"payload": {"qa_verified": True}},
        area="D2",
        step="硬泄漏版本冻结",
    )
    expect_status("D2", "硬泄漏版本冻结返回 409", leak_freeze, 409)

    # 硬规则豁免被拒绝。
    hard_waiver_dataset = create_dataset("editor_a", "project_a", "hard-waiver-ds")
    hard_waiver_policies = create_policies(
        "editor_a",
        "project_a",
        hard_waiver_dataset["dataset_id"],
        dedup_keys=["EPISODE_LINEAGE"],
        leakage={
            "hard_rules": ["EPISODE_LINEAGE_CROSS_SPLIT"],
            "soft_rules": [],
            "waiver_allowed_rules": [],
            "scene_grouping_enabled": False,
            "temporal_overlap_enabled": False,
        },
    )
    hard_waiver_version = create_version(
        "editor_a",
        "project_a",
        hard_waiver_dataset["dataset_id"],
        hard_waiver_policies,
        selection_filters={"code_prefix": "base-"},
    )
    expect_status("D2", "硬规则豁免版本创建成功", hard_waiver_version, 201)
    hard_waiver_build = version_action(
        "editor_a",
        "project_a",
        hard_waiver_dataset["dataset_id"],
        hard_waiver_version.json()["dataset_version_id"],
        "build",
        expected_version=hard_waiver_version.json()["version"],
        json_body={
            "waivers": [
                {
                    "rule": "EPISODE_LINEAGE_CROSS_SPLIT",
                    "reason": "尝试豁免硬规则",
                }
            ]
        },
        area="D2",
        step="硬规则豁免构建",
    )
    expect_status("D2", "硬规则豁免返回 403", hard_waiver_build, 403)
    expect_code("D2", "硬规则豁免错误码", hard_waiver_build, "dataset_leakage_waiver_forbidden")

    # 软规则豁免 -> WAIVED。
    waived_dataset = create_dataset("editor_a", "project_a", "waived-ds")
    waived_policies = create_policies(
        "editor_a",
        "project_a",
        waived_dataset["dataset_id"],
        dedup_keys=["EPISODE_LINEAGE"],
        leakage={
            "hard_rules": ["EPISODE_LINEAGE_CROSS_SPLIT"],
            "soft_rules": ["SCENE_GROUP_CROSS_SPLIT"],
            "waiver_allowed_rules": ["SCENE_GROUP_CROSS_SPLIT"],
            "scene_grouping_enabled": True,
            "temporal_overlap_enabled": False,
        },
    )
    waived_version = create_version(
        "editor_a",
        "project_a",
        waived_dataset["dataset_id"],
        waived_policies,
        selection_filters={"code_prefix": "base-"},
    )
    expect_status("D2", "软规则豁免版本创建成功", waived_version, 201)
    waived_build = version_action(
        "editor_a",
        "project_a",
        waived_dataset["dataset_id"],
        waived_version.json()["dataset_version_id"],
        "build",
        expected_version=waived_version.json()["version"],
        json_body={
            "waivers": [
                {
                    "rule": "SCENE_GROUP_CROSS_SPLIT",
                    "reason": "场景组跨划分已人工确认",
                    "impact": "仅影响同场景样本分布",
                }
            ]
        },
        area="D2",
        step="软规则豁免构建",
    )
    expect_status("D2", "软规则豁免构建返回 200", waived_build, 200)
    expect_eq("D2", "软规则豁免后状态 WAIVED", "WAIVED", waived_build.json().get("leakage_status"))

    # 泄漏 UNKNOWN 失败关闭：硬规则要求相似簇但能力未启用。
    unknown_dataset = create_dataset("editor_a", "project_a", "unknown-leak-ds")
    unknown_policies = create_policies(
        "editor_a",
        "project_a",
        unknown_dataset["dataset_id"],
        dedup_keys=["EPISODE_LINEAGE"],
        leakage={
            "hard_rules": ["SIMILARITY_GROUP_CROSS_SPLIT"],
            "soft_rules": [],
            "waiver_allowed_rules": [],
            "similarity_grouping_enabled": False,
            "scene_grouping_enabled": True,
            "temporal_overlap_enabled": False,
        },
    )
    unknown_version = create_version(
        "editor_a",
        "project_a",
        unknown_dataset["dataset_id"],
        unknown_policies,
        selection_filters={"code_prefix": "base-"},
    )
    expect_status("D2", "UNKNOWN 泄漏版本创建成功", unknown_version, 201)
    unknown_build = version_action(
        "editor_a",
        "project_a",
        unknown_dataset["dataset_id"],
        unknown_version.json()["dataset_version_id"],
        "build",
        expected_version=unknown_version.json()["version"],
        area="D2",
        step="UNKNOWN 泄漏构建",
    )
    expect_status("D2", "UNKNOWN 泄漏构建返回 200", unknown_build, 200)
    unknown_body = unknown_build.json()
    expect_eq("D2", "UNKNOWN 泄漏状态 UNKNOWN", "UNKNOWN", unknown_body.get("leakage_status"))
    expect_eq(
        "D2",
        "UNKNOWN 泄漏记录 LEAKAGE_UNKNOWN 阻断项",
        True,
        "LEAKAGE_UNKNOWN" in unknown_body.get("blocking_issues", []),
    )
    unknown_validate = version_action(
        "editor_a",
        "project_a",
        unknown_dataset["dataset_id"],
        unknown_version.json()["dataset_version_id"],
        "validate",
        expected_version=unknown_body["version"],
        area="D2",
        step="UNKNOWN 泄漏校验",
    )
    expect_status("D2", "UNKNOWN 泄漏校验返回 200", unknown_validate, 200)
    expect_eq(
        "D2",
        "UNKNOWN 泄漏校验后 VALIDATION_FAILED（失败关闭）",
        "VALIDATION_FAILED",
        unknown_validate.json().get("status"),
    )
    unknown_freeze = version_action(
        "owner_a",
        "project_a",
        unknown_dataset["dataset_id"],
        unknown_version.json()["dataset_version_id"],
        "freeze",
        expected_version=unknown_validate.json()["version"],
        json_body={"payload": {"qa_verified": True}},
        area="D2",
        step="UNKNOWN 泄漏冻结",
    )
    expect_status("D2", "UNKNOWN 泄漏冻结返回 409", unknown_freeze, 409)

    return {"dedup_body": dedup_body, "leak_build": leak_build_body}


# ---------------------------------------------------------------------------
# 导出辅助
# ---------------------------------------------------------------------------
engine = create_engine(PG_URL)
factory: sessionmaker[Session] = create_session_factory(engine)


def worker_settings() -> Settings:
    return Settings(
        env="local",
        database_url=PG_URL,
        s3_endpoint=S3_ENDPOINT,
        s3_access_key=S3_ACCESS_KEY,
        s3_secret_key=S3_SECRET_KEY,
        s3_bucket=QA_BUCKET,
        s3_region="us-east-1",
        export_storage_prefix="exports",
        export_default_timeout_seconds=3600,
        export_max_timeout_seconds=86400,
        export_default_max_attempts=3,
        export_max_attempts_limit=20,
        export_artifact_chunk_size_bytes=1024 * 1024,
        export_worker_poll_interval_seconds=0.1,
        export_worker_max_attempts=5,
        export_worker_retry_base_seconds=0.1,
        export_worker_retry_max_seconds=1.0,
        rate_limit_enabled=False,
    )


WORKER_SETTINGS = worker_settings()
REAL_STORAGE = S3MultipartStorage(WORKER_SETTINGS)


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name="us-east-1",
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def start_managed_minio() -> subprocess.Popen[str]:
    """启动一次性真实 MinIO；独立端口、独立临时目录、随机凭据。"""
    MINIO_DATA_DIR.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update(
        {
            "MINIO_ROOT_USER": S3_ACCESS_KEY,
            "MINIO_ROOT_PASSWORD": S3_SECRET_KEY,
            "MINIO_BROWSER": "off",
        }
    )
    log_path = EVIDENCE / "minio.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            "/usr/local/bin/minio",
            "server",
            str(MINIO_DATA_DIR),
            "--address",
            f"127.0.0.1:{S3_PORT}",
            "--console-address",
            f"127.0.0.1:{S3_PORT + 1}",
        ],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 40
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"MinIO 提前退出，见 {log_path}")
        try:
            if httpx.get(f"{S3_ENDPOINT}/minio/health/live", timeout=1.0).status_code == 200:
                observe(
                    f"临时真实 MinIO 已启动：{S3_ENDPOINT}，数据目录 {MINIO_DATA_DIR}。"
                )
                return process
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    process.terminate()
    raise RuntimeError(f"MinIO 未能启动，见 {log_path}")


def stop_managed_minio() -> None:
    if minio_process is None:
        return
    minio_process.terminate()
    try:
        minio_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        minio_process.kill()


def ensure_qa_bucket() -> None:
    client_obj = s3_client()
    names = {item["Name"] for item in client_obj.list_buckets().get("Buckets", [])}
    if QA_BUCKET not in names:
        client_obj.create_bucket(Bucket=QA_BUCKET)
    observe(f"导出验收使用独立 MinIO 桶：{QA_BUCKET}（未触碰 ddp-staging-raw）。")


def run_worker(storage: Any, *, now: datetime | None = None):
    worker = ExportWorker(factory, WORKER_SETTINGS, storage)
    return worker.process_once(now=now)


def worker_drain(storage: Any, *, max_events: int = 10, now: datetime | None = None) -> list[Any]:
    results = []
    for _ in range(max_events):
        result = run_worker(storage, now=now)
        results.append(result)
        if str(result.status) == "IDLE":
            break
    return results


class FaultyStorage:
    """显式故障注入包装器：底层仍是真实 MinIO，只对指定写/读注入失败。"""

    def __init__(
        self,
        inner: Any,
        *,
        fail_puts_after: int | None = None,
        corrupt_reads_after: int | None = None,
    ) -> None:
        self.inner = inner
        self.fail_puts_after = fail_puts_after
        self.corrupt_reads_after = corrupt_reads_after

    def put_bytes(self, object_key: str, data: bytes, *, content_type: str) -> None:
        if self.fail_puts_after is not None:
            if self.fail_puts_after <= 0:
                from app.domain.ingestion.errors import ObjectStorageUnavailableError

                raise ObjectStorageUnavailableError("QA330 注入：写入失败")
            self.fail_puts_after -= 1
        self.inner.put_bytes(object_key, data, content_type=content_type)

    def stream_object(self, object_key: str, *, chunk_size: int) -> Iterable[bytes]:
        data = b"".join(self.inner.stream_object(object_key, chunk_size=chunk_size))
        if self.corrupt_reads_after is not None:
            if self.corrupt_reads_after <= 0:
                data = data + b"corrupted"
            else:
                self.corrupt_reads_after -= 1
        yield data

    def presign_download(self, object_key: str, *, expires_in_seconds: int):
        return self.inner.presign_download(
            object_key, expires_in_seconds=expires_in_seconds
        )


def export_payload(version_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dataset_version_id": version_id,
        "format": "LE_ROBOT",
        "format_version": "ddp.lerobot/1.0.0",
        "exporter_id": "ddp.lerobot-metadata",
        "exporter_version": "1.0.0",
        "mapping_version": "ddp.lerobot/1.0.0",
        "config": {"include_sidecar": True},
    }
    payload.update(overrides)
    return payload


def create_export_job(
    user: str,
    project: str,
    version_id: str,
    key: str | None,
    *,
    overrides: dict[str, Any] | None = None,
    area: str = "D3",
) -> httpx.Response:
    headers = {"Idempotency-Key": key} if key else None
    return request(
        area,
        "创建导出任务",
        user,
        "POST",
        api(project, "/exports"),
        json_body=export_payload(version_id, **(overrides or {})),
        headers=headers,
    )


def export_detail(user: str, project: str, job_id: str, *, area: str = "D3") -> httpx.Response:
    return request(
        area,
        "读取导出任务",
        user,
        "GET",
        api(project, f"/exports/{job_id}"),
    )


def export_action(
    user: str,
    project: str,
    job_id: str,
    action: str,
    *,
    expected_version: int | None,
    json_body: dict[str, Any] | None = None,
    area: str = "D3",
) -> httpx.Response:
    headers = ifmatch(expected_version) if expected_version is not None else None
    return request(
        area,
        f"导出任务动作 {action}",
        user,
        "POST",
        api(project, f"/exports/{job_id}/actions/{action}"),
        json_body=json_body or {},
        headers=headers,
    )


def _download_and_hash(url: str) -> tuple[int, bytes]:
    response = httpx.get(url, timeout=20.0)
    return response.status_code, response.content


def scenario_export_success_and_acl(baseline: dict[str, Any], grant_ctx: dict[str, Any]) -> dict[str, Any]:
    version_id = baseline["version_id"]
    dataset_id = baseline["dataset_id"]

    missing = create_export_job("editor_a", "project_a", version_id, None)
    expect_status("D3", "缺少 Idempotency-Key 创建导出返回 428", missing, 428)

    viewer_create = create_export_job(
        "viewer_a", "project_a", version_id, "export-viewer-330"
    )
    expect_status("D3", "VIEWER 创建导出返回 403", viewer_create, 403)

    draft_dataset = create_dataset("editor_a", "project_a", "draft-export-ds", area="D3")
    draft_policies = create_policies(
        "editor_a", "project_a", draft_dataset["dataset_id"], area="D3"
    )
    draft_version = create_version(
        "editor_a",
        "project_a",
        draft_dataset["dataset_id"],
        draft_policies,
        selection_filters={"code_prefix": "base-"},
        area="D3",
    )
    expect_status("D3", "DRAFT 版本创建成功", draft_version, 201)
    draft_id = draft_version.json()["dataset_version_id"]
    not_frozen = create_export_job("editor_a", "project_a", draft_id, "export-not-frozen-330")
    expect_status("D3", "未冻结版本导出返回 409", not_frozen, 409)
    expect_code("D3", "未冻结版本导出错误码", not_frozen, "dataset_version_not_frozen")

    unsupported = create_export_job(
        "editor_a",
        "project_a",
        draft_id,
        "export-format-bad-330",
        overrides={"format": "PARQUET"},
    )
    expect_status("D3", "未知导出格式返回 422", unsupported, 422)
    expect_code("D3", "未知导出格式错误码", unsupported, "export_format_unsupported")

    bad_version = create_export_job(
        "editor_a",
        "project_a",
        draft_id,
        "export-version-bad-330",
        overrides={"format_version": "ddp.lerobot/9.9.9"},
    )
    expect_status("D3", "未知导出格式版本返回 422", bad_version, 422)
    expect_code("D3", "未知导出格式版本错误码", bad_version, "export_format_version_unsupported")

    bad_config = create_export_job(
        "editor_a",
        "project_a",
        draft_id,
        "export-config-bad-330",
        overrides={"config": {"api_token": "must-not-store"}},
    )
    expect_status("D3", "凭据类配置返回 422", bad_config, 422)
    expect_code("D3", "凭据类配置错误码", bad_config, "export_config_invalid")

    created = create_export_job("editor_a", "project_a", version_id, "export-success-330")
    expect_status("D3", "成功导出任务创建返回 202", created, 202)
    job = created.json()
    job_id = job["export_job_id"]
    expect_eq("D3", "导出任务初始状态 QUEUED", "QUEUED", job.get("status"))
    expect_eq("D3", "导出任务 attempt 为 0", 0, job.get("attempt"))
    expect_eq("D3", "导出验证状态 UNVERIFIED", "UNVERIFIED", job.get("verification_status"))
    expect_eq(
        "D3",
        "导出 output_prefix 以 exports/ 开头",
        True,
        str(job.get("output_prefix", "")).startswith("exports/"),
    )
    expect_eq("D3", "导出 config_sha256 存在", True, bool(job.get("config_sha256")))

    replay = create_export_job("editor_a", "project_a", version_id, "export-success-330")
    expect_status("D3", "同键同体幂等重放返回 202", replay, 202)
    expect_eq("D3", "幂等重放返回同一导出任务", job_id, replay.json().get("export_job_id"))

    reused = create_export_job(
        "editor_a",
        "project_a",
        version_id,
        "export-success-330",
        overrides={"config": {"include_sidecar": False}},
    )
    expect_status("D3", "同键异体创建导出返回 409", reused, 409)
    expect_code("D3", "同键异体导出错误码", reused, "idempotency_key_reused")

    listed = request(
        "D3", "列出导出任务", "viewer_a", "GET", api("project_a", "/exports")
    )
    expect_status("D3", "列出导出任务返回 200", listed, 200)
    listed_ids = {item["export_job_id"] for item in listed.json().get("items", [])}
    expect_eq("D3", "导出列表包含刚创建任务", True, job_id in listed_ids)

    detail = export_detail("viewer_a", "project_a", job_id)
    expect_status("D3", "VIEWER 读取导出任务返回 200", detail, 200)

    outsider = request(
        "D3", "项目外用户列出导出任务", "outsider", "GET", api("project_a", "/exports")
    )
    expect_status("D3", "项目外用户列出导出返回 403", outsider, 403)
    cross = request(
        "D3",
        "跨项目读取导出任务",
        "owner_b",
        "GET",
        api("project_b", f"/exports/{job_id}"),
    )
    expect_status("D3", "跨项目读取导出任务返回 404", cross, 404)
    expect_code("D3", "跨项目读取导出任务错误码", cross, "resource_not_found")

    result = run_worker(REAL_STORAGE)
    expect_eq("D3", "导出 Worker 处理事件状态", "PUBLISHED", str(result.status))
    success = export_detail("editor_a", "project_a", job_id)
    expect_status("D3", "读取成功导出任务返回 200", success, 200)
    success_body = success.json()
    expect_eq("D3", "导出任务成功状态 SUCCEEDED", "SUCCEEDED", success_body.get("status"))
    expect_eq("D3", "导出任务 attempt 为 1", 1, success_body.get("attempt"))
    expect_eq("D3", "导出产物数量为 4", 4, success_body.get("artifact_count"))
    expect_eq("D3", "导出总字节数大于 0", True, (success_body.get("total_bytes") or 0) > 0)
    expect_eq(
        "D3",
        "导出 aggregate_sha256 存在",
        True,
        str(success_body.get("aggregate_sha256", "")).startswith("sha256:"),
    )
    expect_eq(
        "D3",
        "导出 artifact_manifest_sha256 存在",
        True,
        str(success_body.get("artifact_manifest_sha256", "")).startswith("sha256:"),
    )
    expect_eq("D3", "真实 LeRobot Loader 未验证，状态保持 UNVERIFIED", "UNVERIFIED", success_body.get("verification_status"))

    manifest = request(
        "D3",
        "读取导出产物清单",
        "editor_a",
        "GET",
        api("project_a", f"/exports/{job_id}/artifacts"),
    )
    expect_status("D3", "读取导出产物清单返回 200", manifest, 200)
    manifest_body = manifest.json()
    paths = {item["path"] for item in manifest_body.get("artifacts", [])}
    expect_eq(
        "D3",
        "导出产物路径集合",
        {
            "meta/info.json",
            "meta/episodes.jsonl",
            "meta/ddp_divisions.json",
            "ddp/sidecar.json",
        },
        paths,
    )
    expect_eq(
        "D3",
        "产物清单 aggregate_sha256 与任务一致",
        success_body.get("aggregate_sha256"),
        manifest_body.get("aggregate_sha256"),
    )

    # 逐文件下载并核对 size / SHA256；汇总 Hash 由清单条目复算。
    artifact_entries = []
    for entry in sorted(manifest_body.get("artifacts", []), key=lambda item: item["path"]):
        download = request(
            "D3",
            f"获取产物下载地址 {entry['path']}",
            "editor_a",
            "GET",
            api("project_a", f"/exports/{job_id}/artifacts/{entry['path']}/download"),
        )
        expect_status("D3", f"产物下载地址 {entry['path']} 返回 200", download, 200)
        url = download.json().get("download_url")
        status, content = _download_and_hash(url)
        expect_eq("D3", f"MinIO 下载 {entry['path']} 返回 200", 200, status)
        expect_eq("D3", f"产物 {entry['path']} 大小一致", entry["size_bytes"], len(content))
        actual_sha = sha256_text(content.decode("utf-8"))
        expect_eq("D3", f"产物 {entry['path']} SHA256 一致", entry["sha256"], actual_sha)
        artifact_entries.append(
            {
                "path": entry["path"],
                "size_bytes": entry["size_bytes"],
                "sha256": entry["sha256"],
                "content_type": entry["content_type"],
            }
        )
    recomputed = sha256_text(canonical_json({"artifacts": artifact_entries}))
    expect_eq(
        "D3",
        "逐文件摘要复算得到汇总 SHA256",
        success_body.get("aggregate_sha256"),
        recomputed,
    )

    viewer_manifest = request(
        "D3",
        "VIEWER 读取导出产物清单",
        "viewer_a",
        "GET",
        api("project_a", f"/exports/{job_id}/artifacts"),
    )
    expect_status("D3", "VIEWER 读取产物清单返回 200", viewer_manifest, 200)
    outsider_artifact = request(
        "D3",
        "项目外用户读取产物清单",
        "outsider",
        "GET",
        api("project_a", f"/exports/{job_id}/artifacts"),
    )
    expect_status("D3", "项目外用户读取产物清单返回 403", outsider_artifact, 403)

    traversal_paths = (
        f"/api/v1/projects/{ids['project_a']}/exports/{job_id}/artifacts/%2e%2e/secret/download",
        f"/api/v1/projects/{ids['project_a']}/exports/{job_id}/artifacts/..%2Fsecret/download",
        f"/api/v1/projects/{ids['project_a']}/exports/{job_id}/artifacts/%2Fetc%2Fpasswd/download",
        f"/api/v1/projects/{ids['project_a']}/exports/{job_id}/artifacts/..%5Csecret/download",
    )
    for index, path in enumerate(traversal_paths):
        response = request(
            "D3",
            f"路径穿越尝试 {index + 1}",
            "editor_a",
            "GET",
            path,
        )
        expect_status("D3", f"路径穿越尝试 {index + 1} 返回 404", response, 404)

    direct_download = request(
        "D3",
        "已登记产物下载",
        "editor_a",
        "GET",
        api("project_a", f"/exports/{job_id}/artifacts/meta/info.json/download"),
    )
    expect_status("D3", "已登记产物下载返回 200", direct_download, 200)

    # DOWNLOAD 授权：跨项目 grantee 可读产物；无授权 403。
    grantee_denied = request(
        "D3",
        "无 DOWNLOAD 授权跨项目读产物",
        "grantee_b",
        "GET",
        api("project_a", f"/exports/{job_id}/artifacts"),
    )
    expect_status("D3", "无 DOWNLOAD 授权跨项目读产物返回 403", grantee_denied, 403)
    download_grant = grant_ctx.get("download_grant")
    if download_grant:
        approved_download = grant_action(
            "approver_a",
            "project_a",
            grant_ctx["dataset_id"],
            download_grant["grant_id"],
            "approve",
            expected_version=download_grant["version"],
        )
        expect_status("D5", "审批 DOWNLOAD 授权返回 200", approved_download, 200)
        grantee_allowed = request(
            "D3",
            "持有 DOWNLOAD 授权跨项目读产物",
            "grantee_b",
            "GET",
            api("project_a", f"/exports/{job_id}/artifacts"),
        )
        expect_status("D3", "持有 DOWNLOAD 授权跨项目读产物返回 200", grantee_allowed, 200)

    second = run_worker(REAL_STORAGE)
    expect_eq("D3", "再次运行 Worker 为空闲", "IDLE", str(second.status))

    return {
        "job_id": job_id,
        "job": success_body,
        "manifest": manifest_body,
        "dataset_id": dataset_id,
    }


from app.infrastructure.db.models.datasets import DatasetVersionModel  # noqa: E402


def _set_version_validity(version_id: str, value: str) -> None:
    with factory() as session:
        session.execute(
            update(DatasetVersionModel)
            .where(DatasetVersionModel.id == UUID(version_id))
            .values(validity_status=value)
        )
        session.commit()


def _list_objects(prefix: str) -> list[str]:
    client_obj = s3_client()
    keys: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": QA_BUCKET, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        response = client_obj.list_objects_v2(**kwargs)
        keys.extend(item["Key"] for item in response.get("Contents", []))
        if not response.get("IsTruncated"):
            break
        token = response.get("NextContinuationToken")
    return keys


def _manual_start(job_id: str) -> datetime:
    with factory() as session:
        repo = SqlExportJobRepository(session)
        scope = ProjectScope(ids["project_a"], actor_id=ids["editor_a"])
        current = repo.get(scope, UUID(job_id))
        assert current is not None
        started_at = datetime.now(UTC)
        repo.save(scope, current.start(started_at), expected_version=current.row_version)
        session.commit()
        return started_at


def scenario_export_failures(baseline: dict[str, Any]) -> None:
    version_id = baseline["version_id"]

    # FAILED -> retry -> SUCCEEDED。
    failed_job_resp = create_export_job(
        "editor_a", "project_a", version_id, "export-fail-retry-330"
    )
    expect_status("D3", "失败重试任务创建返回 202", failed_job_resp, 202)
    failed_job_id = failed_job_resp.json()["export_job_id"]
    faulty = FaultyStorage(REAL_STORAGE, fail_puts_after=0)
    result = run_worker(faulty)
    expect_eq("D3", "故障注入 Worker 处理事件", "PUBLISHED", str(result.status))
    failed = export_detail("editor_a", "project_a", failed_job_id)
    expect_status("D3", "读取失败任务返回 200", failed, 200)
    failed_body = failed.json()
    expect_eq("D3", "注入写入失败后状态 FAILED", "FAILED", failed_body.get("status"))
    expect_eq("D3", "失败任务 attempt 为 1", 1, failed_body.get("attempt"))
    expect_eq("D3", "失败任务错误码", "export_job_failed", failed_body.get("error_code"))
    expect_eq("D3", "失败任务记录 finished_at", True, bool(failed_body.get("finished_at")))

    retried = export_action(
        "editor_a",
        "project_a",
        failed_job_id,
        "retry",
        expected_version=failed_body["version"],
    )
    expect_status("D3", "FAILED 任务重试返回 202", retried, 202)
    expect_eq("D3", "重试后状态 QUEUED", "QUEUED", retried.json().get("status"))
    expect_eq("D3", "重试后 attempt 为 2", 2, retried.json().get("attempt"))
    result = run_worker(REAL_STORAGE)
    expect_eq("D3", "重试 Worker 处理事件", "PUBLISHED", str(result.status))
    succeeded = export_detail("editor_a", "project_a", failed_job_id).json()
    expect_eq("D3", "重试后 SUCCEEDED", "SUCCEEDED", succeeded.get("status"))
    expect_eq("D3", "重试后 attempt 为 2", 2, succeeded.get("attempt"))
    expect_eq("D3", "重试后产物数量 4", 4, succeeded.get("artifact_count"))

    # PARTIAL 不得视为成功，且不可重试。
    partial_resp = create_export_job(
        "editor_a", "project_a", version_id, "export-partial-330"
    )
    expect_status("D3", "部分失败任务创建返回 202", partial_resp, 202)
    partial_job_id = partial_resp.json()["export_job_id"]
    faulty = FaultyStorage(REAL_STORAGE, fail_puts_after=1)
    result = run_worker(faulty)
    expect_eq("D3", "部分失败 Worker 处理事件", "PUBLISHED", str(result.status))
    partial = export_detail("editor_a", "project_a", partial_job_id).json()
    expect_eq("D3", "写出部分产物后状态 PARTIAL", "PARTIAL", partial.get("status"))
    expect_eq("D3", "PARTIAL 错误码", "export_partial", partial.get("error_code"))
    expect_eq("D3", "PARTIAL 已写产物数 1", 1, partial.get("artifact_count"))
    partial_manifest = request(
        "D3",
        "PARTIAL 任务读取产物清单",
        "editor_a",
        "GET",
        api("project_a", f"/exports/{partial_job_id}/artifacts"),
    )
    expect_status("D3", "PARTIAL 产物清单返回 409", partial_manifest, 409)
    expect_code("D3", "PARTIAL 产物清单错误码", partial_manifest, "export_incomplete")
    partial_retry = export_action(
        "editor_a",
        "project_a",
        partial_job_id,
        "retry",
        expected_version=partial["version"],
    )
    expect_status("D3", "PARTIAL 重试返回 409", partial_retry, 409)
    expect_code("D3", "PARTIAL 重试错误码", partial_retry, "state_transition_invalid")
    idle = run_worker(REAL_STORAGE)
    expect_eq("D3", "PARTIAL 后 Worker 空闲", "IDLE", str(idle.status))
    expect_eq(
        "D3",
        "PARTIAL 不被再次运行升级为成功",
        "PARTIAL",
        export_detail("editor_a", "project_a", partial_job_id).json().get("status"),
    )

    # 回读校验失败 -> PARTIAL。
    checksum_resp = create_export_job(
        "editor_a", "project_a", version_id, "export-checksum-330"
    )
    expect_status("D3", "回读校验任务创建返回 202", checksum_resp, 202)
    checksum_job_id = checksum_resp.json()["export_job_id"]
    faulty = FaultyStorage(REAL_STORAGE, corrupt_reads_after=0)
    result = run_worker(faulty)
    expect_eq("D3", "回读校验 Worker 处理事件", "PUBLISHED", str(result.status))
    checksum = export_detail("editor_a", "project_a", checksum_job_id).json()
    expect_eq("D3", "回读校验失败状态 PARTIAL", "PARTIAL", checksum.get("status"))
    expect_eq(
        "D3",
        "回读校验失败错误码",
        "export_artifact_verify_failed",
        checksum.get("error_code"),
    )

    # 超时：手工进入 RUNNING，Worker 以未来时间处理。
    timeout_resp = create_export_job(
        "editor_a",
        "project_a",
        version_id,
        "export-timeout-330",
        overrides={"timeout_seconds": 1},
    )
    expect_status("D3", "超时任务创建返回 202", timeout_resp, 202)
    timeout_job_id = timeout_resp.json()["export_job_id"]
    started_at = _manual_start(timeout_job_id)
    result = run_worker(REAL_STORAGE, now=started_at + timedelta(seconds=30))
    expect_eq("D3", "超时 Worker 处理事件", "PUBLISHED", str(result.status))
    timed_out = export_detail("editor_a", "project_a", timeout_job_id).json()
    expect_eq("D3", "超时任务状态 FAILED", "FAILED", timed_out.get("status"))
    expect_eq("D3", "超时任务错误码", "export_job_timeout", timed_out.get("error_code"))

    # 取消：未开始前取消，Worker 不得写产物。
    cancel_resp = create_export_job(
        "editor_a", "project_a", version_id, "export-cancel-330"
    )
    expect_status("D3", "取消任务创建返回 202", cancel_resp, 202)
    cancel_job = cancel_resp.json()
    cancel_job_id = cancel_job["export_job_id"]
    missing = export_action(
        "editor_a",
        "project_a",
        cancel_job_id,
        "cancel",
        expected_version=None,
        json_body={"reason": "不再需要"},
    )
    expect_status("D3", "取消缺少 If-Match 返回 428", missing, 428)
    stale = export_action(
        "editor_a",
        "project_a",
        cancel_job_id,
        "cancel",
        expected_version=99,
        json_body={"reason": "不再需要"},
    )
    expect_status("D3", "取消陈旧 If-Match 返回 409", stale, 409)
    cancelled = export_action(
        "editor_a",
        "project_a",
        cancel_job_id,
        "cancel",
        expected_version=cancel_job["version"],
        json_body={"reason": "不再需要"},
    )
    expect_status("D3", "取消导出任务返回 200", cancelled, 200)
    expect_eq("D3", "取消后状态 CANCELLED", "CANCELLED", cancelled.json().get("status"))
    result = run_worker(REAL_STORAGE)
    expect_eq("D3", "取消后 Worker 处理事件", "PUBLISHED", str(result.status))
    expect_eq(
        "D3",
        "取消后状态仍为 CANCELLED",
        "CANCELLED",
        export_detail("editor_a", "project_a", cancel_job_id).json().get("status"),
    )
    objects = _list_objects(cancel_job["output_prefix"])
    expect_eq("D3", "取消任务未写出任何产物", [], objects)
    cancel_retry = export_action(
        "editor_a",
        "project_a",
        cancel_job_id,
        "retry",
        expected_version=cancelled.json()["version"],
    )
    expect_status("D3", "取消后重试返回 410", cancel_retry, 410)
    expect_code("D3", "取消后重试错误码", cancel_retry, "export_job_cancelled")

    # 最大尝试次数：FAILED 且 attempt == max_attempts 时不可重试。
    max_resp = create_export_job(
        "editor_a",
        "project_a",
        version_id,
        "export-max-attempts-330",
        overrides={"max_attempts": 1},
    )
    expect_status("D3", "最大尝试次数任务创建返回 202", max_resp, 202)
    max_job_id = max_resp.json()["export_job_id"]
    faulty = FaultyStorage(REAL_STORAGE, fail_puts_after=0)
    run_worker(faulty)
    max_failed = export_detail("editor_a", "project_a", max_job_id).json()
    expect_eq("D3", "最大尝试任务首次失败", "FAILED", max_failed.get("status"))
    max_retry = export_action(
        "editor_a",
        "project_a",
        max_job_id,
        "retry",
        expected_version=max_failed["version"],
    )
    expect_status("D3", "达到最大尝试次数后重试返回 409", max_retry, 409)
    expect_code("D3", "最大尝试次数错误码", max_retry, "export_job_not_retryable")

    # 上游失效：已创建任务在 Worker 执行前 validity 变为 STALE。
    stale_dataset = create_dataset("editor_a", "project_a", "stale-export-ds", area="D3")
    stale_policies = create_policies(
        "editor_a", "project_a", stale_dataset["dataset_id"], area="D3"
    )
    stale_version = build_validate_freeze(
        "editor_a",
        "owner_a",
        "project_a",
        stale_dataset["dataset_id"],
        stale_policies,
        selection_filters={"code_prefix": "base-"},
        area="D3",
    )
    stale_job_resp = create_export_job(
        "editor_a", "project_a", stale_version["dataset_version_id"], "export-stale-330"
    )
    expect_status("D3", "失效前创建导出任务返回 202", stale_job_resp, 202)
    stale_job = stale_job_resp.json()
    _set_version_validity(stale_version["dataset_version_id"], "STALE")
    stale_create = create_export_job(
        "editor_a", "project_a", stale_version["dataset_version_id"], "export-stale-create-330"
    )
    expect_status("D3", "STALE 版本创建导出返回 409", stale_create, 409)
    expect_code("D3", "STALE 版本创建导出错误码", stale_create, "dataset_upstream_invalidated")
    run_worker(REAL_STORAGE)
    stale_job_body = export_detail("editor_a", "project_a", stale_job["export_job_id"]).json()
    expect_eq("D3", "STALE 版本 Worker 执行后 FAILED", "FAILED", stale_job_body.get("status"))
    expect_eq(
        "D3",
        "STALE 版本 Worker 错误码",
        "dataset_upstream_invalidated",
        stale_job_body.get("error_code"),
    )
    stale_objects = _list_objects(stale_job["output_prefix"])
    expect_eq("D3", "STALE 版本未写出产物", [], stale_objects)


# ---------------------------------------------------------------------------
# D5：DatasetGrant 跨项目授权
# ---------------------------------------------------------------------------
def grant_payload(
    *,
    dataset_version_id: str | None,
    grantee_project_id: UUID,
    scope: list[str],
    expires_at: datetime,
    reason: str = "QA330 跨项目授权",
) -> dict[str, Any]:
    return {
        "dataset_version_id": dataset_version_id,
        "grantee_project_id": str(grantee_project_id),
        "grantee_team": "项目 B 算法组",
        "scope": scope,
        "reason": reason,
        "expires_at": expires_at.isoformat(),
    }


def create_grant(
    user: str,
    project: str,
    dataset_id: str,
    payload: dict[str, Any],
    key: str | None = None,
) -> httpx.Response:
    headers = {"Idempotency-Key": key} if key else None
    return request(
        "D5",
        "创建数据集授权",
        user,
        "POST",
        api(project, f"/datasets/{dataset_id}/grants"),
        json_body=payload,
        headers=headers,
    )


def grant_action(
    user: str,
    project: str,
    dataset_id: str,
    grant_id: str,
    action: str,
    *,
    expected_version: int | None,
    reason: str | None = None,
) -> httpx.Response:
    headers = ifmatch(expected_version) if expected_version is not None else None
    return request(
        "D5",
        f"授权动作 {action}",
        user,
        "POST",
        api(project, f"/datasets/{dataset_id}/grants/{grant_id}/actions/{action}"),
        json_body={"reason": reason} if reason else {},
        headers=headers,
    )


def scenario_dataset_grants(baseline: dict[str, Any]) -> dict[str, Any]:
    dataset_id = baseline["dataset_id"]
    version_id = baseline["version_id"]
    result: dict[str, Any] = {}

    no_grant_read = request(
        "D5",
        "无授权跨项目读取数据集",
        "grantee_b",
        "GET",
        api("project_a", f"/datasets/{dataset_id}"),
    )
    expect_status("D5", "无授权跨项目读取数据集返回 403", no_grant_read, 403)
    no_grant_list = request(
        "D5",
        "无授权跨项目列出数据集",
        "grantee_b",
        "GET",
        api("project_a", "/datasets"),
    )
    expect_status("D5", "无授权跨项目列出数据集返回 403", no_grant_list, 403)

    editor_create = create_grant(
        "editor_a",
        "project_a",
        dataset_id,
        grant_payload(
            dataset_version_id=version_id,
            grantee_project_id=ids["project_b"],
            scope=["VIEW"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        key="grant-editor-denied-330",
    )
    expect_status("D5", "EDITOR 创建授权返回 403", editor_create, 403)

    same_project = create_grant(
        "owner_a",
        "project_a",
        dataset_id,
        grant_payload(
            dataset_version_id=version_id,
            grantee_project_id=ids["project_a"],
            scope=["VIEW"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        key="grant-same-project-330",
    )
    expect_status("D5", "同项目授权返回 422", same_project, 422)

    past_expiry = create_grant(
        "owner_a",
        "project_a",
        dataset_id,
        grant_payload(
            dataset_version_id=version_id,
            grantee_project_id=ids["project_b"],
            scope=["VIEW"],
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        ),
        key="grant-past-330",
    )
    expect_status("D5", "过去到期时间授权返回 410", past_expiry, 410)
    expect_code("D5", "过去到期时间授权错误码", past_expiry, "dataset_grant_expired")

    invalid_scope = create_grant(
        "owner_a",
        "project_a",
        dataset_id,
        grant_payload(
            dataset_version_id=version_id,
            grantee_project_id=ids["project_b"],
            scope=["ADMIN"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        key="grant-invalid-scope-330",
    )
    expect_status("D5", "非法授权范围返回 422", invalid_scope, 422)

    view_payload = grant_payload(
        dataset_version_id=version_id,
        grantee_project_id=ids["project_b"],
        scope=["VIEW"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    view_created = create_grant(
        "owner_a", "project_a", dataset_id, view_payload, key="grant-view-330"
    )
    expect_status("D5", "创建 VIEW 授权返回 201", view_created, 201)
    view_grant = view_created.json()
    expect_eq("D5", "新建授权状态 PENDING_APPROVAL", "PENDING_APPROVAL", view_grant.get("status"))

    replay = create_grant(
        "owner_a", "project_a", dataset_id, view_payload, key="grant-view-330"
    )
    expect_status("D5", "授权幂等重放返回 201", replay, 201)
    expect_eq("D5", "授权幂等重放返回同一 grant_id", view_grant["grant_id"], replay.json().get("grant_id"))

    self_approve = grant_action(
        "owner_a",
        "project_a",
        dataset_id,
        view_grant["grant_id"],
        "approve",
        expected_version=view_grant["version"],
    )
    expect_status("D5", "授权创建人自审批返回 403", self_approve, 403)
    expect_code("D5", "授权自审批错误码", self_approve, "dataset_grant_forbidden")

    approved = grant_action(
        "approver_a",
        "project_a",
        dataset_id,
        view_grant["grant_id"],
        "approve",
        expected_version=view_grant["version"],
    )
    expect_status("D5", "第二 PO 审批授权返回 200", approved, 200)
    approved_body = approved.json()
    expect_eq("D5", "审批后授权状态 ACTIVE", "ACTIVE", approved_body.get("status"))
    expect_eq("D5", "授权记录审批人", str(ids["approver_a"]), approved_body.get("approved_by"))

    granted_dataset = request(
        "D5",
        "持有 VIEW 授权跨项目读取数据集",
        "grantee_b",
        "GET",
        api("project_a", f"/datasets/{dataset_id}"),
    )
    expect_status("D5", "持有 VIEW 授权跨项目读取数据集返回 200", granted_dataset, 200)
    granted_version = request(
        "D5",
        "持有 VIEW 授权跨项目读取版本",
        "grantee_b",
        "GET",
        api("project_a", f"/datasets/{dataset_id}/versions/{version_id}"),
    )
    expect_status("D5", "持有 VIEW 授权跨项目读取版本返回 200", granted_version, 200)
    granted_versions = request(
        "D5",
        "持有 VIEW 授权跨项目列出版本",
        "grantee_b",
        "GET",
        api("project_a", f"/datasets/{dataset_id}/versions"),
    )
    expect_status("D5", "持有 VIEW 授权跨项目列出版本返回 200", granted_versions, 200)

    view_only_export = create_export_job(
        "grantee_b", "project_a", version_id, "grantee-view-export-330"
    )
    expect_status("D5", "仅 VIEW 授权导出返回 403", view_only_export, 403)

    grant_list_owner = request(
        "D5",
        "授权方列出授权",
        "owner_a",
        "GET",
        api("project_a", f"/datasets/{dataset_id}/grants"),
    )
    expect_status("D5", "授权方列出授权返回 200", grant_list_owner, 200)
    grant_list_grantee = request(
        "D5",
        "接收方列出授权",
        "grantee_b",
        "GET",
        api("project_a", f"/datasets/{dataset_id}/grants"),
    )
    expect_status("D5", "接收方列出授权返回 403", grant_list_grantee, 403)

    export_payload_body = grant_payload(
        dataset_version_id=version_id,
        grantee_project_id=ids["project_b"],
        scope=["EXPORT"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        reason="联合训练导出",
    )
    export_created = create_grant(
        "owner_a", "project_a", dataset_id, export_payload_body, key="grant-export-330"
    )
    expect_status("D5", "创建 EXPORT 授权返回 201", export_created, 201)
    export_grant = export_created.json()
    export_approved = grant_action(
        "approver_a",
        "project_a",
        dataset_id,
        export_grant["grant_id"],
        "approve",
        expected_version=export_grant["version"],
    )
    expect_status("D5", "审批 EXPORT 授权返回 200", export_approved, 200)

    granted_export = create_export_job(
        "grantee_b", "project_a", version_id, "grantee-export-granted-330"
    )
    expect_status("D5", "持有 EXPORT 授权跨项目创建导出返回 202", granted_export, 202)
    granted_job = granted_export.json()
    cancelled = export_action(
        "owner_a",
        "project_a",
        granted_job["export_job_id"],
        "cancel",
        expected_version=granted_job["version"],
        json_body={"reason": "QA330 授权导出冒烟后取消"},
    )
    expect_status("D5", "授权方取消跨项目导出返回 200", cancelled, 200)
    worker_drain(REAL_STORAGE)

    download_payload = grant_payload(
        dataset_version_id=version_id,
        grantee_project_id=ids["project_b"],
        scope=["DOWNLOAD"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        reason="联合训练产物下载",
    )
    download_created = create_grant(
        "owner_a", "project_a", dataset_id, download_payload, key="grant-download-330"
    )
    expect_status("D5", "创建 DOWNLOAD 授权返回 201", download_created, 201)
    download_grant = download_created.json()
    expect_eq("D5", "DOWNLOAD 授权保持 PENDING_APPROVAL", "PENDING_APPROVAL", download_grant.get("status"))
    result["download_grant"] = download_grant
    result["dataset_id"] = dataset_id

    # 撤销立即失效（独立数据集，避免其他 ACTIVE VIEW 授权掩盖）。
    revoke_dataset = create_dataset("editor_a", "project_a", "grant-revoke-ds", area="D5")
    revoke_payload = grant_payload(
        dataset_version_id=None,
        grantee_project_id=ids["project_b"],
        scope=["VIEW"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        reason="待撤销授权",
    )
    revoke_created = create_grant(
        "owner_a",
        "project_a",
        revoke_dataset["dataset_id"],
        revoke_payload,
        key="grant-revoke-330",
    )
    expect_status("D5", "创建待撤销授权返回 201", revoke_created, 201)
    revoke_grant = revoke_created.json()
    revoke_approved = grant_action(
        "approver_a",
        "project_a",
        revoke_dataset["dataset_id"],
        revoke_grant["grant_id"],
        "approve",
        expected_version=revoke_grant["version"],
    )
    expect_status("D5", "审批待撤销授权返回 200", revoke_approved, 200)
    revoked = grant_action(
        "approver_a",
        "project_a",
        revoke_dataset["dataset_id"],
        revoke_grant["grant_id"],
        "revoke",
        expected_version=revoke_approved.json()["version"],
        reason="合作结束",
    )
    expect_status("D5", "撤销授权返回 200", revoked, 200)
    expect_eq("D5", "撤销后授权状态 REVOKED", "REVOKED", revoked.json().get("status"))
    revoked_access = request(
        "D5",
        "撤销后立即读取数据集",
        "grantee_b",
        "GET",
        api("project_a", f"/datasets/{revoke_dataset['dataset_id']}"),
    )
    expect_status("D5", "撤销后立即读取返回 410", revoked_access, 410)
    expect_code("D5", "撤销后立即读取错误码", revoked_access, "dataset_grant_revoked")

    # 到期立即失效（不依赖后台任务）。
    expire_dataset = create_dataset("editor_a", "project_a", "grant-expire-ds", area="D5")
    expire_payload = grant_payload(
        dataset_version_id=None,
        grantee_project_id=ids["project_b"],
        scope=["VIEW"],
        expires_at=datetime.now(UTC) + timedelta(seconds=2),
        reason="短时授权",
    )
    expire_created = create_grant(
        "owner_a",
        "project_a",
        expire_dataset["dataset_id"],
        expire_payload,
        key="grant-expire-330",
    )
    expect_status("D5", "创建短时授权返回 201", expire_created, 201)
    expire_grant = expire_created.json()
    expire_approved = grant_action(
        "approver_a",
        "project_a",
        expire_dataset["dataset_id"],
        expire_grant["grant_id"],
        "approve",
        expected_version=expire_grant["version"],
    )
    expect_status("D5", "审批短时授权返回 200", expire_approved, 200)
    time.sleep(2.4)
    expired_access = request(
        "D5",
        "到期后立即读取数据集",
        "grantee_b",
        "GET",
        api("project_a", f"/datasets/{expire_dataset['dataset_id']}"),
    )
    expect_status("D5", "到期后立即读取返回 410", expired_access, 410)
    expect_code("D5", "到期后立即读取错误码", expired_access, "dataset_grant_expired")
    expire_detail = request(
        "D5",
        "授权方读取到期授权详情",
        "owner_a",
        "GET",
        api("project_a", f"/datasets/{expire_dataset['dataset_id']}/grants/{expire_grant['grant_id']}"),
    )
    expect_status("D5", "读取到期授权详情返回 200", expire_detail, 200)
    expect_eq("D5", "授权详情派生状态 EXPIRED", "EXPIRED", expire_detail.json().get("status"))

    result.update(
        {
            "view_grant": approved_body,
            "export_grant": export_approved.json(),
            "revoke_grant": revoked.json(),
            "expire_grant": expire_detail.json(),
        }
    )
    return result


# ---------------------------------------------------------------------------
# D4：TrainingRequest / Revision / Delivery / ExternalReference
# ---------------------------------------------------------------------------
def requirement_snapshot(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "business_goal": "训练抓取策略",
        "model_type": "diffusion-policy",
        "training_type": "IMITATION",
        "data_modalities": ["RGB", "PROPRIOCEPTION"],
        "scene_targets": ["kitchen"],
        "action_targets": ["pick"],
        "robot_targets": ["arm-a"],
        "device_targets": ["cam-a"],
        "required_labels": ["action"],
        "required_schema_versions": [],
        "target_episode_count": 10,
        "target_duration_ns": None,
        "quality_thresholds": {"min_quality_score": 0.8},
        "split_requirements": {
            "train_ratio": 0.8,
            "validation_ratio": 0.1,
            "test_ratio": 0.1,
        },
        "leakage_policy_id": None,
        "format": "LE_ROBOT",
        "format_version": "ddp.lerobot/1.0.0",
        "delivery_due_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        "acceptance_method": "离线回放验收",
        "priority": "P1",
    }
    payload.update(overrides)
    return payload


def training_request_payload(
    owner_id: UUID,
    *,
    priority: str = "P1",
    snapshot_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": f"TR-{uuid4().hex[:8]}",
        "title": "抓取策略训练数据需求",
        "owner_id": str(owner_id),
        "priority": priority,
        "snapshot": requirement_snapshot(priority=priority, **(snapshot_overrides or {})),
    }


def create_training_request(
    user: str,
    project: str,
    owner_id: UUID,
    *,
    key: str | None,
    snapshot_overrides: dict[str, Any] | None = None,
) -> httpx.Response:
    headers = {"Idempotency-Key": key} if key else None
    return request(
        "D4",
        "创建训练需求",
        user,
        "POST",
        api(project, "/training-requests"),
        json_body=training_request_payload(owner_id, snapshot_overrides=snapshot_overrides),
        headers=headers,
    )


def training_request_detail(user: str, project: str, request_id: str) -> httpx.Response:
    return request(
        "D4",
        "读取训练需求",
        user,
        "GET",
        api(project, f"/training-requests/{request_id}"),
    )


def training_revision_detail(
    user: str, project: str, request_id: str, revision_id: str
) -> httpx.Response:
    return request(
        "D4",
        "读取需求 revision",
        user,
        "GET",
        api(project, f"/training-requests/{request_id}/revisions/{revision_id}"),
    )


def revision_action(
    user: str,
    project: str,
    request_id: str,
    revision_id: str,
    action: str,
    *,
    expected_version: int | None,
    reason: str | None = None,
) -> httpx.Response:
    headers = ifmatch(expected_version) if expected_version is not None else None
    return request(
        "D4",
        f"需求 revision 动作 {action}",
        user,
        "POST",
        api(
            project,
            f"/training-requests/{request_id}/revisions/{revision_id}/actions/{action}",
        ),
        json_body={"reason": reason} if reason else {},
        headers=headers,
    )


def approve_training_request(
    user: str, project: str, request_body: dict[str, Any]
) -> dict[str, Any]:
    request_id = request_body["training_request_id"]
    revision_id = request_body["current_revision_id"]
    revision = training_revision_detail(user, project, request_id, revision_id).json()
    submitted = revision_action(
        user,
        project,
        request_id,
        revision_id,
        "submit",
        expected_version=revision["version"],
    )
    if submitted.status_code != 200:
        return submitted.json()
    submitted_body = submitted.json()
    approved = revision_action(
        "approver_a",
        project,
        request_id,
        revision_id,
        "approve",
        expected_version=submitted_body["version"],
    )
    return approved.json()


def scenario_training_requests_and_delivery(
    baseline: dict[str, Any], success_export: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    project = "project_a"

    invalid = create_training_request(
        "editor_a",
        project,
        ids["owner_a"],
        key="training-invalid-330",
        snapshot_overrides={"business_goal": ""},
    )
    expect_status("D4", "缺字段训练需求返回 422", invalid, 422)

    viewer_create = create_training_request(
        "viewer_a", project, ids["owner_a"], key="training-viewer-330"
    )
    expect_status("D4", "VIEWER 创建训练需求返回 403", viewer_create, 403)
    outsider_list = request(
        "D4", "项目外用户列出训练需求", "outsider", "GET", api(project, "/training-requests")
    )
    expect_status("D4", "项目外用户列出训练需求返回 403", outsider_list, 403)

    create_payload = training_request_payload(ids["owner_a"])
    created = request(
        "D4",
        "创建训练需求",
        "editor_a",
        "POST",
        api(project, "/training-requests"),
        json_body=create_payload,
        headers={"Idempotency-Key": "training-create-330"},
    )
    expect_status("D4", "创建训练需求返回 201", created, 201)
    request_body = created.json()
    request_id = request_body["training_request_id"]
    expect_eq("D4", "训练需求初始状态 DRAFT", "DRAFT", request_body.get("status"))
    expect_eq("D4", "训练需求初始 revision_no", 1, request_body.get("current_revision_no"))

    replay = request(
        "D4",
        "训练需求幂等重放",
        "editor_a",
        "POST",
        api(project, "/training-requests"),
        json_body=create_payload,
        headers={"Idempotency-Key": "training-create-330"},
    )
    expect_status("D4", "训练需求幂等重放返回 201", replay, 201)
    expect_eq("D4", "训练需求幂等重放返回同一 ID", request_id, replay.json().get("training_request_id"))
    reused = request(
        "D4",
        "训练需求同键异体",
        "editor_a",
        "POST",
        api(project, "/training-requests"),
        json_body={**create_payload, "title": "不同标题"},
        headers={"Idempotency-Key": "training-create-330"},
    )
    expect_status("D4", "训练需求同键异体返回 409", reused, 409)
    expect_code("D4", "训练需求同键异体错误码", reused, "idempotency_key_reused")

    revision = training_revision_detail(
        "editor_a", project, request_id, request_body["current_revision_id"]
    )
    expect_status("D4", "读取初始 revision 返回 200", revision, 200)
    revision_body = revision.json()
    expect_eq("D4", "初始 revision 状态 DRAFT", "DRAFT", revision_body.get("status"))
    expect_eq(
        "D4",
        "revision_sha256 存在",
        True,
        str(revision_body.get("revision_sha256", "")).startswith("sha256:"),
    )

    missing_if_match = revision_action(
        "owner_a",
        project,
        request_id,
        revision_body["revision_id"],
        "submit",
        expected_version=None,
    )
    expect_status("D4", "revision 提交缺少 If-Match 返回 428", missing_if_match, 428)

    submitted = revision_action(
        "owner_a",
        project,
        request_id,
        revision_body["revision_id"],
        "submit",
        expected_version=revision_body["version"],
    )
    expect_status("D4", "提交 revision 返回 200", submitted, 200)
    submitted_body = submitted.json()
    expect_eq("D4", "提交后 revision PENDING_REVIEW", "PENDING_REVIEW", submitted_body.get("status"))
    expect_eq("D4", "提交人记录", str(ids["owner_a"]), submitted_body.get("submitted_by"))

    self_approve = revision_action(
        "owner_a",
        project,
        request_id,
        revision_body["revision_id"],
        "approve",
        expected_version=submitted_body["version"],
    )
    expect_status("D4", "提交人自审批返回 403", self_approve, 403)
    expect_code("D4", "提交人自审批错误码", self_approve, "training_request_approval_forbidden")

    approved = revision_action(
        "approver_a",
        project,
        request_id,
        revision_body["revision_id"],
        "approve",
        expected_version=submitted_body["version"],
    )
    expect_status("D4", "第二 PO 审批 revision 返回 200", approved, 200)
    approved_body = approved.json()
    expect_eq("D4", "审批后 revision APPROVED", "APPROVED", approved_body.get("status"))
    expect_eq("D4", "审批人记录", str(ids["approver_a"]), approved_body.get("approved_by"))

    patched = request(
        "D4",
        "修改已批准 revision",
        "editor_a",
        "PATCH",
        api(project, f"/training-requests/{request_id}/revisions/{revision_body['revision_id']}"),
        json_body={"snapshot": {**revision_body["snapshot"], "business_goal": "擅自修改"}},
        headers=ifmatch(approved_body["version"]),
    )
    expect_status("D4", "修改已批准 revision 返回 409", patched, 409)
    expect_code("D4", "修改已批准 revision 错误码", patched, "training_request_immutable")

    new_revision = request(
        "D4",
        "创建 revision v2",
        "editor_a",
        "POST",
        api(project, f"/training-requests/{request_id}/revisions"),
        json_body={
            "snapshot": {**revision_body["snapshot"], "target_episode_count": 20},
            "change_reason": "扩大样本量",
        },
        headers={"Idempotency-Key": "training-revision-330"},
    )
    expect_status("D4", "创建 revision v2 返回 201", new_revision, 201)
    new_revision_body = new_revision.json()
    expect_eq("D4", "revision v2 编号 2", 2, new_revision_body.get("revision_no"))
    expect_eq("D4", "revision v2 状态 DRAFT", "DRAFT", new_revision_body.get("status"))
    expect_eq(
        "D4",
        "revision v2 supersedes v1",
        revision_body["revision_id"],
        new_revision_body.get("supersedes_revision_id"),
    )
    old_revision = training_revision_detail(
        "editor_a", project, request_id, revision_body["revision_id"]
    ).json()
    expect_eq("D4", "旧 revision 状态 SUPERSEDED", "SUPERSEDED", old_revision.get("status"))
    expect_eq(
        "D4",
        "旧 revision 摘要保持不变",
        revision_body.get("revision_sha256"),
        old_revision.get("revision_sha256"),
    )
    request_after = training_request_detail("editor_a", project, request_id).json()
    expect_eq("D4", "需求当前 revision_no 为 2", 2, request_after.get("current_revision_no"))

    # 交付场景使用单独需求 R2，保持当前 revision 为已批准。
    r2_created = create_training_request(
        "editor_a", project, ids["owner_a"], key="training-delivery-request-330"
    )
    expect_status("D4", "创建交付需求返回 201", r2_created, 201)
    r2 = r2_created.json()
    r2_approved = approve_training_request("owner_a", project, r2)
    expect_eq("D4", "交付需求 revision 已批准", "APPROVED", r2_approved.get("status"))
    r2_detail = training_request_detail("approver_a", project, r2["training_request_id"]).json()
    expect_eq("D4", "交付需求状态 APPROVED", "APPROVED", r2_detail.get("status"))
    result["r2"] = r2_detail
    result["r2_revision"] = r2_approved

    delivery_payload_body = {
        "training_request_id": r2_detail["training_request_id"],
        "training_request_revision_id": r2_detail["current_revision_id"],
        "dataset_version_id": baseline["version_id"],
        "export_job_id": success_export["job_id"],
        "recipient_id": str(ids["owner_a"]),
        "recipient_team": "算法组",
        "delivery_note_no": f"DN-{uuid4().hex[:8]}",
    }
    delivery_created = request(
        "D4",
        "创建训练交付",
        "editor_a",
        "POST",
        api(project, "/training-deliveries"),
        json_body=delivery_payload_body,
        headers={"Idempotency-Key": "training-delivery-330"},
    )
    expect_status("D4", "创建训练交付返回 201", delivery_created, 201)
    delivery = delivery_created.json()
    expect_eq("D4", "交付初始状态 DRAFT", "DRAFT", delivery.get("status"))
    expect_eq(
        "D4",
        "交付 item_count 与版本一致",
        baseline.get("version", {}).get("item_count"),
        delivery.get("item_count"),
    )
    expect_eq(
        "D4",
        "交付产物清单 Hash 与导出任务一致",
        success_export["job"].get("artifact_manifest_sha256"),
        delivery.get("artifact_manifest_sha256"),
    )
    expect_eq(
        "D4",
        "交付汇总 Hash 与导出任务一致",
        success_export["job"].get("aggregate_sha256"),
        delivery.get("aggregate_sha256"),
    )

    delivery_replay = request(
        "D4",
        "训练交付幂等重放",
        "editor_a",
        "POST",
        api(project, "/training-deliveries"),
        json_body=delivery_payload_body,
        headers={"Idempotency-Key": "training-delivery-330"},
    )
    expect_status("D4", "训练交付幂等重放返回 201", delivery_replay, 201)
    expect_eq(
        "D4",
        "训练交付幂等重放返回同一 ID",
        delivery["training_delivery_id"],
        delivery_replay.json().get("training_delivery_id"),
    )

    missing_ready = request(
        "D4",
        "交付就绪缺少 If-Match",
        "editor_a",
        "POST",
        api(project, f"/training-deliveries/{delivery['training_delivery_id']}/actions/ready"),
        json_body={},
    )
    expect_status("D4", "交付就绪缺少 If-Match 返回 428", missing_ready, 428)

    ready = request(
        "D4",
        "交付就绪",
        "editor_a",
        "POST",
        api(project, f"/training-deliveries/{delivery['training_delivery_id']}/actions/ready"),
        json_body={},
        headers=ifmatch(delivery["version"]),
    )
    expect_status("D4", "交付就绪返回 200", ready, 200)
    delivered = request(
        "D4",
        "交付发送",
        "editor_a",
        "POST",
        api(project, f"/training-deliveries/{delivery['training_delivery_id']}/actions/deliver"),
        json_body={},
        headers=ifmatch(ready.json()["version"]),
    )
    expect_status("D4", "交付发送返回 200", delivered, 200)
    expect_eq("D4", "交付发送后状态 DELIVERED", "DELIVERED", delivered.json().get("status"))

    self_accept = request(
        "D4",
        "交付创建人自验收",
        "editor_a",
        "POST",
        api(project, f"/training-deliveries/{delivery['training_delivery_id']}/actions/accept"),
        json_body={"acceptance_opinion": "自验"},
        headers=ifmatch(delivered.json()["version"]),
    )
    expect_status("D4", "交付创建人自验收返回 403", self_accept, 403)
    expect_code("D4", "交付自验收错误码", self_accept, "training_delivery_acceptance_forbidden")

    rejected = request(
        "D4",
        "验收方驳回交付",
        "owner_a",
        "POST",
        api(project, f"/training-deliveries/{delivery['training_delivery_id']}/actions/reject"),
        json_body={"reason": "划分比例与需求不一致"},
        headers=ifmatch(delivered.json()["version"]),
    )
    expect_status("D4", "验收方驳回交付返回 200", rejected, 200)
    expect_eq("D4", "驳回后交付 REJECTED", "REJECTED", rejected.json().get("status"))
    request_after_reject = training_request_detail(
        "owner_a", project, r2_detail["training_request_id"]
    ).json()
    expect_eq(
        "D4",
        "驳回后训练需求 REVISION_REQUIRED",
        "REVISION_REQUIRED",
        request_after_reject.get("status"),
    )

    second_payload = {
        **delivery_payload_body,
        "delivery_note_no": f"DN-{uuid4().hex[:8]}",
    }
    second_created = request(
        "D4",
        "创建第二笔交付",
        "editor_a",
        "POST",
        api(project, "/training-deliveries"),
        json_body=second_payload,
        headers={"Idempotency-Key": "training-delivery-second-330"},
    )
    expect_status("D4", "创建第二笔交付返回 201", second_created, 201)
    second_delivery = second_created.json()
    expect_eq(
        "D4",
        "第二笔交付引用同一 revision",
        delivery.get("training_request_revision_id"),
        second_delivery.get("training_request_revision_id"),
    )

    reworked = request(
        "D4",
        "第一笔交付返工",
        "editor_a",
        "POST",
        api(project, f"/training-deliveries/{delivery['training_delivery_id']}/actions/rework"),
        json_body={
            "reason": "返工范围：重新导出并复核",
            "rework_ref": second_delivery["training_delivery_id"],
        },
        headers=ifmatch(rejected.json()["version"]),
    )
    expect_status("D4", "交付返工返回 200", reworked, 200)
    reworked_body = reworked.json()
    expect_eq("D4", "返工后旧交付 REWORK", "REWORK", reworked_body.get("status"))
    expect_eq(
        "D4",
        "返工记录 rework_ref",
        second_delivery["training_delivery_id"],
        reworked_body.get("rework_ref"),
    )
    expect_eq(
        "D4",
        "返工不改变旧交付 dataset_version_id",
        delivery.get("dataset_version_id"),
        reworked_body.get("dataset_version_id"),
    )

    second_ready = request(
        "D4",
        "第二笔交付就绪",
        "editor_a",
        "POST",
        api(project, f"/training-deliveries/{second_delivery['training_delivery_id']}/actions/ready"),
        json_body={},
        headers=ifmatch(second_delivery["version"]),
    )
    expect_status("D4", "第二笔交付就绪返回 200", second_ready, 200)
    second_delivered = request(
        "D4",
        "第二笔交付发送",
        "editor_a",
        "POST",
        api(project, f"/training-deliveries/{second_delivery['training_delivery_id']}/actions/deliver"),
        json_body={},
        headers=ifmatch(second_ready.json()["version"]),
    )
    expect_status("D4", "第二笔交付发送返回 200", second_delivered, 200)
    accepted = request(
        "D4",
        "验收方接受第二笔交付",
        "owner_a",
        "POST",
        api(project, f"/training-deliveries/{second_delivery['training_delivery_id']}/actions/accept"),
        json_body={"acceptance_opinion": "Manifest、校验和与验收单一致"},
        headers=ifmatch(second_delivered.json()["version"]),
    )
    expect_status("D4", "验收方接受交付返回 200", accepted, 200)
    expect_eq("D4", "第二笔交付 ACCEPTED", "ACCEPTED", accepted.json().get("status"))
    final_request = training_request_detail(
        "owner_a", project, r2_detail["training_request_id"]
    ).json()
    expect_eq("D4", "训练需求最终 ACCEPTED", "ACCEPTED", final_request.get("status"))
    old_delivery = request(
        "D4",
        "读取旧交付历史",
        "editor_a",
        "GET",
        api(project, f"/training-deliveries/{delivery['training_delivery_id']}"),
    ).json()
    expect_eq("D4", "旧交付历史保留 REWORK", "REWORK", old_delivery.get("status"))

    # 跨项目隔离。
    cross_read = request(
        "D4",
        "跨项目读取训练需求",
        "owner_b",
        "GET",
        api(project, f"/training-requests/{r2_detail['training_request_id']}"),
    )
    expect_status("D4", "跨项目读取训练需求返回 403", cross_read, 403)
    cross_wrong_project = request(
        "D4",
        "以项目 B 路径读取项目 A 需求",
        "owner_b",
        "GET",
        api("project_b", f"/training-requests/{r2_detail['training_request_id']}"),
    )
    expect_status("D4", "项目 B 路径读取项目 A 需求返回 404", cross_wrong_project, 404)
    expect_code(
        "D4",
        "项目 B 路径读取项目 A 需求错误码",
        cross_wrong_project,
        "resource_not_found",
    )

    # 未批准 revision 不得交付。
    r3_created = create_training_request(
        "editor_a", project, ids["owner_a"], key="training-r3-330"
    )
    expect_status("D4", "创建未批准需求返回 201", r3_created, 201)
    r3 = r3_created.json()
    not_approved = request(
        "D4",
        "未批准需求创建交付",
        "editor_a",
        "POST",
        api(project, "/training-deliveries"),
        json_body={
            **delivery_payload_body,
            "training_request_id": r3["training_request_id"],
            "training_request_revision_id": r3["current_revision_id"],
            "delivery_note_no": f"DN-{uuid4().hex[:8]}",
        },
        headers={"Idempotency-Key": "training-delivery-not-approved-330"},
    )
    expect_status("D4", "未批准需求创建交付返回 409", not_approved, 409)
    expect_code(
        "D4",
        "未批准需求创建交付错误码",
        not_approved,
        "training_request_not_approved",
    )

    # ExternalReference 边界：能力缺失/未知 adapter/真实平台不可用。
    accepted_delivery_id = second_delivery["training_delivery_id"]
    limited_ref = request(
        "D4",
        "受限 Adapter 提交带 model_ref 的外部引用",
        "editor_a",
        "POST",
        api(project, f"/training-deliveries/{accepted_delivery_id}/external-references"),
        json_body={
            "adapter_id": LIMITED_ADAPTER_ID,
            "adapter_version": "1.0.0",
            "correlation_id": f"corr-{uuid4().hex[:8]}",
            "model_ref": {"model_id": "model-qa-1"},
        },
        headers={"Idempotency-Key": "external-limited-330"},
    )
    expect_status("D4", "受限 Adapter 带 model_ref 返回 422", limited_ref, 422)
    expect_code("D4", "受限 Adapter 带 model_ref 错误码", limited_ref, "training_adapter_unsupported")

    unknown_adapter = request(
        "D4",
        "未知 Adapter 提交外部引用",
        "editor_a",
        "POST",
        api(project, f"/training-deliveries/{accepted_delivery_id}/external-references"),
        json_body={
            "adapter_id": "unknown-adapter",
            "adapter_version": "1.0.0",
            "correlation_id": f"corr-{uuid4().hex[:8]}",
        },
        headers={"Idempotency-Key": "external-unknown-330"},
    )
    expect_status("D4", "未知 Adapter 提交返回 422", unknown_adapter, 422)
    expect_code("D4", "未知 Adapter 提交错误码", unknown_adapter, "training_adapter_unsupported")

    unavailable = request(
        "D4",
        "真实训练平台未接入时提交外部引用",
        "editor_a",
        "POST",
        api(project, f"/training-deliveries/{accepted_delivery_id}/external-references"),
        json_body={
            "adapter_id": ADAPTER_ID,
            "adapter_version": "1.0.0",
            "correlation_id": f"corr-{uuid4().hex[:8]}",
        },
        headers={"Idempotency-Key": "external-real-platform-330"},
    )
    expect_status("D4", "真实训练平台提交返回 503（BLOCKED）", unavailable, 503)
    expect_code("D4", "真实训练平台提交错误码", unavailable, "training_adapter_unavailable")

    not_delivered_ref = request(
        "D4",
        "返工交付提交外部引用",
        "editor_a",
        "POST",
        api(project, f"/training-deliveries/{delivery['training_delivery_id']}/external-references"),
        json_body={
            "adapter_id": ADAPTER_ID,
            "adapter_version": "1.0.0",
            "correlation_id": f"corr-{uuid4().hex[:8]}",
        },
        headers={"Idempotency-Key": "external-not-delivered-330"},
    )
    expect_status("D4", "非 DELIVERED/ACCEPTED 交付提交返回 409", not_delivered_ref, 409)

    result.update(
        {
            "delivery_id": accepted_delivery_id,
            "delivery": accepted.json(),
            "request": final_request,
            "request_revision_id": r2_detail["current_revision_id"],
            "request_id": r2_detail["training_request_id"],
        }
    )
    return result


# ---------------------------------------------------------------------------
# D6：Adapter 回调与直接写负向控制
# ---------------------------------------------------------------------------
from app.api.v1.training_schemas import AdapterCallbackPayload  # noqa: E402
from app.domain.datasets.canonical import plain, sha256_value  # noqa: E402
from app.infrastructure.db.models.training import TrainingAdapterCallbackModel  # noqa: E402
def callback_body(
    *,
    event_id: str,
    external_status: str,
    external_run_id: str,
    adapter_id: str = ADAPTER_ID,
    adapter_version: str = "1.0.0",
    model_ref: dict[str, Any] | None = None,
    evaluation_ref: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_summary: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "external_run_id": external_run_id,
        "external_status": external_status,
        "occurred_at": datetime.now(UTC).isoformat(),
        "model_ref": model_ref,
        "evaluation_ref": evaluation_ref,
        "error_code": error_code,
        "error_summary": error_summary,
        "payload": {"source": "ddp-330"},
    }


def callback_signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    message = f"{timestamp}.{nonce}.".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def send_callback(
    step: str,
    *,
    adapter_id: str,
    body: dict[str, Any],
    secret: str,
    timestamp: str | None = None,
    nonce: str | None = None,
    signature: str | None = None,
    include_headers: bool = True,
    raw_body: bytes | None = None,
) -> tuple[httpx.Response, bytes, str, str, str]:
    raw = raw_body if raw_body is not None else json.dumps(
        body, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    timestamp_text = timestamp or str(int(time.time()))
    nonce_text = nonce or f"nonce-{uuid4().hex}"
    signature_text = signature or callback_signature(secret, timestamp_text, nonce_text, raw)
    headers = {}
    if include_headers:
        headers = {
            "X-DDP-Signature": signature_text,
            "X-DDP-Timestamp": timestamp_text,
            "X-DDP-Nonce": nonce_text,
            "Content-Type": "application/json",
        }
    response = raw_request(
        "D6",
        step,
        "POST",
        api_root(f"/integrations/training-adapters/{adapter_id}/callbacks"),
        content=raw,
        headers=headers,
    )
    return response, raw, timestamp_text, nonce_text, signature_text


def seed_external_reference(training_ctx: dict[str, Any], baseline: dict[str, Any]) -> str:
    reference_id = uuid4()
    now = datetime.now(UTC)
    with factory() as session:
        session.add(
            ExternalTrainingReferenceModel(
                id=reference_id,
                project_id=ids["project_a"],
                adapter_id=ADAPTER_ID,
                adapter_version="1.0.0",
                training_delivery_id=UUID(training_ctx["delivery_id"]),
                training_request_revision_id=UUID(training_ctx["request_revision_id"]),
                dataset_version_id=UUID(baseline["version_id"]),
                external_run_id="run-qa-callback-330",
                external_run_url=None,
                external_status="SUBMITTED",
                status_stale=False,
                submitted_at=now,
                last_synced_at=now,
                model_ref=None,
                evaluation_ref=None,
                correlation_id="corr-qa-callback-330",
                idempotency_key="idem-qa-callback-330",
                error_code=None,
                error_summary=None,
                version=1,
                created_at=now,
                updated_at=now,
                created_by=ids["owner_a"],
                updated_by=ids["owner_a"],
            )
        )
        session.commit()
    return "run-qa-callback-330"


def _external_status(reference_id: UUID) -> dict[str, Any]:
    with factory() as session:
        row = session.get(ExternalTrainingReferenceModel, reference_id)
        assert row is not None
        return {
            "external_status": row.external_status,
            "status_stale": row.status_stale,
            "model_ref": row.model_ref,
            "evaluation_ref": row.evaluation_ref,
            "error_code": row.error_code,
            "version": row.version,
        }


def scenario_callbacks_and_direct_db(
    baseline: dict[str, Any],
    success_export: dict[str, Any],
    training_ctx: dict[str, Any],
    grant_ctx: dict[str, Any],
) -> None:
    run_id = seed_external_reference(training_ctx, baseline)
    reference_id = None
    with factory() as session:
        row = session.execute(
            select(ExternalTrainingReferenceModel.id).where(
                ExternalTrainingReferenceModel.external_run_id == run_id
            )
        ).scalar_one()
        reference_id = row

    # 根因检查：外部训练引用守卫是否使用了正确的列名。
    with psycopg.connect(PG_DSN) as connection:
        guard_source = connection.execute(
            "SELECT prosrc FROM pg_proc "
            "WHERE proname = 'ddp_external_training_reference_guard'"
        ).fetchone()
    source_text = guard_source[0] if guard_source else ""
    check(
        "D6",
        "外部训练引用守卫引用 external_status 列而非 status",
        "OLD.external_status" in source_text and "OLD.status" not in source_text,
        "使用 OLD.external_status/NEW.external_status",
        {
            "has_external_status": "OLD.external_status" in source_text,
            "has_wrong_status": "OLD.status" in source_text,
        },
        severity="blocker",
        note="若使用 OLD.status，真实 PG 会在任何有效回调更新时抛 UndefinedColumn",
    )

    # 正常回调：RUNNING。
    event_running = f"event-{uuid4().hex}"
    nonce_running = f"nonce-{uuid4().hex}"
    body_running = callback_body(
        event_id=event_running,
        external_status="RUNNING",
        external_run_id=run_id,
        model_ref={"model_id": "model-qa-330", "model_version": "1"},
    )
    response, raw_running, ts_running, nonce_running_echo, sig_running = send_callback(
        "有效 RUNNING 回调",
        adapter_id=ADAPTER_ID,
        body=body_running,
        secret=ADAPTER_SECRET,
        nonce=nonce_running,
    )
    expect_status("D6", "有效 RUNNING 回调返回 204", response, 204)
    status = _external_status(reference_id)
    expect_eq("D6", "回调后外部训练状态 RUNNING", "RUNNING", status["external_status"])
    expect_eq("D6", "RUNNING 回调记录 model_ref", True, bool(status["model_ref"]))
    with factory() as session:
        running_callback = session.execute(
            text(
                "SELECT nonce, payload_sha256 FROM training_adapter_callbacks "
                "WHERE adapter_id = :adapter_id AND event_id = :event_id"
            ),
            {"adapter_id": ADAPTER_ID, "event_id": event_running},
        ).fetchone()
        running_outbox = session.execute(
            text(
                "SELECT payload FROM outbox_events "
                "WHERE event_type = 'training.external_reference.callback' "
                "AND payload->>'event_id' = :event_id"
            ),
            {"event_id": event_running},
        ).fetchone()
        running_callback_count = session.execute(
            text("SELECT count(*) FROM training_adapter_callbacks")
        ).scalar_one()
        running_outbox_count = session.execute(
            text(
                "SELECT count(*) FROM outbox_events "
                "WHERE event_type = 'training.external_reference.callback'"
            )
        ).scalar_one()
    check(
        "D6",
        "RUNNING 回调防重放记录已落库",
        running_callback is not None and running_callback[0] == nonce_running,
        {"exists": True, "nonce": nonce_running},
        {
            "exists": running_callback is not None,
            "nonce": running_callback[0] if running_callback else None,
        },
    )
    check(
        "D6",
        "RUNNING 回调 outbox 已落库且记录 to_status=RUNNING",
        running_outbox is not None and running_outbox[0].get("to_status") == "RUNNING",
        {"exists": True, "to_status": "RUNNING"},
        {
            "exists": running_outbox is not None,
            "payload": running_outbox[0] if running_outbox else None,
        },
    )

    # 同 event_id + 同 payload 幂等重放。
    replay, _raw, _ts, _nonce, _sig = send_callback(
        "同 event 同 payload 重放",
        adapter_id=ADAPTER_ID,
        body=body_running,
        secret=ADAPTER_SECRET,
        timestamp=ts_running,
        nonce=nonce_running_echo,
        signature=sig_running,
        raw_body=raw_running,
    )
    expect_status("D6", "同 event 同 payload 重放返回 204", replay, 204)
    with factory() as session:
        replay_callback_count = session.execute(
            text("SELECT count(*) FROM training_adapter_callbacks")
        ).scalar_one()
        replay_outbox_count = session.execute(
            text(
                "SELECT count(*) FROM outbox_events "
                "WHERE event_type = 'training.external_reference.callback'"
            )
        ).scalar_one()
    expect_eq(
        "D6",
        "同 event 同 payload 重放不新增回调记录",
        running_callback_count,
        replay_callback_count,
    )
    expect_eq(
        "D6",
        "同 event 同 payload 重放不新增 outbox",
        running_outbox_count,
        replay_outbox_count,
    )

    # 同 event_id + 不同 payload -> 409。
    changed_body = callback_body(
        event_id=event_running,
        external_status="SUCCEEDED",
        external_run_id=run_id,
        model_ref={"model_id": "model-qa-330", "model_version": "2"},
    )
    changed, _raw2, _ts2, _nonce2, _sig2 = send_callback(
        "同 event 不同 payload",
        adapter_id=ADAPTER_ID,
        body=changed_body,
        secret=ADAPTER_SECRET,
    )
    expect_status("D6", "同 event 不同 payload 返回 409", changed, 409)
    expect_code("D6", "同 event 不同 payload 错误码", changed, "training_callback_replayed")

    # 新 event + 旧 nonce -> 409。
    replay_nonce, _raw3, _ts3, _nonce3, _sig3 = send_callback(
        "新 event 复用旧 nonce",
        adapter_id=ADAPTER_ID,
        body=callback_body(
            event_id=f"event-{uuid4().hex}",
            external_status="RUNNING",
            external_run_id=run_id,
        ),
        secret=ADAPTER_SECRET,
        nonce=nonce_running_echo,
    )
    expect_status("D6", "新 event 复用旧 nonce 返回 409", replay_nonce, 409)
    expect_code("D6", "新 event 复用旧 nonce 错误码", replay_nonce, "training_callback_replayed")

    # 签名错误 / 时间窗 / 缺少头 / nonce 过短 -> 401。
    wrong_signature, _raw4, _ts4, _nonce4, _sig4 = send_callback(
        "错误签名回调",
        adapter_id=ADAPTER_ID,
        body=callback_body(
            event_id=f"event-{uuid4().hex}",
            external_status="RUNNING",
            external_run_id=run_id,
        ),
        secret=ADAPTER_SECRET,
        signature="sha256=" + "0" * 64,
    )
    expect_status("D6", "错误签名回调返回 401", wrong_signature, 401)
    expect_code("D6", "错误签名回调错误码", wrong_signature, "training_callback_invalid")

    stale_timestamp, _raw5, _ts5, _nonce5, _sig5 = send_callback(
        "超时间窗回调",
        adapter_id=ADAPTER_ID,
        body=callback_body(
            event_id=f"event-{uuid4().hex}",
            external_status="RUNNING",
            external_run_id=run_id,
        ),
        secret=ADAPTER_SECRET,
        timestamp=str(int(time.time()) - 1000),
    )
    expect_status("D6", "超时间窗回调返回 401", stale_timestamp, 401)
    expect_code("D6", "超时间窗回调错误码", stale_timestamp, "training_callback_invalid")

    missing_headers, _raw6, _ts6, _nonce6, _sig6 = send_callback(
        "缺少签名头回调",
        adapter_id=ADAPTER_ID,
        body=callback_body(
            event_id=f"event-{uuid4().hex}",
            external_status="RUNNING",
            external_run_id=run_id,
        ),
        secret=ADAPTER_SECRET,
        include_headers=False,
    )
    expect_status("D6", "缺少签名头回调返回 401", missing_headers, 401)

    short_nonce, _raw7, _ts7, _nonce7, _sig7 = send_callback(
        "过短 nonce 回调",
        adapter_id=ADAPTER_ID,
        body=callback_body(
            event_id=f"event-{uuid4().hex}",
            external_status="RUNNING",
            external_run_id=run_id,
        ),
        secret=ADAPTER_SECRET,
        nonce="short",
    )
    expect_status("D6", "过短 nonce 回调返回 401", short_nonce, 401)

    # Adapter 不支持 callback 能力 -> 422。
    unsupported, _raw8, _ts8, _nonce8, _sig8 = send_callback(
        "不支持 callback 能力的 Adapter",
        adapter_id=LIMITED_ADAPTER_ID,
        body=callback_body(
            event_id=f"event-{uuid4().hex}",
            external_status="RUNNING",
            external_run_id=run_id,
            adapter_id=LIMITED_ADAPTER_ID,
        ),
        secret=LIMITED_SECRET,
    )
    expect_status("D6", "不支持 callback 能力返回 422", unsupported, 422)
    expect_code("D6", "不支持 callback 能力错误码", unsupported, "training_adapter_unsupported")

    # 未知 external_run_id -> 404。
    unknown_run, _raw9, _ts9, _nonce9, _sig9 = send_callback(
        "未知 external_run_id 回调",
        adapter_id=ADAPTER_ID,
        body=callback_body(
            event_id=f"event-{uuid4().hex}",
            external_status="RUNNING",
            external_run_id="run-unknown-330",
        ),
        secret=ADAPTER_SECRET,
    )
    expect_status("D6", "未知 external_run_id 返回 404", unknown_run, 404)

    # adapter_version 不一致 -> 422。
    version_mismatch, _raw10, _ts10, _nonce10, _sig10 = send_callback(
        "adapter_version 不一致回调",
        adapter_id=ADAPTER_ID,
        body=callback_body(
            event_id=f"event-{uuid4().hex}",
            external_status="RUNNING",
            external_run_id=run_id,
            adapter_version="2.0.0",
        ),
        secret=ADAPTER_SECRET,
    )
    expect_status("D6", "adapter_version 不一致返回 422", version_mismatch, 422)

    # 非法 external_status：期望失败关闭 422，实际若 500 则记录缺陷。
    invalid_status, _raw11, _ts11, _nonce11, _sig11 = send_callback(
        "非法 external_status 回调",
        adapter_id=ADAPTER_ID,
        body=callback_body(
            event_id=f"event-{uuid4().hex}",
            external_status="BOGUS",
            external_run_id=run_id,
        ),
        secret=ADAPTER_SECRET,
    )
    expect_status("D6", "非法 external_status 失败关闭", invalid_status, 422)
    expect_code(
        "D6",
        "非法 external_status 返回稳定业务错误码",
        invalid_status,
        "validation_failed",
    )

    # 正常 SUCCEEDED 回调。
    event_succeeded = f"event-{uuid4().hex}"
    succeeded_body = callback_body(
        event_id=event_succeeded,
        external_status="SUCCEEDED",
        external_run_id=run_id,
        model_ref={"model_id": "model-qa-330", "model_version": "1"},
        evaluation_ref={"evaluation_id": "eval-qa-330", "evaluation_version": "1"},
    )
    succeeded, _raw12, _ts12, _nonce12, _sig12 = send_callback(
        "有效 SUCCEEDED 回调",
        adapter_id=ADAPTER_ID,
        body=succeeded_body,
        secret=ADAPTER_SECRET,
    )
    expect_status("D6", "有效 SUCCEEDED 回调返回 204", succeeded, 204)
    status = _external_status(reference_id)
    expect_eq("D6", "回调后外部训练状态 SUCCEEDED", "SUCCEEDED", status["external_status"])
    expect_eq("D6", "SUCCEEDED 回调记录 model_ref", True, bool(status["model_ref"]))
    expect_eq("D6", "SUCCEEDED 回调记录 evaluation_ref", True, bool(status["evaluation_ref"]))
    with factory() as session:
        succeeded_callback = session.execute(
            text(
                "SELECT nonce FROM training_adapter_callbacks "
                "WHERE adapter_id = :adapter_id AND event_id = :event_id"
            ),
            {"adapter_id": ADAPTER_ID, "event_id": event_succeeded},
        ).fetchone()
        succeeded_outbox = session.execute(
            text(
                "SELECT payload FROM outbox_events "
                "WHERE event_type = 'training.external_reference.callback' "
                "AND payload->>'event_id' = :event_id"
            ),
            {"event_id": event_succeeded},
        ).fetchone()
    check(
        "D6",
        "SUCCEEDED 回调防重放记录已落库",
        succeeded_callback is not None,
        True,
        succeeded_callback is not None,
    )
    check(
        "D6",
        "SUCCEEDED 回调 outbox 已落库且记录 to_status=SUCCEEDED",
        succeeded_outbox is not None
        and succeeded_outbox[0].get("to_status") == "SUCCEEDED",
        {"exists": True, "to_status": "SUCCEEDED"},
        {
            "exists": succeeded_outbox is not None,
            "payload": succeeded_outbox[0] if succeeded_outbox else None,
        },
    )

    # 终态后再回退 -> 409。
    rollback, _raw13, _ts13, _nonce13, _sig13 = send_callback(
        "终态后回退回调",
        adapter_id=ADAPTER_ID,
        body=callback_body(
            event_id=f"event-{uuid4().hex}",
            external_status="FAILED",
            external_run_id=run_id,
            error_code="adapter_failed",
            error_summary="外部平台失败",
        ),
        secret=ADAPTER_SECRET,
    )
    expect_status("D6", "终态后回退返回 409", rollback, 409)
    expect_code("D6", "终态后回退错误码", rollback, "training_external_run_conflict")

    # 超大请求体 -> 422。
    oversized = json.dumps(
        callback_body(
            event_id=f"event-{uuid4().hex}",
            external_status="RUNNING",
            external_run_id=run_id,
        )
    ).encode("utf-8") + b"x" * (1024 * 1024 + 10)
    oversized_response, _raw14, _ts14, _nonce14, _sig14 = send_callback(
        "超大回调请求体",
        adapter_id=ADAPTER_ID,
        body={},
        secret=ADAPTER_SECRET,
        raw_body=oversized,
    )
    expect_status("D6", "超大回调请求体返回 422", oversized_response, 422)

    # 独立验证 nonce 重放与 event 幂等：预置回调记录，避免受引用更新守卫缺陷阻断。
    idem_body = callback_body(
        event_id="event-idem-seed-330",
        external_status="RUNNING",
        external_run_id=run_id,
    )
    idem_payload = AdapterCallbackPayload.model_validate(idem_body).model_dump(mode="json")
    idem_sha = sha256_value(plain(idem_payload))
    with factory() as session:
        session.add(
            TrainingAdapterCallbackModel(
                id=uuid4(),
                adapter_id=ADAPTER_ID,
                event_id="event-idem-seed-330",
                nonce="nonce-idem-seed-330",
                payload_sha256=idem_sha,
                received_at=datetime.now(UTC),
            )
        )
        session.add(
            TrainingAdapterCallbackModel(
                id=uuid4(),
                adapter_id=ADAPTER_ID,
                event_id="event-nonce-seed-330",
                nonce="nonce-replay-seed-330",
                payload_sha256="sha256:" + "0" * 64,
                received_at=datetime.now(UTC),
            )
        )
        session.commit()

    idem_response, _raw_i, _ts_i, _nonce_i, _sig_i = send_callback(
        "预置 event 幂等重放",
        adapter_id=ADAPTER_ID,
        body=idem_body,
        secret=ADAPTER_SECRET,
        nonce="nonce-idem-new-330",
    )
    expect_status("D6", "预置 event 同 payload 重放返回 204", idem_response, 204)

    nonce_replay_response, _raw_n, _ts_n, _nonce_n, _sig_n = send_callback(
        "预置 nonce 重放",
        adapter_id=ADAPTER_ID,
        body=callback_body(
            event_id="event-nonce-new-330",
            external_status="RUNNING",
            external_run_id=run_id,
        ),
        secret=ADAPTER_SECRET,
        nonce="nonce-replay-seed-330",
    )
    expect_status("D6", "预置 nonce 重放返回 409", nonce_replay_response, 409)
    expect_code("D6", "预置 nonce 重放错误码", nonce_replay_response, "training_callback_replayed")

    # 回调落库与 outbox 事件。
    with factory() as session:
        callback_count = session.execute(
            text("SELECT count(*) FROM training_adapter_callbacks")
        ).scalar_one()
        outbox_count = session.execute(
            text(
                "SELECT count(*) FROM outbox_events "
                "WHERE event_type='training.external_reference.callback'"
            )
        ).scalar_one()
    expect_eq("D6", "回调防重放记录已落库", True, callback_count >= 3)
    expect_eq("D6", "回调 outbox 事件已记录", True, outbox_count >= 2)

    # ---------------------------------------------------------------
    # 直接 SQL 负向控制：数据库兜底必须拒绝绕过领域动作的写操作
    # ---------------------------------------------------------------
    def direct_execute(
        area: str,
        step: str,
        sql: str,
        params: tuple[Any, ...],
        expected_fragment: str,
    ) -> None:
        item: dict[str, Any] = {
            "step": step,
            "sql": sql,
            "expected_fragment": expected_fragment,
        }
        try:
            with psycopg.connect(PG_DSN) as connection:
                connection.execute(sql, params)
                connection.commit()
            item.update({"status": "FAIL", "actual": "未报错（守卫未触发）"})
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            ok = (
                expected_fragment in message
                and "operator does not exist" not in message
                and "does not exist" not in message.lower()
            )
            item.update(
                {
                    "status": "PASS" if ok else "FAIL",
                    "exception": type(exc).__name__,
                    "actual": message.splitlines()[0] if message else "",
                }
            )
        direct_write_results.append(item)
        check(
            area,
            step,
            item["status"] == "PASS",
            f"业务错误包含 {expected_fragment}",
            item["actual"],
            evidence={"sql": sql, "exception": item.get("exception")},
        )

    version_id = baseline["version_id"]
    dataset_id = baseline["dataset_id"]
    success_job_id = success_export["job_id"]
    request_id = training_ctx["request_id"]
    delivery_id = training_ctx["delivery_id"]
    grant_id = grant_ctx["expire_grant"]["grant_id"]
    external_ref_id = str(reference_id)

    direct_execute(
        "D9",
        "直接改写 FROZEN 版本 manifest_sha256",
        "UPDATE dataset_versions SET manifest_sha256 = %s WHERE id = %s",
        ("sha256:" + "f" * 64, UUID(version_id)),
        "dataset_version_immutable",
    )
    direct_execute(
        "D9",
        "直接非法迁移 validity VALID→INVALIDATED",
        "UPDATE dataset_versions SET validity_status = 'INVALIDATED' WHERE id = %s",
        (UUID(version_id),),
        "dataset_version_validity_transition_invalid",
    )
    direct_execute(
        "D9",
        "直接删除数据集",
        "DELETE FROM datasets WHERE id = %s",
        (UUID(dataset_id),),
        "datasets_delete_forbidden",
    )
    direct_execute(
        "D9",
        "直接改写导出任务输入字段",
        "UPDATE export_jobs SET format_version = %s WHERE id = %s",
        ("ddp.lerobot/9.9.9", UUID(success_job_id)),
        "export_job_input_immutable",
    )
    direct_execute(
        "D9",
        "直接删除导出任务",
        "DELETE FROM export_jobs WHERE id = %s",
        (UUID(success_job_id),),
        "export_job_delete_forbidden",
    )
    direct_execute(
        "D9",
        "直接非法迁移训练需求 ACCEPTED→APPROVED",
        "UPDATE training_requests SET status = 'APPROVED' WHERE id = %s",
        (UUID(request_id),),
        "training_request_state_transition_invalid",
    )
    direct_execute(
        "D9",
        "直接删除训练需求",
        "DELETE FROM training_requests WHERE id = %s",
        (UUID(request_id),),
        "training_requests_delete_forbidden",
    )
    direct_execute(
        "D9",
        "直接非法迁移授权 ACTIVE→PENDING_APPROVAL",
        "UPDATE dataset_grants SET status = 'PENDING_APPROVAL' WHERE id = %s",
        (UUID(grant_id),),
        "dataset_grant_state_transition_invalid",
    )
    direct_execute(
        "D9",
        "直接删除授权",
        "DELETE FROM dataset_grants WHERE id = %s",
        (UUID(grant_id),),
        "dataset_grants_delete_forbidden",
    )
    direct_execute(
        "D9",
        "直接改写终态训练交付引用",
        "UPDATE training_deliveries SET export_job_id = %s WHERE id = %s",
        (uuid4(), UUID(delivery_id)),
        "training_delivery_reference_immutable",
    )
    direct_execute(
        "D9",
        "直接改写终态外部训练引用内容",
        "UPDATE external_training_references SET model_ref = %s::jsonb WHERE id = %s",
        ('{"model_id": "tampered"}', UUID(external_ref_id)),
        "external_training_reference_terminal_immutable",
    )
    direct_execute(
        "D9",
        "直接回退终态外部训练状态",
        "UPDATE external_training_references SET external_status = 'FAILED' WHERE id = %s",
        (UUID(external_ref_id),),
        "external_training_reference_state_transition_invalid",
    )
    direct_execute(
        "D9",
        "直接插入 ACTIVE 数据集",
        (
            "INSERT INTO datasets (id, project_id, dataset_lineage_id, code, name, purpose, "
            "owner_id, status, tags, usage_policy, frozen_version_count, version, "
            "created_at, updated_at, created_by, updated_by) VALUES "
            "(%s, %s, %s, %s, '坏数据集', 'TRAINING', %s, 'ACTIVE', '[]', "
            "'TRAINING_ALLOWED', 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, %s, %s)"
        ),
        (
            uuid4(),
            ids["project_a"],
            uuid4(),
            f"bad-ds-{uuid4().hex[:8]}",
            ids["owner_a"],
            ids["owner_a"],
            ids["owner_a"],
        ),
        "dataset_initial_status_invalid",
    )
    direct_execute(
        "D9",
        "直接插入 RUNNING 导出任务",
        (
            "INSERT INTO export_jobs (id, project_id, dataset_version_id, status, format, "
            "format_version, exporter_id, exporter_version, mapping_version, config, "
            "config_sha256, idempotency_key, requested_by, output_prefix, attempt, "
            "max_attempts, timeout_seconds, started_at, verification_status, version, "
            "created_at, updated_at, created_by, updated_by) VALUES "
            "(%s, %s, %s, 'RUNNING', 'LE_ROBOT', 'ddp.lerobot/1.0.0', 'exporter', "
            "'1.0.0', 'ddp.lerobot/1.0.0', '{}', %s, %s, %s, %s, 1, 3, 60, "
            "CURRENT_TIMESTAMP, 'UNVERIFIED', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
            "%s, %s)"
        ),
        (
            uuid4(),
            ids["project_a"],
            UUID(version_id),
            "sha256:" + "a" * 64,
            f"bad-export-{uuid4().hex[:8]}",
            ids["owner_a"],
            f"exports/bad/{uuid4().hex}/",
            ids["owner_a"],
            ids["owner_a"],
        ),
        "export_job_initial_status_invalid",
    )
    direct_execute(
        "D9",
        "直接插入 APPROVED 训练需求",
        (
            "INSERT INTO training_requests (id, project_id, code, title, status, priority, "
            "owner_id, version, created_at, updated_at, created_by, updated_by) VALUES "
            "(%s, %s, %s, '坏需求', 'APPROVED', 'P1', %s, 1, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP, %s, %s)"
        ),
        (
            uuid4(),
            ids["project_a"],
            f"BAD-TR-{uuid4().hex[:8]}",
            ids["owner_a"],
            ids["owner_a"],
            ids["owner_a"],
        ),
        "training_request_initial_status_invalid",
    )
    direct_execute(
        "D9",
        "直接插入 ACTIVE 数据集授权",
        (
            "INSERT INTO dataset_grants (id, project_id, dataset_id, grantee_project_id, "
            "scope, status, reason, approved_by, approved_at, expires_at, version, "
            "created_at, updated_at, created_by, updated_by) VALUES "
            "(%s, %s, %s, %s, '[\"VIEW\"]', 'ACTIVE', '坏授权', %s, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP + interval '1 hour', 1, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP, %s, %s)"
        ),
        (
            uuid4(),
            ids["project_a"],
            UUID(dataset_id),
            ids["project_b"],
            ids["owner_a"],
            ids["owner_a"],
            ids["owner_a"],
        ),
        "dataset_grant_initial_status_invalid",
    )


# ---------------------------------------------------------------------------
# 服务启动与输出
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
            "DDP_JWT_SECRET": "qa330-local-secret-not-for-production-0123456789",
            "DDP_S3_ENDPOINT": S3_ENDPOINT,
            "DDP_S3_ACCESS_KEY": S3_ACCESS_KEY,
            "DDP_S3_SECRET_KEY": S3_SECRET_KEY,
            "DDP_S3_BUCKET": QA_BUCKET,
            "DDP_S3_REGION": "us-east-1",
            "DDP_EXPORT_STORAGE_PREFIX": "exports",
            "DDP_TRAINING_CALLBACK_TIME_WINDOW_SECONDS": "300",
            "DDP_TRAINING_CALLBACK_NONCE_TTL_SECONDS": "600",
            "DDP_TRAINING_ADAPTER_SHARED_SECRETS": json.dumps(
                {
                    ADAPTER_ID: ADAPTER_SECRET,
                    LIMITED_ADAPTER_ID: LIMITED_SECRET,
                }
            ),
            "DDP_TRAINING_ADAPTER_CAPABILITIES": json.dumps(
                {
                    ADAPTER_ID: [
                        "submit",
                        "callback",
                        "status",
                        "cancel",
                        "model_ref",
                        "evaluation_ref",
                    ],
                    LIMITED_ADAPTER_ID: ["submit"],
                }
            ),
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


def _json_default(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def write_outputs(extra: dict[str, Any]) -> None:
    failures = [item for item in checks if item["status"] == "FAIL"]
    area_counts: dict[str, dict[str, int]] = {}
    for item in checks:
        bucket = area_counts.setdefault(item["area"], {"PASS": 0, "FAIL": 0})
        bucket[item["status"]] = bucket.get(item["status"], 0) + 1
    summary_lines = [
        "DDP-330 阶段 4 独立验收摘要",
        f"基线 HEAD：f4d32a4d004129ee1827d277f8d720ac74b2ab52",
        f"一次性 PG：{DB_NAME}",
        f"QA MinIO 桶：{QA_BUCKET}",
        f"HTTP：{BASE_URL}（真实 uvicorn）",
        f"检查总数：{len(checks)}，PASS {len(checks) - len(failures)}，FAIL {len(failures)}",
        "",
        "分区统计：",
    ]
    for area in sorted(area_counts):
        summary_lines.append(
            f"  {area}: PASS {area_counts[area].get('PASS', 0)} / "
            f"FAIL {area_counts[area].get('FAIL', 0)}"
        )
    if failures:
        summary_lines.append("")
        summary_lines.append("失败项：")
        for item in failures:
            summary_lines.append(
                f"  [{item['area']}] {item['step']}：期望 {item['expected']}，实际 {item['actual']}"
            )
    if observations:
        summary_lines.append("")
        summary_lines.append("观察：")
        summary_lines.extend(f"  - {item}" for item in observations)
    (EVIDENCE / "summary.log").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    payload = {
        "checked_at": CHECKED_AT.isoformat(),
        "head": "f4d32a4d004129ee1827d277f8d720ac74b2ab52",
        "database": DB_NAME,
        "qa_bucket": QA_BUCKET,
        "s3_endpoint": S3_ENDPOINT,
        "managed_minio": MANAGED_MINIO,
        "minio_data_dir": str(MINIO_DATA_DIR) if MANAGED_MINIO else None,
        "base_url": BASE_URL,
        "checks": checks,
        "http_log": http_log,
        "migration_steps": migration_steps,
        "trigger_snapshots": trigger_snapshots,
        "direct_write_results": direct_write_results,
        "observations": observations,
        **extra,
    }
    (EVIDENCE / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    (EVIDENCE / "migration-evidence.json").write_text(
        json.dumps(
            {
                "database": DB_NAME,
                "steps": migration_steps,
                "trigger_snapshots": trigger_snapshots,
            },
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        ),
        encoding="utf-8",
    )
    (EVIDENCE / "direct-db-results.json").write_text(
        json.dumps(direct_write_results, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )

    key_lines = ["# DDP-330 关键 HTTP/DB 证据摘录", ""]
    for item in checks:
        if item["area"] in {"D1", "D2", "D3", "D4", "D5", "D6", "D7", "D9"}:
            key_lines.append(
                f"- **{item['status']}** [{item['area']}] {item['step']} "
                f"(期望 {item['expected']}，实际 {item['actual']})"
            )
    (EVIDENCE / "key-http-evidence.md").write_text(
        "\n".join(key_lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    global client, server_process, minio_process
    extra: dict[str, Any] = {}
    try:
        create_database()
        observe(f"一次性 PostgreSQL 数据库：{DB_NAME}")
        if MANAGED_MINIO:
            minio_process = start_managed_minio()
        ensure_qa_bucket()
        migration_probe()
        observe("迁移 0011→0019→0020 逐级升降级完成，阶段 4 触发器清单已核对。")

        seed_identity(factory)
        seed_all_episodes(factory)
        observe("身份、Episode、RawAsset、已批准标注链播种完成。")

        server_process = start_server()
        client = httpx.Client(base_url=BASE_URL, timeout=60.0)
        health = client.get(f"{BASE_URL}/health")
        expect_status("D8", "健康检查", health, 200)

        state_ctx = scenario_dataset_state_machine()
        extra["state_ctx"] = {
            "dataset_id": state_ctx["dataset_id"],
            "version_id": state_ctx["version_id"],
            "status": state_ctx["version"].get("status"),
            "validity_status": state_ctx["version"].get("validity_status"),
        }
        scenario_dataset_permissions_and_pagination()
        baseline = scenario_selection_gates_and_split()
        extra["baseline"] = {
            "dataset_id": baseline["dataset_id"],
            "version_id": baseline["version_id"],
            "status": baseline["version"].get("status"),
            "item_count": baseline["version"].get("item_count"),
        }
        scenario_dedup_and_leakage()
        grant_ctx = scenario_dataset_grants(baseline)
        extra["grant_ctx"] = {
            "view_grant_id": grant_ctx["view_grant"].get("grant_id"),
            "export_grant_id": grant_ctx["export_grant"].get("grant_id"),
            "download_grant_id": grant_ctx["download_grant"].get("grant_id"),
        }
        success_export = scenario_export_success_and_acl(baseline, grant_ctx)
        extra["export"] = {
            "job_id": success_export["job_id"],
            "status": success_export["job"].get("status"),
            "artifact_count": success_export["job"].get("artifact_count"),
            "aggregate_sha256": success_export["job"].get("aggregate_sha256"),
        }
        scenario_export_failures(baseline)
        training_ctx = scenario_training_requests_and_delivery(baseline, success_export)
        extra["training"] = {
            "request_id": training_ctx["request_id"],
            "delivery_id": training_ctx["delivery_id"],
            "request_status": training_ctx["request"].get("status"),
        }
        scenario_callbacks_and_direct_db(baseline, success_export, training_ctx, grant_ctx)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        check(
            "INFRA",
            "探针执行未抛出未处理异常",
            False,
            "正常结束",
            f"{type(exc).__name__}: {exc}",
        )
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
        stop_managed_minio()
        write_outputs(extra)
        if DROP_DB:
            drop_database()

    failures = [item for item in checks if item["status"] == "FAIL"]
    print(
        f"DDP-330 完成：{len(checks)} 检查，"
        f"PASS {len(checks) - len(failures)}，FAIL {len(failures)}"
    )
    print(f"证据目录：{EVIDENCE.relative_to(ROOT)}")
    print(f"一次性数据库：{DB_NAME}（默认保留，便于复核）")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
