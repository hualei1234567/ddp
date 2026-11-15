"""IAA 用例：从真实提交重叠构建观测、计算指标并落库。

本用例不读取黄金集；仅使用状态为 SUBMITTED/APPROVED 的标注记录。计算可由
Worker 调用，HTTP 线程只负责入队与查询。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    AnnotationRecordRepository,
    AnnotationSchemaRepository,
    AnnotationSubmissionRepository,
    AnnotationTaskRepository,
    AuditRepository,
    IAAReportRecord,
    IAAReportRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.annotation.enums import (
    AnnotationSchemaStatus,
    IAAReportAction,
    IAAReportStatus,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    IAAInsufficientOverlapError,
    ResourceNotFoundError,
    SchemaSnapshotMismatchError,
)
from app.domain.annotation.iaa import IAAObservation, Span, compute_iaa
from app.domain.annotation.schema import compute_schema_sha256
from app.domain.annotation.state_machines import assert_iaa_report_action
from app.domain.identity.models import ProjectScope

_RESOURCE_TYPE = "IAA_REPORT"
_METRIC_SOURCE_STATUSES = ("SUBMITTED", "APPROVED")


class IAAUseCases:
    def __init__(
        self,
        reports: IAAReportRepository,
        records: AnnotationRecordRepository,
        submissions: AnnotationSubmissionRepository,
        tasks: AnnotationTaskRepository,
        schemas: AnnotationSchemaRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
        idempotency: IdempotencyRepository | None = None,
    ) -> None:
        self._reports = reports
        self._records = records
        self._submissions = submissions
        self._tasks = tasks
        self._schemas = schemas
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency) if idempotency is not None else None
        self._uow = uow

    def get(self, scope: ProjectScope, report_id: UUID) -> IAAReportRecord:
        report = self._reports.get(scope, report_id)
        if report is None:
            raise ResourceNotFoundError("IAA 报告", report_id)
        return report

    def compute(
        self,
        scope: ProjectScope,
        task_id: UUID,
        *,
        metric_definitions_version: str,
        iou_threshold: float = 0.5,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str = "",
        request_id: str | None = None,
    ) -> IAAReportRecord:
        """计算任务 IAA；样本不足结果是 INSUFFICIENT_DATA，而不是 0。"""
        key = validate_key(idempotency_key)
        body: dict[str, object] = {
            "task_id": str(task_id),
            "metric_definitions_version": metric_definitions_version,
            "iou_threshold": iou_threshold,
        }
        now = _utcnow()
        if self._idempotency is not None:
            replay = self._idempotency.lookup(
                scope,
                key,
                method=method,
                path=path,
                body=body,
                now=now,
            )
            if replay is not None:
                return self.get(scope, _as_uuid(replay.response_ref, "IAA 报告"))
        task = self._tasks.get(scope, task_id)
        if task is None:
            raise ResourceNotFoundError("标注任务", task_id)
        schema = self._schemas.get(scope, task.schema_id)
        if schema is None:
            raise ResourceNotFoundError("标注 Schema", task.schema_id)
        if schema.status is not AnnotationSchemaStatus.PUBLISHED:
            raise SchemaSnapshotMismatchError("IAA 计算要求 Schema 仍为已发布快照")
        if (
            schema.schema_version != task.schema_version
            or compute_schema_sha256(schema) != task.schema_sha256
        ):
            raise SchemaSnapshotMismatchError("任务 Schema 快照不可复算")
        observations = [
            _to_observation(item.record, item.annotator_id)
            for item in self._records.list_for_task(
                scope,
                task_id=task_id,
                statuses=_METRIC_SOURCE_STATUSES,
            )
        ]
        result = compute_iaa(
            observations,
            iou_threshold=iou_threshold,
            metric_definitions_version=metric_definitions_version,
        )
        now = _utcnow()
        report = IAAReportRecord(
            report_id=uuid4(),
            project_id=scope.project_id,
            task_id=task_id,
            schema_id=schema.schema_id,
            schema_version=schema.schema_version,
            status=IAAReportStatus.QUEUED.value,
            metric_definitions_version=metric_definitions_version,
            sample_item_count=result.sample_item_count,
            overlap_item_count=result.overlap_item_count,
            annotator_count=result.annotator_count,
            metrics=tuple(item.to_payload() for item in result.metrics),
            excluded_items=tuple(dict(item) for item in result.excluded_items),
            computed_at=None,
            version=1,
        )
        stored = self._reports.add(scope, report)
        running = replace(
            stored,
            status=assert_iaa_report_action(
                IAAReportStatus(stored.status),
                IAAReportAction.WORKER_START.value,
            ).value,
        )
        stored = self._reports.save(scope, running, expected_version=stored.version)
        terminal_status = (
            IAAReportStatus.SUCCEEDED if result.is_sufficient else IAAReportStatus.INSUFFICIENT_DATA
        )
        terminal_action = (
            IAAReportAction.WORKER_SUCCEED
            if result.is_sufficient
            else IAAReportAction.WORKER_INSUFFICIENT
        )
        final = replace(
            stored,
            status=assert_iaa_report_action(
                IAAReportStatus(stored.status),
                terminal_action.value,
            ).value,
            computed_at=now,
        )
        final = self._reports.save(scope, final, expected_version=stored.version)
        if task.iaa_report_id != final.report_id:
            self._tasks.save(
                scope,
                replace(
                    task,
                    iaa_report_id=final.report_id,
                    updated_at=now,
                    updated_by=scope.actor_id,
                ),
                expected_version=task.version,
            )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=final.report_id,
            action=terminal_action.value,
            from_status=IAAReportStatus.RUNNING.value,
            to_status=terminal_status.value,
            reason=None if result.is_sufficient else "重叠样本不足",
            request_id=request_id,
            details={
                "task_id": str(task_id),
                "overlap_item_count": result.overlap_item_count,
                "annotator_count": result.annotator_count,
                "metric_count": len(result.metrics),
            },
            event_type="annotation.iaa_report.status_changed",
            event_payload={
                "iaa_report_id": str(final.report_id),
                "task_id": str(task_id),
                "status": terminal_status.value,
                "overlap_item_count": result.overlap_item_count,
                "annotator_count": result.annotator_count,
            },
            occurred_at=now,
        )
        if self._idempotency is not None:
            self._idempotency.store(
                scope,
                key,
                method=method,
                path=path,
                body=body,
                response_status=201,
                response_ref=str(final.report_id),
                now=now,
            )
        self._uow.commit()
        return final

    def list_for_task(
        self,
        scope: ProjectScope,
        task_id: UUID,
        *,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[IAAReportRecord]:
        return self._reports.list_for_task(
            scope,
            task_id=task_id,
            after_id=after_id,
            limit=limit,
        )

    def require_sufficient(self, report: IAAReportRecord) -> IAAReportRecord:
        if report.status == IAAReportStatus.INSUFFICIENT_DATA.value:
            raise IAAInsufficientOverlapError("重叠样本不足，无法给出 IAA 结论")
        return report


def _to_observation(record, annotator_id: UUID) -> IAAObservation:
    values: dict[str, object] = {}
    spans: list[Span] = []
    for label in record.labels:
        values[label.label_key] = label.value
        if isinstance(label.value, Mapping):
            start = label.value.get("start_time_ns")
            end = label.value.get("end_time_ns")
            if isinstance(start, int) and isinstance(end, int) and start < end:
                spans.append(Span(label.label_key, start, end))
    return IAAObservation(
        episode_id=record.episode_id,
        annotator_id=annotator_id,
        label_values=values,
        spans=tuple(spans),
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_uuid(value: object, resource: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise DomainValidationError(f"{resource} ID 非法") from exc
    raise DomainValidationError(f"{resource} ID 非法")


__all__ = ["IAAUseCases"]
