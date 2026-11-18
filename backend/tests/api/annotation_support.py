"""标注 HTTP 测试共享夹具：Episode 种子与完整任务链路构造。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.identity.models import ProjectRole
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.db.models.identity import ProjectMember, User
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64


def url(project_id: object, suffix: str) -> str:
    return f"/api/v1/projects/{project_id}{suffix}"


def seed_episode(
    factory: sessionmaker[Session],
    project_id: UUID,
    actor_id: UUID,
    code: str,
) -> UUID:
    episode_id = uuid4()
    with factory() as session:
        session.add(
            EpisodeModel(
                id=episode_id,
                project_id=project_id,
                episode_lineage_id=uuid4(),
                episode_version=1,
                code=code,
                status="PENDING",
                quality_status="NOT_CHECKED",
                annotation_status="NOT_STARTED",
                scene_labels=["kitchen"],
                start_time_ns=1_000,
                end_time_ns=2_000,
                duration_ns=1_000,
                time_domain="UTC",
                # 必须与 ``validate_time_base`` 的契约一致：空对象虽然能直接
                # 落库，但任何经 Episode 领域读取的接口（例如血缘查询）都会
                # 报 ``episode_time_base_invalid``，让测试夹具本身失真。
                time_base={
                    "time_domain": "UTC",
                    "clock_source": "HOST_CLOCK",
                    "sync_method": "PTP",
                    "sync_status": "VERIFIED",
                },
                canonical_schema_version="ddp.canonical/1.0.0-draft",
                pipeline_id="parser",
                pipeline_version="1.0.0",
                segmentation_rule_version="seg-v1",
                calibration_snapshot=[],
                manifest_key=f"canonical/{project_id}/{episode_id}/1/manifest.json",
                manifest_sha256=SHA,
                frame_count=10,
                attempt=1,
                max_attempts=3,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        for status, quality in (
            ("PROCESSING", "NOT_CHECKED"),
            ("READY", "NOT_CHECKED"),
            ("QUALITY_CHECKING", "PASS"),
            ("ACCEPTED", "PASS"),
        ):
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode_id)
                .values(status=status, quality_status=quality)
            )
        session.commit()
    return episode_id


def seed_arbiter(factory: sessionmaker[Session], project_id: UUID) -> UUID:
    user_id = uuid4()
    with factory() as session:
        session.add(User(id=user_id, display_name="仲裁员"))
        session.flush()
        session.add(
            ProjectMember(
                project_id=project_id,
                user_id=user_id,
                role=ProjectRole.EDITOR.value,
            )
        )
        session.commit()
    return user_id


def schema_payload(code: str) -> dict[str, object]:
    return {
        "code": code,
        "name": f"标签 {code}",
        "annotation_levels": ["EPISODE"],
        "labels": [
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
        ],
    }


def create_published_schema(
    editor: TestClient,
    owner: TestClient,
    project_id: object,
    code: str,
) -> dict[str, object]:
    created = editor.post(url(project_id, "/annotation-schemas"), json=schema_payload(code))
    assert created.status_code == 201, created.text
    schema = created.json()
    submitted = editor.post(
        url(project_id, f"/annotation-schemas/{schema['schema_id']}/actions/submit-review"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    published = owner.post(
        url(project_id, f"/annotation-schemas/{schema['schema_id']}/actions/publish"),
        headers={"If-Match": '"2"'},
        json={},
    )
    assert published.status_code == 200, published.text
    return published.json()


def create_snapshot_set(
    editor: TestClient,
    project_id: object,
    code: str,
) -> dict[str, object]:
    created = editor.post(
        url(project_id, "/annotation-sample-sets"),
        json={"code": code, "filters": {"scene_labels": ["kitchen"]}},
    )
    assert created.status_code == 201, created.text
    sample_set = created.json()
    previewed = editor.post(
        url(
            project_id,
            f"/annotation-sample-sets/{sample_set['sample_set_id']}/actions/preview",
        ),
        headers={"If-Match": '"1"'},
        json={"filters": {"scene_labels": ["kitchen"]}},
    )
    assert previewed.status_code == 200, previewed.text
    snapshot = editor.post(
        url(
            project_id,
            f"/annotation-sample-sets/{sample_set['sample_set_id']}/actions/snapshot",
        ),
        headers={"If-Match": '"2"'},
        json={},
    )
    assert snapshot.status_code == 200, snapshot.text
    return snapshot.json()


def create_ready_work(
    editor: TestClient,
    owner: TestClient,
    project_id: object,
    schema: dict[str, object],
    sample_set: dict[str, object],
    episode_id: UUID,
    actor_id: UUID,
    code: str,
    quality_policy: dict[str, object] | None = None,
    iaa_policy: dict[str, object] | None = None,
    *,
    creator: TestClient | None = None,
    episode_ids: list[UUID] | None = None,
    planned_item_count: int | None = None,
    target_item_count: int | None = None,
    claim_only: bool = False,
) -> dict[str, object]:
    """创建一个可直接标注的任务（Schema 已发布、样本集已快照）。

    ``episode_ids`` / ``planned_item_count`` 只为多 Episode 场景补充：
    端到端链路一次提交多个 Episode 的标注；默认仍是最小的一条，既有调用不变。

    ``claim_only=True`` 停在"已领取（CLAIMED）"这一步：租约过期巡检需要
    CLAIMED/ASSIGNED 的分配，继续 start 会把它推进到 IN_PROGRESS，那已经
    不是"领了活没干"的状态。
    """
    target_ids = list(episode_ids) if episode_ids is not None else [episode_id]
    item_count = planned_item_count if planned_item_count is not None else len(target_ids)
    total_item_count = target_item_count if target_item_count is not None else item_count
    payload: dict[str, object] = {
        "code": code,
        "name": f"任务 {code}",
        "sample_set_id": sample_set["sample_set_id"],
        "schema_id": schema["schema_id"],
        "target_item_count": total_item_count,
        "target_episode_count": len(target_ids),
        "priority": "P1",
    }
    if quality_policy is not None:
        payload["quality_policy"] = quality_policy
    if iaa_policy is not None:
        payload["iaa_policy"] = iaa_policy
    # 任务创建人决定能否开启高风险策略（如双标自动通过）；其余步骤仍由 operator 执行。
    created = (creator or editor).post(url(project_id, "/annotation-tasks"), json=payload)
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]
    submitted = editor.post(
        url(project_id, f"/annotation-tasks/{task_id}/actions/submit-review"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    approved = owner.post(
        url(project_id, f"/annotation-tasks/{task_id}/actions/approve"),
        headers={"If-Match": '"2"'},
        json={},
    )
    assert approved.status_code == 200, approved.text
    ready = editor.post(
        url(project_id, f"/annotation-tasks/{task_id}/actions/ready"),
        headers={"If-Match": '"3"'},
        json={},
    )
    assert ready.status_code == 200, ready.text

    batch_created = editor.post(
        url(project_id, f"/annotation-tasks/{task_id}/batches"),
        json={
            "planned_item_count": item_count,
            "item_refs": [str(item) for item in target_ids],
        },
    )
    assert batch_created.status_code == 201, batch_created.text
    batch = batch_created.json()
    batch_ready = editor.post(
        url(project_id, f"/annotation-batches/{batch['batch_id']}/actions/ready"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert batch_ready.status_code == 200, batch_ready.text

    assignment_created = editor.post(
        url(project_id, f"/annotation-batches/{batch['batch_id']}/annotation-assignments"),
        json={
            "assignee_id": str(actor_id),
            "planned_item_count": item_count,
            "planned_minutes": 30,
        },
    )
    assert assignment_created.status_code == 201, assignment_created.text
    assignment = assignment_created.json()

    task_assigned = editor.post(
        url(project_id, f"/annotation-tasks/{task_id}/actions/assign"),
        headers={"If-Match": '"4"'},
        json={},
    )
    assert task_assigned.status_code == 200, task_assigned.text
    batch_assigned = editor.post(
        url(project_id, f"/annotation-batches/{batch['batch_id']}/actions/assign"),
        headers={"If-Match": '"2"'},
        json={},
    )
    assert batch_assigned.status_code == 200, batch_assigned.text
    claimed = editor.post(
        url(project_id, f"/annotation-assignments/{assignment['assignment_id']}/actions/claim"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert claimed.status_code == 200, claimed.text
    if claim_only:
        return {
            "task": created.json(),
            "batch": batch,
            "assignment": claimed.json(),
        }
    task_started = editor.post(
        url(project_id, f"/annotation-tasks/{task_id}/actions/start"),
        headers={"If-Match": '"5"'},
        json={},
    )
    assert task_started.status_code == 200, task_started.text
    batch_started = editor.post(
        url(project_id, f"/annotation-batches/{batch['batch_id']}/actions/start"),
        headers={"If-Match": '"3"'},
        json={},
    )
    assert batch_started.status_code == 200, batch_started.text
    assignment_started = editor.post(
        url(
            project_id,
            f"/annotation-assignments/{assignment['assignment_id']}/actions/start",
        ),
        headers={"If-Match": '"2"'},
        json={},
    )
    assert assignment_started.status_code == 200, assignment_started.text
    return {
        "task": task_started.json(),
        "batch": batch_started.json(),
        "assignment": assignment_started.json(),
    }


def record_payload(
    episode_id: UUID,
    *,
    action: str = "PICK",
    segment: tuple[int, int] = (1_100, 1_500),
) -> dict[str, object]:
    return {
        "episode_id": str(episode_id),
        "episode_version": 1,
        "labels": [
            {
                "label_key": "action",
                "value": action,
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            },
            {
                "label_key": "segment",
                "value": {
                    "start_time_ns": segment[0],
                    "end_time_ns": segment[1],
                    "time_domain": "UTC",
                },
                "source": "HUMAN",
                "label_status": "CONFIRMED",
            },
        ],
    }


def create_submitted(
    editor: TestClient,
    project_id: object,
    assignment_id: object,
    episode_id: UUID,
    *,
    episode_ids: list[UUID] | None = None,
    action: str = "PICK",
    segment: tuple[int, int] = (1_100, 1_500),
) -> dict[str, object]:
    target_ids = list(episode_ids) if episode_ids is not None else [episode_id]
    created = editor.post(
        url(project_id, "/annotation-submissions"),
        json={
            "assignment_id": str(assignment_id),
            "records": [
                record_payload(item, action=action, segment=segment) for item in target_ids
            ],
            "data_source": "REAL",
        },
    )
    assert created.status_code == 201, created.text
    submission = created.json()
    submitted = editor.post(
        url(project_id, f"/annotation-submissions/{submission['submission_id']}/actions/submit"),
        headers={"If-Match": '"1"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    return submitted.json()
