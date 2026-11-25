"""迁移 0080 测试：annotator_skill_tags 表 + annotation_tasks.required_skills。

**为什么需要这个测试**（对应 MEM-INC-013 的既有约定）

迁移测试跑在 SQLite 上（``tests/migrations/`` 的既有约定，无需起容器），
验证三件事：

1. upgrade → downgrade → upgrade 往返在 SQLite 上可跑通——
   ``annotation_tasks`` 带守卫触发器（0013/0016），删列必须走
   ``batch_alter_table``； downgrade 直接 ``ALTER TABLE DROP COLUMN``
   会在重建期触发器解析时报错（0041 实测）。
2. 列存在时写 NULL（不路由）与写 JSON 数组（路由）都合法。
3. 新表唯一约束与外键兜底真实生效：同一成员同一标签重复登记必须被拒；
   非项目成员不得持有技能标签。

PG 侧（生产/集成）约束语义由迁移内显式 ``public.`` 限定保证，
真实 PG 的 upgrade→downgrade→upgrade 往返在 staging 验证（本仓库既有惯例）。
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    return config


def _tables(db_path: Path) -> set[str]:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        return set(__import__("sqlalchemy").inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _columns(db_path: Path, table: str) -> set[str]:
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        return {col["name"] for col in __import__("sqlalchemy").inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def test_skill_tags_migration_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "skill_tags.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    assert "annotator_skill_tags" in _tables(db_path)
    assert "required_skills" in _columns(db_path, "annotation_tasks")

    command.downgrade(config, "0074")
    assert "annotator_skill_tags" not in _tables(db_path)
    assert "required_skills" not in _columns(db_path, "annotation_tasks")

    command.upgrade(config, "head")
    assert "annotator_skill_tags" in _tables(db_path)
    assert "required_skills" in _columns(db_path, "annotation_tasks")


def _seed_project_member(engine, *, with_member: bool = True) -> tuple[str, str]:
    project_id, user_id = uuid4().hex, uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO projects (id, code, name) VALUES (:id, :code, '项目')"),
            {"id": project_id, "code": f"p-{project_id[:8]}"},
        )
        connection.execute(
            text("INSERT INTO users (id, display_name) VALUES (:id, '用户')"),
            {"id": user_id},
        )
        if with_member:
            connection.execute(
                text(
                    "INSERT INTO project_members (id, project_id, user_id, role) "
                    "VALUES (:id, :project_id, :user_id, 'editor')"
                ),
                {"id": uuid4().hex, "project_id": project_id, "user_id": user_id},
            )
    return project_id, user_id


def _insert_skill_tag(engine, project_id: str, user_id: str, tag: str, actor_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO annotator_skill_tags ("
                "id, project_id, annotator_id, tag_name, category, created_by"
                ") VALUES (:id, :project_id, :annotator_id, :tag, NULL, :actor)"
            ),
            {
                "id": uuid4().hex,
                "project_id": project_id,
                "annotator_id": user_id,
                "tag": tag,
                "actor": actor_id,
            },
        )


def _fk_engine(db_path: Path):
    """带 ``PRAGMA foreign_keys=ON`` 的引擎（与应用同源，见 session.py）。

    SQLite 默认不校验外键；证明复合外键"非成员不得持标签"必须开 PRAGMA。
    """
    from sqlalchemy import event

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):  # pragma: no cover - 标准写法
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    return engine


def test_skill_tag_unique_and_member_fk(tmp_path: Path) -> None:
    db_path = tmp_path / "skill_tags_constraints.db"
    command.upgrade(_config(db_path), "head")
    engine = _fk_engine(db_path)
    try:
        project_id, user_id = _seed_project_member(engine)
        _insert_skill_tag(engine, project_id, user_id, "kitchen", user_id)

        # 同一成员同一标签重复登记 → 唯一约束拦截
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            _insert_skill_tag(engine, project_id, user_id, "kitchen", user_id)

        # 同一标签换个人可以登记（先补成员）
        other_user = uuid4().hex
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO users (id, display_name) VALUES (:id, '乙')"),
                {"id": other_user},
            )
            connection.execute(
                text(
                    "INSERT INTO project_members (id, project_id, user_id, role) "
                    "VALUES (:id, :project_id, :user_id, 'editor')"
                ),
                {"id": uuid4().hex, "project_id": project_id, "user_id": other_user},
            )
        _insert_skill_tag(engine, project_id, other_user, "kitchen", other_user)

        # 非项目成员不得持有技能标签（复合外键拦截）
        outsider = uuid4().hex
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO users (id, display_name) VALUES (:id, '外人')"),
                {"id": outsider},
            )
        from sqlalchemy.exc import IntegrityError as IntegrityError2

        with pytest.raises(IntegrityError2):
            _insert_skill_tag(engine, project_id, outsider, "driving", outsider)
    finally:
        engine.dispose()


def test_required_skills_column_accepts_null_and_json(tmp_path: Path) -> None:
    db_path = tmp_path / "required_skills.db"
    command.upgrade(_config(db_path), "head")
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        project_id, user_id = _seed_project_member(engine)
        schema_id, sample_set_id = uuid4().hex, uuid4().hex
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO annotation_schemas ("
                    "id, project_id, schema_lineage_id, schema_version, code, name, status, "
                    "annotation_levels, labels, compatibility, schema_sha256, "
                    "created_by, updated_by"
                    ") VALUES (:id, :project_id, :lineage, 1, :code, 'S', 'DRAFT', "
                    "'[\"EPISODE\"]', '[]', 'BACKWARD_COMPATIBLE', :sha, :user_id, :user_id)"
                ),
                {
                    "id": schema_id,
                    "project_id": project_id,
                    "lineage": uuid4().hex,
                    "code": f"s-{schema_id[:8]}",
                    "sha": "sha256:" + "a" * 64,
                    "user_id": user_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO annotation_sample_sets ("
                    "id, project_id, code, status, selection_rule_version, strategy, filters, "
                    "strata, dedup_key, candidate_count, selected_count, excluded_count, "
                    "deduplicated_count, data_source, exclusion_summary, created_by, updated_by"
                    ") VALUES (:id, :project_id, :code, 'DRAFT', 'v1', 'FILTER', '{}', '[]', "
                    "'EPISODE_LINEAGE', 1, 0, 0, 0, 'UNKNOWN', '{}', :user_id, :user_id)"
                ),
                {
                    "id": sample_set_id,
                    "project_id": project_id,
                    "code": f"ss-{sample_set_id[:8]}",
                    "user_id": user_id,
                },
            )
            # 任务绑定触发器要求：Schema 已发布 + 样本集已快照（0013 守卫）。
            connection.execute(
                text("UPDATE annotation_schemas SET status = 'PENDING_REVIEW' WHERE id = :id"),
                {"id": schema_id},
            )
            connection.execute(
                text(
                    "UPDATE annotation_schemas SET status = 'PUBLISHED', "
                    "published_at = CURRENT_TIMESTAMP WHERE id = :id"
                ),
                {"id": schema_id},
            )
            connection.execute(
                text("UPDATE annotation_sample_sets SET status = 'PREVIEW' WHERE id = :id"),
                {"id": sample_set_id},
            )
            connection.execute(
                text(
                    "UPDATE annotation_sample_sets SET status = 'SNAPSHOTTED', "
                    "snapshot_sha256 = :sha, snapshot_at = CURRENT_TIMESTAMP WHERE id = :id"
                ),
                {"id": sample_set_id, "sha": "sha256:" + "a" * 64},
            )
        # NULL（不路由）与 JSON 数组（路由）都合法
        cases: list[tuple[str, object]] = [
            ("null-case", None),
            ("json-case", json.dumps([{"tag_name": "kitchen", "weight": 0.8}])),
        ]
        for label, required_skills in cases:
            task_id = uuid4().hex
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO annotation_tasks ("
                        "id, project_id, code, name, status, sample_set_id, schema_id, "
                        "schema_version, schema_sha256, iaa_policy, quality_policy, "
                        "required_skills, assignment_mode, target_item_count, "
                        "target_episode_count, priority, progress, quality_summary, "
                        "created_by, updated_by"
                        ") VALUES (:id, :project_id, :code, '任务', 'DRAFT', :ss, :schema, 1, "
                        ":sha, '{}', '{}', :required_skills, 'MANUAL', 1, 1, 'P1', '{}', '{}', "
                        ":user_id, :user_id)"
                    ),
                    {
                        "id": task_id,
                        "project_id": project_id,
                        "code": f"t-{uuid4().hex[:8]}",
                        "ss": sample_set_id,
                        "schema": schema_id,
                        "sha": "sha256:" + "a" * 64,
                        "required_skills": required_skills,
                        "user_id": user_id,
                    },
                )
            with engine.begin() as connection:
                row = connection.execute(
                    text("SELECT required_skills FROM annotation_tasks WHERE id = :id"),
                    {"id": task_id},
                ).scalar()
                if label == "null-case":
                    assert row is None
                else:
                    assert json.loads(row)[0]["tag_name"] == "kitchen"
    finally:
        engine.dispose()
