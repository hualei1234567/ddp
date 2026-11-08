"""stage2 ingestion/episode/lineage guards and indexes

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-08

安装阶段 2 状态机、不可变、只追加、跨项目与成环兜底触发器，并补充查询索引。
触发器 DDL 与 ``Base.metadata.create_all`` 使用同一份实现，避免测试库漂移。
"""

from __future__ import annotations

from alembic import op
from app.infrastructure.db.episode_guards import (
    drop_episode_state_guards,
    install_episode_state_guards,
)

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_ingestion_jobs_project_status_attempt",
        "ingestion_jobs",
        ["project_id", "status", "attempt"],
        unique=False,
    )
    op.create_index(
        "ix_episodes_project_quality_status",
        "episodes",
        ["project_id", "quality_status", "status"],
        unique=False,
    )
    op.create_index(
        "ix_lineage_node_refs_project_active",
        "lineage_node_refs",
        ["project_id", "is_active", "node_type"],
        unique=False,
    )
    op.create_index(
        "ix_data_lineage_edges_project_relation_created_at",
        "data_lineage_edges",
        ["project_id", "relation", "created_at"],
        unique=False,
    )
    install_episode_state_guards(op.get_bind())


def downgrade() -> None:
    drop_episode_state_guards(op.get_bind())
    op.drop_index(
        "ix_data_lineage_edges_project_relation_created_at",
        table_name="data_lineage_edges",
    )
    op.drop_index("ix_lineage_node_refs_project_active", table_name="lineage_node_refs")
    op.drop_index("ix_episodes_project_quality_status", table_name="episodes")
    op.drop_index("ix_ingestion_jobs_project_status_attempt", table_name="ingestion_jobs")
