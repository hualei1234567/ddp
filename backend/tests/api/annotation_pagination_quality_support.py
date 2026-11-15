"""标注域分页测试的质量/IAA 播种与翻页遍历辅助。

与 ``annotation_pagination_support`` 同属测试夹具层，按数据种类拆分：
工作链（schema/task/batch/assignment/submission）在前者，质量抽检与 IAA
报告在本文件。两者都按守卫要求逐级迁移状态。
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from app.infrastructure.db.models.annotation_quality import IAAReport, QualitySample
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_pagination_support import NOW, _add_submission, url


def _walk_pages(
    client,
    project_id: object,
    path: str,
    *,
    limit: int,
    id_key: str,
    extra_params: dict[str, str] | None = None,
) -> list[str]:
    """按游标一路翻到末页，返回 ``id_key`` 列表（顺序即服务端顺序）。"""
    collected: list[str] = []
    cursor: str | None = None
    for _ in range(50):  # 上限保护：分页若是死循环，测试要失败而不是挂住
        params: dict[str, str] = {"limit": str(limit), **(extra_params or {})}
        if cursor is not None:
            params["cursor"] = cursor
        response = client.get(url(project_id, path), params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= limit
        collected.extend(_row_id(row, id_key) for row in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return collected
    raise AssertionError("分页未在 50 页内结束，疑似游标没有推进")


def _row_id(item: dict[str, object], id_key: str) -> str:
    value = item.get(id_key)
    if not isinstance(value, str):
        raise AssertionError(f"列表行缺少 {id_key}：{sorted(item)}")
    return value


def _seed_submission_for_quality(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    actor_id: UUID,
    chain: dict[str, UUID],
) -> UUID:
    """抽检必须引用真实提交（外键），这里补一条已提交提交。"""
    with factory() as session:
        submission_id = _add_submission(
            session,
            project_id=project_id,
            task_id=chain["task_id"],
            batch_id=chain["batch_id"],
            assignment_id=chain["assignment_id"],
            annotator_id=actor_id,
            schema_id=chain["schema_id"],
            status="SUBMITTED",
            submission_version=1,
        )
        session.commit()
    return submission_id


def _seed_quality_samples(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    actor_id: UUID,
    chain: dict[str, UUID],
    submission_id: UUID,
    count: int,
) -> list[UUID]:
    created: list[UUID] = []
    with factory() as session:
        for index in range(count):
            sample_id = uuid4()
            created.append(sample_id)
            session.add(
                QualitySample(
                    id=sample_id,
                    project_id=project_id,
                    task_id=chain["task_id"],
                    submission_id=submission_id,
                    annotator_id=actor_id,
                    status="PLANNED",
                    sampling_policy_version="quality/1",
                    sample_type="RANDOM",
                    sample_reason=f"例行抽检 {index}",
                    item_refs=[],
                    metadata={},
                    version=1,
                    created_at=NOW + timedelta(seconds=index),
                    updated_at=NOW + timedelta(seconds=index),
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
        session.commit()
    return created


def _seed_iaa_reports(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    actor_id: UUID,
    chain: dict[str, UUID],
    count: int,
    same_created_at: bool = False,
) -> list[UUID]:
    """先插 QUEUED 再迁移到 SUCCEEDED，满足守卫的「初始状态」约束。

    ``same_created_at=True`` 时所有报告的 ``created_at`` 相同，用于验证倒序
    游标在同时间戳并列时靠 ``id`` 兜底（单列游标会在这里漏页或重复）。
    """
    created: list[UUID] = []
    with factory() as session:
        for index in range(count):
            report_id = uuid4()
            created.append(report_id)
            session.add(
                IAAReport(
                    id=report_id,
                    project_id=project_id,
                    task_id=chain["task_id"],
                    schema_id=chain["schema_id"],
                    schema_version=1,
                    status="QUEUED",
                    metric_definitions_version="ddp.stage3.iaa/1",
                    sample_item_count=2,
                    overlap_item_count=2,
                    annotator_count=2,
                    excluded_items=[],
                    metrics=[],
                    computed_at=None,
                    version=1,
                    created_at=NOW if same_created_at else NOW + timedelta(seconds=index),
                    updated_at=NOW if same_created_at else NOW + timedelta(seconds=index),
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            session.flush()
            for status, version in (("RUNNING", 2), ("SUCCEEDED", 3)):
                session.execute(
                    update(IAAReport)
                    .where(IAAReport.id == report_id)
                    .values(
                        status=status,
                        version=version,
                        computed_at=NOW + timedelta(seconds=index),
                    )
                )
        session.commit()
    return created


def _seed_passed_quality_sample(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    actor_id: UUID,
    chain: dict[str, UUID],
    submission_id: UUID,
    seconds_offset: int = 30,
) -> UUID:
    """补一条已 ``PASSED`` 的抽检，用于验证 ``status`` 过滤与游标组合。

    守卫只允许从 PLANNED 初始状态逐级迁移，因此这里按状态机一步步 UPDATE。
    """
    passed_id = uuid4()
    stamp = NOW + timedelta(seconds=seconds_offset)
    with factory() as session:
        session.add(
            QualitySample(
                id=passed_id,
                project_id=project_id,
                task_id=chain["task_id"],
                submission_id=submission_id,
                annotator_id=actor_id,
                status="PLANNED",
                sampling_policy_version="quality/1",
                sample_type="RANDOM",
                sample_reason="已通过",
                item_refs=[],
                metadata={},
                version=1,
                created_at=stamp,
                updated_at=stamp,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        for status, version in (
            ("SAMPLED", 2),
            ("ASSIGNED", 3),
            ("IN_REVIEW", 4),
            ("PASSED", 5),
        ):
            session.execute(
                update(QualitySample)
                .where(QualitySample.id == passed_id)
                .values(status=status, version=version)
            )
        session.commit()
    return passed_id
