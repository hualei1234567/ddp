"""数据集质量结论门禁 API 测试：构造过滤、导出阻断与默认行为不变。

质量运行以真实 ORM 行写入（只追加事实），门禁读取复用生产仓储，
不复制规则；跨项目运行不能被当前项目读到。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.domain.quality.enums import QualityCategory, QualityRunStatus
from app.infrastructure.db.models.quality import QualityRunModel
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import (
    create_dataset,
    create_policies,
    seed_dataset_episodes,
    url,
    version_payload,
)
from tests.api.export_support import (
    FakeExportStorage,
    export_payload,
    run_export_worker,
)

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
_RULE_SET_VERSION = "sha256:" + "a" * 64


def _category_summary() -> dict[str, dict[str, object]]:
    return {
        category.value: {
            "category": category.value,
            "status": "NOT_APPLICABLE",
            "finding_count": 0,
            "pass_count": 0,
            "fail_count": 0,
            "not_applicable_count": 0,
            "error_count": 0,
            "score": None,
        }
        for category in QualityCategory
    }


def _insert_run(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    actor_id: UUID,
    episode_id: UUID,
    status: QualityRunStatus,
    score: float | None,
    created_at: datetime = NOW,
) -> UUID:
    run_id = uuid4()
    with factory() as session:
        session.add(
            QualityRunModel(
                id=run_id,
                project_id=project_id,
                subject_type="EPISODE",
                subject_id=episode_id,
                rule_set_version=_RULE_SET_VERSION,
                status=status.value,
                score=score,
                finding_count=0,
                error_count=0,
                warn_count=0,
                category_summary=_category_summary(),
                started_at=created_at,
                finished_at=created_at,
                created_at=created_at,
                created_by=actor_id,
            )
        )
        session.commit()
    return run_id


def _preview_body(policies: dict[str, object], **filters: object) -> dict[str, object]:
    return {
        "selection_filters": filters,
        "dedup_policy_id": policies["dedup"]["dedup_policy_id"],
        "dedup_policy_version": policies["dedup"]["version"],
        "split_policy_id": policies["split"]["split_policy_id"],
        "split_policy_version": policies["split"]["version"],
        "leakage_policy_id": policies["leakage"]["leakage_policy_id"],
        "leakage_policy_version": policies["leakage"]["version"],
        "quality_policy_id": policies["quality"]["quality_policy_id"],
        "quality_policy_version": policies["quality"]["version"],
        "sample_limit": 20,
    }


def _setup_version(
    factory: sessionmaker[Session],
    ids: ApiIds,
    *,
    count: int,
    **filters: object,
) -> dict[str, object]:
    episodes = seed_dataset_episodes(
        factory, ids.project_a, ids.editor_a, count=count, prefix=f"ep-{uuid4().hex[:8]}"
    )
    editor = make_client(factory, ids.editor_a)
    dataset = create_dataset(editor, ids.project_a, ids.owner_a, f"qg-{uuid4().hex[:8]}")
    policies = create_policies(editor, ids.project_a, dataset["dataset_id"])
    created = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(policies, **filters),
    )
    assert created.status_code == 201, created.text
    return {
        "episodes": episodes,
        "editor": editor,
        "dataset": dataset,
        "policies": policies,
        "version": created.json(),
    }


def test_preview_filters_by_latest_run_threshold_and_fails_closed(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup_version(factory, ids, count=4, min_quality_score=0.8)
    passing, below, incomplete, missing = ctx["episodes"]

    _insert_run(
        factory,
        project_id=ids.project_a,
        actor_id=ids.editor_a,
        episode_id=passing,
        status=QualityRunStatus.PASSED,
        score=0.8,  # 恰好等于阈值 → 通过
        created_at=NOW - timedelta(minutes=10),
    )
    _insert_run(
        factory,
        project_id=ids.project_a,
        actor_id=ids.editor_a,
        episode_id=below,
        status=QualityRunStatus.PASSED,
        score=0.7999,
        created_at=NOW - timedelta(minutes=5),
    )
    _insert_run(
        factory,
        project_id=ids.project_a,
        actor_id=ids.editor_a,
        episode_id=incomplete,
        status=QualityRunStatus.INCOMPLETE,
        score=None,
        created_at=NOW,
    )
    # 跨项目同 Episode ID 的运行不得被当前项目读到。
    _insert_run(
        factory,
        project_id=ids.project_b,
        actor_id=ids.owner_a,
        episode_id=missing,
        status=QualityRunStatus.PASSED,
        score=1.0,
        created_at=NOW,
    )

    editor = ctx["editor"]
    dataset_id = ctx["dataset"]["dataset_id"]
    version_id = ctx["version"]["dataset_version_id"]
    policies = ctx["policies"]

    gated = editor.post(
        url(ids.project_a, f"/datasets/{dataset_id}/versions/{version_id}/selection-preview"),
        json=_preview_body(policies, min_quality_score=0.8),
    )
    assert gated.status_code == 200, gated.text
    body = gated.json()
    assert body["candidate_count"] == 4
    assert body["selected_count"] == 1
    codes = {item["code"]: item for item in body["exclusion_summary"]}
    assert codes["QUALITY_SCORE_BELOW_THRESHOLD"]["count"] == 1
    assert codes["QUALITY_RUN_NOT_PASSED"]["count"] == 1
    assert codes["QUALITY_RUN_MISSING"]["count"] == 1

    # 未在过滤器里要求门槛时，预览行为与旧版一致（4 条全入选）。
    ungated = editor.post(
        url(ids.project_a, f"/datasets/{dataset_id}/versions/{version_id}/selection-preview"),
        json=_preview_body(policies),
    )
    assert ungated.status_code == 200, ungated.text
    assert ungated.json()["selected_count"] == 4


def test_build_blocks_when_explicit_candidates_fail_quality_gate(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    episodes = seed_dataset_episodes(
        factory, ids.project_a, ids.editor_a, count=3, prefix=f"ep-{uuid4().hex[:8]}"
    )
    for episode_id in episodes:
        _insert_run(
            factory,
            project_id=ids.project_a,
            actor_id=ids.editor_a,
            episode_id=episode_id,
            status=QualityRunStatus.INCOMPLETE,
            score=None,
        )
    editor = make_client(factory, ids.editor_a)
    dataset = create_dataset(editor, ids.project_a, ids.owner_a, f"qg-{uuid4().hex[:8]}")
    policies = create_policies(editor, ids.project_a, dataset["dataset_id"])
    created = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(
            policies,
            episode_ids=[str(item) for item in episodes],
            min_quality_score=0.8,
        ),
    )
    assert created.status_code == 201, created.text

    built = editor.post(
        url(
            ids.project_a,
            f"/datasets/{dataset['dataset_id']}/versions/"
            f"{created.json()['dataset_version_id']}/actions/build",
        ),
        headers={"If-Match": f'"{created.json()["version"]}"'},
        json={},
    )
    assert built.status_code == 422, built.text
    error = built.json()["error"]
    assert error["code"] == "dataset_item_ineligible"
    assert set(error["details"]["episode_ids"]) == {str(item) for item in episodes}


def test_export_gate_uses_latest_run_and_worker_rechecks(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup_version(factory, ids, count=10, min_quality_score=0.8)
    episodes = ctx["episodes"]
    for episode_id in episodes:
        _insert_run(
            factory,
            project_id=ids.project_a,
            actor_id=ids.editor_a,
            episode_id=episode_id,
            status=QualityRunStatus.PASSED,
            score=0.9,
            created_at=NOW - timedelta(hours=2),
        )

    storage = FakeExportStorage()
    editor = make_client(factory, ids.editor_a, object_storage=storage)
    owner = make_client(factory, ids.owner_a, object_storage=storage)
    dataset_id = ctx["dataset"]["dataset_id"]
    version_id = ctx["version"]["dataset_version_id"]
    version_path = url(ids.project_a, f"/datasets/{dataset_id}/versions/{version_id}")
    built = editor.post(
        f"{version_path}/actions/build",
        headers={"If-Match": f'"{ctx["version"]["version"]}"'},
        json={},
    )
    assert built.status_code == 200, built.text
    validated = editor.post(
        f"{version_path}/actions/validate",
        headers={"If-Match": f'"{built.json()["version"]}"'},
        json={},
    )
    assert validated.status_code == 200, validated.text
    frozen = owner.post(
        f"{version_path}/actions/freeze",
        headers={"If-Match": f'"{validated.json()["version"]}"'},
        json={"reason": "QA 独立核验通过", "payload": {"qa_verified": True}},
    )
    assert frozen.status_code == 200, frozen.text
    queued = editor.post(
        url(ids.project_a, "/exports"),
        json=export_payload(version_id),
        headers={"Idempotency-Key": "quality-gate-export-1"},
    )
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["export_job_id"]

    # 更新的一次运行把其中一个 Episode 变成 INCOMPLETE：最新口径必须阻断。
    _insert_run(
        factory,
        project_id=ids.project_a,
        actor_id=ids.editor_a,
        episode_id=episodes[0],
        status=QualityRunStatus.INCOMPLETE,
        score=None,
        created_at=NOW,
    )

    blocked = editor.post(
        url(ids.project_a, "/exports"),
        json=export_payload(version_id),
        headers={"Idempotency-Key": "quality-gate-export-2"},
    )
    assert blocked.status_code == 409, blocked.text
    error = blocked.json()["error"]
    assert error["code"] == "dataset_quality_gate_failed"
    assert error["details"]["min_quality_score"] == 0.8
    assert error["details"]["blocked_episode_count"] == 1
    assert error["details"]["episodes"][0]["episode_id"] == str(episodes[0])
    assert error["details"]["episodes"][0]["code"] == "QUALITY_RUN_NOT_PASSED"

    # Worker 执行前复核：已排队的任务也必须失败，不能绕过门禁产出。
    result = run_export_worker(factory, storage)
    assert result.job_id == UUID(job_id)
    with factory() as session:
        from app.infrastructure.db.models.export_jobs import ExportJobModel

        row = session.get(ExportJobModel, UUID(job_id))
        assert row is not None
        assert row.status == "FAILED"
        assert row.error_code == "dataset_quality_gate_failed"
