"""DDP-310 API 测试共享构造器（真实 SQLite/PostgreSQL 仓储，非 Mock 业务）。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.infrastructure.db.models.ingestion import RawAssetModel
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

_NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def seed_valid_asset(
    factory: sessionmaker[Session],
    project_id: UUID,
    actor_id: UUID,
    *,
    label: str | None = None,
    size_bytes: int = 1024,
    status: str = "VALID",
) -> UUID:
    asset_id = uuid4()
    label = label or asset_id.hex[:8]
    digest = hashlib.sha256(f"{project_id}:{asset_id}:{label}".encode()).hexdigest()
    with factory() as session:
        session.add(
            RawAssetModel(
                id=asset_id,
                project_id=project_id,
                code=f"RAW-{label}-{asset_id.hex[:12]}",
                source_type="UPLOAD",
                original_filename=f"robot-{label}.mcap",
                object_key=f"raw/{project_id}/2026/11/08/{asset_id}/content.mcap",
                size_bytes=size_bytes,
                content_type="application/x-mcap",
                format="MCAP",
                sha256=f"sha256:{digest}",
                status="VALIDATING" if status == "VALID" else status,
                ingested_at=_NOW,
                storage_class="STANDARD",
                quarantine_codes=[],
                uploaded_by=actor_id,
                version=1,
                created_at=_NOW,
                updated_at=_NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        if status == "VALID":
            session.execute(
                update(RawAssetModel)
                .where(RawAssetModel.id == asset_id)
                .values(status="VALID")
                .execution_options(synchronize_session=False)
            )
        session.commit()
    return asset_id


def time_base() -> dict[str, object]:
    return {
        "time_domain": "UTC",
        "clock_source": "NTP",
        "sync_method": "NTP",
        "sync_status": "VERIFIED",
    }


def calibration_snapshot() -> list[dict[str, object]]:
    return [
        {
            "calibration_id": str(uuid4()),
            "calibration_version": 1,
            "checksum": f"sha256:{'a' * 64}",
            "target_type": "ROBOT",
            "target_id": str(uuid4()),
            "schema_version": "ddp.calibration/1.0.0",
        }
    ]


def episode_payload(
    asset_id: UUID,
    *,
    code: str,
    start_time_ns: int = 1_000,
    end_time_ns: int = 2_000,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
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


def manifest_for_episode(episode: dict[str, object]) -> dict[str, object]:
    """按 API 返回的 Episode 组装合法 Manifest。"""
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
        "time_base": dict(episode["time_base"]),  # type: ignore[arg-type]
        "time_alignment": {"method": "NTP"},
        "calibration_snapshot": [
            dict(item)
            for item in episode["calibration_snapshot"]  # type: ignore[union-attr]
        ],
        "streams": [],
        "raw_asset_ids": [],
        "provenance": {"pipeline_id": episode["pipeline_id"]},
        "quality": {"status": "PASS", "rule_version": "q1", "issues": []},
    }


def job_payload(
    asset_id: UUID,
    *,
    job_type: str = "PARSE_METADATA",
    timeout_sec: int = 60,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_type": job_type,
        "timeout_sec": timeout_sec,
        "raw_asset_ids": [str(asset_id)],
        "max_attempts": 3,
        "pipeline_id": "parser",
        "pipeline_version": "1.0.0",
    }
    payload.update(overrides)
    return payload


def project_url(project_id: UUID, suffix: str) -> str:
    return f"/api/v1/projects/{project_id}{suffix}"
