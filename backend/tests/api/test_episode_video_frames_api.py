"""Episode 视频帧索引与时间同步只读 API 测试（V6.1 Sprint 5 · F3）。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.identity.models import ProjectRole
from app.infrastructure.db.models.episodes import EpisodeModel, EpisodeRawAssetModel
from app.infrastructure.db.models.identity import ProjectMember
from app.infrastructure.db.models.ingestion import RawAssetModel
from app.infrastructure.db.models.video_frame import VideoFrameModel
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client


def _seed_episode(
    session: Session,
    *,
    project_id: UUID,
    actor_id: UUID,
    code: str,
) -> EpisodeModel:
    now = datetime.now(UTC)
    episode = EpisodeModel(
        id=uuid4(),
        project_id=project_id,
        episode_lineage_id=uuid4(),
        episode_version=1,
        code=code,
        status="PENDING",
        quality_status="NOT_CHECKED",
        annotation_status="NOT_STARTED",
        start_time_ns=1_000_000_000,
        end_time_ns=2_000_000_000,
        duration_ns=1_000_000_000,
        time_domain="UTC",
        time_base={
            "clock_source": "PTP",
            "sync_method": "PTP",
            "sync_status": "VERIFIED",
            "time_domain": "UTC",
        },
        canonical_schema_version="v1.1",
        pipeline_id="video-ingest",
        pipeline_version="1.0.0",
        segmentation_rule_version="v1",
        frame_count=3,
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(episode)
    session.flush()
    return episode


def _seed_raw_asset(
    session: Session,
    *,
    project_id: UUID,
    actor_id: UUID,
    episode_id: UUID,
    filename: str = "front.mp4",
) -> RawAssetModel:
    now = datetime.now(UTC)
    asset_id = uuid4()
    asset = RawAssetModel(
        id=asset_id,
        project_id=project_id,
        code=f"asset-{asset_id.hex[:8]}",
        source_type="UPLOAD",
        format="MP4",
        original_filename=filename,
        object_key=f"projects/{project_id}/raw/{asset_id}/{filename}",
        size_bytes=4096,
        sha256="sha256:" + "a" * 64,
        content_type="video/mp4",
        status="VALIDATING",
        storage_class="STANDARD",
        uploaded_by=actor_id,
        ingested_at=now,
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )
    session.add(asset)
    session.flush()
    session.add(
        EpisodeRawAssetModel(
            project_id=project_id,
            episode_id=episode_id,
            asset_id=asset.id,
            role="INPUT",
            sha256_snapshot=asset.sha256,
            size_bytes_snapshot=asset.size_bytes,
        )
    )
    session.flush()
    return asset


def test_list_video_frames_returns_summary_and_paginated_items(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    project_id = ids.project_a
    actor_id = ids.viewer_a

    with factory() as session:
        episode = _seed_episode(session, project_id=project_id, actor_id=actor_id, code="ep-vid-1")
        asset = _seed_raw_asset(
            session,
            project_id=project_id,
            actor_id=actor_id,
            episode_id=episode.id,
        )
        session.add_all(
            [
                VideoFrameModel(
                    id=uuid4(),
                    project_id=project_id,
                    asset_id=asset.id,
                    stream_id="camera_front",
                    frame_index=0,
                    pts_seconds=Decimal("0.000000000"),
                    timestamp_raw="0",
                    time_base="1/30",
                    timestamp_normalized_seconds=Decimal("0.000000000"),
                    normalize_method="SECONDS_IDENTITY",
                    is_keyframe=True,
                    decode_status="OK",
                    parser_version="pyav-1.0",
                ),
                VideoFrameModel(
                    id=uuid4(),
                    project_id=project_id,
                    asset_id=asset.id,
                    stream_id="camera_front",
                    frame_index=1,
                    pts_seconds=None,
                    timestamp_raw="1",
                    time_base="1/30",
                    timestamp_normalized_seconds=Decimal("0.033333333"),
                    normalize_method="RAW_PARSED",
                    is_keyframe=False,
                    decode_status="OK",
                    parser_version="pyav-1.0",
                ),
                VideoFrameModel(
                    id=uuid4(),
                    project_id=project_id,
                    asset_id=asset.id,
                    stream_id="camera_wrist",
                    frame_index=2,
                    pts_seconds=None,
                    timestamp_raw=None,
                    time_base="1/30",
                    timestamp_normalized_seconds=None,
                    normalize_method="NONE",
                    is_keyframe=False,
                    decode_status="DECODE_ERROR",
                    parser_version="pyav-1.0",
                ),
            ]
        )
        session.commit()
        episode_id = episode.id
        asset_id = asset.id

    client = make_client(factory, actor_id)
    first_page = client.get(
        f"/api/v1/projects/{project_id}/episodes/{episode_id}/video-frames",
        params={"limit": 2, "offset": 0},
    )
    assert first_page.status_code == 200
    body = first_page.json()
    assert body["episode_id"] == str(episode_id)
    assert body["raw_asset_ids"] == [str(asset_id)]
    assert body["total_frames"] == 3
    assert body["decoded_count"] == 2
    assert body["failed_count"] == 1
    assert body["normalized_count"] == 2
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["items"]) == 2
    assert body["items"][0] == {
        "frame_index": 0,
        "stream_id": "camera_front",
        "pts_seconds": 0.0,
        "timestamp_raw": "0",
        "timestamp_normalized": 0.0,
        "normalize_method": "SECONDS_IDENTITY",
        "time_base": "1/30",
        "is_keyframe": True,
        "decode_status": "OK",
        "error_message": None,
    }
    assert body["items"][1]["frame_index"] == 1
    assert body["items"][1]["pts_seconds"] is None
    assert abs(body["items"][1]["timestamp_normalized"] - 0.033333333) < 1e-9

    second_page = client.get(
        f"/api/v1/projects/{project_id}/episodes/{episode_id}/video-frames",
        params={"limit": 2, "offset": 2},
    )
    assert second_page.status_code == 200
    second_body = second_page.json()
    assert len(second_body["items"]) == 1
    assert second_body["items"][0]["stream_id"] == "camera_wrist"
    assert second_body["items"][0]["decode_status"] == "DECODE_ERROR"
    assert second_body["items"][0]["error_message"] is None

    filtered = client.get(
        f"/api/v1/projects/{project_id}/episodes/{episode_id}/video-frames",
        params={"stream_id": "camera_front"},
    )
    assert filtered.status_code == 200
    filtered_body = filtered.json()
    assert filtered_body["total_frames"] == 2
    assert filtered_body["decoded_count"] == 2
    assert filtered_body["failed_count"] == 0
    assert filtered_body["normalized_count"] == 2


def test_list_video_frames_empty_when_no_raw_assets_or_no_frames(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    project_id = ids.project_a
    actor_id = ids.viewer_a

    with factory() as session:
        ep_no_asset = _seed_episode(
            session, project_id=project_id, actor_id=actor_id, code="ep-empty-1"
        )
        ep_no_frames = _seed_episode(
            session, project_id=project_id, actor_id=actor_id, code="ep-empty-2"
        )
        asset = _seed_raw_asset(
            session,
            project_id=project_id,
            actor_id=actor_id,
            episode_id=ep_no_frames.id,
        )
        session.commit()
        ep_no_asset_id = ep_no_asset.id
        ep_no_frames_id = ep_no_frames.id
        asset_id = asset.id

    client = make_client(factory, actor_id)
    resp_no_asset = client.get(
        f"/api/v1/projects/{project_id}/episodes/{ep_no_asset_id}/video-frames"
    )
    assert resp_no_asset.status_code == 200
    assert resp_no_asset.json() == {
        "episode_id": str(ep_no_asset_id),
        "raw_asset_ids": [],
        "total_frames": 0,
        "decoded_count": 0,
        "failed_count": 0,
        "normalized_count": 0,
        "limit": 50,
        "offset": 0,
        "items": [],
    }

    resp_no_frames = client.get(
        f"/api/v1/projects/{project_id}/episodes/{ep_no_frames_id}/video-frames"
    )
    assert resp_no_frames.status_code == 200
    assert resp_no_frames.json() == {
        "episode_id": str(ep_no_frames_id),
        "raw_asset_ids": [str(asset_id)],
        "total_frames": 0,
        "decoded_count": 0,
        "failed_count": 0,
        "normalized_count": 0,
        "limit": 50,
        "offset": 0,
        "items": [],
    }


def test_list_video_frames_cross_project_returns_404(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    project_a = ids.project_a
    project_b = ids.project_b

    with factory() as session:
        session.add(
            ProjectMember(
                project_id=project_b,
                user_id=ids.outsider,
                role=ProjectRole.VIEWER.value,
            )
        )
        episode = _seed_episode(
            session,
            project_id=project_a,
            actor_id=ids.viewer_a,
            code="ep-iso-1",
        )
        session.commit()
        episode_id = episode.id

    cross_client = make_client(factory, ids.outsider)
    cross_resp = cross_client.get(
        f"/api/v1/projects/{project_b}/episodes/{episode_id}/video-frames"
    )
    assert cross_resp.status_code == 404

    viewer_client = make_client(factory, ids.viewer_a)
    missing_resp = viewer_client.get(
        f"/api/v1/projects/{project_a}/episodes/{uuid4()}/video-frames"
    )
    assert missing_resp.status_code == 404
