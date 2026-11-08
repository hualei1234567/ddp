"""PostgreSQL 数据集守卫回归：迁移/create_all 安装的状态机与不可变兜底。

未配置 ``DDP_TEST_POSTGRES_URL`` / ``DDP_DATABASE_URL`` 时跳过并标注 BLOCKED。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.datasets import DatasetModel, DatasetVersionModel
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_session_factory
from sqlalchemy import create_engine, delete, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL") or os.environ.get(
    "DDP_DATABASE_URL",
    "",
)
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64

_EXPECTED_DATASET_TRIGGERS = frozenset(
    {
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
    }
)


@pytest.fixture()
def postgres_engine() -> Iterator[Engine]:
    schema = f"ddp_dataset_it_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    engine: Engine | None = None
    try:
        with admin.connect() as connection:
            connection.execute(CreateSchema(schema))
        engine = create_engine(
            _POSTGRES_URL,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        Base.metadata.create_all(engine)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


def _seed(engine: Engine) -> tuple[UUID, UUID, UUID, UUID]:
    factory = create_session_factory(engine)
    project_id, user_id, dataset_id = uuid4(), uuid4(), uuid4()
    version_id = uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_id, code=f"p-{project_id.hex[:8]}", name="项目"),
                User(id=user_id, display_name="数据工程师"),
            ]
        )
        session.flush()
        session.add(
            ProjectMember(
                project_id=project_id,
                user_id=user_id,
                role="owner",
            )
        )
        session.add(
            DatasetModel(
                id=dataset_id,
                project_id=project_id,
                dataset_lineage_id=uuid4(),
                code=f"ds-{dataset_id.hex[:8]}",
                name="数据集",
                purpose="TRAINING",
                owner_id=user_id,
                status="DRAFT",
                tags=[],
                usage_policy="TRAINING_ALLOWED",
                frozen_version_count=0,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=user_id,
                updated_by=user_id,
            )
        )
        session.flush()
        session.add(
            DatasetVersionModel(
                id=version_id,
                project_id=project_id,
                dataset_id=dataset_id,
                dataset_lineage_id=uuid4(),
                dataset_version=1,
                status="DRAFT",
                validity_status="VALID",
                selection_rule_version="selection/1",
                selection_filters={},
                selection_snapshot_at=NOW,
                dedup_policy_id=uuid4(),
                dedup_policy_version=1,
                dedup_policy_sha256=SHA_A,
                split_policy_id=uuid4(),
                split_policy_version=1,
                split_policy_sha256=SHA_A,
                leakage_policy_id=uuid4(),
                leakage_policy_version=1,
                leakage_policy_sha256=SHA_A,
                quality_policy_id=uuid4(),
                quality_policy_version=1,
                quality_policy_sha256=SHA_A,
                candidate_count=0,
                selected_count=0,
                excluded_count=0,
                duplicated_count=0,
                item_count=0,
                train_count=0,
                validation_count=0,
                test_count=0,
                annotation_schema_versions=[],
                blocking_issues=[],
                warnings=[],
                exclusion_summary={},
                build_attempt=0,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=user_id,
                updated_by=user_id,
            )
        )
        session.commit()
    return project_id, user_id, dataset_id, version_id


def test_postgres_dataset_triggers_and_immutability(postgres_engine: Engine) -> None:
    """数据集状态机、身份不可变、冻结不可变与禁删在 PG 上真实生效。"""
    factory = create_session_factory(postgres_engine)
    _project_id, user_id, dataset_id, version_id = _seed(postgres_engine)

    with postgres_engine.connect() as connection:
        names = {
            str(row[0])
            for row in connection.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            )
        }
    missing = _EXPECTED_DATASET_TRIGGERS - names
    assert not missing, f"缺少触发器：{sorted(missing)}"

    with factory() as session:
        session.execute(
            update(DatasetModel).where(DatasetModel.id == dataset_id).values(status="ACTIVE")
        )
        session.flush()
        with pytest.raises(IntegrityError, match="dataset_identity_immutable"):
            session.execute(
                update(DatasetModel).where(DatasetModel.id == dataset_id).values(code="changed")
            )
            session.flush()
        session.rollback()

        with pytest.raises(
            IntegrityError,
            match="dataset_version_state_transition_invalid",
        ):
            session.execute(
                update(DatasetVersionModel)
                .where(DatasetVersionModel.id == version_id)
                .values(status="FROZEN")
            )
            session.flush()
        session.rollback()

        # 测试夹具：临时关闭用户触发器构造 FROZEN 行，再验证冻结不可变。
        session.execute(text("ALTER TABLE dataset_versions DISABLE TRIGGER USER"))
        session.execute(
            update(DatasetVersionModel)
            .where(DatasetVersionModel.id == version_id)
            .values(
                status="FROZEN",
                manifest_sha256=SHA_A,
                quality_report_sha256=SHA_A,
                item_count=2,
                train_count=1,
                validation_count=1,
                test_count=0,
                leakage_status="PASS",
                leakage_report_sha256=SHA_A,
                freeze_approval_id=uuid4(),
                frozen_by=user_id,
                frozen_at=NOW,
            )
        )
        session.execute(text("ALTER TABLE dataset_versions ENABLE TRIGGER USER"))
        session.commit()

        with pytest.raises(IntegrityError, match="dataset_version_immutable"):
            session.execute(
                update(DatasetVersionModel)
                .where(DatasetVersionModel.id == version_id)
                .values(manifest_sha256=SHA_B)
            )
            session.flush()
        session.rollback()

        with pytest.raises(IntegrityError, match="datasets_delete_forbidden"):
            session.execute(delete(DatasetModel))
            session.flush()
        session.rollback()
