"""Spool SQLite 表结构定义（WAL 模式，本地轻量状态库）。"""

from __future__ import annotations

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS spool_entries (
    entry_id TEXT PRIMARY KEY,
    capture_session_id TEXT NOT NULL,
    collection_task_id TEXT,
    project_id TEXT NOT NULL,
    state TEXT NOT NULL,
    manifest_hash TEXT,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    server_receipt_json TEXT,
    failure_reason TEXT,
    quarantine_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS spool_artifacts (
    entry_id TEXT NOT NULL REFERENCES spool_entries(entry_id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    format TEXT NOT NULL,
    duration_ms INTEGER,
    PRIMARY KEY (entry_id, rel_path)
);

CREATE TABLE IF NOT EXISTS upload_cursors (
    entry_id TEXT NOT NULL REFERENCES spool_entries(entry_id) ON DELETE CASCADE,
    artifact_path TEXT NOT NULL,
    upload_session_id TEXT NOT NULL,
    session_version INTEGER NOT NULL,
    part_size_bytes INTEGER NOT NULL,
    total_parts INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    raw_asset_id TEXT,
    server_sha256 TEXT,
    ingestion_status TEXT,
    deduplicated INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (entry_id, artifact_path)
);

CREATE TABLE IF NOT EXISTS upload_parts (
    entry_id TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    part_number INTEGER NOT NULL,
    offset_bytes INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    etag TEXT,
    acked INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (entry_id, artifact_path, part_number),
    FOREIGN KEY (entry_id, artifact_path)
        REFERENCES upload_cursors(entry_id, artifact_path) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS preflight_evidence (
    evidence_id TEXT PRIMARY KEY,
    capture_session_id TEXT NOT NULL,
    report_hash TEXT NOT NULL,
    acknowledged_by TEXT,
    document_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_spool_entries_state ON spool_entries(state);
CREATE INDEX IF NOT EXISTS idx_spool_entries_session ON spool_entries(capture_session_id);
"""
