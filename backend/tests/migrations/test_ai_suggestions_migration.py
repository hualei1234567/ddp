"""迁移 0036：``annotation_ai_suggestions`` 建表、约束与双向回滚。

设计取舍
--------
种子数据用**真实夹具**（``tests/annotation_results_support``）而不是手写 SQL：
手写 SQL 会绕过状态机触发器，需要为 task / batch / assignment / submission
逐条拼出合法迁移路径，既脆又会随既有守卫演进而失效。用真实夹具的额外收益是
**顺带验证了迁移建出的表结构与 ORM 完全一致**——若 ORM 加了列而迁移没加，
本文件会立刻失败。

约束用例仍然绕过领域层直接写 ORM 行：领域实体本身会先拒绝非法值，
只有直插才能证明"库层 CHECK 真的存在"，这是防绕过应用层的最后一道闸。

覆盖：
- 升级建表 / 降级删表（含索引），可反复升降级；
- 库层约束：``confidence`` 越界、模型身份为空、``human_confirmed`` 与人工决定
  字段不一致、跨项目引用记录；
- 同一模型版本对同一记录/标签只允许一条；换 Prompt 版本可并存；
- 未确认的 ``human_value`` 必须落成 SQL NULL；
- 全新库 ``upgrade head`` 成功（防回到 ``Model.__table__.create`` 老坑）；
- 配 ``DDP_TEST_POSTGRES_URL`` 时在真实 PostgreSQL 上再跑一遍。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.infrastructure.db.models.annotation_ai import AnnotationAiSuggestion
from app.infrastructure.db.session import create_db_engine, create_session_factory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.annotation_results_support import (
    build_stacks,
    make_submission,
    prepare_ready_task,
    seed_identity,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLE = "annotation_ai_suggestions"
INDEXES = {
    "ix_annotation_ai_suggestions_project_annotation",
    "ix_annotation_ai_suggestions_project_model",
}
_POSTGRES_URL = os.environ.get("DDP_TEST_POSTGRES_URL", "")
NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)


def _config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    # ConfigParser 会对 ``%`` 做插值：PG 的 ``?options=-csearch_path%3D...``
    # 里那个 ``%`` 必须转义成 ``%%``，否则 set_main_option 直接抛 ValueError。
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+pysqlite:///{db_path}"


def _engine(url: str) -> Engine:
    """统一用 ``create_db_engine``：它会给每条 SQLite 连接开 ``PRAGMA foreign_keys``。

    裸 ``create_engine`` 下 SQLite 默认**关闭外键**，复合外键用例会假绿——
    跨项目引用根本不会被拦下，看起来"通过"其实什么都没验证。
    """
    if url.startswith("sqlite"):
        return create_db_engine(
            url,
            pool_size=1,
            max_overflow=0,
            pool_timeout_seconds=5,
            connect_timeout_seconds=5,
        )
    return create_engine(url)


@dataclass
class AiSeed:
    """一条已提交的标注记录 + 其提交与项目上下文。"""

    factory: sessionmaker[Session]
    project_id: UUID
    user_id: UUID
    annotation_id: UUID


def _make_seed(factory: sessionmaker[Session]) -> AiSeed:
    """用真实夹具造一条已提交的标注记录；夹具本身接收 ``Session``。"""
    with factory() as session:
        ids = seed_identity(session)
        stacks = build_stacks(session, ids)
        prepare_ready_task(stacks)
        submission = make_submission(stacks, stacks.scope_a, stacks.assignment_a)
        record = stacks.records_repo.list_for_submission(
            stacks.scope_a,
            submission.submission_id,
        )[0]
    return AiSeed(
        factory=factory,
        project_id=ids.project_a,
        user_id=ids.user_a,
        annotation_id=record.annotation_id,
    )


def _insert(
    seed: AiSeed,
    **overrides: object,
) -> None:
    """绕过领域层直插一行；非法值由库层约束拒绝。"""
    values: dict[str, object] = {
        "id": uuid4(),
        "project_id": seed.project_id,
        "annotation_id": seed.annotation_id,
        "label_key": "action",
        "suggested_value": "PICK",
        "model_id": "ddp-action-vlm",
        "model_version": "1.2.0",
        "prompt_version": "prompt-7",
        "confidence": 0.87,
        "generated_at": NOW,
        "human_confirmed": False,
        "human_value": None,
        "decided_by": None,
        "decided_at": None,
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
        "created_by": seed.user_id,
        "updated_by": seed.user_id,
    }
    values.update(overrides)
    with seed.factory() as session:
        session.add(AnnotationAiSuggestion(**values))  # type: ignore[arg-type]
        session.commit()


def _table_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return set(inspect(connection).get_table_names())


def _index_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return {item["name"] for item in inspect(connection).get_indexes(TABLE)}


def test_upgrade_creates_table_and_downgrade_removes_it(tmp_path: Path) -> None:
    db_path = tmp_path / "ai_suggestions.db"
    url = _sqlite_url(db_path)
    config = _config(url)

    command.upgrade(config, "0032")
    engine = _engine(url)
    try:
        assert TABLE not in _table_names(engine)
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = _engine(url)
    try:
        names = _table_names(engine)
        assert TABLE in names
        assert INDEXES <= _index_names(engine)
        # 既有表结构不受影响（本次不改 annotation_records）。
        assert "annotation_records" in names
    finally:
        engine.dispose()

    command.downgrade(config, "0032")
    engine = _engine(url)
    try:
        names = _table_names(engine)
        assert TABLE not in names
        assert "annotation_records" in names
    finally:
        engine.dispose()

    # 可反复升降级。
    command.upgrade(config, "head")
    engine = _engine(url)
    try:
        assert TABLE in _table_names(engine)
    finally:
        engine.dispose()


def test_constraints_reject_invalid_rows(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "ai_constraints.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        seed = _make_seed(create_session_factory(engine))
        _insert(seed)  # 合法行：未确认，人工字段全空

        cases = {
            "confidence 越界": {"confidence": 1.5},
            "confidence 为负": {"confidence": -0.1},
            "model_id 为空": {"model_id": ""},
            "model_version 为空": {"model_version": ""},
            "prompt_version 为空": {"prompt_version": ""},
            "label_key 为空": {"label_key": ""},
            "确认但缺人工值": {
                "human_confirmed": True,
                "decided_by": seed.user_id,
                "decided_at": NOW,
            },
            "确认但缺决定人": {
                "human_confirmed": True,
                "human_value": "PLACE",
                "decided_at": NOW,
            },
            "确认但缺决定时间": {
                "human_confirmed": True,
                "human_value": "PLACE",
                "decided_by": seed.user_id,
            },
            "未确认却带人工值": {"human_value": "PLACE"},
            "未确认却带决定人": {"decided_by": seed.user_id},
        }
        for label, overrides in cases.items():
            with pytest.raises(IntegrityError) as excinfo:
                _insert(seed, **overrides)
            message = str(excinfo.value)
            assert (
                "ck_annotation_ai_suggestions" in message or "CHECK" in message
            ), f"{label} 应由本表 CHECK 拒绝，实际：{message}"
    finally:
        engine.dispose()


def test_duplicate_identity_rejected_but_new_prompt_version_allowed(
    tmp_path: Path,
) -> None:
    url = _sqlite_url(tmp_path / "ai_identity.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        seed = _make_seed(create_session_factory(engine))
        _insert(seed)

        with pytest.raises(IntegrityError):
            _insert(seed)

        # 换 Prompt 版本是"另一个模型假设"，必须允许并存——多版本对比的前提。
        _insert(seed, prompt_version="prompt-8")
        with seed.factory() as session:
            assert session.query(AnnotationAiSuggestion).count() == 2
    finally:
        engine.dispose()


def test_unconfirmed_human_value_is_sql_null(tmp_path: Path) -> None:
    """``human_value`` 未确认时必须落成 SQL NULL，而不是 JSON 字面量。

    若只是 JSON ``null``，``human_value IS NULL`` 判定为假，``decision_consistency``
    会误拒每一条未确认的建议（实测 SQLite 如此）。
    """
    url = _sqlite_url(tmp_path / "ai_null.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        seed = _make_seed(create_session_factory(engine))
        _insert(seed)
        with engine.connect() as connection:
            is_null = connection.execute(
                text(f"SELECT human_value IS NULL FROM {TABLE}")  # noqa: S608
            ).scalar()
        assert is_null == 1
    finally:
        engine.dispose()


def test_cross_project_reference_is_rejected(tmp_path: Path) -> None:
    """记录属于项目 A，建议却声明项目 B——复合外键必须拦下。"""
    url = _sqlite_url(tmp_path / "ai_cross.db")
    command.upgrade(_config(url), "head")
    engine = _engine(url)
    try:
        seed = _make_seed(create_session_factory(engine))
        ids = seed_identity_other_project(seed, engine)
        with pytest.raises(IntegrityError):
            _insert(
                AiSeed(
                    factory=seed.factory,
                    project_id=ids[0],
                    user_id=ids[1],
                    annotation_id=seed.annotation_id,
                )
            )
    finally:
        engine.dispose()


def seed_identity_other_project(seed: AiSeed, engine: Engine) -> tuple[UUID, UUID]:
    """在库里造第二个项目与用户（用于跨项目引用用例）。"""
    from app.infrastructure.db.models.identity import Project, User

    project_id, user_id = uuid4(), uuid4()
    with seed.factory() as session:
        session.add_all(
            [
                Project(id=project_id, code=f"p-{project_id.hex[:8]}", name="项目 C"),
                User(id=user_id, display_name="丙"),
            ]
        )
        session.commit()
    return project_id, user_id


@pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="未配置 PostgreSQL 测试库（BLOCKED）：设置 DDP_TEST_POSTGRES_URL 后运行",
)
def test_migration_roundtrip_on_postgresql() -> None:
    """真实 PostgreSQL 上的升级/回滚与 CHECK 约束（SQLite 不作为验收替代）。"""
    schema = f"ddp_ai_sug_{uuid4().hex}"
    admin = create_engine(_POSTGRES_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = f"{_POSTGRES_URL}?options=-csearch_path%3D{schema}"
        config = _config(url)

        command.upgrade(config, "head")
        engine = _engine(url)
        try:
            seed = _make_seed(create_session_factory(engine))
            _insert(seed)
            with pytest.raises(IntegrityError):
                _insert(seed, confidence=2)
        finally:
            engine.dispose()

        command.downgrade(config, "0032")
        engine = _engine(url)
        try:
            assert TABLE not in _table_names(engine)
        finally:
            engine.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def _unused(_value: object) -> Iterator[None]:  # pragma: no cover - 占位，避免误删导入
    yield
