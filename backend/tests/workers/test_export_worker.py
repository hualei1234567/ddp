"""Export Worker 测试：失败、部分失败、重试、超时与失效版本。"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.logging import JsonFormatter, trace_id_var
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from app.infrastructure.db.models.datasets import DatasetVersionModel
from app.infrastructure.repositories.export_jobs import SqlExportJobRepository
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import url
from tests.api.export_support import (
    FakeExportStorage,
    build_frozen_version,
    export_payload,
    run_export_worker,
)


def _create(
    factory: sessionmaker[Session],
    ids: ApiIds,
    version_id: str,
    key: str,
    **overrides: object,
) -> tuple[object, dict[str, object]]:
    client = make_client(factory, ids.editor_a, object_storage=overrides.pop("storage", None))
    response = client.post(
        url(ids.project_a, "/exports"),
        json=export_payload(version_id, **overrides),
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    return client, response.json()


def _detail(client, project_id: UUID, job_id: str) -> dict[str, object]:
    response = client.get(url(project_id, f"/exports/{job_id}"))
    assert response.status_code == 200, response.text
    return response.json()


def test_export_failure_then_retry_succeeds(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    storage = FakeExportStorage()
    storage.fail_next_put(after_successes=0)
    client, job = _create(
        factory,
        ids,
        ctx["version_id"],
        "export-fail-retry",
        storage=storage,
    )
    result = run_export_worker(factory, storage)
    assert result.status == "PUBLISHED"

    failed = _detail(client, ids.project_a, job["export_job_id"])
    assert failed["status"] == "FAILED"
    assert failed["attempt"] == 1
    assert failed["error_code"] == "export_job_failed"
    assert failed["finished_at"]

    retried = client.post(
        url(ids.project_a, f"/exports/{job['export_job_id']}/actions/retry"),
        headers={"If-Match": f'"{failed["version"]}"'},
        json={},
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["status"] == "QUEUED"
    assert retried.json()["attempt"] == 2

    result = run_export_worker(factory, storage)
    assert result.status == "PUBLISHED"
    succeeded = _detail(client, ids.project_a, job["export_job_id"])
    assert succeeded["status"] == "SUCCEEDED"
    assert succeeded["attempt"] == 2
    assert succeeded["artifact_count"] == 18  # 6 DDP 自有 + tasks/stats + 10 episode parquet


def test_export_not_retryable_after_max_attempts(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    storage = FakeExportStorage()
    storage.fail_next_put(after_successes=0)
    client, job = _create(
        factory,
        ids,
        ctx["version_id"],
        "export-max-attempts",
        storage=storage,
        max_attempts=1,
    )
    assert run_export_worker(factory, storage).status == "PUBLISHED"
    failed = _detail(client, ids.project_a, job["export_job_id"])
    retry = client.post(
        url(ids.project_a, f"/exports/{job['export_job_id']}/actions/retry"),
        headers={"If-Match": f'"{failed["version"]}"'},
        json={},
    )
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "export_job_not_retryable"


def test_export_partial_is_not_success_and_cannot_retry(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    storage = FakeExportStorage()
    storage.fail_next_put(after_successes=1)
    client, job = _create(
        factory,
        ids,
        ctx["version_id"],
        "export-partial-put",
        storage=storage,
    )
    assert run_export_worker(factory, storage).status == "PUBLISHED"

    partial = _detail(client, ids.project_a, job["export_job_id"])
    assert partial["status"] == "PARTIAL"
    assert partial["error_code"] == "export_partial"
    assert partial["artifact_count"] == 1

    manifest = client.get(url(ids.project_a, f"/exports/{job['export_job_id']}/artifacts"))
    assert manifest.status_code == 409
    assert manifest.json()["error"]["code"] == "export_incomplete"

    retry = client.post(
        url(ids.project_a, f"/exports/{job['export_job_id']}/actions/retry"),
        headers={"If-Match": f'"{partial["version"]}"'},
        json={},
    )
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "state_transition_invalid"

    # 再次运行 Worker 不得把 PARTIAL 升级为成功。
    assert run_export_worker(factory, storage).status == "IDLE"
    assert _detail(client, ids.project_a, job["export_job_id"])["status"] == "PARTIAL"


def test_export_checksum_failure_becomes_partial(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    storage = FakeExportStorage()
    storage.corrupt_next_read(after_successes=0)
    client, job = _create(
        factory,
        ids,
        ctx["version_id"],
        "export-partial-checksum",
        storage=storage,
    )
    assert run_export_worker(factory, storage).status == "PUBLISHED"
    partial = _detail(client, ids.project_a, job["export_job_id"])
    assert partial["status"] == "PARTIAL"
    assert partial["error_code"] == "export_artifact_verify_failed"
    assert partial["artifact_count"] == 1


def test_export_worker_timeout_and_stale_version(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)

    timeout_storage = FakeExportStorage()
    timeout_client, timeout_job = _create(
        factory,
        ids,
        ctx["version_id"],
        "export-timeout",
        storage=timeout_storage,
        timeout_seconds=1,
    )
    started_at = datetime.now(UTC)
    with factory() as session:
        scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)
        repo = SqlExportJobRepository(session)
        current = repo.get(scope, UUID(timeout_job["export_job_id"]))
        assert current is not None
        repo.save(scope, current.start(started_at), expected_version=current.row_version)
        session.commit()
    assert (
        run_export_worker(
            factory,
            timeout_storage,
            now=started_at + timedelta(seconds=30),
        ).status
        == "PUBLISHED"
    )
    timed_out = _detail(
        timeout_client,
        ids.project_a,
        timeout_job["export_job_id"],
    )
    assert timed_out["status"] == "FAILED"
    assert timed_out["error_code"] == "export_job_timeout"

    stale_storage = FakeExportStorage()
    stale_client, stale_job = _create(
        factory,
        ids,
        ctx["version_id"],
        "export-stale-version",
        storage=stale_storage,
    )
    with factory() as session:
        session.execute(
            update(DatasetVersionModel)
            .where(DatasetVersionModel.id == UUID(ctx["version_id"]))
            .values(validity_status="STALE")
        )
        session.commit()
    assert run_export_worker(factory, stale_storage).status == "PUBLISHED"
    stale = _detail(stale_client, ids.project_a, stale_job["export_job_id"])
    assert stale["status"] == "FAILED"
    assert stale["error_code"] == "dataset_upstream_invalidated"
    assert stale_storage.objects == {}


class _CollectingJsonHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.payloads.append(json.loads(self.format(record)))


def test_export_worker_reuses_request_id_from_event(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """HTTP request_id → 导出事件 → Worker 日志/审计，同一 ID 可检索。"""
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    storage = FakeExportStorage()
    client = make_client(factory, ids.editor_a)
    response = client.post(
        url(ids.project_a, "/exports"),
        json=export_payload(ctx["version_id"]),
        headers={
            "Idempotency-Key": "export-trace-id",
            "X-Request-Id": "req-export-1",
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["export_job_id"]

    handler = _CollectingJsonHandler()
    handler.setFormatter(JsonFormatter())
    worker_logger = logging.getLogger("ddp.worker.exports")
    worker_logger.addHandler(handler)
    try:
        result = run_export_worker(factory, storage)
    finally:
        worker_logger.removeHandler(handler)

    assert result.status == "PUBLISHED"
    with factory() as session:
        requested = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "dataset.export.requested")
        )
        assert requested is not None
        assert requested.payload["request_id"] == "req-export-1"
        audits = session.scalars(select(AuditLog).where(AuditLog.resource_id == UUID(job_id))).all()
        assert audits
        assert all(item.request_id == "req-export-1" for item in audits)

    assert handler.payloads, "Worker 必须留下可检索的结果日志"
    assert all(item["trace_id"] == "req-export-1" for item in handler.payloads)
    assert all(item["trace_source"] == "event" for item in handler.payloads)
    assert trace_id_var.get() is None


def test_export_worker_blocks_unknown_validity(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """执行期来源变 UNKNOWN 也要 fail-closed：不能写出任何产物。"""
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    storage = FakeExportStorage()
    client, job = _create(
        factory,
        ids,
        ctx["version_id"],
        "export-unknown-version",
        storage=storage,
    )
    with factory() as session:
        session.execute(
            update(DatasetVersionModel)
            .where(DatasetVersionModel.id == UUID(ctx["version_id"]))
            .values(validity_status="UNKNOWN")
        )
        session.commit()

    assert run_export_worker(factory, storage).status == "PUBLISHED"
    blocked = _detail(client, ids.project_a, job["export_job_id"])
    assert blocked["status"] == "FAILED"
    assert blocked["error_code"] == "dataset_upstream_invalidated"
    assert storage.objects == {}
