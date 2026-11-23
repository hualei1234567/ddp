"""Spool 本地轻量 SQLite 持久化（WAL 模式）。

ADR-0047 硬边界：Agent 仅维护本地轻量 SQLite 状态库，绝不连接 DDP PostgreSQL 主库。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from edge_agent.errors import (
    IllegalTransitionError,
    SpoolEntryNotFoundError,
    StateConflictError,
)
from edge_agent.preflight.models import ProceedEvidence
from edge_agent.spool.models import (
    ALLOWED_TRANSITIONS,
    ArtifactReceipt,
    ServerReceipt,
    SpoolArtifactRecord,
    SpoolEntry,
    SpoolResidueSummary,
    SpoolState,
    UploadCursor,
    UploadPartRecord,
)
from edge_agent.spool.schema import SCHEMA_SQL


def _serialize_receipt(receipt: ServerReceipt | None) -> str | None:
    if receipt is None:
        return None
    return json.dumps(
        {
            "received_at": receipt.received_at.isoformat(),
            "artifacts": [
                {
                    "artifact_path": item.artifact_path,
                    "raw_asset_id": item.raw_asset_id,
                    "sha256": item.sha256,
                    "ingestion_status": item.ingestion_status,
                    "deduplicated": item.deduplicated,
                }
                for item in receipt.artifacts
            ],
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _deserialize_receipt(raw: str | None) -> ServerReceipt | None:
    if raw is None:
        return None
    data = json.loads(raw)
    return ServerReceipt(
        received_at=datetime.fromisoformat(data["received_at"]),
        artifacts=tuple(
            ArtifactReceipt(
                artifact_path=item["artifact_path"],
                raw_asset_id=item["raw_asset_id"],
                sha256=item["sha256"],
                ingestion_status=item["ingestion_status"],
                deduplicated=bool(item.get("deduplicated", False)),
            )
            for item in data["artifacts"]
        ),
    )


class SpoolStore:
    """SQLite (WAL) 状态存储。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA_SQL)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def journal_mode(self) -> str:
        row = self._conn.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).lower() if row else ""

    def create_tmp_entry(
        self,
        *,
        entry_id: str,
        capture_session_id: str,
        collection_task_id: str | None,
        project_id: str,
        now: datetime,
    ) -> SpoolEntry:
        ts = now.isoformat()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO spool_entries (
                    entry_id, capture_session_id, collection_task_id, project_id,
                    state, created_at, updated_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    entry_id,
                    capture_session_id,
                    collection_task_id,
                    project_id,
                    SpoolState.TMP.value,
                    ts,
                    ts,
                ),
            )
        return self.get_entry(entry_id)

    def record_manifest_and_artifacts(
        self,
        entry_id: str,
        *,
        manifest_hash: str,
        artifacts: Sequence[SpoolArtifactRecord],
        now: datetime,
    ) -> SpoolEntry:
        total_bytes = sum(item.size_bytes for item in artifacts)
        with self._tx() as conn:
            conn.execute("DELETE FROM spool_artifacts WHERE entry_id = ?", (entry_id,))
            conn.executemany(
                """
                INSERT INTO spool_artifacts (
                    entry_id, rel_path, mime_type, size_bytes, sha256, format, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        entry_id,
                        a.rel_path,
                        a.mime_type,
                        a.size_bytes,
                        a.sha256,
                        a.format,
                        a.duration_ms,
                    )
                    for a in artifacts
                ],
            )
            cur = conn.execute(
                """
                UPDATE spool_entries
                SET manifest_hash = ?, total_bytes = ?, updated_at = ?, version = version + 1
                WHERE entry_id = ?
                """,
                (manifest_hash, total_bytes, now.isoformat(), entry_id),
            )
            if cur.rowcount == 0:
                raise SpoolEntryNotFoundError(f"Spool 条目不存在：{entry_id}")
        return self.get_entry(entry_id)

    def transition(
        self,
        entry_id: str,
        to_state: SpoolState,
        *,
        now: datetime,
        expected_state: SpoolState | None = None,
        server_receipt: ServerReceipt | None = None,
        failure_reason: str | None = None,
        quarantine_reason: str | None = None,
    ) -> SpoolEntry:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT state, version FROM spool_entries WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            if row is None:
                raise SpoolEntryNotFoundError(f"Spool 条目不存在：{entry_id}")
            current = SpoolState(row["state"])
            if expected_state is not None and current != expected_state:
                raise StateConflictError(
                    f"Spool 条目 {entry_id} 状态冲突：期望 {expected_state}，实际 {current}"
                )
            if to_state not in ALLOWED_TRANSITIONS[current]:
                raise IllegalTransitionError(current.value, to_state.value)
            cur = conn.execute(
                """
                UPDATE spool_entries
                SET state = ?,
                    server_receipt_json = COALESCE(?, server_receipt_json),
                    failure_reason = COALESCE(?, failure_reason),
                    quarantine_reason = COALESCE(?, quarantine_reason),
                    updated_at = ?,
                    version = version + 1
                WHERE entry_id = ? AND version = ?
                """,
                (
                    to_state.value,
                    _serialize_receipt(server_receipt),
                    failure_reason,
                    quarantine_reason,
                    now.isoformat(),
                    entry_id,
                    row["version"],
                ),
            )
            if cur.rowcount != 1:
                raise StateConflictError(f"Spool 条目 {entry_id} 发生并发更新冲突")
        return self.get_entry(entry_id)

    def get_entry(self, entry_id: str) -> SpoolEntry:
        row = self._conn.execute(
            "SELECT * FROM spool_entries WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            raise SpoolEntryNotFoundError(f"Spool 条目不存在：{entry_id}")
        return self._hydrate_entry(row)

    def list_entries(self, states: Sequence[SpoolState] | None = None) -> list[SpoolEntry]:
        if states:
            placeholders = ",".join("?" for _ in states)
            rows = self._conn.execute(
                f"SELECT * FROM spool_entries WHERE state IN ({placeholders}) ORDER BY created_at",  # noqa: S608
                [s.value for s in states],
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM spool_entries ORDER BY created_at"
            ).fetchall()
        return [self._hydrate_entry(row) for row in rows]

    def residue_for_other_sessions(self, capture_session_id: str) -> SpoolResidueSummary:
        rows = self._conn.execute(
            """
            SELECT capture_session_id, total_bytes FROM spool_entries
            WHERE capture_session_id != ?
              AND state IN (?, ?, ?)
            """,
            (
                capture_session_id,
                SpoolState.PENDING.value,
                SpoolState.UPLOADING.value,
                SpoolState.FAILED.value,
            ),
        ).fetchall()
        session_ids = tuple(sorted({str(r["capture_session_id"]) for r in rows}))
        return SpoolResidueSummary(
            entry_count=len(rows),
            total_bytes=sum(int(r["total_bytes"]) for r in rows),
            session_ids=session_ids,
        )

    def delete_entry_record(self, entry_id: str) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM spool_entries WHERE entry_id = ?", (entry_id,))

    def save_upload_cursor(self, cursor: UploadCursor, *, now: datetime) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO upload_cursors (
                    entry_id, artifact_path, upload_session_id, session_version,
                    part_size_bytes, total_parts, expires_at, idempotency_key,
                    raw_asset_id, server_sha256, ingestion_status, deduplicated, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id, artifact_path) DO UPDATE SET
                    upload_session_id = excluded.upload_session_id,
                    session_version = excluded.session_version,
                    part_size_bytes = excluded.part_size_bytes,
                    total_parts = excluded.total_parts,
                    expires_at = excluded.expires_at,
                    idempotency_key = excluded.idempotency_key,
                    raw_asset_id = excluded.raw_asset_id,
                    server_sha256 = excluded.server_sha256,
                    ingestion_status = excluded.ingestion_status,
                    deduplicated = excluded.deduplicated,
                    updated_at = excluded.updated_at
                """,
                (
                    cursor.entry_id,
                    cursor.artifact_path,
                    cursor.upload_session_id,
                    cursor.session_version,
                    cursor.part_size_bytes,
                    cursor.total_parts,
                    cursor.expires_at.isoformat(),
                    cursor.idempotency_key,
                    cursor.raw_asset_id,
                    cursor.server_sha256,
                    cursor.ingestion_status,
                    int(cursor.deduplicated),
                    now.isoformat(),
                ),
            )

    def reset_upload_cursor(self, entry_id: str, artifact_path: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM upload_cursors WHERE entry_id = ? AND artifact_path = ?",
                (entry_id, artifact_path),
            )

    def save_upload_part(self, part: UploadPartRecord, *, now: datetime) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO upload_parts (
                    entry_id, artifact_path, part_number, offset_bytes,
                    size_bytes, sha256, etag, acked, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id, artifact_path, part_number) DO UPDATE SET
                    offset_bytes = excluded.offset_bytes,
                    size_bytes = excluded.size_bytes,
                    sha256 = excluded.sha256,
                    etag = excluded.etag,
                    acked = excluded.acked,
                    updated_at = excluded.updated_at
                """,
                (
                    part.entry_id,
                    part.artifact_path,
                    part.part_number,
                    part.offset_bytes,
                    part.size_bytes,
                    part.sha256,
                    part.etag,
                    int(part.acked),
                    now.isoformat(),
                ),
            )

    def get_upload_cursor(self, entry_id: str, artifact_path: str) -> UploadCursor | None:
        row = self._conn.execute(
            "SELECT * FROM upload_cursors WHERE entry_id = ? AND artifact_path = ?",
            (entry_id, artifact_path),
        ).fetchone()
        if row is None:
            return None
        part_rows = self._conn.execute(
            """
            SELECT * FROM upload_parts
            WHERE entry_id = ? AND artifact_path = ?
            ORDER BY part_number
            """,
            (entry_id, artifact_path),
        ).fetchall()
        parts = tuple(
            UploadPartRecord(
                entry_id=p["entry_id"],
                artifact_path=p["artifact_path"],
                part_number=int(p["part_number"]),
                offset_bytes=int(p["offset_bytes"]),
                size_bytes=int(p["size_bytes"]),
                sha256=p["sha256"],
                etag=p["etag"],
                acked=bool(p["acked"]),
            )
            for p in part_rows
        )
        return UploadCursor(
            entry_id=row["entry_id"],
            artifact_path=row["artifact_path"],
            upload_session_id=row["upload_session_id"],
            session_version=int(row["session_version"]),
            part_size_bytes=int(row["part_size_bytes"]),
            total_parts=int(row["total_parts"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            idempotency_key=row["idempotency_key"],
            raw_asset_id=row["raw_asset_id"],
            server_sha256=row["server_sha256"],
            ingestion_status=row["ingestion_status"],
            deduplicated=bool(row["deduplicated"]),
            parts=parts,
        )

    def save_preflight_evidence(self, evidence: ProceedEvidence) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO preflight_evidence (
                    evidence_id, capture_session_id, report_hash,
                    acknowledged_by, document_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    evidence.capture_session_id,
                    evidence.report_hash,
                    evidence.acknowledged_by,
                    json.dumps(evidence.to_document(), sort_keys=True, ensure_ascii=False),
                    evidence.decided_at.isoformat(),
                ),
            )

    def list_preflight_evidence(self, capture_session_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT document_json FROM preflight_evidence
            WHERE capture_session_id = ? ORDER BY created_at
            """,
            (capture_session_id,),
        ).fetchall()
        return [json.loads(row["document_json"]) for row in rows]

    def _hydrate_entry(self, row: sqlite3.Row) -> SpoolEntry:
        art_rows = self._conn.execute(
            "SELECT * FROM spool_artifacts WHERE entry_id = ? ORDER BY rel_path",
            (row["entry_id"],),
        ).fetchall()
        artifacts = tuple(
            SpoolArtifactRecord(
                rel_path=a["rel_path"],
                mime_type=a["mime_type"],
                size_bytes=int(a["size_bytes"]),
                sha256=a["sha256"],
                format=a["format"],
                duration_ms=int(a["duration_ms"]) if a["duration_ms"] is not None else None,
            )
            for a in art_rows
        )
        return SpoolEntry(
            entry_id=row["entry_id"],
            capture_session_id=row["capture_session_id"],
            collection_task_id=row["collection_task_id"],
            project_id=row["project_id"],
            state=SpoolState(row["state"]),
            manifest_hash=row["manifest_hash"],
            total_bytes=int(row["total_bytes"]),
            artifacts=artifacts,
            server_receipt=_deserialize_receipt(row["server_receipt_json"]),
            failure_reason=row["failure_reason"],
            quarantine_reason=row["quarantine_reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            version=int(row["version"]),
        )
