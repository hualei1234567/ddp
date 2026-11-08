#!/usr/bin/env python3
"""DDP-308 阶段 2 真实 PostgreSQL 验收探针（数据库与 Episode 应用层）。

只读业务实现，不修改 backend/app、migrations、packages/contracts。
脚本在本地真实 PostgreSQL 16 上创建一次性数据库，执行：
- 0006–0010 逐级升级、降级、再升级；
- 11 个上传触发器与 0008 升降级复核；
- 上传/资产/链接/审计/outbox/Episode/血缘的数据库直写拒绝；
- Episode 应用层：READY 冻结、Manifest Hash、质检门禁、版本修订、血缘正反向。

输出：docs/testing/evidence/2026-11-08-ddp-308/db-probe.json 与 .log
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
EVIDENCE = ROOT / "docs" / "testing" / "evidence" / "2026-11-08-ddp-308"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DDP_ENV", "local")
os.environ.setdefault("DDP_LOCAL_HEADER_AUTH", "false")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, delete, select, text, update  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.application.episodes.commands import (  # noqa: E402
    CreateEpisodeCommand,
    EpisodeActionCommand,
    MarkEpisodeReadyCommand,
    ReviseEpisodeCommand,
)
from app.application.episodes.episodes import EpisodeService  # noqa: E402
from app.domain.episodes.enums import (  # noqa: E402
    EpisodeQualityStatus,
    LineageNodeType,
    LineageRelation,
    SegmentationMethod,
)
from app.domain.episodes.errors import (  # noqa: E402
    DomainValidationError as EpisodeValidationError,
)
from app.domain.episodes.manifest import (  # noqa: E402
    assert_manifest_hash,
    compute_manifest_sha256,
)
from app.domain.episodes.models import Episode, Segmentation  # noqa: E402
from app.domain.episodes.validation import build_manifest_key  # noqa: E402
from app.domain.identity.models import ProjectScope  # noqa: E402
from app.infrastructure.db.models.collection import (  # noqa: E402
    AuditLog,
    OutboxEvent,
)
from app.infrastructure.db.models.episodes import (  # noqa: E402
    DataLineageEdgeModel,
    EpisodeModel,
    EpisodeRawAssetModel,
    LineageNodeRefModel,
)
from app.infrastructure.db.models.identity import (  # noqa: E402
    Project,
    ProjectMember,
    User,
)
from app.infrastructure.db.models.ingestion import (  # noqa: E402
    RawAssetLinkModel,
    RawAssetModel,
    UploadPartModel,
    UploadSessionModel,
)
from app.infrastructure.db.session import create_session_factory  # noqa: E402
from app.infrastructure.repositories.collection_audit import (  # noqa: E402
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.episodes import (  # noqa: E402
    SqlEpisodeRawAssetRepository,
    SqlEpisodeRepository,
)
from app.infrastructure.repositories.ingestion_jobs import (  # noqa: E402
    SqlIngestionJobRepository,
)
from app.infrastructure.repositories.lineage import SqlLineageRepository  # noqa: E402
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository  # noqa: E402

PG_ADMIN = "postgresql+psycopg://ddp:ddp@127.0.0.1:55432/postgres"
PG_TEMPLATE = "postgresql+psycopg://ddp:ddp@127.0.0.1:55432/{dbname}"

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SHA_A = f"sha256:{'a' * 64}"
SHA_B = f"sha256:{'b' * 64}"

UPLOAD_TRIGGERS = {
    "trg_upload_sessions_initial_status",
    "trg_upload_sessions_transition",
    "trg_upload_sessions_no_delete",
    "trg_upload_parts_no_update",
    "trg_upload_parts_no_delete",
    "trg_raw_assets_initial_status",
    "trg_raw_assets_transition",
    "trg_raw_assets_content_immutable",
    "trg_raw_assets_no_delete",
    "trg_raw_asset_links_no_delete",
    "trg_raw_asset_links_terminal_status",
}
EPISODE_TRIGGERS = {
    "trg_ingestion_jobs_initial_status",
    "trg_ingestion_jobs_transition",
    "trg_ingestion_jobs_no_delete",
    "trg_episodes_initial_status",
    "trg_episodes_transition",
    "trg_episodes_no_delete",
    "trg_episode_raw_assets_no_update",
    "trg_episode_raw_assets_no_delete",
    "trg_episode_raw_assets_parent_frozen",
    "trg_lineage_node_refs_identity_immutable",
    "trg_lineage_node_refs_no_delete",
    "trg_data_lineage_edges_no_update",
    "trg_data_lineage_edges_no_delete",
    "trg_data_lineage_edges_no_self",
    "trg_data_lineage_edges_supersedes_linear",
}


class Probe:
    def __init__(self, dbname: str, url: str) -> None:
        self.dbname = dbname
        self.url = url
        self.results: list[dict[str, object]] = []
        self.log_lines: list[str] = []
        self.engine = create_engine(url, pool_pre_ping=True)
        self.factory = create_session_factory(self.engine)
        self.ids: dict[str, UUID] = {}
        self.episode_created: Episode | None = None
        self.episode_ready: Episode | None = None
        self.episode_accepted: Episode | None = None
        self.episode_revised: Episode | None = None

    # ---------------------------------------------------------------- 记录
    def record(self, check_id: str, status: str, detail: str) -> None:
        item = {"id": check_id, "status": status, "detail": detail}
        self.results.append(item)
        line = f"[{status}] {check_id}: {detail}"
        self.log_lines.append(line)
        print(line)

    def check(self, check_id: str, func: Callable[[], str], *, status: str = "PASS") -> None:
        try:
            detail = func()
        except Exception as exc:  # noqa: BLE001 - 探针需记录所有失败
            self.record(check_id, "FAIL", f"{type(exc).__name__}: {exc}")
            self.log_lines.append(traceback.format_exc())
        else:
            self.record(check_id, status, detail)

    def expect_db_error(self, session: Session, operation: Callable[[], object], code: str) -> str:
        try:
            operation()
            session.flush()
        except IntegrityError as exc:
            message = str(getattr(exc, "orig", exc))
            session.rollback()
            if code not in message:
                raise AssertionError(f"错误码不匹配：期望 {code}，实际 {message}") from exc
            return message.splitlines()[0][:240]
        except Exception:
            session.rollback()
            raise
        session.rollback()
        raise AssertionError(f"数据库未拒绝，期望错误码 {code}")

    # ---------------------------------------------------------------- 迁移
    def _config(self) -> Config:
        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "migrations"))
        config.set_main_option("prepend_sys_path", str(BACKEND))
        config.set_main_option("sqlalchemy.url", self.url)
        return config

    def version(self) -> str | None:
        with self.engine.connect() as conn:
            return conn.execute(text("select version_num from alembic_version")).scalar()

    def tables(self) -> set[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "select table_name from information_schema.tables "
                    "where table_schema='public' and table_type='BASE TABLE'"
                )
            )
            return {row[0] for row in rows}

    def triggers(self) -> set[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "select t.tgname from pg_trigger t "
                    "join pg_class c on c.oid=t.tgrelid "
                    "join pg_namespace n on n.oid=c.relnamespace "
                    "where not t.tgisinternal and n.nspname='public'"
                )
            )
            return {row[0] for row in rows}

    def run_migrations(self) -> None:
        config = self._config()
        for rev in ("0006", "0007", "0008", "0009", "0010"):
            command.upgrade(config, rev)
            self.record(
                f"MIG-UP-{rev}",
                "PASS",
                f"upgrade {rev} 完成，当前 revision={self.version()}，表数={len(self.tables())}",
            )

        upload_tables = {"upload_sessions", "upload_parts", "raw_assets", "raw_asset_links"}
        stage2_tables = {
            "audit_logs",
            "outbox_events",
            "idempotency_records",
            "collection_tasks",
            "collection_batches",
            "upload_sessions",
            "upload_parts",
            "raw_assets",
            "raw_asset_links",
            "ingestion_jobs",
            "episodes",
            "episode_raw_assets",
            "lineage_node_refs",
            "data_lineage_edges",
        }

        def after_0010() -> str:
            tables = self.tables()
            triggers = self.triggers()
            assert upload_tables <= tables, f"上传表缺失：{upload_tables - tables}"
            assert stage2_tables <= tables, f"阶段 2 表缺失：{stage2_tables - tables}"
            assert UPLOAD_TRIGGERS <= triggers, f"上传触发器缺失：{UPLOAD_TRIGGERS - triggers}"
            assert EPISODE_TRIGGERS <= triggers, (
                f"Episode/血缘触发器缺失：{EPISODE_TRIGGERS - triggers}"
            )
            return (
                f"head=0010；阶段 2 表 {len(stage2_tables)} 张齐全；"
                f"上传触发器 {len(UPLOAD_TRIGGERS)} 个、"
                f"Episode/血缘触发器 {len(EPISODE_TRIGGERS)} 个齐全"
            )

        self.check("MIG-HEAD-0010", after_0010)

        command.downgrade(config, "0009")
        self.check(
            "MIG-DOWN-0010",
            lambda: (
                f"downgrade 0010→{self.version()}；Episode/血缘触发器已移除"
                if not (EPISODE_TRIGGERS & self.triggers())
                else self._raise("Episode/血缘触发器未移除")
            ),
        )
        command.downgrade(config, "0008")
        self.check(
            "MIG-DOWN-0009",
            lambda: (
                f"downgrade 0009→{self.version()}；episodes/ingestion_jobs 已移除"
                if not (
                    {
                        "episodes",
                        "ingestion_jobs",
                        "episode_raw_assets",
                        "lineage_node_refs",
                        "data_lineage_edges",
                    }
                    & self.tables()
                )
                else self._raise("0009 表未移除")
            ),
        )
        command.downgrade(config, "0007")
        self.check(
            "MIG-DOWN-0008",
            lambda: (
                f"downgrade 0008→{self.version()}；上传表与 11 触发器已移除"
                if not (upload_tables & self.tables())
                and not (UPLOAD_TRIGGERS & self.triggers())
                else self._raise("0008 表或触发器未移除")
            ),
        )
        command.upgrade(config, "0008")
        self.check(
            "MIG-REUP-0008",
            lambda: (
                f"upgrade 0008 完成；上传表 {len(upload_tables & self.tables())}/4，"
                f"触发器 {len(UPLOAD_TRIGGERS & self.triggers())}/11"
                if upload_tables <= self.tables() and UPLOAD_TRIGGERS <= self.triggers()
                else self._raise("0008 重新升级后表或触发器缺失")
            ),
        )
        command.upgrade(config, "0010")
        self.check("MIG-REUP-HEAD", after_0010)

        command.downgrade(config, "0005")
        self.check(
            "MIG-DOWN-0005",
            lambda: (
                f"downgrade 全链→{self.version()}；阶段 2 表全部移除"
                if not (stage2_tables & self.tables())
                else self._raise(f"阶段 2 表残留：{stage2_tables & self.tables()}")
            ),
        )
        command.upgrade(config, "head")
        self.check("MIG-REUP-ALL", after_0010)

    @staticmethod
    def _raise(message: str) -> str:
        raise AssertionError(message)

    def trigger_inventory(self) -> None:
        def run() -> str:
            triggers = self.triggers()
            missing = (UPLOAD_TRIGGERS | EPISODE_TRIGGERS) - triggers
            assert not missing, f"缺失触发器：{sorted(missing)}"
            return (
                "上传触发器 11/11、Episode/血缘触发器 15/15 存在；"
                f"上传触发器={sorted(UPLOAD_TRIGGERS)}"
            )

        self.check("DB-TRG-INVENTORY", run)

    # ---------------------------------------------------------------- 数据
    def seed(self) -> None:
        project_a, project_b = uuid4(), uuid4()
        user_a, user_b, outsider = uuid4(), uuid4(), uuid4()
        self.ids.update(
            project_a=project_a,
            project_b=project_b,
            user_a=user_a,
            user_b=user_b,
            outsider=outsider,
        )
        with self.factory() as session:
            session.add_all(
                [
                    Project(id=project_a, code=f"qa-a-{project_a.hex[:8]}", name="QA 项目 A"),
                    Project(id=project_b, code=f"qa-b-{project_b.hex[:8]}", name="QA 项目 B"),
                    User(id=user_a, display_name="QA 甲"),
                    User(id=user_b, display_name="QA 乙"),
                    User(id=outsider, display_name="QA 外部用户"),
                ]
            )
            session.commit()
            session.add_all(
                [
                    ProjectMember(project_id=project_a, user_id=user_a, role="editor"),
                    ProjectMember(project_id=project_a, user_id=user_b, role="owner"),
                    ProjectMember(project_id=project_b, user_id=user_b, role="owner"),
                ]
            )
            session.commit()
        self.record("DB-SEED", "PASS", "真实 PG 预置项目 A/B、用户与成员关系")

    def _session_row(self, status: str = "CREATED") -> UploadSessionModel:
        project_id = self.ids["project_a"]
        user_id = self.ids["user_a"]
        return UploadSessionModel(
            id=uuid4(),
            project_id=project_id,
            status=status,
            verification_status="PENDING",
            original_filename="robot.mcap",
            declared_content_type="application/x-mcap",
            declared_size_bytes=8,
            part_size_bytes=4,
            max_size_bytes=64,
            object_key=f"raw/{project_id}/2026/11/08/{uuid4()}/content.mcap",
            multipart_upload_id=f"upload-{uuid4().hex}",
            expires_at=NOW + timedelta(hours=1),
            created_by=user_id,
            updated_by=user_id,
        )

    def _asset_row(
        self,
        status: str = "VALIDATING",
        project_id: UUID | None = None,
    ) -> RawAssetModel:
        project_id = project_id or self.ids["project_a"]
        user_id = self.ids["user_a"]
        asset_id = uuid4()
        return RawAssetModel(
            id=asset_id,
            project_id=project_id,
            code=f"RAW-{asset_id.hex[:20]}",
            source_type="UPLOAD",
            original_filename="robot.mcap",
            object_key=f"raw/{project_id}/2026/11/08/{asset_id}/content.mcap",
            size_bytes=8,
            content_type="application/x-mcap",
            format="MCAP",
            sha256=SHA_A,
            status=status,
            ingested_at=NOW,
            storage_class="STANDARD",
            uploaded_by=user_id,
            created_by=user_id,
            updated_by=user_id,
        )

    # ---------------------------------------------------------------- 上传触发器
    def upload_trigger_checks(self) -> None:
        project_id = self.ids["project_a"]
        user_id = self.ids["user_a"]

        with self.factory() as session:
            session.add(self._session_row("UPLOADING"))
            detail = self.expect_db_error(
                session,
                lambda: session.flush(),
                "upload_session_initial_status_invalid",
            )
            self.record("DB-TRG-01", "PASS", detail)

        with self.factory() as session:
            row = self._session_row()
            session.add(row)
            session.commit()
            session_id = row.id
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(UploadSessionModel)
                    .where(UploadSessionModel.id == session_id)
                    .values(status="COMPLETED")
                ),
                "upload_session_state_transition_invalid",
            )
            self.record("DB-TRG-02", "PASS", detail)
            for status in ("UPLOADING", "COMPLETING", "COMPLETED"):
                session.execute(
                    update(UploadSessionModel)
                    .where(UploadSessionModel.id == session_id)
                    .values(status=status)
                )
                session.commit()
            self.record(
                "DB-TRG-02B",
                "PASS",
                "合法链路 CREATED→UPLOADING→COMPLETING→COMPLETED 通过",
            )
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    delete(UploadSessionModel).where(UploadSessionModel.id == session_id)
                ),
                "upload_session_delete_forbidden",
            )
            self.record("DB-TRG-03", "PASS", detail)

        with self.factory() as session:
            row = self._session_row()
            session.add(row)
            session.commit()
            session.add(
                UploadPartModel(
                    project_id=project_id,
                    upload_session_id=row.id,
                    part_number=1,
                    size_bytes=4,
                    etag="etag-1",
                    sha256=SHA_B,
                    recorded_at=NOW,
                )
            )
            session.commit()
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(UploadPartModel)
                    .where(UploadPartModel.upload_session_id == row.id)
                    .values(etag="etag-2")
                ),
                "upload_part_immutable",
            )
            self.record("DB-TRG-04", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    delete(UploadPartModel).where(UploadPartModel.upload_session_id == row.id)
                ),
                "upload_part_delete_forbidden",
            )
            self.record("DB-TRG-05", "PASS", detail)

        with self.factory() as session:
            session.add(self._asset_row("VALID"))
            detail = self.expect_db_error(
                session,
                lambda: session.flush(),
                "raw_asset_initial_status_invalid",
            )
            self.record("DB-TRG-06", "PASS", detail)

        with self.factory() as session:
            row = self._asset_row()
            session.add(row)
            session.commit()
            asset_id = row.id
            session.execute(
                update(RawAssetModel).where(RawAssetModel.id == asset_id).values(status="VALID")
            )
            session.commit()
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(RawAssetModel)
                    .where(RawAssetModel.id == asset_id)
                    .values(status="VALIDATING")
                ),
                "raw_asset_state_transition_invalid",
            )
            self.record("DB-TRG-07", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(RawAssetModel)
                    .where(RawAssetModel.id == asset_id)
                    .values(object_key=row.object_key + ".tampered")
                ),
                "raw_asset_content_immutable",
            )
            self.record("DB-TRG-08", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.execute(delete(RawAssetModel).where(RawAssetModel.id == asset_id)),
                "raw_asset_delete_forbidden",
            )
            self.record("DB-TRG-09", "PASS", detail)

        with self.factory() as session:
            session_row = self._session_row()
            asset_row = self._asset_row()
            session.add_all([session_row, asset_row])
            session.commit()
            link = RawAssetLinkModel(
                id=uuid4(),
                project_id=project_id,
                asset_id=asset_row.id,
                upload_session_id=session_row.id,
                link_type="UPLOAD_SESSION",
                link_status="PENDING_VERIFICATION",
                created_by=user_id,
                created_at=NOW,
            )
            session.add(link)
            session.commit()
            session.execute(
                update(RawAssetLinkModel)
                .where(RawAssetLinkModel.id == link.id)
                .values(link_status="ACTIVE")
            )
            session.commit()
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(RawAssetLinkModel)
                    .where(RawAssetLinkModel.id == link.id)
                    .values(link_status="PENDING_VERIFICATION")
                ),
                "raw_asset_link_state_transition_invalid",
            )
            self.record("DB-TRG-10", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    delete(RawAssetLinkModel).where(RawAssetLinkModel.id == link.id)
                ),
                "raw_asset_link_delete_forbidden",
            )
            self.record("DB-TRG-11", "PASS", detail)


    # ---------------------------------------------------------------- 采集任务/批次触发器
    def collection_trigger_checks(self) -> None:
        from app.infrastructure.db.models.collection import CollectionBatch, CollectionTask

        project_id = self.ids["project_a"]
        user_id = self.ids["user_a"]

        with self.factory() as session:
            session.add(
                CollectionTask(
                    id=uuid4(),
                    project_id=project_id,
                    code=f"TASK-BAD-{uuid4().hex[:8]}",
                    name="非法初始任务",
                    status="READY",
                    priority="P1",
                    created_by=user_id,
                    updated_by=user_id,
                )
            )
            detail = self.expect_db_error(
                session,
                lambda: session.flush(),
                "collection_task_initial_status_invalid",
            )
            self.record("DB-COL-TRG-01", "PASS", detail)

        with self.factory() as session:
            task = CollectionTask(
                id=uuid4(),
                project_id=project_id,
                code=f"TASK-{uuid4().hex[:8]}",
                name="采集任务",
                status="DRAFT",
                priority="P1",
                created_by=user_id,
                updated_by=user_id,
            )
            session.add(task)
            session.commit()
            task_id = task.id
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(CollectionTask)
                    .where(CollectionTask.id == task_id)
                    .values(status="IN_PROGRESS")
                ),
                "collection_task_state_transition_invalid",
            )
            self.record("DB-COL-TRG-02", "PASS", detail)
            session.execute(
                update(CollectionTask)
                .where(CollectionTask.id == task_id)
                .values(status="PENDING_REVIEW")
            )
            session.commit()
            self.record("DB-COL-TRG-02B", "PASS", "合法链路 DRAFT→PENDING_REVIEW 通过")
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    delete(CollectionTask).where(CollectionTask.id == task_id)
                ),
                "collection_task_delete_forbidden",
            )
            self.record("DB-COL-TRG-03", "PASS", detail)

            batch = CollectionBatch(
                id=uuid4(),
                project_id=project_id,
                task_id=task_id,
                code=f"BATCH-{uuid4().hex[:8]}",
                sequence_no=1,
                status="PLANNED",
                created_by=user_id,
                updated_by=user_id,
            )
            session.add(batch)
            session.commit()
            batch_id = batch.id
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(CollectionBatch)
                    .where(CollectionBatch.id == batch_id)
                    .values(status="IN_PROGRESS")
                ),
                "collection_batch_state_transition_invalid",
            )
            self.record("DB-COL-TRG-04", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(CollectionBatch)
                    .where(CollectionBatch.id == batch_id)
                    .values(status="READY")
                ),
                "collection_batch_leads_task",
            )
            self.record("DB-COL-TRG-05", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    delete(CollectionBatch).where(CollectionBatch.id == batch_id)
                ),
                "collection_batch_delete_forbidden",
            )
            self.record("DB-COL-TRG-06", "PASS", detail)

        with self.factory() as session:
            session.add(
                CollectionBatch(
                    id=uuid4(),
                    project_id=project_id,
                    task_id=task_id,
                    code=f"BATCH-BAD-{uuid4().hex[:8]}",
                    sequence_no=2,
                    status="READY",
                    created_by=user_id,
                    updated_by=user_id,
                )
            )
            detail = self.expect_db_error(
                session,
                lambda: session.flush(),
                "collection_batch_initial_status_invalid",
            )
            self.record("DB-COL-TRG-07", "PASS", detail)

    # ---------------------------------------------------------------- 审计/outbox
    def audit_outbox_checks(self) -> None:
        project_id = self.ids["project_a"]
        user_id = self.ids["user_a"]
        with self.factory() as session:
            audit = AuditLog(
                id=uuid4(),
                project_id=project_id,
                actor_user_id=user_id,
                actor_role="editor",
                resource_type="UPLOAD_SESSION",
                resource_id=uuid4(),
                action="create",
                result="SUCCESS",
                details={"probe": "ddp-308"},
                occurred_at=NOW,
            )
            event = OutboxEvent(
                id=uuid4(),
                project_id=project_id,
                aggregate_type="UPLOAD_SESSION",
                aggregate_id=audit.resource_id,
                event_type="ingestion.upload_session.created",
                payload={"probe": "ddp-308"},
                status="PENDING",
                attempts=0,
                available_at=NOW,
                created_at=NOW,
            )
            session.add_all([audit, event])
            session.commit()
            audit_id, event_id = audit.id, event.id
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(AuditLog).where(AuditLog.id == audit_id).values(action="tampered")
                ),
                "audit_log_append_only",
            )
            self.record("DB-AUD-01", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.execute(delete(AuditLog).where(AuditLog.id == audit_id)),
                "audit_log_append_only",
            )
            self.record("DB-AUD-02", "PASS", detail)
            stored = session.scalar(select(OutboxEvent).where(OutboxEvent.id == event_id))
            assert stored is not None and stored.status == "PENDING"
            self.record("DB-OUTBOX-01", "PASS", "outbox_events 可写入并读取，状态 PENDING")

    # ---------------------------------------------------------------- Episode/血缘触发器
    def _episode_row(
        self,
        status: str = "PENDING",
        project_id: UUID | None = None,
    ) -> EpisodeModel:
        project_id = project_id or self.ids["project_a"]
        user_id = self.ids["user_a"]
        return EpisodeModel(
            id=uuid4(),
            project_id=project_id,
            episode_lineage_id=uuid4(),
            episode_version=1,
            code=f"EP-{uuid4().hex[:12]}",
            status=status,
            quality_status="NOT_CHECKED",
            annotation_status="NOT_STARTED",
            start_time_ns=1_000_000,
            end_time_ns=2_000_000,
            duration_ns=1_000_000,
            time_domain="UTC",
            time_base={"time_domain": "UTC"},
            canonical_schema_version="ddp.canonical/1.0.0-draft",
            pipeline_id="parser",
            pipeline_version="1.0.0",
            segmentation_rule_version="seg-manual-v1",
            manifest_key=None,
            manifest_sha256=None,
            created_by=user_id,
            updated_by=user_id,
        )

    def episode_trigger_checks(self) -> None:
        project_a = self.ids["project_a"]
        project_b = self.ids["project_b"]
        user_a = self.ids["user_a"]

        with self.factory() as session:
            session.add(self._episode_row("PROCESSING"))
            detail = self.expect_db_error(
                session,
                lambda: session.flush(),
                "episode_initial_status_invalid",
            )
            self.record("DB-EPI-TRG-01", "PASS", detail)

            episode = self._episode_row()
            session.add(episode)
            session.commit()
            episode_id = episode.id
            session.execute(
                update(EpisodeModel).where(EpisodeModel.id == episode_id).values(status="PROCESSING")
            )
            session.commit()
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(EpisodeModel)
                    .where(EpisodeModel.id == episode_id)
                    .values(status="ACCEPTED")
                ),
                "episode_state_transition_invalid",
            )
            self.record("DB-EPI-TRG-02", "PASS", detail)

            # READY 前子表允许插入，但插入后不可更新/删除
            asset_row = self._asset_row()
            session.add(asset_row)
            session.commit()
            child = EpisodeRawAssetModel(
                project_id=project_a,
                episode_id=episode_id,
                asset_id=asset_row.id,
                role="INPUT",
                start_time_ns=1_000_000,
                end_time_ns=2_000_000,
                sha256_snapshot=SHA_A,
                size_bytes_snapshot=8,
            )
            session.add(child)
            session.commit()
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(EpisodeRawAssetModel)
                    .where(EpisodeRawAssetModel.episode_id == episode_id)
                    .values(role="TAMPERED")
                ),
                "episode_raw_asset_immutable",
            )
            self.record("DB-EPI-TRG-03", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    delete(EpisodeRawAssetModel).where(
                        EpisodeRawAssetModel.episode_id == episode_id
                    )
                ),
                "episode_raw_asset_immutable",
            )
            self.record("DB-EPI-TRG-04", "PASS", detail)

            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(EpisodeModel)
                    .where(EpisodeModel.id == episode_id)
                    .values(status="READY")
                ),
                "episode_manifest_required",
            )
            self.record("DB-EPI-TRG-05", "PASS", detail)

            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode_id)
                .values(
                    status="READY",
                    manifest_key=f"canonical/{project_a}/{episode_id}/1/manifest.json",
                    manifest_sha256=SHA_B,
                )
            )
            session.commit()

            second_asset = self._asset_row()
            session.add(second_asset)
            session.commit()
            detail = self.expect_db_error(
                session,
                lambda: session.add(
                    EpisodeRawAssetModel(
                        project_id=project_a,
                        episode_id=episode_id,
                        asset_id=second_asset.id,
                        role="INPUT2",
                        start_time_ns=1_000_000,
                        end_time_ns=2_000_000,
                        sha256_snapshot=SHA_A,
                        size_bytes_snapshot=8,
                    )
                )
                or session.flush(),
                "episode_raw_asset_parent_frozen",
            )
            self.record("DB-EPI-TRG-06", "PASS", detail)

            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(EpisodeModel)
                    .where(EpisodeModel.id == episode_id)
                    .values(start_time_ns=1)
                ),
                "episode_data_immutable",
            )
            self.record("DB-EPI-TRG-07", "PASS", detail)

            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode_id)
                .values(status="QUALITY_CHECKING", quality_status="PASS")
            )
            session.commit()
            session.execute(
                update(EpisodeModel).where(EpisodeModel.id == episode_id).values(status="ACCEPTED")
            )
            session.commit()
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(EpisodeModel)
                    .where(EpisodeModel.id == episode_id)
                    .values(quality_status="FAIL")
                ),
                "episode_quality_immutable",
            )
            self.record("DB-EPI-TRG-08", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.execute(delete(EpisodeModel).where(EpisodeModel.id == episode_id)),
                "episode_delete_forbidden",
            )
            self.record("DB-EPI-TRG-09", "PASS", detail)

            # 血缘节点身份不可变/禁删
            node_id = uuid4()
            node = LineageNodeRefModel(
                project_id=project_a,
                node_type="EPISODE",
                node_id=node_id,
                is_active=True,
                updated_at=NOW,
            )
            session.add(node)
            session.commit()
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(LineageNodeRefModel)
                    .where(
                        LineageNodeRefModel.project_id == project_a,
                        LineageNodeRefModel.node_id == node_id,
                    )
                    .values(node_id=uuid4())
                ),
                "lineage_node_ref_identity_immutable",
            )
            self.record("DB-LIN-TRG-01", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    delete(LineageNodeRefModel).where(
                        LineageNodeRefModel.project_id == project_a,
                        LineageNodeRefModel.node_id == node_id,
                    )
                ),
                "lineage_node_ref_delete_forbidden",
            )
            self.record("DB-LIN-TRG-02", "PASS", detail)

            target1, target2 = uuid4(), uuid4()
            session.add_all(
                [
                    LineageNodeRefModel(
                        project_id=project_a,
                        node_type="EPISODE",
                        node_id=target1,
                        is_active=True,
                        updated_at=NOW,
                    ),
                    LineageNodeRefModel(
                        project_id=project_a,
                        node_type="EPISODE",
                        node_id=target2,
                        is_active=True,
                        updated_at=NOW,
                    ),
                ]
            )
            session.commit()
            edge = DataLineageEdgeModel(
                id=uuid4(),
                project_id=project_a,
                from_node_type="EPISODE",
                from_node_id=node_id,
                to_node_type="EPISODE",
                to_node_id=target1,
                relation="SUPERSEDES",
                edge_metadata={},
                created_by=user_a,
                created_at=NOW,
            )
            session.add(edge)
            session.commit()
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(DataLineageEdgeModel)
                    .where(DataLineageEdgeModel.id == edge.id)
                    .values(relation="INVALIDATES")
                ),
                "lineage_edge_append_only",
            )
            self.record("DB-LIN-TRG-03", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    delete(DataLineageEdgeModel).where(DataLineageEdgeModel.id == edge.id)
                ),
                "lineage_edge_append_only",
            )
            self.record("DB-LIN-TRG-04", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.add(
                    DataLineageEdgeModel(
                        id=uuid4(),
                        project_id=project_a,
                        from_node_type="EPISODE",
                        from_node_id=node_id,
                        to_node_type="EPISODE",
                        to_node_id=node_id,
                        relation="INVALIDATES",
                        edge_metadata={},
                        created_by=user_a,
                        created_at=NOW,
                    )
                )
                or session.flush(),
                "lineage_edge_self_forbidden",
            )
            self.record("DB-LIN-TRG-05", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.add(
                    DataLineageEdgeModel(
                        id=uuid4(),
                        project_id=project_a,
                        from_node_type="EPISODE",
                        from_node_id=node_id,
                        to_node_type="EPISODE",
                        to_node_id=target2,
                        relation="SUPERSEDES",
                        edge_metadata={},
                        created_by=user_a,
                        created_at=NOW,
                    )
                )
                or session.flush(),
                "lineage_supersedes_not_linear",
            )
            self.record("DB-LIN-TRG-06", "PASS", detail)
            detail = self.expect_db_error(
                session,
                lambda: session.add(
                    DataLineageEdgeModel(
                        id=uuid4(),
                        project_id=project_a,
                        from_node_type="EPISODE",
                        from_node_id=target1,
                        to_node_type="EPISODE",
                        to_node_id=node_id,
                        relation="SUPERSEDES",
                        edge_metadata={},
                        created_by=user_a,
                        created_at=NOW,
                    )
                )
                or session.flush(),
                "lineage_supersedes_not_linear",
            )
            self.record("DB-LIN-TRG-07", "PASS", detail)

            node_b = LineageNodeRefModel(
                project_id=project_b,
                node_type="EPISODE",
                node_id=uuid4(),
                is_active=True,
                updated_at=NOW,
            )
            session.add(node_b)
            session.commit()
            detail = self.expect_db_error(
                session,
                lambda: session.add(
                    DataLineageEdgeModel(
                        id=uuid4(),
                        project_id=project_a,
                        from_node_type="EPISODE",
                        from_node_id=node_id,
                        to_node_type="EPISODE",
                        to_node_id=node_b.node_id,
                        relation="INVALIDATES",
                        edge_metadata={},
                        created_by=user_a,
                        created_at=NOW,
                    )
                )
                or session.flush(),
                "fk_data_lineage_edges_project_to_node",
            )
            self.record("DB-LIN-TRG-08", "PASS", detail)

    # ---------------------------------------------------------------- Episode 应用层
    def _episode_service(self, session: Session) -> EpisodeService:
        return EpisodeService(
            episodes=SqlEpisodeRepository(session),
            episode_assets=SqlEpisodeRawAssetRepository(session),
            lineage=SqlLineageRepository(session),
            raw_assets=SqlRawAssetRepository(session),
            jobs=SqlIngestionJobRepository(session),
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
            uow=session,
        )

    @staticmethod
    def _manifest_for(episode: Episode, raw_asset_ids: tuple[UUID, ...]) -> dict[str, object]:
        return {
            "schema_version": "ddp.canonical/1.0.0-draft",
            "episode_id": str(episode.episode_id),
            "episode_lineage_id": str(episode.episode_lineage_id),
            "episode_version": episode.episode_version,
            "project_id": str(episode.project_id),
            "code": episode.code,
            "task_id": None,
            "batch_id": None,
            "robot_id": None,
            "device_ids": [],
            "sensor_ids": [],
            "start_time": str(episode.start_time_ns),
            "end_time": str(episode.end_time_ns),
            "duration_ns": str(episode.duration_ns),
            "time_base": dict(episode.time_base),
            "time_alignment": {"method": "PTP"},
            "calibration_snapshot": [dict(item) for item in episode.calibration_snapshot],
            "streams": [],
            "raw_asset_ids": [str(item) for item in raw_asset_ids],
            "provenance": {"pipeline_id": episode.pipeline_id},
            "quality": {"status": "PASS", "rule_version": "q1", "issues": []},
            "frame_count": 1,
        }

    def episode_application_checks(self) -> None:
        project_a = self.ids["project_a"]
        user_a = self.ids["user_a"]
        scope = ProjectScope(project_a, actor_id=user_a)

        with self.factory() as session:
            asset = session.scalar(
                select(RawAssetModel).where(
                    RawAssetModel.project_id == project_a,
                    RawAssetModel.status == "VALID",
                )
            )
            if asset is None:
                asset = self._asset_row()
                session.add(asset)
                session.commit()
                session.execute(
                    update(RawAssetModel)
                    .where(RawAssetModel.id == asset.id)
                    .values(status="VALID")
                )
                session.commit()
            asset_id = asset.id
        self.record("EPI-ASSET-VALID", "PASS", f"复用真实 PG 中 VALID 资产 {asset_id}")

        calibration = (
            {
                "calibration_id": str(uuid4()),
                "calibration_version": 1,
                "checksum": SHA_A,
                "target_type": "SENSOR",
                "target_id": str(uuid4()),
                "schema_version": "ddp.device.calibration/1.0.0",
            },
        )
        time_base = {
            "time_domain": "UTC",
            "clock_source": "PTP",
            "sync_method": "PTP",
            "sync_status": "VERIFIED",
        }
        segmentation = Segmentation(
            method=SegmentationMethod.MANUAL_TIME_RANGE,
            rule_version="seg-manual-v1",
            start_time_ns=1_000_000,
            end_time_ns=2_000_000,
            allow_overlap=False,
        )
        with self.factory() as session:
            service = self._episode_service(session)
            created = service.create(
                scope,
                CreateEpisodeCommand(
                    raw_asset_ids=(asset_id,),
                    segmentation=segmentation,
                    time_base=time_base,
                    calibration_snapshot=calibration,
                    canonical_schema_version="ddp.canonical/1.0.0-draft",
                    pipeline_id="parser",
                    pipeline_version="1.0.0",
                    code=f"EP-QA-{uuid4().hex[:12]}",
                ),
            )
            assert created.status.value == "PENDING"
            self.episode_created = created
            self.record(
                "EPI-CREATE",
                "PASS",
                f"Episode {created.episode_id} 创建成功，status=PENDING，血缘节点/边已写入",
            )

        with self.factory() as session:
            service = self._episode_service(session)
            processing = service.apply_action(
                scope,
                self.episode_created.episode_id,
                EpisodeActionCommand(action="system-start", expected_version=1),
            )
            assert processing.status.value == "PROCESSING"
            manifest = self._manifest_for(processing, (asset_id,))
            manifest_key = build_manifest_key(project_a, processing.episode_id, 1)
            ready = service.mark_ready(
                scope,
                processing.episode_id,
                MarkEpisodeReadyCommand(
                    expected_version=processing.version,
                    manifest_key=manifest_key,
                    manifest=manifest,
                    frame_count=600,
                ),
            )
            assert ready.status.value == "READY"
            expected_hash = compute_manifest_sha256(manifest)
            assert ready.manifest_sha256 == expected_hash, "Manifest Hash 不一致"
            assert_manifest_hash(manifest, ready.manifest_sha256)
            self.episode_ready = ready
            self.record(
                "EPI-READY",
                "PASS",
                f"READY 成功，manifest_sha256={ready.manifest_sha256} 与服务端重算一致",
            )
            try:
                assert_manifest_hash(manifest, "sha256:" + "0" * 64)
            except Exception as exc:
                assert type(exc).__name__ == "ManifestHashMismatchError", type(exc).__name__
                self.record(
                    "EPI-MANIFEST-HASH",
                    "PASS",
                    f"错误 Manifest Hash 被拒绝：{type(exc).__name__}",
                )
            else:
                self.record("EPI-MANIFEST-HASH", "FAIL", "错误 Manifest Hash 未被拒绝")

        with self.factory() as session:
            service = self._episode_service(session)
            tampered = dict(manifest)
            tampered["quality"] = {"status": "FAIL"}
            try:
                service.mark_ready(
                    scope,
                    self.episode_created.episode_id,
                    MarkEpisodeReadyCommand(
                        expected_version=self.episode_ready.version,
                        manifest_key=manifest_key,
                        manifest=tampered,
                        frame_count=1,
                    ),
                )
            except Exception as exc:  # expected: 状态已 READY，非法迁移
                self.record(
                    "EPI-READY-RESUBMIT",
                    "PASS",
                    f"READY 后再次提交不同 Manifest 被拒绝：{type(exc).__name__}: {exc}",
                )
            else:
                self.record("EPI-READY-RESUBMIT", "FAIL", "READY 后不同 Manifest 未被拒绝")

        with self.factory() as session:
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(EpisodeModel)
                    .where(EpisodeModel.id == self.episode_created.episode_id)
                    .values(manifest_key=manifest_key + ".tampered")
                ),
                "episode_data_immutable",
            )
            self.record("EPI-READY-FREEZE", "PASS", detail)

        with self.factory() as session:
            service = self._episode_service(session)
            checking = service.apply_action(
                scope,
                self.episode_created.episode_id,
                EpisodeActionCommand(
                    action="quality-check",
                    expected_version=self.episode_ready.version,
                ),
            )
            assert checking.status.value == "QUALITY_CHECKING"
            try:
                service.apply_action(
                    scope,
                    self.episode_created.episode_id,
                    EpisodeActionCommand(action="accept", expected_version=checking.version),
                )
            except EpisodeValidationError as exc:
                self.record("EPI-QGATE-01", "PASS", f"NOT_CHECKED 时 accept 被拒绝：{exc}")
            else:
                self.record("EPI-QGATE-01", "FAIL", "NOT_CHECKED 时 accept 未被拒绝")

            failed = service.apply_action(
                scope,
                self.episode_created.episode_id,
                EpisodeActionCommand(
                    action="record-quality",
                    expected_version=checking.version,
                    quality_status=EpisodeQualityStatus.FAIL.value,
                ),
            )
            try:
                service.apply_action(
                    scope,
                    self.episode_created.episode_id,
                    EpisodeActionCommand(action="accept", expected_version=failed.version),
                )
            except EpisodeValidationError as exc:
                self.record("EPI-QGATE-02", "PASS", f"质检 FAIL 时 accept 被拒绝：{exc}")
            else:
                self.record("EPI-QGATE-02", "FAIL", "质检 FAIL 时 accept 未被拒绝")

            passed = service.apply_action(
                scope,
                self.episode_created.episode_id,
                EpisodeActionCommand(
                    action="record-quality",
                    expected_version=failed.version,
                    quality_status=EpisodeQualityStatus.PASS.value,
                ),
            )
            accepted = service.apply_action(
                scope,
                self.episode_created.episode_id,
                EpisodeActionCommand(action="accept", expected_version=passed.version),
            )
            assert accepted.status.value == "ACCEPTED"
            self.episode_accepted = accepted
            self.record("EPI-QGATE-03", "PASS", "质检 PASS 后 accept 成功，status=ACCEPTED")

        with self.factory() as session:
            detail = self.expect_db_error(
                session,
                lambda: session.execute(
                    update(EpisodeModel)
                    .where(EpisodeModel.id == self.episode_created.episode_id)
                    .values(quality_status="FAIL")
                ),
                "episode_quality_immutable",
            )
            self.record("EPI-QGATE-FREEZE", "PASS", detail)

        with self.factory() as session:
            service = self._episode_service(session)
            revised = service.revise(
                scope,
                self.episode_created.episode_id,
                ReviseEpisodeCommand(
                    code=f"EP-QA-REV-{uuid4().hex[:8]}",
                    expected_version=self.episode_accepted.version,
                ),
            )
            assert revised.episode_id != self.episode_created.episode_id
            assert revised.episode_version == 2
            assert revised.supersedes_id == self.episode_created.episode_id
            old = SqlEpisodeRepository(session).get(scope, self.episode_created.episode_id)
            assert old is not None and old.status.value == "ACCEPTED", "旧版本被修改"
            lineage = SqlLineageRepository(session)
            edges = lineage.list_edges_from(
                scope,
                node_type=LineageNodeType.EPISODE,
                node_id=revised.episode_id,
            )
            assert any(
                edge.relation is LineageRelation.SUPERSEDES
                and edge.to_node_id == self.episode_created.episode_id
                for edge in edges
            ), "修订未写入 SUPERSEDES 边"
            self.episode_revised = revised
            self.record(
                "EPI-REVISE",
                "PASS",
                f"修订新 ID {revised.episode_id}，version={revised.episode_version}，"
                f"supersedes_id={revised.supersedes_id}，旧版保持 ACCEPTED",
            )

        with self.factory() as session:
            lineage = SqlLineageRepository(session)
            forward = lineage.list_edges_from(
                scope,
                node_type=LineageNodeType.RAW_ASSET,
                node_id=asset_id,
            )
            reverse = lineage.list_edges_to(
                scope,
                node_type=LineageNodeType.EPISODE,
                node_id=self.episode_created.episode_id,
            )
            assert any(edge.relation is LineageRelation.SEGMENTED_INTO for edge in forward)
            assert any(edge.from_node_id == asset_id for edge in reverse)
            self.record(
                "EPI-LIN-DIR",
                "PASS",
                f"正向 {len(forward)} 条（含 SEGMENTED_INTO），反向 {len(reverse)} 条（含来源资产）",
            )

        with self.factory() as session:
            scope_b = ProjectScope(self.ids["project_b"], actor_id=self.ids["user_b"])
            assert SqlEpisodeRepository(session).get(scope_b, self.episode_created.episode_id) is None
            assert not SqlLineageRepository(session).list_edges_from(
                scope_b,
                node_type=LineageNodeType.EPISODE,
                node_id=self.episode_created.episode_id,
            )
            self.record("EPI-XPROJ", "PASS", "项目 B 作用域读取项目 A Episode/血缘均为空")

        with self.factory() as session:
            audits = session.scalars(
                select(AuditLog).where(
                    AuditLog.project_id == project_a,
                    AuditLog.resource_type == "EPISODE",
                    AuditLog.resource_id == self.episode_created.episode_id,
                )
            ).all()
            events = session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.project_id == project_a,
                    OutboxEvent.event_type.like("ingestion.episode.%"),
                )
            ).all()
            assert audits, "Episode 审计缺失"
            assert events, "Episode outbox 缺失"
            self.record(
                "EPI-AUDIT-OUTBOX",
                "PASS",
                f"Episode 审计 {len(audits)} 条、outbox {len(events)} 条，"
                f"动作={sorted({item.action for item in audits})}",
            )

    # ---------------------------------------------------------------- 主流程
    def run(self) -> None:
        self.run_migrations()
        self.trigger_inventory()
        self.seed()
        self.collection_trigger_checks()
        self.upload_trigger_checks()
        self.audit_outbox_checks()
        self.episode_trigger_checks()
        self.episode_application_checks()


def create_database() -> tuple[str, str]:
    dbname = f"ddp_qa_308_{uuid4().hex[:8]}"
    engine = create_engine(PG_ADMIN, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    engine.dispose()
    return dbname, PG_TEMPLATE.format(dbname=dbname)


def drop_database(dbname: str) -> None:
    engine = create_engine(PG_ADMIN, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(
            text(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where datname=:name and pid <> pg_backend_pid()"
            ),
            {"name": dbname},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
    engine.dispose()


def write_evidence(probe: Probe, dbname: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    payload = {
        "task": "DDP-308",
        "kind": "真实 PostgreSQL 16 数据库与 Episode 应用层探针",
        "executed_at": datetime.now(UTC).isoformat(),
        "postgres": {
            "admin_url": PG_ADMIN.replace(":ddp@", ":***@"),
            "database": dbname,
            "url": probe.url.replace(":ddp@", ":***@"),
        },
        "alembic_head": probe.version(),
        "summary": {
            "PASS": sum(1 for item in probe.results if item["status"] == "PASS"),
            "FAIL": sum(1 for item in probe.results if item["status"] == "FAIL"),
            "BLOCKED": sum(1 for item in probe.results if item["status"] == "BLOCKED"),
            "PARTIAL": sum(1 for item in probe.results if item["status"] == "PARTIAL"),
        },
        "results": probe.results,
    }
    (EVIDENCE / "db-probe.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (EVIDENCE / "db-probe.log").write_text("\n".join(probe.log_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-db", action="store_true", help="保留临时验收数据库")
    args = parser.parse_args()
    logging.getLogger("alembic").setLevel(logging.WARNING)
    dbname, url = create_database()
    probe = Probe(dbname, url)
    failed = False
    try:
        print(f"临时验收数据库：{dbname}")
        probe.run()
        failed = any(item["status"] == "FAIL" for item in probe.results)
    except Exception as exc:  # noqa: BLE001 - 顶层失败也必须落证据
        probe.record("PROBE-CRASH", "FAIL", f"{type(exc).__name__}: {exc}")
        probe.log_lines.append(traceback.format_exc())
        failed = True
    finally:
        write_evidence(probe, dbname)
        print(f"证据已写入 {EVIDENCE}")
        probe.engine.dispose()
        if not args.keep_db:
            drop_database(dbname)
            print(f"已清理临时数据库 {dbname}")
        else:
            print(f"按要求保留临时数据库 {dbname}")
    summary = {
        status: sum(1 for item in probe.results if item["status"] == status)
        for status in ("PASS", "FAIL", "BLOCKED", "PARTIAL")
    }
    print(f"汇总：{summary}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
