"""SQLAlchemy 声明式基类与约束命名规范。"""

from __future__ import annotations

from sqlalchemy import MetaData, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase

from app.infrastructure.db.annotation_catalog_guards import install_catalog_guards
from app.infrastructure.db.annotation_quality_guards import install_quality_guards
from app.infrastructure.db.annotation_result_guards import install_result_guards
from app.infrastructure.db.annotation_work_guards import install_work_guards
from app.infrastructure.db.collection_guards import (
    install_audit_guards,
    install_collection_state_guards,
)
from app.infrastructure.db.dataset_guards import install_dataset_guards
from app.infrastructure.db.episode_guards import install_episode_state_guards
from app.infrastructure.db.export_guards import install_export_guards
from app.infrastructure.db.guards import (
    install_calibration_record_guards,
    install_calibration_target_guards,
)
from app.infrastructure.db.ingestion_guards import install_ingestion_state_guards
from app.infrastructure.db.training_guards import install_training_guards

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@event.listens_for(Base.metadata, "after_create")
def _install_ddp_guards(
    target: MetaData,
    connection: Connection,
    **_kwargs: object,
) -> None:
    """让测试/工具用 ``create_all`` 建库时与 Alembic 迁移后的约束一致。"""
    install_calibration_target_guards(connection)
    install_calibration_record_guards(connection)
    install_audit_guards(connection)
    install_collection_state_guards(connection)
    install_ingestion_state_guards(connection)
    install_episode_state_guards(connection)
    install_catalog_guards(connection)
    install_work_guards(connection)
    install_result_guards(connection)
    install_quality_guards(connection)
    install_dataset_guards(connection)
    install_export_guards(connection)
    install_training_guards(connection)
