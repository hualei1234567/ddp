#!/usr/bin/env python3
"""DDP-V21-N8-007 前端 E2E 种子：为三条关键路径准备确定性数据。

**只写本机临时环境**：SQLite 临时文件 + 本机回环 MinIO 桶。脚本拒绝任何
非 loopback 数据库/对象存储地址，避免误连共享环境。

产出的固定 UUID 供 Playwright 用例引用：

- 项目 ``11111111-...``、提交人 ``88888888-...``、审核人 ``99999999-...``；
- 采集任务 ``22222222-...``（DRAFT 起步，脚本随后经真实 HTTP 动作推进到
  ``IN_PROGRESS``，因为上传入口只在该状态开放）；
- 数据源 ``33333333-...``、导入 ``44444444-...``、审核记录 ``55555555-...``。

**为什么用真实 HTTP 推进任务状态**：``collection_tasks`` 有数据库触发器
（``trg_collection_tasks_initial_status`` / ``trg_collection_tasks_transition``），
直接 UPDATE 状态会被拒绝。走 API 既满足守卫，也顺带验证了状态机本身。

用法（由 ``scripts/e2e/run-e2e.sh`` 调用）::

    DDP_DATABASE_URL=sqlite+pysqlite:////tmp/x.db \\
    DDP_E2E_API_BASE_URL=http://127.0.0.1:8010 \\
    backend/.venv/bin/python apps/web/scripts/e2e/seed_e2e.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

PROJECT_ID = UUID("11111111-1111-7111-8111-111111111111")
SUBMITTER_ID = UUID("88888888-8888-7888-8888-888888888888")
REVIEWER_ID = UUID("99999999-9999-7999-8999-999999999999")
TASK_ID = UUID("22222222-2222-7222-8222-222222222222")
DATA_SOURCE_ID = UUID("33333333-3333-7333-8333-333333333333")
IMPORT_ID = UUID("44444444-4444-7444-8444-444444444444")
REVIEW_ID = UUID("55555555-5555-7555-8555-555555555555")

PROJECT_CODE = "ddp-e2e-n8-007"
PROJECT_NAME = "DDP E2E 关键路径项目"
TASK_CODE = "E2E-TASK-001"
TASK_NAME = "E2E 采集任务（上传与动作条）"
DATA_SOURCE_CODE = "e2e-hf-source"
REVISION = "0" * 40

_LOOPBACK_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "::1", "postgres", "minio", "redis", "api"}
)


def _is_local_endpoint(value: str) -> bool:
    host = value
    if "://" in value:
        host = urlparse(value).hostname or ""
    elif "@" in value:
        host = value.rsplit("@", 1)[1].split(":")[0].split("/")[0]
    else:
        host = value.split(":")[0].split("/")[0]
    host = host.strip().lower()
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _database_url() -> str:
    value = os.environ.get("DDP_DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("缺少 DDP_DATABASE_URL")

    prefix = "sqlite+pysqlite:///"
    database = value.removeprefix(prefix) if value.startswith(prefix) else ""
    database_path = Path(database)
    temp_roots = (Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve())
    is_temporary_sqlite = (
        value.startswith(prefix)
        and database != ":memory:"
        and database_path.is_absolute()
        and any(database_path.resolve().is_relative_to(root) for root in temp_roots)
    )
    if not is_temporary_sqlite:
        raise SystemExit("拒绝使用非临时 E2E 数据库（仅允许临时目录中的 SQLite 文件）")
    return value


def _api_base_url() -> str:
    value = os.environ.get("DDP_E2E_API_BASE_URL", "").strip()
    if not value:
        raise SystemExit("缺少 DDP_E2E_API_BASE_URL")
    if not _is_local_endpoint(value):
        raise SystemExit("拒绝使用非本机后端（该脚本只用于本地 E2E）")
    return value.rstrip("/")


def _seed_identity(session: Session) -> None:
    from sqlalchemy import select

    from app.infrastructure.db.models.identity import Project, ProjectMember, User

    if session.get(Project, PROJECT_ID) is None:
        session.add(Project(id=PROJECT_ID, code=PROJECT_CODE, name=PROJECT_NAME))
    for user_id, name in (
        (SUBMITTER_ID, "E2E 提交人"),
        (REVIEWER_ID, "E2E 审核人"),
    ):
        if session.get(User, user_id) is None:
            session.add(User(id=user_id, display_name=name))
    session.flush()
    for user_id in (SUBMITTER_ID, REVIEWER_ID):
        exists = session.scalar(
            select(ProjectMember.id).where(
                ProjectMember.project_id == PROJECT_ID,
                ProjectMember.user_id == user_id,
            )
        )
        if exists is None:
            session.add(
                ProjectMember(
                    project_id=PROJECT_ID,
                    user_id=user_id,
                    role="owner",
                )
            )


def _seed_task(session: Session) -> None:
    from app.infrastructure.db.models.collection import CollectionTask

    if session.get(CollectionTask, TASK_ID) is not None:
        return
    # 初始状态必须是 DRAFT（数据库触发器强制），后续经真实 HTTP 动作推进。
    session.add(
        CollectionTask(
            id=TASK_ID,
            project_id=PROJECT_ID,
            code=TASK_CODE,
            name=TASK_NAME,
            status="DRAFT",
            priority="P1",
            target_episode_count=10,
            target_duration_sec=600,
            created_by=SUBMITTER_ID,
            updated_by=SUBMITTER_ID,
        )
    )


def _seed_datasource(session: Session) -> None:
    from app.infrastructure.db.models.datasources import (
        DataSourceImportModel,
        DataSourceModel,
    )

    now = datetime.now(UTC)
    if session.get(DataSourceModel, DATA_SOURCE_ID) is None:
        session.add(
            DataSourceModel(
                id=DATA_SOURCE_ID,
                project_id=PROJECT_ID,
                code=DATA_SOURCE_CODE,
                name="E2E 公开数据集",
                kind="HUGGINGFACE",
                repo_id="lerobot/e2e_sample",
                revision=REVISION,
                license="Apache-2.0",
                access_condition="PUBLIC",
                is_gated=False,
                metadata_json={},
                created_by=SUBMITTER_ID,
                updated_by=SUBMITTER_ID,
                created_at=now,
                updated_at=now,
            )
        )
    if session.get(DataSourceImportModel, IMPORT_ID) is None:
        session.add(
            DataSourceImportModel(
                id=IMPORT_ID,
                project_id=PROJECT_ID,
                data_source_id=DATA_SOURCE_ID,
                status="PENDING",
                scope="SAMPLE",
                sample_limit=1,
                tool_name="e2e-import",
                tool_version="1.0.0",
                created_by=SUBMITTER_ID,
                updated_by=SUBMITTER_ID,
                created_at=now,
                updated_at=now,
            )
        )


def _seed_review(session: Session) -> None:
    from app.infrastructure.db.models.datasources import DataReviewModel

    if session.get(DataReviewModel, REVIEW_ID) is not None:
        return

    # 提交人 = SUBMITTER；E2E 用 REVIEWER 身份裁决，满足职责分离。
    session.add(
        DataReviewModel(
            id=REVIEW_ID,
            project_id=PROJECT_ID,
            import_id=IMPORT_ID,
            status="PENDING",
            decision=None,
            reason=None,
            reviewer_id=None,
            submitted_by=SUBMITTER_ID,
            created_by=SUBMITTER_ID,
            updated_by=SUBMITTER_ID,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )


def _advance_task_to_in_progress(api_base_url: str) -> None:
    """经真实 HTTP 动作把任务推进到 IN_PROGRESS（上传入口只在该状态开放）。

    每一步都带 ``If-Match`` 与 ``Idempotency-Key``；``approve`` 由审核人
    执行，避免"创建人审批自己的任务"被服务端拒绝。
    """
    import httpx

    base = f"{api_base_url}/api/v1/projects/{PROJECT_ID}/collection-tasks/{TASK_ID}"
    headers = {"X-User-Id": str(SUBMITTER_ID)}
    reviewer_headers = {"X-User-Id": str(REVIEWER_ID)}

    with httpx.Client(timeout=30.0) as client:
        detail = client.get(base, headers=headers)
        detail.raise_for_status()
        version = detail.json()["version"]
        if detail.json()["status"] == "IN_PROGRESS":
            return

        def act(
            action: str, *, actor_headers: dict[str, str], body: dict | None = None
        ) -> None:
            nonlocal version
            response = client.post(
                f"{base}/actions/{action}",
                headers={
                    **actor_headers,
                    "If-Match": f'"{version}"',
                    "Idempotency-Key": f"e2e-{action}-{version}",
                },
                json=body or {},
            )
            response.raise_for_status()
            version = response.json()["version"]

        act("submit-review", actor_headers=headers)
        act("approve", actor_headers=reviewer_headers)
        act("ready", actor_headers=headers)
        act(
            "assign",
            actor_headers=headers,
            body={"assigned_to": str(SUBMITTER_ID)},
        )
        act("start", actor_headers=headers)


def main() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    database_url = _database_url()
    api_base_url = _api_base_url()
    engine = create_engine(database_url)
    with Session(engine) as session:
        _seed_identity(session)
        _seed_task(session)
        _seed_datasource(session)
        _seed_review(session)
        session.commit()

    _advance_task_to_in_progress(api_base_url)

    print(
        json.dumps(
            {
                "project_id": str(PROJECT_ID),
                "submitter_id": str(SUBMITTER_ID),
                "reviewer_id": str(REVIEWER_ID),
                "task_id": str(TASK_ID),
                "data_source_id": str(DATA_SOURCE_ID),
                "import_id": str(IMPORT_ID),
                "review_id": str(REVIEW_ID),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - 种子失败必须让启动脚本显式失败
        print(f"E2E 种子失败：{exc}", file=sys.stderr)
        raise
