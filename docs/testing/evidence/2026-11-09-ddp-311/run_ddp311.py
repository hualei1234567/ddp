#!/usr/bin/env python3
"""DDP-311 阶段 2 复验探针（真实 PostgreSQL 16 + 真实 MinIO + 真实 HTTP + Worker）。

覆盖：
1. DDP-308-D01：真实 PG + 真实 MinIO 下并发 complete；
2. DDP-308-D02：Episode / IngestionJob / 血缘 / 下载地址新端点成功与错误路径；
3. 真实 MinIO：预签名 PUT、ListParts、ETag、Complete、Range GET、预签名下载与短时过期；
4. Worker：``python -m app.workers.outbox --drain``，VALIDATING→VALID 与 Hash 不符隔离；
5. 真实 PG 0006–0010 逐级升降级与触发器复核。

边界：只读业务代码；所有数据写入一次性临时数据库与临时 MinIO 目录，结束清理。
输出：同目录 run.json / run.log / api.log / minio.log / worker-*.log。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

import httpx
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "backend"
EVIDENCE = ROOT / "docs" / "testing" / "evidence" / "2026-11-09-ddp-311"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DDP_ENV", "local")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

PG_HOST = "127.0.0.1"
PG_PORT = 55432
PG_USER = "ddp"
PG_PASSWORD = "ddp"
PG_ADMIN_DSN = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/postgres"
PG_DSN_TEMPLATE = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{{dbname}}"
PG_URL_TEMPLATE = (
    f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{{dbname}}"
)

MINIO_BIN = "/usr/local/bin/minio"
MINIO_PORT = 9100
MINIO_CONSOLE_PORT = 9101
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
BUCKET = "ddp-raw"
API_PORT = 8010
API_BASE = f"http://127.0.0.1:{API_PORT}"
API_PREFIX = f"{API_BASE}/api/v1"

PART_SIZE = 5 * 1024 * 1024
MCAP_MAGIC = b"\x89MCAP0\r\n"
SHA_A = f"sha256:{'a' * 64}"

UPLOAD_TRIGGERS = {
    "trg_upload_sessions_initial_status",
    "trg_upload_sessions_transition",
    "trg_upload_sessions_no_delete",
    "trg_upload_parts_no_update",
    "trg_upload_parts_no_delete",
    "trg_raw_assets_initial_status",
    "trg_raw_assets_transition",
    "trg_raw_assets_content_immutable",
    "trg_raw_assets_no_delete",
    "trg_raw_asset_links_no_delete",
    "trg_raw_asset_links_terminal_status",
}
EPISODE_TRIGGERS = {
    "trg_ingestion_jobs_initial_status",
    "trg_ingestion_jobs_transition",
    "trg_ingestion_jobs_no_delete",
    "trg_episodes_initial_status",
    "trg_episodes_transition",
    "trg_episodes_no_delete",
    "trg_episode_raw_assets_no_update",
    "trg_episode_raw_assets_no_delete",
    "trg_episode_raw_assets_parent_frozen",
    "trg_lineage_node_refs_identity_immutable",
    "trg_lineage_node_refs_no_delete",
    "trg_data_lineage_edges_no_update",
    "trg_data_lineage_edges_no_delete",
    "trg_data_lineage_edges_no_self",
    "trg_data_lineage_edges_supersedes_linear",
}


# --------------------------------------------------------------------------- 记录
class Recorder:
    def __init__(self, log_path: Path) -> None:
        self.results: list[dict[str, Any]] = []
        self.log_path = log_path
        self._file = log_path.open("a", encoding="utf-8")

    def record(self, check_id: str, status: str, detail: str) -> None:
        item = {
            "id": check_id,
            "status": status,
            "detail": detail,
            "at": datetime.now(UTC).isoformat(),
        }
        self.results.append(item)
        line = f"[{status}] {check_id}: {detail}"
        print(line, flush=True)
        self._file.write(line + "\n")
        self._file.flush()

    def check(self, check_id: str, func: Callable[[], str]) -> None:
        try:
            detail = func()
        except Exception as exc:  # noqa: BLE001 - 探针必须记录全部失败
            self.record(check_id, "FAIL", f"{type(exc).__name__}: {exc}")
            self._file.write(traceback.format_exc() + "\n")
            self._file.flush()
        else:
            self.record(check_id, "PASS", detail)

    def blocked(self, check_id: str, detail: str) -> None:
        self.record(check_id, "BLOCKED", detail)

    def close(self) -> None:
        self._file.close()

    def summary(self) -> dict[str, int]:
        result = {"PASS": 0, "FAIL": 0, "BLOCKED": 0}
        for item in self.results:
            result[item["status"]] = result.get(item["status"], 0) + 1
        return result


# --------------------------------------------------------------------------- 通用
def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_ref(data: bytes) -> str:
    return f"sha256:{sha256_hex(data)}"


def wait_port(host: str, port: int, timeout: float = 30.0, label: str = "") -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"{label or f'{host}:{port}'} 未在 {timeout}s 内就绪")


def wait_http(url: str, timeout: float = 30.0) -> httpx.Response:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=2)
            if response.status_code < 500:
                return response
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.25)
    raise RuntimeError(f"HTTP 未就绪：{url}；最后错误：{last}")


def create_database(dbname: str) -> None:
    with psycopg.connect(PG_ADMIN_DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))


def drop_database(dbname: str) -> None:
    with psycopg.connect(PG_ADMIN_DSN, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(dbname))
        )


def dsn(dbname: str) -> str:
    return PG_DSN_TEMPLATE.format(dbname=dbname)


def db_connect(dbname: str) -> psycopg.Connection[Any]:
    return psycopg.connect(dsn(dbname), autocommit=True)


def pg_scalar(dbname: str, query: str, params: tuple[Any, ...] = ()) -> Any:
    with db_connect(dbname) as conn:
        row = conn.execute(query, params).fetchone()
        return row[0] if row else None


def pg_rows(dbname: str, query: str, params: tuple[Any, ...] = ()) -> list[Any]:
    with db_connect(dbname) as conn:
        return list(conn.execute(query, params).fetchall())


def expect_error(conn: psycopg.Connection[Any], statement: str, code: str) -> str:
    """执行预期被数据库拒绝的语句；错误码必须出现在异常文本中。"""
    try:
        conn.execute(statement)
    except Exception as exc:  # noqa: BLE001 - 期望数据库拒绝
        message = str(exc)
        if code not in message:
            raise AssertionError(f"拒绝原因不匹配：期望 {code}，实际 {message}") from exc
        return message.splitlines()[0][:240]
    raise AssertionError(f"数据库未拒绝，期望 {code}")


# --------------------------------------------------------------------------- 迁移
def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND))
    config.set_main_option("sqlalchemy.url", url)
    return config


def run_migration_and_trigger_checks(rec: Recorder, dbname: str) -> None:
    url = PG_URL_TEMPLATE.format(dbname=dbname)
    engine = create_engine(url, pool_pre_ping=True)
    config = alembic_config(url)
    upload_tables = {"upload_sessions", "upload_parts", "raw_assets", "raw_asset_links"}
    stage2_tables = {
        "audit_logs",
        "outbox_events",
        "idempotency_records",
        "collection_tasks",
        "collection_batches",
        "upload_sessions",
        "upload_parts",
        "raw_assets",
        "raw_asset_links",
        "ingestion_jobs",
        "episodes",
        "episode_raw_assets",
        "lineage_node_refs",
        "data_lineage_edges",
    }

    def current() -> str | None:
        with engine.connect() as conn:
            return conn.execute(text("select version_num from alembic_version")).scalar()

    def tables() -> set[str]:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "select table_name from information_schema.tables "
                    "where table_schema='public' and table_type='BASE TABLE'"
                )
            )
            return {row[0] for row in rows}

    def triggers() -> set[str]:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "select t.tgname from pg_trigger t "
                    "join pg_class c on c.oid=t.tgrelid "
                    "join pg_namespace n on n.oid=c.relnamespace "
                    "where not t.tgisinternal and n.nspname='public'"
                )
            )
            return {row[0] for row in rows}

    try:
        for revision in ("0006", "0007", "0008", "0009", "0010"):
            command.upgrade(config, revision)
            rec.record(
                f"MIG-UP-{revision}",
                "PASS",
                f"upgrade {revision} 完成，revision={current()}，表数={len(tables())}",
            )

        def head_check() -> str:
            got_tables = tables()
            got_triggers = triggers()
            assert upload_tables <= got_tables, f"上传表缺失：{upload_tables - got_tables}"
            assert stage2_tables <= got_tables, f"阶段 2 表缺失：{stage2_tables - got_tables}"
            assert UPLOAD_TRIGGERS <= got_triggers, (
                f"上传触发器缺失：{UPLOAD_TRIGGERS - got_triggers}"
            )
            assert EPISODE_TRIGGERS <= got_triggers, (
                f"Episode/血缘触发器缺失：{EPISODE_TRIGGERS - got_triggers}"
            )
            return (
                f"head=0010；阶段 2 表 {len(stage2_tables)} 张齐全；"
                f"上传触发器 {len(UPLOAD_TRIGGERS)}/11、"
                f"Episode/血缘触发器 {len(EPISODE_TRIGGERS)}/15 齐全"
            )

        rec.check("MIG-HEAD-0010", head_check)

        command.downgrade(config, "0009")
        rec.check(
            "MIG-DOWN-0010",
            lambda: (
                f"downgrade 0010→{current()}；Episode/血缘触发器已移除"
                if not (EPISODE_TRIGGERS & triggers())
                else _raise("Episode/血缘触发器未移除")
            ),
        )
        command.downgrade(config, "0008")
        rec.check(
            "MIG-DOWN-0009",
            lambda: (
                f"downgrade 0009→{current()}；0009 表已移除"
                if not (
                    {
                        "episodes",
                        "ingestion_jobs",
                        "episode_raw_assets",
                        "lineage_node_refs",
                        "data_lineage_edges",
                    }
                    & tables()
                )
                else _raise("0009 表未移除")
            ),
        )
        command.downgrade(config, "0007")
        rec.check(
            "MIG-DOWN-0008",
            lambda: (
                f"downgrade 0008→{current()}；上传表与 11 个触发器已移除"
                if not (upload_tables & tables()) and not (UPLOAD_TRIGGERS & triggers())
                else _raise("0008 表或触发器未移除")
            ),
        )
        command.upgrade(config, "0008")
        rec.check(
            "MIG-REUP-0008",
            lambda: (
                f"re-upgrade 0008；上传表 {len(upload_tables & tables())}/4，"
                f"触发器 {len(UPLOAD_TRIGGERS & triggers())}/11"
                if upload_tables <= tables() and UPLOAD_TRIGGERS <= triggers()
                else _raise("0008 重新升级后表或触发器缺失")
            ),
        )
        command.upgrade(config, "0010")
        rec.check("MIG-REUP-HEAD", head_check)
        command.downgrade(config, "0005")
        rec.check(
            "MIG-DOWN-0005",
            lambda: (
                f"downgrade 全链→{current()}；阶段 2 表全部移除"
                if not (stage2_tables & tables())
                else _raise(f"阶段 2 表残留：{stage2_tables & tables()}")
            ),
        )
        command.upgrade(config, "head")
        rec.check("MIG-REUP-ALL", head_check)

        rec.check(
            "DB-TRG-INVENTORY",
            lambda: (
                f"上传触发器 11/11、Episode/血缘触发器 15/15 存在"
                if (UPLOAD_TRIGGERS | EPISODE_TRIGGERS) <= triggers()
                else _raise("触发器清单不完整")
            ),
        )
        trigger_direct_write_checks(rec, dbname)
    finally:
        engine.dispose()


def _raise(message: str) -> str:
    raise AssertionError(message)


def trigger_direct_write_checks(rec: Recorder, dbname: str) -> None:
    project_a, project_b = uuid4(), uuid4()
    user_a = uuid4()
    with db_connect(dbname) as conn:
        conn.execute("INSERT INTO projects (id, code, name) VALUES (%s, %s, %s)", (
            project_a, f"trg-a-{project_a.hex[:8]}", "触发器项目 A",
        ))
        conn.execute("INSERT INTO projects (id, code, name) VALUES (%s, %s, %s)", (
            project_b, f"trg-b-{project_b.hex[:8]}", "触发器项目 B",
        ))
        conn.execute("INSERT INTO users (id, display_name) VALUES (%s, %s)", (
            user_a, "触发器用户",
        ))

    session_id, asset_id, job_id, episode_id = uuid4(), uuid4(), uuid4(), uuid4()
    node_a, node_b, node_c, edge_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)

    with db_connect(dbname) as conn:
        # DB-TRG-01 上传会话初始状态
        rec.check(
            "DB-TRG-01",
            lambda: expect_error(
                conn,
                "INSERT INTO upload_sessions (id, project_id, status, verification_status, "
                "original_filename, declared_content_type, declared_size_bytes, "
                "part_size_bytes, max_size_bytes, object_key, multipart_upload_id, "
                "expires_at, created_by, updated_by) VALUES "
                f"('{uuid4()}', '{project_a}', 'UPLOADING', 'PENDING', 'x.mcap', "
                f"'application/x-mcap', 8, 4, 64, 'raw/x/content.mcap', 'upload-x', "
                f"'{now.isoformat()}', '{user_a}', '{user_a}')",
                "upload_session_initial_status_invalid",
            ),
        )
        conn.execute(
            "INSERT INTO upload_sessions (id, project_id, status, verification_status, "
            "original_filename, declared_content_type, declared_size_bytes, part_size_bytes, "
            "max_size_bytes, object_key, multipart_upload_id, expires_at, created_by, updated_by) "
            "VALUES (%s, %s, 'CREATED', 'PENDING', 'x.mcap', 'application/x-mcap', 8, 4, 64, "
            "'raw/x/content.mcap', 'upload-x', %s, %s, %s)",
            (session_id, project_a, now, user_a, user_a),
        )
        conn.execute(
            "INSERT INTO raw_assets (id, project_id, code, source_type, original_filename, "
            "object_key, size_bytes, content_type, format, sha256, status, ingested_at, "
            "storage_class, uploaded_by, created_by, updated_by) VALUES "
            "(%s, %s, %s, 'UPLOAD', 'x.mcap', 'raw/x/content.mcap', 8, 'application/x-mcap', "
            "'MCAP', %s, 'VALIDATING', %s, 'STANDARD', %s, %s, %s)",
            (asset_id, project_a, f"RAW-{asset_id.hex[:20]}", SHA_A, now, user_a, user_a, user_a),
        )

        # DB-TRG-02 上传会话非法迁移
        rec.check(
            "DB-TRG-02",
            lambda: expect_error(
                conn,
                f"UPDATE upload_sessions SET status='COMPLETED' WHERE id='{session_id}'",
                "upload_session_state_transition_invalid",
            ),
        )
        # DB-TRG-03 分片不可变与禁删
        conn.execute(
            "INSERT INTO upload_parts (project_id, upload_session_id, part_number, size_bytes, "
            "etag, sha256, recorded_at) VALUES (%s, %s, 1, 4, 'etag-1', %s, %s)",
            (project_a, session_id, SHA_A, now),
        )
        rec.check(
            "DB-TRG-03",
            lambda: expect_error(
                conn,
                f"UPDATE upload_parts SET etag='etag-2' WHERE upload_session_id='{session_id}'",
                "upload_part_immutable",
            ),
        )
        rec.check(
            "DB-TRG-04",
            lambda: expect_error(
                conn,
                f"DELETE FROM upload_parts WHERE upload_session_id='{session_id}'",
                "upload_part_delete_forbidden",
            ),
        )
        # DB-TRG-05 RawAsset 初始状态
        rec.check(
            "DB-TRG-05",
            lambda: expect_error(
                conn,
                "INSERT INTO raw_assets (id, project_id, code, source_type, original_filename, "
                "object_key, size_bytes, content_type, format, sha256, status, ingested_at, "
                "storage_class, uploaded_by, created_by, updated_by) VALUES "
                f"('{uuid4()}', '{project_a}', 'RAW-BAD', 'UPLOAD', 'x.mcap', "
                f"'raw/y/content.mcap', 8, 'application/x-mcap', 'MCAP', '{SHA_A}', 'VALID', "
                f"'{now.isoformat()}', 'STANDARD', '{user_a}', '{user_a}', '{user_a}')",
                "raw_asset_initial_status_invalid",
            ),
        )
        # DB-TRG-06 内容不可变
        rec.check(
            "DB-TRG-06",
            lambda: expect_error(
                conn,
                f"UPDATE raw_assets SET object_key='raw/z/content.mcap' WHERE id='{asset_id}'",
                "raw_asset_content_immutable",
            ),
        )
        # DB-TRG-07 非法迁移
        rec.check(
            "DB-TRG-07",
            lambda: expect_error(
                conn,
                f"UPDATE raw_assets SET status='ARCHIVED' WHERE id='{asset_id}'",
                "raw_asset_state_transition_invalid",
            ),
        )
        # DB-TRG-08 禁删
        rec.check(
            "DB-TRG-08",
            lambda: expect_error(
                conn,
                f"DELETE FROM raw_assets WHERE id='{asset_id}'",
                "raw_asset_delete_forbidden",
            ),
        )
        # DB-TRG-09 链接只允许白名单迁移与禁删
        link_id = uuid4()
        conn.execute(
            "INSERT INTO raw_asset_links (id, project_id, asset_id, link_type, link_status, "
            "created_by) VALUES (%s, %s, %s, 'UPLOAD_SESSION', 'PENDING_VERIFICATION', %s)",
            (link_id, project_a, asset_id, user_a),
        )
        rec.check(
            "DB-TRG-09",
            lambda: expect_error(
                conn,
                f"DELETE FROM raw_asset_links WHERE id='{link_id}'",
                "raw_asset_link_delete_forbidden",
            ),
        )
        # DB-TRG-10 Job 初始状态与输入不可变
        rec.check(
            "DB-TRG-10",
            lambda: expect_error(
                conn,
                "INSERT INTO ingestion_jobs (id, project_id, job_type, raw_asset_id, status, "
                "timeout_sec, created_by, updated_by) VALUES "
                f"('{uuid4()}', '{project_a}', 'VALIDATE', '{asset_id}', 'RUNNING', 60, "
                f"'{user_a}', '{user_a}')",
                "ingestion_job_initial_status_invalid",
            ),
        )
        conn.execute(
            "INSERT INTO ingestion_jobs (id, project_id, job_type, raw_asset_id, status, "
            "timeout_sec, created_by, updated_by) VALUES (%s, %s, 'VALIDATE', %s, 'QUEUED', "
            "60, %s, %s)",
            (job_id, project_a, asset_id, user_a, user_a),
        )
        rec.check(
            "DB-TRG-11",
            lambda: expect_error(
                conn,
                f"UPDATE ingestion_jobs SET job_type='REVALIDATE' WHERE id='{job_id}'",
                "ingestion_job_input_immutable",
            ),
        )
        # DB-TRG-12 Episode 初始状态、READY 冻结与禁删
        rec.check(
            "DB-TRG-12",
            lambda: expect_error(
                conn,
                "INSERT INTO episodes (id, project_id, episode_lineage_id, episode_version, "
                "code, status, start_time_ns, end_time_ns, duration_ns, "
                "canonical_schema_version, pipeline_id, pipeline_version, "
                "segmentation_rule_version, created_by, updated_by) VALUES "
                f"('{uuid4()}', '{project_a}', '{uuid4()}', 1, 'EP-BAD', 'READY', 1, 2, 1, "
                f"'v1', 'p', 'v1', 's', '{user_a}', '{user_a}')",
                "episode_initial_status_invalid",
            ),
        )
        conn.execute(
            "INSERT INTO episodes (id, project_id, episode_lineage_id, episode_version, code, "
            "status, start_time_ns, end_time_ns, duration_ns, canonical_schema_version, "
            "pipeline_id, pipeline_version, segmentation_rule_version, created_by, updated_by) "
            "VALUES (%s, %s, %s, 1, 'EP-TRG', 'PENDING', 1, 2, 1, 'v1', 'p', 'v1', 's', "
            "%s, %s)",
            (episode_id, project_a, uuid4(), user_a, user_a),
        )
        conn.execute(
            f"UPDATE episodes SET status='PROCESSING' WHERE id='{episode_id}'"
        )
        conn.execute(
            "UPDATE episodes SET status='READY', manifest_key='m.json', manifest_sha256=%s "
            "WHERE id=%s",
            (SHA_A, episode_id),
        )
        rec.check(
            "DB-TRG-13",
            lambda: expect_error(
                conn,
                f"UPDATE episodes SET code='EP-TRG-CHANGED' WHERE id='{episode_id}'",
                "episode_data_immutable",
            ),
        )
        rec.check(
            "DB-TRG-14",
            lambda: expect_error(
                conn,
                f"DELETE FROM episodes WHERE id='{episode_id}'",
                "episode_delete_forbidden",
            ),
        )
        conn.execute(
            "INSERT INTO lineage_node_refs (project_id, node_type, node_id) VALUES "
            "(%s, 'RAW_ASSET', %s), (%s, 'RAW_ASSET', %s), (%s, 'EPISODE', %s), "
            "(%s, 'RAW_ASSET', %s)",
            (
                project_a,
                asset_id,
                project_a,
                node_a,
                project_a,
                node_b,
                project_b,
                node_c,
            ),
        )
        # DB-TRG-15 血缘节点身份不可变与禁删
        rec.check(
            "DB-TRG-15",
            lambda: expect_error(
                conn,
                f"UPDATE lineage_node_refs SET node_id='{uuid4()}' "
                f"WHERE project_id='{project_a}' AND node_type='RAW_ASSET' AND node_id='{node_a}'",
                "lineage_node_ref_identity_immutable",
            ),
        )
        conn.execute(
            "INSERT INTO data_lineage_edges (id, project_id, from_node_type, from_node_id, "
            "to_node_type, to_node_id, relation, created_by) VALUES "
            "(%s, %s, 'RAW_ASSET', %s, 'EPISODE', %s, 'SEGMENTED_INTO', %s)",
            (edge_id, project_a, asset_id, node_b, user_a),
        )
        rec.check(
            "DB-TRG-16",
            lambda: expect_error(
                conn,
                f"UPDATE data_lineage_edges SET relation='SUPERSEDES' WHERE id='{edge_id}'",
                "lineage_edge_append_only",
            ),
        )
        rec.check(
            "DB-TRG-17",
            lambda: expect_error(
                conn,
                f"DELETE FROM data_lineage_edges WHERE id='{edge_id}'",
                "lineage_edge_append_only",
            ),
        )
        # DB-TRG-18 自环
        rec.check(
            "DB-TRG-18",
            lambda: expect_error(
                conn,
                "INSERT INTO data_lineage_edges (id, project_id, from_node_type, from_node_id, "
                "to_node_type, to_node_id, relation, created_by) VALUES "
                f"('{uuid4()}', '{project_a}', 'RAW_ASSET', '{asset_id}', 'RAW_ASSET', "
                f"'{asset_id}', 'SEGMENTED_INTO', '{user_a}')",
                "lineage_edge_self_forbidden",
            ),
        )
        # DB-TRG-19 跨项目边（复合外键兜底）
        rec.check(
            "DB-TRG-19",
            lambda: expect_error(
                conn,
                "INSERT INTO data_lineage_edges (id, project_id, from_node_type, from_node_id, "
                "to_node_type, to_node_id, relation, created_by) VALUES "
                f"('{uuid4()}', '{project_a}', 'RAW_ASSET', '{asset_id}', 'RAW_ASSET', "
                f"'{node_c}', 'PRODUCED', '{user_a}')",
                "fk_data_lineage_edges_project_to_node",
            ),
        )


# --------------------------------------------------------------------------- MinIO
class MinioServer:
    def __init__(self, rec: Recorder) -> None:
        self.rec = rec
        self.data_dir = Path(tempfile.mkdtemp(prefix="ddp311-minio-"))
        self.log_path = EVIDENCE / "minio.log"
        self._log = self.log_path.open("wb")
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def env(self) -> dict[str, str]:
        return {
            "MINIO_ROOT_USER": MINIO_ACCESS_KEY,
            "MINIO_ROOT_PASSWORD": MINIO_SECRET_KEY,
            "MINIO_BROWSER": "off",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        }

    def start(self) -> None:
        self._process = subprocess.Popen(
            [
                MINIO_BIN,
                "server",
                str(self.data_dir),
                "--address",
                f"127.0.0.1:{MINIO_PORT}",
                "--console-address",
                f"127.0.0.1:{MINIO_CONSOLE_PORT}",
            ],
            env=self.env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        wait_port("127.0.0.1", MINIO_PORT, timeout=30, label="MinIO")
        response = wait_http(f"http://127.0.0.1:{MINIO_PORT}/minio/health/live", timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"MinIO 健康检查失败：{response.status_code}")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            self._process = None
        self._log.close()
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def version(self) -> dict[str, Any]:
        result = subprocess.run(
            [MINIO_BIN, "--version"], capture_output=True, text=True, check=False
        )
        digest = hashlib.sha256(Path(MINIO_BIN).read_bytes()).hexdigest()
        stat = Path(MINIO_BIN).stat()
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "binary": MINIO_BIN,
            "binary_sha256": digest,
            "binary_size": stat.st_size,
            "declared_release": "RELEASE.2024-08-17T01-24-54Z",
            "declared_commit": "72cff79c8a7c",
        }


def s3_client() -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{MINIO_PORT}",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


# --------------------------------------------------------------------------- API
class ApiServer:
    def __init__(self, rec: Recorder, dbname: str) -> None:
        self.rec = rec
        self.dbname = dbname
        self.log_path = EVIDENCE / "api.log"
        self._log = self.log_path.open("wb")
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def env(self) -> dict[str, str]:
        current = dict(os.environ)
        current.update(
            {
                "DDP_ENV": "local",
                "DDP_LOCAL_HEADER_AUTH": "true",
                "DDP_DATABASE_URL": PG_URL_TEMPLATE.format(dbname=self.dbname),
                "DDP_S3_ENDPOINT": f"http://127.0.0.1:{MINIO_PORT}",
                "DDP_S3_ACCESS_KEY": MINIO_ACCESS_KEY,
                "DDP_S3_SECRET_KEY": MINIO_SECRET_KEY,
                "DDP_S3_BUCKET": BUCKET,
                "DDP_S3_REGION": "us-east-1",
                "DDP_UPLOAD_PART_SIZE_BYTES": str(PART_SIZE),
                "DDP_UPLOAD_MAX_SIZE_BYTES": str(64 * 1024 * 1024),
                "DDP_UPLOAD_SESSION_TTL_HOURS": "1",
                "DDP_UPLOAD_URL_TTL_SECONDS": "300",
                "DDP_DOWNLOAD_URL_TTL_SECONDS": "15",
                "DDP_VALIDATION_WORKER_POLL_INTERVAL_SECONDS": "0.2",
                "DDP_VALIDATION_STREAM_CHUNK_SIZE_BYTES": str(1024 * 1024),
                "DDP_RATE_LIMIT_ENABLED": "false",
                "DDP_LOG_LEVEL": "INFO",
            }
        )
        return current

    def start(self) -> None:
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(API_PORT),
                "--log-level",
                "info",
            ],
            cwd=BACKEND,
            env=self.env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        wait_port("127.0.0.1", API_PORT, timeout=30, label="uvicorn")
        response = wait_http(f"{API_BASE}/health", timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"API 健康检查失败：{response.status_code} {response.text}")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            self._process = None
        self._log.close()

    def run_worker(self, label: str) -> int:
        path = EVIDENCE / f"worker-{label}.log"
        with path.open("wb") as log:
            result = subprocess.run(
                [sys.executable, "-m", "app.workers.outbox", "--drain"],
                cwd=BACKEND,
                env=self.env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=240,
                check=False,
            )
        return result.returncode


# --------------------------------------------------------------------------- HTTP 工具
def headers_for(user_id: UUID, *, if_match: str | None = None, idem: str | None = None) -> dict[str, str]:
    headers = {"X-User-Id": str(user_id)}
    if if_match is not None:
        headers["If-Match"] = if_match
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


def api_call(
    method: str,
    path: str,
    user_id: UUID,
    *,
    json_body: Any = None,
    if_match: str | None = None,
    idem: str | None = None,
    timeout: float = 120,
) -> httpx.Response:
    return httpx.request(
        method,
        f"{API_PREFIX}{path}",
        json=json_body,
        headers=headers_for(user_id, if_match=if_match, idem=idem),
        timeout=timeout,
    )


def expect_status(response: httpx.Response, expected: int, *, code: str | None = None) -> Any:
    if response.status_code != expected:
        raise AssertionError(
            f"HTTP {response.status_code} != {expected}；body={response.text[:500]}"
        )
    if code is not None:
        payload = response.json()
        actual = (payload.get("error") or {}).get("code")
        if actual != code:
            raise AssertionError(f"错误码 {actual!r} != {code!r}；body={response.text[:500]}")
    return response


def create_upload_session(
    user_id: UUID,
    project_id: UUID,
    content: bytes,
    *,
    filename: str = "robot.mcap",
    idem: str | None = None,
) -> dict[str, Any]:
    response = api_call(
        "POST",
        f"/projects/{project_id}/upload-sessions",
        user_id,
        json_body={
            "original_filename": filename,
            "declared_content_type": "application/x-mcap",
            "declared_size_bytes": len(content),
            "format_hint": "MCAP",
        },
        idem=idem,
    )
    expect_status(response, 201)
    return response.json()


def upload_parts(
    user_id: UUID,
    project_id: UUID,
    session: dict[str, Any],
    content: bytes,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session_id = session["upload_session_id"]
    materials: list[dict[str, Any]] = []
    for number, start in enumerate(range(0, len(content), PART_SIZE), start=1):
        chunk = content[start : start + PART_SIZE]
        url_response = api_call(
            "POST",
            f"/projects/{project_id}/upload-sessions/{session_id}/parts/{number}/url",
            user_id,
        )
        expect_status(url_response, 200)
        presigned = url_response.json()
        put = httpx.put(
            presigned["url"],
            content=chunk,
            headers=dict(presigned.get("headers") or {}),
            timeout=120,
        )
        if put.status_code != 200:
            raise AssertionError(f"真实 MinIO 分片 PUT 失败：{put.status_code} {put.text[:300]}")
        etag = put.headers.get("etag")
        if not etag:
            raise AssertionError("MinIO 未返回 ETag")
        ack = api_call(
            "POST",
            f"/projects/{project_id}/upload-sessions/{session_id}/parts/{number}/ack",
            user_id,
            json_body={
                "etag": etag,
                "size_bytes": len(chunk),
                "sha256": sha256_ref(chunk),
            },
        )
        expect_status(ack, 200)
        materials.append(
            {
                "part_number": number,
                "etag": etag,
                "size_bytes": len(chunk),
                "sha256": sha256_ref(chunk),
                "md5": hashlib.md5(chunk).hexdigest(),  # noqa: S324 - 仅比对 ETag
            }
        )
    detail = api_call(
        "GET",
        f"/projects/{project_id}/upload-sessions/{session_id}",
        user_id,
    )
    expect_status(detail, 200)
    return materials, detail.json()


def complete_payload(content: bytes, materials: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "expected_sha256": sha256_ref(content),
        "expected_size_bytes": len(content),
        "parts": [
            {
                "part_number": item["part_number"],
                "etag": item["etag"],
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
            }
            for item in materials
        ],
    }


def complete_upload(
    user_id: UUID,
    project_id: UUID,
    session_id: str,
    content: bytes,
    materials: list[dict[str, Any]],
    version: int,
    *,
    idem: str | None = None,
) -> httpx.Response:
    return api_call(
        "POST",
        f"/projects/{project_id}/upload-sessions/{session_id}/complete",
        user_id,
        json_body=complete_payload(content, materials),
        if_match=f'"{version}"',
        idem=idem,
        timeout=180,
    )


def upload_complete(
    user_id: UUID,
    project_id: UUID,
    content: bytes,
    *,
    prefix: str,
    idem_suffix: str,
) -> dict[str, Any]:
    session = create_upload_session(
        user_id,
        project_id,
        content,
        filename=f"{prefix}.mcap",
        idem=f"{idem_suffix}-create-0001",
    )
    materials, detail = upload_parts(user_id, project_id, session, content)
    response = complete_upload(
        user_id,
        project_id,
        session["upload_session_id"],
        content,
        materials,
        int(detail["version"]),
        idem=f"{idem_suffix}-complete-0001",
    )
    expect_status(response, 202)
    return {
        "session": session,
        "materials": materials,
        "detail": detail,
        "complete": response.json(),
    }


# --------------------------------------------------------------------------- 数据构造
def make_content(tag: bytes, size: int = PART_SIZE + 1024) -> bytes:
    body = (tag + b"-DDP-311-SYNTHETIC-" + bytes(range(256))) * (size // 256 + 2)
    return MCAP_MAGIC + body[: size - len(MCAP_MAGIC)]


def seed_identity(dbname: str) -> dict[str, UUID]:
    project_a, project_b = uuid4(), uuid4()
    editor_a, owner_a, viewer_a, outsider = uuid4(), uuid4(), uuid4(), uuid4()
    with db_connect(dbname) as conn:
        conn.execute(
            "INSERT INTO projects (id, code, name) VALUES (%s, %s, %s), (%s, %s, %s)",
            (
                project_a,
                f"qa311-a-{project_a.hex[:8]}",
                "DDP-311 项目 A",
                project_b,
                f"qa311-b-{project_b.hex[:8]}",
                "DDP-311 项目 B",
            ),
        )
        conn.execute(
            "INSERT INTO users (id, display_name) VALUES "
            "(%s, '编辑器'), (%s, '负责人'), (%s, '只读用户'), (%s, '项目外用户')",
            (editor_a, owner_a, viewer_a, outsider),
        )
        conn.execute(
            "INSERT INTO project_members (id, project_id, user_id, role) VALUES "
            "(%s, %s, %s, 'editor'), (%s, %s, %s, 'owner'), (%s, %s, %s, 'viewer'), "
            "(%s, %s, %s, 'owner')",
            (
                uuid4(), project_a, editor_a,
                uuid4(), project_a, owner_a,
                uuid4(), project_a, viewer_a,
                uuid4(), project_b, owner_a,
            ),
        )
    return {
        "project_a": project_a,
        "project_b": project_b,
        "editor_a": editor_a,
        "owner_a": owner_a,
        "viewer_a": viewer_a,
        "outsider": outsider,
    }


def time_base() -> dict[str, Any]:
    return {
        "time_domain": "UTC",
        "clock_source": "NTP",
        "sync_method": "NTP",
        "sync_status": "VERIFIED",
    }


def calibration_snapshot() -> list[dict[str, Any]]:
    return [
        {
            "calibration_id": str(uuid4()),
            "calibration_version": 1,
            "checksum": SHA_A,
            "target_type": "ROBOT",
            "target_id": str(uuid4()),
            "schema_version": "ddp.calibration/1.0.0",
        }
    ]


def episode_payload(
    asset_id: UUID,
    *,
    code: str,
    start_time_ns: int,
    end_time_ns: int,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "raw_asset_ids": [str(asset_id)],
        "segmentation": {
            "method": "MANUAL_TIME_RANGE",
            "rule_version": "seg-manual-v1",
            "start_time_ns": start_time_ns,
            "end_time_ns": end_time_ns,
        },
        "time_base": time_base(),
        "calibration_snapshot": calibration_snapshot(),
        "canonical_schema_version": "ddp.canonical/1.0.0-draft",
        "pipeline_id": "parser",
        "pipeline_version": "1.0.0",
        "code": code,
    }
    payload.update(overrides)
    return payload


def manifest_for_episode(episode: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": episode["canonical_schema_version"],
        "episode_id": episode["episode_id"],
        "episode_lineage_id": episode["episode_lineage_id"],
        "episode_version": episode["episode_version"],
        "project_id": episode["project_id"],
        "code": episode["code"],
        "task_id": episode["task_id"],
        "batch_id": episode["batch_id"],
        "robot_id": episode["robot_id"],
        "device_ids": [],
        "sensor_ids": [],
        "start_time": str(episode["start_time_ns"]),
        "end_time": str(episode["end_time_ns"]),
        "duration_ns": str(episode["duration_ns"]),
        "time_base": dict(episode["time_base"]),
        "time_alignment": {"method": "NTP"},
        "calibration_snapshot": [dict(item) for item in episode["calibration_snapshot"]],
        "streams": [],
        "raw_asset_ids": [],
        "provenance": {"pipeline_id": episode["pipeline_id"]},
        "quality": {"status": "PASS", "rule_version": "q1", "issues": []},
    }


def job_payload(asset_id: UUID, *, job_type: str = "PARSE_METADATA") -> dict[str, Any]:
    return {
        "job_type": job_type,
        "timeout_sec": 60,
        "raw_asset_ids": [str(asset_id)],
        "max_attempts": 3,
        "pipeline_id": "parser",
        "pipeline_version": "1.0.0",
    }


# --------------------------------------------------------------------------- 功能验收
def run_functional_checks(
    rec: Recorder,
    dbname: str,
    ids: dict[str, UUID],
    api: ApiServer,
) -> dict[str, Any]:
    project_a = ids["project_a"]
    project_b = ids["project_b"]
    editor = ids["editor_a"]
    owner = ids["owner_a"]
    viewer = ids["viewer_a"]
    outsider = ids["outsider"]
    s3 = s3_client()
    state: dict[str, Any] = {}

    # ---------------------------------------------------------------- 正常上传全链路
    content = make_content(b"NORMAL")

    def normal_upload() -> str:
        session = create_upload_session(
            editor,
            project_a,
            content,
            filename="normal.mcap",
            idem="normal-upload-create-0001",
        )
        materials, detail = upload_parts(editor, project_a, session, content)
        upload_id = pg_scalar(
            dbname,
            "SELECT multipart_upload_id FROM upload_sessions WHERE id = %s",
            (UUID(session["upload_session_id"]),),
        )
        assert upload_id
        listed = s3.list_parts(
            Bucket=BUCKET,
            Key=session["object_key"],
            UploadId=upload_id,
        )
        parts = listed["Parts"]
        assert len(parts) == 2, parts
        by_number = {int(item["PartNumber"]): item for item in parts}
        for material in materials:
            item = by_number[material["part_number"]]
            assert int(item["Size"]) == material["size_bytes"]
            assert item["ETag"].strip('"') == material["etag"].strip('"')
            assert item["ETag"].strip('"') == material["md5"], (
                f"ETag 与分片 MD5 不一致：{item['ETag']} vs {material['md5']}"
            )
        response = complete_upload(
            editor,
            project_a,
            session["upload_session_id"],
            content,
            materials,
            int(detail["version"]),
            idem="normal-upload-complete-0001",
        )
        expect_status(response, 202)
        complete = response.json()
        assert complete["raw_asset"] is not None
        assert complete["replayed"] is False
        result = {
            "session": session,
            "materials": materials,
            "detail": detail,
            "complete": complete,
        }
        state["normal"] = result
        return (
            f"真实 MinIO ListParts {len(parts)} 个分片（complete 前）；"
            f"ETag/Size 与 PUT 及 MD5 一致；"
            f"session={session['upload_session_id']}，asset={complete['raw_asset']['asset_id']}"
        )

    rec.check("S3-PRESIGN-ACK-COMPLETE", normal_upload)

    def normal_db_state() -> str:
        session_id = UUID(state["normal"]["session"]["upload_session_id"])
        asset_id = UUID(state["normal"]["complete"]["raw_asset"]["asset_id"])
        status = pg_scalar(dbname, "SELECT status FROM upload_sessions WHERE id = %s", (session_id,))
        asset = pg_scalar(
            dbname,
            "SELECT status FROM raw_assets WHERE id = %s",
            (asset_id,),
        )
        links = pg_scalar(
            dbname,
            "SELECT count(*) FROM raw_asset_links WHERE asset_id = %s",
            (asset_id,),
        )
        assert status == "COMPLETED", status
        assert asset == "VALIDATING", asset
        assert links == 1, links
        return f"会话 COMPLETED、资产初始 VALIDATING、使用关系 1 条"
    rec.check("UPLOAD-DB-STATE", normal_db_state)

    def head_and_range() -> str:
        session = state["normal"]["session"]
        head = s3.head_object(Bucket=BUCKET, Key=session["object_key"])
        ranged = s3.get_object(
            Bucket=BUCKET, Key=session["object_key"], Range="bytes=0-7"
        )
        prefix = ranged["Body"].read()
        assert int(head["ContentLength"]) == len(content)
        assert prefix == MCAP_MAGIC, prefix
        return (
            f"HEAD size={head['ContentLength']}；Range GET 206/prefix={prefix!r}；"
            f"对象 ETag={head['ETag']}"
        )
    rec.check("S3-COMPLETE-RANGE-GET", head_and_range)

    def worker_valid() -> str:
        code = api.run_worker("valid")
        assert code == 0, f"Worker 退出码 {code}"
        asset_id = UUID(state["normal"]["complete"]["raw_asset"]["asset_id"])
        response = api_call("GET", f"/projects/{project_a}/raw-assets/{asset_id}", editor)
        expect_status(response, 200)
        asset = response.json()
        assert asset["status"] == "VALID", asset
        assert asset["sha256"] == sha256_ref(content)
        state["valid_asset_id"] = asset_id
        validated = pg_scalar(
            dbname,
            "SELECT count(*) FROM outbox_events WHERE event_type = "
            "'ingestion.raw_asset.validated' AND aggregate_id = %s",
            (asset_id,),
        )
        published = pg_scalar(
            dbname,
            "SELECT count(*) FROM outbox_events WHERE event_type = "
            "'ingestion.raw_asset.validation_requested' AND aggregate_id = %s "
            "AND status = 'PUBLISHED'",
            (asset_id,),
        )
        assert validated == 1, validated
        assert published == 1, published
        return f"Worker --drain 退出码 0；资产 VALIDATING→VALID；validated 事件 1 条、源事件已 PUBLISHED"
    rec.check("WORKER-VALIDATING-TO-VALID", worker_valid)

    # ---------------------------------------------------------------- 下载地址
    def download_url_and_fetch() -> str:
        asset_id = state["valid_asset_id"]
        response = api_call(
            "GET",
            f"/raw-assets/{asset_id}/download-url?project_id={project_a}",
            editor,
        )
        expect_status(response, 200)
        payload = response.json()
        url = payload["url"]
        expires_at = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
        ttl = (expires_at - datetime.now(UTC)).total_seconds()
        assert "X-Amz-Signature=" in url, url
        assert f"127.0.0.1:{MINIO_PORT}" in url, url
        assert "X-Amz-Expires=15" in url, url
        assert 0 < ttl <= 16, ttl
        downloaded = httpx.get(url, timeout=60)
        assert downloaded.status_code == 200, downloaded.status_code
        assert downloaded.content == content
        ranged = httpx.get(url, headers={"Range": "bytes=0-7"}, timeout=30)
        assert ranged.status_code == 206, ranged.status_code
        assert ranged.content == MCAP_MAGIC
        state["download_url"] = url
        state["download_expires_at"] = expires_at
        return (
            f"预签名下载 200 且内容一致；Range GET 206；X-Amz-Expires=15；"
            f"expires_at 剩余 {ttl:.1f}s"
        )
    rec.check("DOWNLOAD-URL-FETCH-RANGE", download_url_and_fetch)

    def download_audit_no_plaintext() -> str:
        asset_id = state["valid_asset_id"]
        rows = pg_rows(
            dbname,
            "SELECT details FROM audit_logs WHERE project_id = %s AND resource_id = %s "
            "AND action = 'download_requested'",
            (project_a, asset_id),
        )
        assert len(rows) == 1, rows
        serialized = json.dumps(rows[0][0], ensure_ascii=False)
        assert state["download_url"] not in serialized
        assert "X-Amz" not in serialized
        assert "http" not in serialized
        assert "127.0.0.1" not in serialized
        return "download_requested 审计 1 条；不含 URL、X-Amz、http、主机明文"
    rec.check("DOWNLOAD-AUDIT-NO-PLAINTEXT", download_audit_no_plaintext)

    def download_url_expired() -> str:
        wait = (state["download_expires_at"] - datetime.now(UTC)).total_seconds() + 2
        if wait > 0:
            time.sleep(min(wait, 20))
        expired = httpx.get(state["download_url"], timeout=30)
        assert expired.status_code in {400, 403}, expired.status_code
        return f"过期后访问预签名 URL 返回 {expired.status_code}（拒绝）"
    rec.check("DOWNLOAD-URL-EXPIRES", download_url_expired)

    # ---------------------------------------------------------------- Hash 不符隔离
    tampered_content = make_content(b"TAMPER")

    def tampered_upload() -> str:
        result = upload_complete(
            editor,
            project_a,
            tampered_content,
            prefix="tamper",
            idem_suffix="tamper-upload",
        )
        state["tamper"] = result
        return f"第二份资产完成，asset={result['complete']['raw_asset']['asset_id']}"
    rec.check("TAMPER-UPLOAD-COMPLETE", tampered_upload)

    def tamper_object_and_worker() -> str:
        asset_id = UUID(state["tamper"]["complete"]["raw_asset"]["asset_id"])
        session = state["tamper"]["session"]
        altered = bytearray(tampered_content)
        altered[-1] ^= 0xFF
        s3.put_object(Bucket=BUCKET, Key=session["object_key"], Body=bytes(altered))
        code = api.run_worker("tampered")
        assert code == 0, f"Worker 退出码 {code}"
        response = api_call("GET", f"/projects/{project_a}/raw-assets/{asset_id}", editor)
        expect_status(response, 200)
        asset = response.json()
        assert asset["status"] == "QUARANTINED", asset
        assert "hash_mismatch" in asset["quarantine_codes"], asset
        assert asset["quarantine_reason"]
        state["quarantined_asset_id"] = asset_id
        audit = pg_scalar(
            dbname,
            "SELECT count(*) FROM audit_logs WHERE resource_id = %s AND action = 'validate' "
            "AND result = 'FAILURE' AND details->>'outcome' = 'QUARANTINED'",
            (asset_id,),
        )
        assert audit == 1, audit
        blocked = api_call(
            "GET",
            f"/raw-assets/{asset_id}/download-url?project_id={project_a}",
            editor,
        )
        expect_status(blocked, 422, code="asset_not_usable")
        return (
            "对象同大小内容篡改后 Worker 校验：资产 QUARANTINED、码含 hash_mismatch；"
            "审计 FAILURE；下载地址 422 asset_not_usable"
        )
    rec.check("WORKER-HASH-MISMATCH-QUARANTINE", tamper_object_and_worker)

    # ---------------------------------------------------------------- D01 并发 complete
    concurrent_content = make_content(b"CONCURRENT")

    def concurrent_prepare() -> str:
        session = create_upload_session(
            editor,
            project_a,
            concurrent_content,
            filename="concurrent.mcap",
            idem="concurrent-create-0001",
        )
        materials, detail = upload_parts(editor, project_a, session, concurrent_content)
        state["concurrent"] = {
            "session": session,
            "materials": materials,
            "detail": detail,
        }
        return (
            f"并发会话就绪 session={session['upload_session_id']}，"
            f"version={detail['version']}，分片={len(materials)}"
        )
    rec.check("D01-PREPARE", concurrent_prepare)

    def concurrent_complete() -> str:
        prepared = state["concurrent"]
        session_id = prepared["session"]["upload_session_id"]
        version = int(prepared["detail"]["version"])
        body = complete_payload(concurrent_content, prepared["materials"])
        url = (
            f"{API_PREFIX}/projects/{project_a}/upload-sessions/{session_id}/complete"
        )
        barrier = threading.Barrier(2)
        results: list[httpx.Response] = []
        lock = threading.Lock()

        def send(key: str) -> None:
            barrier.wait(timeout=20)
            with httpx.Client(timeout=180) as client:
                response = client.post(
                    url,
                    json=body,
                    headers={
                        "X-User-Id": str(editor),
                        "If-Match": f'"{version}"',
                        "Idempotency-Key": key,
                    },
                )
            with lock:
                results.append(response)

        threads = [
            threading.Thread(target=send, args=("concurrent-complete-key-0001",)),
            threading.Thread(target=send, args=("concurrent-complete-key-0002",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=200)
        assert len(results) == 2, len(results)
        statuses = sorted(item.status_code for item in results)
        assert 503 not in statuses, [item.text for item in results]
        assert statuses in ([202, 202], [202, 409]), [
            (item.status_code, item.text) for item in results
        ]
        if statuses == [202, 202]:
            flags = sorted(
                item.headers.get("Idempotent-Replayed", "").lower() for item in results
            )
            assert flags == ["false", "true"], flags
        state["concurrent"]["responses"] = [
            (item.status_code, item.headers.get("Idempotent-Replayed"), item.text[:300])
            for item in results
        ]
        detail = (
            f"并发状态码={statuses}，无 503；"
            f"响应={state['concurrent']['responses']}"
        )
        return detail
    rec.check("D01-CONCURRENT-COMPLETE", concurrent_complete)

    def concurrent_assets() -> str:
        session_id = UUID(state["concurrent"]["session"]["upload_session_id"])
        session_status = pg_scalar(
            dbname, "SELECT status FROM upload_sessions WHERE id = %s", (session_id,)
        )
        assets = pg_scalar(
            dbname,
            "SELECT count(*) FROM raw_assets WHERE upload_session_id = %s",
            (session_id,),
        )
        links = pg_scalar(
            dbname,
            "SELECT count(*) FROM raw_asset_links WHERE upload_session_id = %s",
            (session_id,),
        )
        assert session_status == "COMPLETED", session_status
        assert assets == 1, assets
        assert links == 1, links
        return f"会话 COMPLETED；该会话资产数=1、使用关系=1（无重复可用资产）"
    rec.check("D01-SINGLE-ASSET", concurrent_assets)

    # 让并发上传的资产也完成校验，并确认只有一个可用资产。
    api.run_worker("concurrent")

    def concurrent_single_valid_asset() -> str:
        session_id = UUID(state["concurrent"]["session"]["upload_session_id"])
        rows = pg_rows(
            dbname,
            "SELECT id, status FROM raw_assets WHERE upload_session_id = %s",
            (session_id,),
        )
        assert len(rows) == 1, rows
        assert rows[0][1] == "VALID", rows
        return f"Worker 后该会话可用资产=1（status=VALID，asset={rows[0][0]}）"

    rec.check("D01-SINGLE-VALID-ASSET", concurrent_single_valid_asset)

    # ---------------------------------------------------------------- Episode
    asset_id = state["valid_asset_id"]
    flow_episode: dict[str, Any] = {}

    def episode_create() -> str:
        payload = episode_payload(
            asset_id,
            code="EP-311-FLOW",
            start_time_ns=1_000,
            end_time_ns=2_000,
        )
        response = api_call(
            "POST",
            f"/projects/{project_a}/episodes",
            editor,
            json_body=payload,
            idem="episode-flow-create-0001",
        )
        expect_status(response, 201)
        episode = response.json()
        assert episode["status"] == "PENDING"
        assert episode["episode_version"] == 1
        assert response.headers["ETag"] == '"1"'
        assert response.headers["Idempotent-Replayed"] == "false"
        flow_episode.update(episode)
        replay = api_call(
            "POST",
            f"/projects/{project_a}/episodes",
            editor,
            json_body=payload,
            idem="episode-flow-create-0001",
        )
        expect_status(replay, 201)
        assert replay.headers["Idempotent-Replayed"] == "true"
        assert replay.json()["episode_id"] == episode["episode_id"]
        conflict = api_call(
            "POST",
            f"/projects/{project_a}/episodes",
            editor,
            json_body=episode_payload(
                asset_id, code="EP-311-OTHER", start_time_ns=5_000, end_time_ns=6_000
            ),
            idem="episode-flow-create-0001",
        )
        expect_status(conflict, 409, code="idempotency_key_reused")
        return (
            f"创建 201（PENDING/version=1/ETag=1）；同键同请求重放 replayed=true；"
            f"同键异体 409 idempotency_key_reused"
        )
    rec.check("D02-EPISODE-CREATE-IDEMPOTENT", episode_create)

    def episode_actions_and_revision() -> str:
        episode_id = flow_episode["episode_id"]
        base = f"/projects/{project_a}/episodes/{episode_id}"
        ready = api_call(
            "POST",
            f"{base}/actions/ready",
            editor,
            json_body={"manifest": manifest_for_episode(flow_episode), "frame_count": 30},
            if_match='"1"',
            idem="episode-flow-ready-0001",
        )
        expect_status(ready, 200)
        ready_body = ready.json()
        assert ready_body["status"] == "READY", ready_body
        assert ready_body["manifest_key"].endswith("/1/manifest.json")
        assert ready_body["manifest_sha256"].startswith("sha256:")
        assert ready.headers["ETag"] == '"3"'
        ready_replay = api_call(
            "POST",
            f"{base}/actions/ready",
            editor,
            json_body={"manifest": manifest_for_episode(flow_episode), "frame_count": 30},
            if_match='"1"',
            idem="episode-flow-ready-0001",
        )
        expect_status(ready_replay, 200)
        assert ready_replay.headers["Idempotent-Replayed"] == "true"
        checking = api_call(
            "POST",
            f"{base}/actions/quality-check",
            editor,
            if_match='"3"',
            idem="episode-flow-qc-0001",
        )
        expect_status(checking, 200)
        assert checking.json()["status"] == "QUALITY_CHECKING"
        quality = api_call(
            "POST",
            f"{base}/actions/quality-result",
            editor,
            json_body={"quality_status": "PASS"},
            if_match='"4"',
            idem="episode-flow-qr-0001",
        )
        expect_status(quality, 200)
        assert quality.json()["quality_status"] == "PASS"
        accepted = api_call(
            "POST",
            f"{base}/actions/accept",
            editor,
            if_match='"5"',
            idem="episode-flow-accept-0001",
        )
        expect_status(accepted, 200)
        assert accepted.json()["status"] == "ACCEPTED"
        completed = api_call(
            "POST",
            f"{base}/actions/complete",
            editor,
            if_match='"6"',
            idem="episode-flow-complete-0001",
        )
        expect_status(completed, 200)
        assert completed.json()["status"] == "COMPLETED"
        revised = api_call(
            "POST",
            f"{base}/revisions",
            editor,
            json_body={"code": "EP-311-FLOW-V2"},
            if_match='"7"',
            idem="episode-flow-revise-0001",
        )
        expect_status(revised, 201)
        revised_body = revised.json()
        assert revised_body["episode_id"] != episode_id
        assert revised_body["episode_version"] == 2
        assert revised_body["supersedes_id"] == episode_id
        assert revised_body["status"] == "PENDING"
        replay = api_call(
            "POST",
            f"{base}/revisions",
            editor,
            json_body={"code": "EP-311-FLOW-V2"},
            if_match='"7"',
            idem="episode-flow-revise-0001",
        )
        expect_status(replay, 201)
        assert replay.headers["Idempotent-Replayed"] == "true"
        state["flow_episode"] = flow_episode
        state["revised_episode"] = revised_body
        return (
            "ready 200/ETag=3、重放 replayed=true；quality-check→quality-result(PASS)→"
            "accept→complete 均 200；revisions 201/version=2/supersedes 指向旧版/重放 true"
        )
    rec.check("D02-EPISODE-ACTIONS-REVISIONS", episode_actions_and_revision)

    def episode_list_pagination() -> str:
        created = []
        for index in range(1, 5):
            payload = episode_payload(
                asset_id,
                code=f"EP-311-LIST-{index}",
                start_time_ns=index * 10_000,
                end_time_ns=index * 10_000 + 500,
            )
            response = api_call(
                "POST",
                f"/projects/{project_a}/episodes",
                editor,
                json_body=payload,
                idem=f"episode-list-create-{index:04d}",
            )
            expect_status(response, 201)
            created.append(response.json()["episode_id"])
        first = api_call(
            "GET", f"/projects/{project_a}/episodes?limit=2", editor
        )
        expect_status(first, 200)
        first_body = first.json()
        assert len(first_body["items"]) == 2
        assert first_body["next_cursor"]
        second = api_call(
            "GET",
            f"/projects/{project_a}/episodes?limit=2&cursor={first_body['next_cursor']}",
            editor,
        )
        expect_status(second, 200)
        second_body = second.json()
        third = api_call(
            "GET",
            f"/projects/{project_a}/episodes?limit=100&cursor={second_body['next_cursor']}",
            editor,
        )
        expect_status(third, 200)
        all_ids = {
            item["episode_id"]
            for item in first_body["items"] + second_body["items"] + third.json()["items"]
        }
        assert len(all_ids) >= 5
        expect_status(
            api_call("GET", f"/projects/{project_a}/episodes?cursor=bad", editor),
            422,
            code="validation_failed",
        )
        expect_status(
            api_call("GET", f"/projects/{project_a}/episodes?status=UNKNOWN", editor),
            422,
            code="validation_failed",
        )
        state["episode_ids"] = list(all_ids)
        return f"分页 limit=2 游标遍历无重复，共 {len(all_ids)} 条；非法游标/状态 422"
    rec.check("D02-EPISODE-LIST-PAGINATION", episode_list_pagination)

    def episode_errors_permissions() -> str:
        episode_id = flow_episode["episode_id"]
        base = f"/projects/{project_a}/episodes/{episode_id}"
        expect_status(
            api_call(
                "POST",
                f"{base}/actions/quality-check",
                editor,
                idem="episode-err-key-0001",
            ),
            428,
            code="precondition_required",
        )
        expect_status(
            api_call(
                "POST",
                f"{base}/actions/accept",
                editor,
                if_match='"1"',
            ),
            428,
            code="precondition_required",
        )
        expect_status(
            api_call(
                "POST",
                f"{base}/actions/accept",
                editor,
                if_match='"99"',
                idem="episode-err-stale-0001",
            ),
            409,
            code="version_conflict",
        )
        expect_status(
            api_call(
                "POST",
                f"{base}/actions/system-fail",
                editor,
                if_match='"1"',
                idem="episode-err-action-0001",
            ),
            422,
        )
        expect_status(
            api_call(
                "POST",
                f"/projects/{project_a}/episodes",
                editor,
                json_body={
                    **episode_payload(
                        asset_id,
                        code="EP-311-EXTRA",
                        start_time_ns=90_000,
                        end_time_ns=91_000,
                    ),
                    "project_id": str(project_a),
                },
                idem="episode-err-extra-0001",
            ),
            422,
        )
        expect_status(
            api_call(
                "POST",
                f"/projects/{project_a}/episodes",
                viewer,
                json_body=episode_payload(
                    asset_id, code="EP-311-VIEWER", start_time_ns=92_000, end_time_ns=93_000
                ),
                idem="episode-err-viewer-0001",
            ),
            403,
            code="forbidden",
        )
        expect_status(
            api_call("GET", f"/projects/{project_a}/episodes", outsider),
            403,
            code="forbidden",
        )
        expect_status(
            api_call("GET", f"/projects/{project_b}/episodes/{episode_id}", owner),
            404,
            code="resource_not_found",
        )
        cross_list = api_call("GET", f"/projects/{project_b}/episodes", owner)
        expect_status(cross_list, 200)
        assert cross_list.json()["items"] == []
        expect_status(
            api_call(
                "GET", f"/projects/{project_a}/episodes/{uuid4()}", editor
            ),
            404,
            code="resource_not_found",
        )
        return (
            "缺 If-Match/Idempotency-Key=428；陈旧版本=409；未知动作/额外字段=422；"
            "viewer 写=403；外部用户=403；跨项目详情=404、列表为空；随机 ID=404"
        )
    rec.check("D02-EPISODE-ERROR-PERMISSIONS", episode_errors_permissions)

    # ---------------------------------------------------------------- 血缘
    def lineage_checks() -> str:
        episode = state.get("flow_episode") or flow_episode
        episode_id = episode["episode_id"]
        revised = state["revised_episode"]
        forward = api_call(
            "GET",
            f"/episodes/{episode_id}/lineage?project_id={project_a}&direction=forward",
            editor,
        )
        expect_status(forward, 200)
        assert forward.json()["direction"] == "forward"
        reverse = api_call(
            "GET",
            f"/episodes/{episode_id}/lineage?project_id={project_a}&direction=reverse",
            editor,
        )
        expect_status(reverse, 200)
        assert any(
            item["relation"] == "SEGMENTED_INTO"
            and item["from_node_id"] == str(asset_id)
            and item["to_node_id"] == str(episode_id)
            for item in reverse.json()["items"]
        ), reverse.text
        asset_forward = api_call(
            "GET",
            f"/raw-assets/{asset_id}/lineage?project_id={project_a}&direction=forward",
            editor,
        )
        expect_status(asset_forward, 200)
        assert any(
            item["relation"] == "SEGMENTED_INTO" and item["to_node_id"] == str(episode_id)
            for item in asset_forward.json()["items"]
        ), asset_forward.text
        asset_reverse = api_call(
            "GET",
            f"/raw-assets/{asset_id}/lineage?project_id={project_a}&direction=reverse",
            editor,
        )
        expect_status(asset_reverse, 200)
        revised_forward = api_call(
            "GET",
            f"/episodes/{revised['episode_id']}/lineage"
            f"?project_id={project_a}&direction=forward",
            editor,
        )
        expect_status(revised_forward, 200)
        assert any(
            item["relation"] == "SUPERSEDES"
            and item["to_node_id"] == str(episode_id)
            for item in revised_forward.json()["items"]
        ), revised_forward.text
        revised_reverse = api_call(
            "GET",
            f"/episodes/{revised['episode_id']}/lineage"
            f"?project_id={project_a}&direction=reverse",
            editor,
        )
        expect_status(revised_reverse, 200)
        assert any(
            item["relation"] == "SEGMENTED_INTO" for item in revised_reverse.json()["items"]
        )
        expect_status(
            api_call(
                "GET",
                f"/episodes/{episode_id}/lineage?project_id={project_a}&direction=sideways",
                editor,
            ),
            422,
        )
        expect_status(
            api_call("GET", f"/episodes/{episode_id}/lineage", editor),
            422,
        )
        expect_status(
            api_call(
                "GET", f"/episodes/{uuid4()}/lineage?project_id={project_a}", editor
            ),
            404,
            code="resource_not_found",
        )
        expect_status(
            api_call(
                "GET",
                f"/episodes/{episode_id}/lineage?project_id={project_b}",
                owner,
            ),
            404,
        )
        expect_status(
            api_call(
                "GET",
                f"/episodes/{episode_id}/lineage?project_id={project_a}",
                outsider,
            ),
            403,
        )
        expect_status(
            api_call(
                "GET",
                f"/raw-assets/{asset_id}/lineage?project_id={project_b}",
                owner,
            ),
            404,
        )
        return (
            "Episode 反向含 SEGMENTED_INTO、RawAsset 正向含 SEGMENTED_INTO；"
            "修订版正向含 SUPERSEDES、反向含来源资产；非法方向/缺项目 422；"
            "随机/跨项目 404；外部用户 403"
        )
    rec.check("D02-LINEAGE-DIRECTIONS-ISOLATION", lineage_checks)

    def download_permissions() -> str:
        expect_status(
            api_call(
                "GET",
                f"/raw-assets/{asset_id}/download-url?project_id={project_a}",
                viewer,
            ),
            200,
        )
        expect_status(
            api_call(
                "GET",
                f"/raw-assets/{asset_id}/download-url?project_id={project_a}",
                outsider,
            ),
            403,
            code="forbidden",
        )
        expect_status(
            api_call(
                "GET",
                f"/raw-assets/{asset_id}/download-url?project_id={project_b}",
                owner,
            ),
            404,
            code="resource_not_found",
        )
        expect_status(
            api_call(
                "GET",
                f"/raw-assets/{uuid4()}/download-url?project_id={project_a}",
                editor,
            ),
            404,
            code="resource_not_found",
        )
        expect_status(
            api_call("GET", f"/raw-assets/{asset_id}/download-url", editor),
            422,
        )
        return "viewer 200；外部用户 403；跨项目 404；随机资产 404；缺 project_id 422"
    rec.check("D02-DOWNLOAD-PERMISSIONS", download_permissions)

    # ---------------------------------------------------------------- IngestionJob
    def job_create_list_detail() -> str:
        body = job_payload(asset_id)
        created = api_call(
            "POST",
            f"/projects/{project_a}/ingestion-jobs",
            editor,
            json_body=body,
            idem="job-flow-create-0001",
        )
        expect_status(created, 201)
        job = created.json()
        assert job["status"] == "QUEUED"
        assert job["job_type"] == "PARSE_METADATA"
        assert created.headers["ETag"] == '"1"'
        replay = api_call(
            "POST",
            f"/projects/{project_a}/ingestion-jobs",
            editor,
            json_body=body,
            idem="job-flow-create-0001",
        )
        expect_status(replay, 201)
        assert replay.headers["Idempotent-Replayed"] == "true"
        assert replay.json()["job_id"] == job["job_id"]
        conflict = api_call(
            "POST",
            f"/projects/{project_a}/ingestion-jobs",
            editor,
            json_body=job_payload(asset_id, job_type="VALIDATE"),
            idem="job-flow-create-0001",
        )
        expect_status(conflict, 409, code="idempotency_key_reused")
        detail = api_call(
            "GET", f"/projects/{project_a}/ingestion-jobs/{job['job_id']}", editor
        )
        expect_status(detail, 200)
        assert detail.headers["ETag"] == '"1"'
        created_ids = [job["job_id"]]
        for index in range(2, 5):
            extra = api_call(
                "POST",
                f"/projects/{project_a}/ingestion-jobs",
                editor,
                json_body=job_payload(asset_id),
                idem=f"job-flow-create-{index:04d}",
            )
            expect_status(extra, 201)
            created_ids.append(extra.json()["job_id"])
        first = api_call(
            "GET", f"/projects/{project_a}/ingestion-jobs?limit=2", editor
        )
        expect_status(first, 200)
        first_body = first.json()
        assert len(first_body["items"]) == 2 and first_body["next_cursor"]
        second = api_call(
            "GET",
            f"/projects/{project_a}/ingestion-jobs?limit=2&cursor={first_body['next_cursor']}",
            editor,
        )
        expect_status(second, 200)
        second_body = second.json()
        assert len(second_body["items"]) == 2
        assert {
            item["job_id"] for item in first_body["items"]
        } & {item["job_id"] for item in second_body["items"]} == set()
        expect_status(
            api_call(
                "GET", f"/projects/{project_a}/ingestion-jobs?cursor=bad", editor
            ),
            422,
        )
        expect_status(
            api_call(
                "GET", f"/projects/{project_a}/ingestion-jobs?status=UNKNOWN", editor
            ),
            422,
        )
        state["job_ids"] = created_ids
        state["job_id"] = job["job_id"]
        return (
            "创建 201/QUEUED/ETag=1；同键重放 true；同键异体 409；详情 200；"
            "分页两页无交集；非法游标/状态 422"
        )
    rec.check("D02-JOB-CREATE-LIST-DETAIL", job_create_list_detail)

    def job_actions() -> str:
        job_id = state["job_id"]
        base = f"/projects/{project_a}/ingestion-jobs/{job_id}"
        cancelled = api_call(
            "POST",
            f"{base}/actions/cancel",
            editor,
            json_body={"reason": "用户取消"},
            if_match='"1"',
            idem="job-flow-cancel-0001",
        )
        expect_status(cancelled, 200)
        assert cancelled.json()["status"] == "CANCELLING"
        assert cancelled.headers["ETag"] == '"2"'
        replay = api_call(
            "POST",
            f"{base}/actions/cancel",
            editor,
            json_body={"reason": "用户取消"},
            if_match='"1"',
            idem="job-flow-cancel-0001",
        )
        expect_status(replay, 200)
        assert replay.headers["Idempotent-Replayed"] == "true"
        again = api_call(
            "POST",
            f"{base}/actions/cancel",
            editor,
            json_body={"reason": "重复取消"},
            if_match='"2"',
            idem="job-flow-cancel-0002",
        )
        expect_status(again, 409, code="state_transition_invalid")
        retry_id = state["job_ids"][1]
        with db_connect(dbname) as conn:
            conn.execute(
                "UPDATE ingestion_jobs SET status='RUNNING', started_at=now() WHERE id=%s",
                (UUID(retry_id),),
            )
            conn.execute(
                "UPDATE ingestion_jobs SET status='FAILED', finished_at=now(), "
                "error_code='parse_failed', error_summary='解析失败' WHERE id=%s",
                (UUID(retry_id),),
            )
        retried = api_call(
            "POST",
            f"/projects/{project_a}/ingestion-jobs/{retry_id}/actions/retry",
            editor,
            if_match='"1"',
            idem="job-flow-retry-0001",
        )
        expect_status(retried, 200)
        retried_body = retried.json()
        assert retried_body["status"] == "QUEUED"
        assert retried_body["attempt"] == 2
        return (
            "cancel 200/CANCELLING/ETag=2；重放 true；重复取消 409；"
            "FAILED 任务 retry 200/QUEUED/attempt=2"
        )
    rec.check("D02-JOB-ACTIONS", job_actions)

    def job_errors_permissions() -> str:
        expect_status(
            api_call(
                "POST",
                f"/projects/{project_a}/ingestion-jobs",
                editor,
                json_body=job_payload(asset_id),
            ),
            428,
            code="precondition_required",
        )
        expect_status(
            api_call(
                "POST",
                f"/projects/{project_a}/ingestion-jobs/{state['job_id']}/actions/cancel",
                editor,
                json_body={"reason": "取消"},
                idem="job-err-no-match-0001",
            ),
            428,
            code="precondition_required",
        )
        expect_status(
            api_call(
                "POST",
                f"/projects/{project_a}/ingestion-jobs/{state['job_id']}/actions/worker-start",
                editor,
                if_match='"1"',
                idem="job-err-action-0001",
            ),
            422,
        )
        expect_status(
            api_call(
                "POST",
                f"/projects/{project_a}/ingestion-jobs",
                editor,
                json_body={**job_payload(asset_id), "status": "SUCCEEDED"},
                idem="job-err-extra-0001",
            ),
            422,
        )
        expect_status(
            api_call(
                "POST",
                f"/projects/{project_a}/ingestion-jobs",
                editor,
                json_body=job_payload(asset_id, job_type="UNKNOWN"),
                idem="job-err-type-0001",
            ),
            422,
        )
        expect_status(
            api_call(
                "POST",
                f"/projects/{project_a}/ingestion-jobs",
                editor,
                json_body=job_payload(uuid4()),
                idem="job-err-missing-asset-0001",
            ),
            404,
            code="resource_not_found",
        )
        expect_status(
            api_call(
                "POST",
                f"/projects/{project_a}/ingestion-jobs",
                viewer,
                json_body=job_payload(asset_id),
                idem="job-err-viewer-0001",
            ),
            403,
            code="forbidden",
        )
        expect_status(
            api_call("GET", f"/projects/{project_a}/ingestion-jobs", outsider),
            403,
        )
        expect_status(
            api_call(
                "GET",
                f"/projects/{project_b}/ingestion-jobs/{state['job_id']}",
                owner,
            ),
            404,
            code="resource_not_found",
        )
        cross_list = api_call(
            "GET", f"/projects/{project_b}/ingestion-jobs", owner
        )
        expect_status(cross_list, 200)
        assert cross_list.json()["items"] == []
        return (
            "缺幂等键/If-Match=428；未知动作/额外字段/非法类型=422；"
            "缺失资产=404；viewer 写=403；外部用户=403；跨项目详情=404、列表为空"
        )
    rec.check("D02-JOB-ERROR-PERMISSIONS", job_errors_permissions)

    # ---------------------------------------------------------------- 审计补充
    def audit_request_ids() -> str:
        rows = pg_rows(
            dbname,
            "SELECT action, request_id FROM audit_logs WHERE project_id = %s "
            "AND action IN ('create', 'complete', 'ready', 'validate') "
            "ORDER BY occurred_at",
            (project_a,),
        )
        total = len(rows)
        missing = [row for row in rows if not row[1]]
        assert total >= 5, total
        assert not missing, missing
        return f"上传/Episode/校验关键审计 {total} 条，均带 request_id"

    rec.check("AUDIT-REQUEST-ID", audit_request_ids)

    download_audits = pg_rows(
        dbname,
        "SELECT request_id FROM audit_logs WHERE project_id = %s "
        "AND action = 'download_requested'",
        (project_a,),
    )
    missing_download_request_ids = [row for row in download_audits if not row[0]]
    if missing_download_request_ids:
        rec.record(
            "AUDIT-DOWNLOAD-REQUEST-ID",
            "FAIL",
            f"download_requested 审计 {len(download_audits)} 条，其中 "
            f"{len(missing_download_request_ids)} 条缺少 request_id；"
            "下载路由未向 create_download_url 传递 request_id（P2 可追溯性缺陷）",
        )
    else:
        rec.record(
            "AUDIT-DOWNLOAD-REQUEST-ID",
            "PASS",
            f"download_requested 审计 {len(download_audits)} 条均带 request_id",
        )

    def logs_no_signed_url() -> str:
        for name in (
            "api.log",
            "worker-valid.log",
            "worker-tampered.log",
            "worker-concurrent.log",
        ):
            path = EVIDENCE / name
            content_text = path.read_text(encoding="utf-8", errors="ignore")
            assert "X-Amz-Signature" not in content_text, f"{name} 含签名参数"
            assert state["download_url"] not in content_text, f"{name} 含下载 URL 明文"
        return "api/worker 日志均不含 X-Amz-Signature 或下载 URL 明文"

    rec.check("LOGS-NO-SIGNED-URL", logs_no_signed_url)

    return state


# --------------------------------------------------------------------------- 主流程
def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    run_log = EVIDENCE / "run.log"
    run_json = EVIDENCE / "run.json"
    if run_log.exists():
        run_log.unlink()
    rec = Recorder(run_log)
    started_at = datetime.now(UTC)
    environment: dict[str, Any] = {
        "baseline": "fcbec76",
        "task": "DDP-311",
        "started_at": started_at.isoformat(),
        "python": sys.version,
        "minio_binary": MINIO_BIN,
    }
    mig_db = f"ddp_qa_311_mig_{uuid4().hex[:8]}"
    app_db = f"ddp_qa_311_app_{uuid4().hex[:8]}"
    minio: MinioServer | None = None
    api: ApiServer | None = None
    exit_code = 0
    try:
        # 环境信息
        environment["postgres"] = pg_scalar(
            "postgres", "select version()"
        )
        environment["postgres_server_version_num"] = pg_scalar(
            "postgres", "select current_setting('server_version_num')"
        )
        rec.record(
            "ENV-POSTGRES",
            "PASS",
            f"{environment['postgres']}；server_version_num={environment['postgres_server_version_num']}",
        )

        # 1. 迁移与触发器（独立临时库）
        create_database(mig_db)
        rec.record("ENV-MIGDB", "PASS", f"临时数据库 {mig_db} 已创建")
        run_migration_and_trigger_checks(rec, mig_db)
        drop_database(mig_db)
        rec.record("ENV-MIGDB-CLEANUP", "PASS", f"临时数据库 {mig_db} 已删除")

        # 2. 应用库 + MinIO + API
        create_database(app_db)
        rec.record("ENV-APPDB", "PASS", f"临时数据库 {app_db} 已创建")
        engine = create_engine(PG_URL_TEMPLATE.format(dbname=app_db), pool_pre_ping=True)
        try:
            command.upgrade(alembic_config(PG_URL_TEMPLATE.format(dbname=app_db)), "head")
            version = pg_scalar(app_db, "select version_num from alembic_version")
            rec.record("ENV-APPDB-HEAD", "PASS", f"应用库迁移到 {version}")
        finally:
            engine.dispose()
        ids = seed_identity(app_db)
        rec.record(
            "ENV-SEED",
            "PASS",
            f"项目 A={ids['project_a']}，B={ids['project_b']}；editor/owner/viewer/outsider 就绪",
        )

        minio = MinioServer(rec)
        minio.start()
        environment["minio"] = minio.version()
        rec.record(
            "S3-MINIO-START",
            "PASS",
            f"MinIO 已启动 127.0.0.1:{MINIO_PORT}；"
            f"binary_size={environment['minio']['binary_size']}；"
            f"sha256={environment['minio']['binary_sha256']}",
        )
        s3 = s3_client()
        s3.create_bucket(Bucket=BUCKET)
        rec.record("S3-BUCKET", "PASS", f"boto3 创建 bucket={BUCKET}")
        rec.record(
            "S3-MINIO-VERSION",
            "PASS",
            f"实际 --version 输出：{environment['minio']['stdout'].splitlines()[0]}；"
            f"声明版本 RELEASE.2024-08-17T01-24-54Z/commit 72cff79c8a7c",
        )

        api = ApiServer(rec, app_db)
        api.start()
        health = httpx.get(f"{API_BASE}/health", timeout=10)
        environment["health"] = health.json()
        rec.record(
            "ENV-API",
            "PASS",
            f"uvicorn 127.0.0.1:{API_PORT} /health=200；{health.json()}",
        )

        # 3. 功能验收
        state = run_functional_checks(rec, app_db, ids, api)
        write_json(
            EVIDENCE / "state.json",
            {
                key: value
                for key, value in state.items()
                if key not in {"download_url", "download_expires_at"}
            },
        )
    except Exception as exc:  # noqa: BLE001 - 顶层必须落盘证据
        rec.record("FATAL", "BLOCKED", f"{type(exc).__name__}: {exc}")
        rec._file.write(traceback.format_exc() + "\n")
        rec._file.flush()
        exit_code = 2
    finally:
        if api is not None:
            api.stop()
        if minio is not None:
            minio.stop()
        try:
            drop_database(app_db)
            rec.record("ENV-CLEANUP", "PASS", f"临时数据库 {app_db} 已删除")
        except Exception as exc:  # noqa: BLE001
            rec.record("ENV-CLEANUP", "FAIL", f"临时数据库清理失败：{exc}")
            exit_code = exit_code or 1
        summary = rec.summary()
        payload = {
            "task": "DDP-311",
            "baseline": "fcbec76",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "environment": environment,
            "summary": summary,
            "checks": rec.results,
            "log": str(run_log),
        }
        write_json(run_json, payload)
        rec.close()
        print(f"\n结果：{summary}；证据：{run_json}", flush=True)

    if exit_code:
        return exit_code
    return 0 if summary.get("FAIL", 0) == 0 and summary.get("BLOCKED", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
