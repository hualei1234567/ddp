"""缺口覆盖统计测试共享构造器：Episode 状态推进与 MODALITY / ACTION 关联数据。

Episode 构造与合法状态推进是从 ``test_episode_coverage_repository`` 抽出来的，
开放轴与封闭轴共用，避免两份"如何把 Episode 推到 ACCEPTED"的实现漂移。

LABEL 轴需要的已批准标注链在 ``annotation_chain_support``（跨七张表 + 触发器
状态机，单独成模块）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.infrastructure.db.models.collection import CollectionTask
from app.infrastructure.db.models.collection_assets import ActionSpecModel
from app.infrastructure.db.models.devices import Robot, Sensor
from app.infrastructure.db.models.episodes import EpisodeModel
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64


# --------------------------------------------------------------------------- #
# Episode 构造与状态推进
# --------------------------------------------------------------------------- #
def episode_row(
    project_id: UUID,
    user_id: UUID,
    *,
    target_status: str = "ACCEPTED",
    quality_status: str = "PASS",
    scene_labels: list[str] | None = None,
    robot_id: UUID | None = None,
    device_ids: list[str] | None = None,
    sensor_ids: list[str] | None = None,
    task_id: UUID | None = None,
    annotation_status: str = "NOT_STARTED",
    updated_at: datetime = NOW,
) -> EpisodeModel:
    """构造一条 PENDING Episode；业务字段在 INSERT 时一次写定。"""
    episode = EpisodeModel(
        id=uuid4(),
        project_id=project_id,
        episode_lineage_id=uuid4(),
        episode_version=1,
        code=f"EP-{uuid4().hex[:12]}",
        task_id=task_id,
        status="PENDING",
        quality_status="NOT_CHECKED",
        annotation_status=annotation_status,
        robot_id=robot_id,
        device_ids=device_ids or [],
        sensor_ids=sensor_ids or [],
        scene_labels=scene_labels or [],
        start_time_ns=1_000,
        end_time_ns=2_000,
        duration_ns=1_000,
        time_domain="UTC",
        time_base={},
        canonical_schema_version="ddp.canonical/1.0.0-draft",
        pipeline_id="parser",
        pipeline_version="1.0.0",
        segmentation_rule_version="seg-manual-v1",
        attempt=1,
        max_attempts=3,
        created_at=NOW,
        updated_at=updated_at,
        created_by=user_id,
        updated_by=user_id,
    )
    # 目标状态与质量结论挂在实例上，仅供本模块的 advance_episode 推进使用。
    episode._coverage_target = (target_status, quality_status)  # type: ignore[attr-defined]
    return episode


def advance_episode(session: Session, episode: EpisodeModel) -> None:
    """沿合法迁移把 Episode 推到目标状态；READY 起冻结字段，故业务字段不再改。"""
    target_status, target_quality = episode._coverage_target  # type: ignore[attr-defined]
    statement = update(EpisodeModel).where(EpisodeModel.id == episode.id)
    # ``updated_at`` 有 onupdate 兜底，会覆写成 CURRENT_TIMESTAMP；测试要显式
    # 保留构造时的时间，否则"最后更新时间"断言会随真实时钟漂移。
    fixed_updated_at = episode.updated_at

    def step(**values: object) -> None:
        session.execute(statement.values(updated_at=fixed_updated_at, **values))
        session.flush()

    if target_status == "PENDING":
        return
    if target_status == "INVALIDATED":
        step(status="INVALIDATED")
        return
    step(status="PROCESSING")
    if target_status == "PROCESSING":
        return
    step(
        status="READY",
        manifest_key=f"canonical/{episode.project_id}/{episode.id}/1/manifest.json",
        manifest_sha256=SHA,
    )
    if target_status == "READY":
        return
    step(status="QUALITY_CHECKING")
    step(quality_status=target_quality)
    if target_status == "QUALITY_CHECKING":
        return
    if target_quality != "PASS":
        # 非 PASS 只能停在 QUALITY_CHECKING；再往下走会被数据库兜底拒绝。
        return
    step(status="ACCEPTED")
    if target_status == "ACCEPTED":
        return
    step(status="COMPLETED")
    if target_status == "COMPLETED":
        return
    step(status="ARCHIVED")


def insert_episodes(factory: sessionmaker[Session], rows: list[EpisodeModel]) -> None:
    with factory() as session:
        session.add_all(rows)
        session.flush()
        for row in rows:
            advance_episode(session, row)
        session.commit()


# --------------------------------------------------------------------------- #
# MODALITY / ACTION 关联数据
# --------------------------------------------------------------------------- #
def add_robot(
    session: Session,
    project_id: UUID,
    user_id: UUID,
    *,
    code: str | None = None,
) -> UUID:
    robot_id = uuid4()
    session.add(
        Robot(
            id=robot_id,
            project_id=project_id,
            code=code or f"robot-{robot_id.hex[:8]}",
            name="覆盖测试机器人",
            robot_type="TEST",
            status="ACTIVE",
            capability_profile={},
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    return robot_id


def add_sensor(
    session: Session,
    project_id: UUID,
    user_id: UUID,
    *,
    robot_id: UUID,
    sensor_type: str,
) -> UUID:
    sensor_id = uuid4()
    session.add(
        Sensor(
            id=sensor_id,
            project_id=project_id,
            code=f"sensor-{sensor_id.hex[:8]}",
            sensor_type=sensor_type,
            robot_id=robot_id,
            status="ACTIVE",
            sample_rate_hz=30.0,
            coordinate_frame="base_link",
            frame_id="base_link",
            frame_convention="ROS",
            sync_mode="NTP",
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    return sensor_id


def add_action_spec(
    session: Session,
    project_id: UUID,
    user_id: UUID,
    *,
    actions: list[dict[str, object]],
    code: str | None = None,
    spec_version: str = "v1",
) -> UUID:
    spec_id = uuid4()
    session.add(
        ActionSpecModel(
            id=spec_id,
            project_id=project_id,
            code=code or f"spec-{spec_id.hex[:8]}",
            name="测试动作库",
            spec_version=spec_version,
            actions=actions,
            # 素材表触发器要求初始状态必须是 DRAFT，再沿合法迁移启用。
            status="DRAFT",
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    session.execute(
        update(ActionSpecModel)
        .where(ActionSpecModel.id == spec_id)
        .values(status="ACTIVE", version=2)
    )
    session.flush()
    return spec_id


def add_collection_task(
    session: Session,
    project_id: UUID,
    user_id: UUID,
    *,
    action_spec_id: UUID | None = None,
) -> UUID:
    task_id = uuid4()
    session.add(
        CollectionTask(
            id=task_id,
            project_id=project_id,
            code=f"task-{task_id.hex[:8]}",
            name="覆盖测试采集任务",
            action_spec_id=action_spec_id,
            action_spec_version="v1" if action_spec_id is not None else None,
            # 采集任务触发器要求初始状态必须是 DRAFT；覆盖统计不看任务状态。
            status="DRAFT",
            priority="P1",
            target_episode_count=1,
            target_duration_sec=0,
            submitted_episode_count=0,
            accepted_episode_count=0,
            quality_summary={},
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    return task_id
