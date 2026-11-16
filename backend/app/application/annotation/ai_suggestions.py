"""AI 预标注建议用例：登记、人工确认/修订、按提交查询。

## 边界

- **登记**（``record_suggestions``）：只写模型输出，不做任何人工判断。
  调用方必须是该提交的标注人（谁的标注，AI 辅助结果就归谁），
  或本就具备审阅权限的人（复核/仲裁场景下补录）。
- **确认**（``confirm``）：人工确认或修订一条建议。调用者必须是该提交的
  标注员，或具备复核权限（项目负责人 / 投过 REJECT 的复核人 / 本案仲裁人）。
  判定复用 :mod:`app.application.annotation.authority`，与返工要求同源。
- **查询**（``list_for_submission``）：沿用提交可见性规则（``DRAFT`` 仅作者
  本人可见），由领域函数 :func:`submission_visible_to` 决定，不另写一份判据。

写用例全部走 ``AuditRepository`` + ``OutboxRepository``：同事务写审计与事件，
任一失败即回滚（fail-closed）。事件是留痕类（``dispatch=False``）——
没有后台动作依赖它，去掉后系统行为不变。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.annotation.audit import record_change
from app.application.annotation.authority import resolve_review_authority
from app.application.annotation.commands import NewAiSuggestions
from app.application.annotation.idempotency import IdempotencyGuard, validate_key
from app.application.annotation.ports import (
    AnnotationAiSuggestionRepository,
    AnnotationArbitrationRepository,
    AnnotationRecordRepository,
    AnnotationReviewRepository,
    AnnotationSubmissionRepository,
    AuditRepository,
    IdempotencyRepository,
    MemberDirectory,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.annotation.ai_suggestion import AiSuggestion
from app.domain.annotation.errors import (
    DomainValidationError,
    ForbiddenOperationError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.annotation.submission import (
    AnnotationRecord,
    AnnotationSubmission,
    submission_visible_to,
)
from app.domain.identity.models import ProjectScope

_RESOURCE_TYPE = "ANNOTATION_AI_SUGGESTION"
_REPLAY_PREFIX = "submission:"

# 事件名必须是模块级常量：f-string 拼接会绕过 outbox 归类自检
# （见 scripts/check_outbox_events.py 记录的 R-13 根因）。
SUGGESTION_RECORDED_EVENT = "annotation.ai_suggestion.recorded"
SUGGESTION_CONFIRMED_EVENT = "annotation.ai_suggestion.confirmed"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AnnotationAiSuggestionUseCases:
    def __init__(
        self,
        suggestions: AnnotationAiSuggestionRepository,
        submissions: AnnotationSubmissionRepository,
        records: AnnotationRecordRepository,
        reviews: AnnotationReviewRepository,
        arbitrations: AnnotationArbitrationRepository,
        members: MemberDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._suggestions = suggestions
        self._submissions = submissions
        self._records = records
        self._reviews = reviews
        self._arbitrations = arbitrations
        self._members = members
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    # ------------------------------------------------------------------ 查询
    def get(self, scope: ProjectScope, suggestion_id: UUID) -> AiSuggestion:
        suggestion = self._suggestions.get(scope, suggestion_id)
        if suggestion is None:
            raise ResourceNotFoundError("AI 建议", suggestion_id)
        return suggestion

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> list[AiSuggestion]:
        """按提交列出全部建议；他人草稿对其不可见（与提交读取同规则）。"""
        submission = self._get_submission(scope, submission_id)
        if not submission_visible_to(
            submission.status,
            submission.annotator_id,
            scope.actor_id,
        ):
            raise ForbiddenOperationError("无权查看他人标注草稿的 AI 建议")
        return self._suggestions.list_for_submission(scope, submission_id)

    # ------------------------------------------------------------------ 登记
    def record_suggestions(
        self,
        scope: ProjectScope,
        submission_id: UUID,
        command: NewAiSuggestions,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> list[AiSuggestion]:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            # 重放必须返回**首次请求创建的那一批**，不能返回"该提交下的全部建议"：
            # 同一提交换模型/Prompt 版本会用不同幂等键再登记一批，若按提交取全量，
            # 重放第一次请求会把后来追加的建议一并返回，响应与首次不一致。
            return self._replay_batch(
                scope,
                _submission_ref(replay.response_ref),
                command.suggestions,
            )

        submission = self._get_submission(scope, submission_id)
        self._assert_can_annotate(scope, submission)
        records = {
            record.annotation_id: record
            for record in self._records.list_for_submission(scope, submission.submission_id)
        }
        suggestions = self._build_suggestions(
            scope,
            records=records,
            payloads=command.suggestions,
            now=now,
        )
        self._suggestions.add_many(scope, suggestions)
        for suggestion in suggestions:
            self._record(
                scope,
                suggestion,
                action="record",
                from_status=None,
                to_status="PENDING",
                request_id=request_id,
                now=now,
                event_type=SUGGESTION_RECORDED_EVENT,
            )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=f"{_REPLAY_PREFIX}{submission.submission_id}",
            now=now,
        )
        self._uow.commit()
        return list(suggestions)

    # ------------------------------------------------------------------ 确认
    def confirm(
        self,
        scope: ProjectScope,
        suggestion_id: UUID,
        *,
        expected_version: int,
        human_value: object,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> AiSuggestion:
        key = validate_key(idempotency_key)
        body = {"expected_version": expected_version, "human_value": human_value}
        now = _utcnow()
        replay = self._idempotency.lookup(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            now=now,
        )
        if replay is not None:
            return self.get(scope, _uuid_ref(replay.response_ref, "AI 建议"))

        current = self.get(scope, suggestion_id)
        if current.version != expected_version:
            raise VersionConflictError("AI 建议")
        self._assert_can_confirm(scope, self._submission_of(scope, current))
        updated = current.confirm(actor_id=scope.actor_id, human_value=human_value, now=now)
        saved = self._suggestions.save(scope, updated, expected_version=expected_version)
        self._record(
            scope,
            saved,
            action="confirm",
            from_status="PENDING",
            to_status="CONFIRMED",
            request_id=request_id,
            now=now,
            event_type=SUGGESTION_CONFIRMED_EVENT,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.suggestion_id),
            now=now,
        )
        self._uow.commit()
        return saved

    # ------------------------------------------------------------------ 权限
    def _assert_can_annotate(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
    ) -> None:
        """登记建议：该提交的标注人本人，或具备审阅权限的人。"""
        if submission.annotator_id == scope.actor_id:
            return
        if self._review_authority(scope, submission).has_review_authority:
            return
        raise ForbiddenOperationError(
            "只有该提交的标注人或复核人/仲裁人/项目负责人可以登记 AI 建议"
        )

    def _assert_can_confirm(
        self,
        scope: ProjectScope,
        submission: AnnotationSubmission,
    ) -> None:
        """人工确认/修订：该提交的标注员，或具备审阅权限的人。"""
        if submission.annotator_id == scope.actor_id:
            return
        if self._review_authority(scope, submission).has_review_authority:
            return
        raise ForbiddenOperationError(
            "只有该提交的标注员或复核人/仲裁人/项目负责人可以确认 AI 建议"
        )

    def _review_authority(self, scope: ProjectScope, submission: AnnotationSubmission):
        return resolve_review_authority(
            scope,
            submission,
            reviews=self._reviews,
            arbitrations=self._arbitrations,
            members=self._members,
        )

    # ------------------------------------------------------------------ 内部
    def _get_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> AnnotationSubmission:
        submission = self._submissions.get(scope, submission_id)
        if submission is None:
            raise ResourceNotFoundError("标注提交", submission_id)
        return submission

    def _submission_of(
        self,
        scope: ProjectScope,
        suggestion: AiSuggestion,
    ) -> AnnotationSubmission:
        """由建议反查提交：``annotation_records`` 是唯一权威来源。"""
        record = self._records.get(scope, suggestion.annotation_id)
        if record is None:
            raise ResourceNotFoundError("标注记录", suggestion.annotation_id)
        return self._get_submission(scope, record.submission_id)

    def _replay_batch(
        self,
        scope: ProjectScope,
        submission_id: UUID,
        payloads: tuple[Mapping[str, object], ...],
    ) -> list[AiSuggestion]:
        """按**请求体里的建议身份**重建首次创建的批次。

        幂等键作用域是 ``(project, actor, key)``，重放时请求体已被
        :func:`request_fingerprint` 校验为与首次一致，因此可据此确定身份集合。
        结果按身份集合过滤，而不是按提交取全量——后者会把后续用别的幂等键追加
        的建议一并返回（同一提交多模型对比是设计上支持的用法）。

        身份缺失时抛 404 而不是返回空列表：说明幂等记录指向的建议已被清理，
        静默返回空会让客户端误以为"登记成功但一条都没有"。
        """
        payload_identities = [
            _identity_of(
                _uuid_value(item.get("annotation_id"), "annotation_id"),
                item.get("label_key"),
                item.get("model_id"),
                item.get("model_version"),
                item.get("prompt_version"),
            )
            for item in payloads
        ]
        by_identity = {
            _identity_of(
                suggestion.annotation_id,
                suggestion.label_key,
                suggestion.model_id,
                suggestion.model_version,
                suggestion.prompt_version,
            ): suggestion
            for suggestion in self._suggestions.list_for_submission(scope, submission_id)
        }
        # 按请求体顺序输出，与首次响应的顺序一致；仓储的排序键是数据库列序，
        # 不是请求顺序，直接复用会让重放响应与首次在多项时对不上。
        try:
            return [by_identity[identity] for identity in payload_identities]
        except KeyError as exc:
            raise ResourceNotFoundError("AI 建议", submission_id) from exc

    def _build_suggestions(
        self,
        scope: ProjectScope,
        *,
        records: Mapping[UUID, AnnotationRecord],
        payloads: tuple[Mapping[str, object], ...],
        now: datetime,
    ) -> tuple[AiSuggestion, ...]:
        """把请求载荷转成实体；记录归属与批内重复在落库前就失败。

        归属校验必须在这里做：``annotation_id`` 由客户端给出，若不属于该提交，
        外键只保证"记录存在"，保证不了"记录属于这个提交"。
        """
        suggestions: list[AiSuggestion] = []
        seen: set[tuple[object, ...]] = set()
        for item in payloads:
            annotation_id = _uuid_value(item.get("annotation_id"), "annotation_id")
            if annotation_id not in records:
                raise ResourceNotFoundError("标注记录", annotation_id)
            suggestion = AiSuggestion(
                suggestion_id=uuid4(),
                project_id=scope.project_id,
                annotation_id=annotation_id,
                label_key=item.get("label_key"),
                suggested_value=item.get("suggested_value"),
                model_id=item.get("model_id"),
                model_version=item.get("model_version"),
                prompt_version=item.get("prompt_version"),
                confidence=item.get("confidence"),
                generated_at=item.get("generated_at") or now,
                created_at=now,
                updated_at=now,
                created_by=scope.actor_id,
                updated_by=scope.actor_id,
            )
            identity = _identity_of(
                suggestion.annotation_id,
                suggestion.label_key,
                suggestion.model_id,
                suggestion.model_version,
                suggestion.prompt_version,
            )
            if identity in seen:
                raise DomainValidationError("同一批次内不得重复登记同一记录/标签/模型版本的建议")
            seen.add(identity)
            suggestions.append(suggestion)
        return tuple(suggestions)

    def _record(
        self,
        scope: ProjectScope,
        suggestion: AiSuggestion,
        *,
        action: str,
        from_status: str | None,
        to_status: str,
        request_id: str | None,
        now: datetime,
        event_type: str,
    ) -> None:
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=suggestion.suggestion_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            reason=None,
            request_id=request_id,
            details={
                "annotation_id": str(suggestion.annotation_id),
                "label_key": suggestion.label_key,
                "model_id": suggestion.model_id,
                "model_version": suggestion.model_version,
                "prompt_version": suggestion.prompt_version,
            },
            event_type=event_type,
            event_payload={
                "suggestion_id": str(suggestion.suggestion_id),
                "annotation_id": str(suggestion.annotation_id),
                "label_key": suggestion.label_key,
                "model_id": suggestion.model_id,
                "model_version": suggestion.model_version,
                "prompt_version": suggestion.prompt_version,
                "human_confirmed": suggestion.human_confirmed,
            },
            occurred_at=now,
        )


def _identity_of(
    annotation_id: UUID,
    label_key: object,
    model_id: object,
    model_version: object,
    prompt_version: object,
) -> tuple[object, ...]:
    """建议的业务身份，与库表唯一约束 ``uq_annotation_ai_suggestions_identity`` 同序。

    身份只在这里拼一次：落库前查重与幂等重放都引用它，两处各自手写会漂移。
    """
    return (annotation_id, label_key, model_id, model_version, prompt_version)


def _uuid_value(value: object, field_name: str) -> UUID:
    """载荷里的 UUID 只接受真正的 UUID 或 UUID 字符串，其余显式失败。"""
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise DomainValidationError(f"{field_name} 不是合法 UUID") from exc
    raise DomainValidationError(f"{field_name} 必须是 UUID")


def _uuid_ref(response_ref: str | None, resource: str) -> UUID:
    if response_ref is None:
        raise ResourceNotFoundError(resource, response_ref)
    return _uuid_value(response_ref, resource)


def _submission_ref(response_ref: str | None) -> UUID:
    if response_ref is None or not response_ref.startswith(_REPLAY_PREFIX):
        raise ResourceNotFoundError("标注提交", response_ref)
    return _uuid_value(response_ref.removeprefix(_REPLAY_PREFIX), "标注提交")


__all__ = [
    "AnnotationAiSuggestionUseCases",
    "SUGGESTION_CONFIRMED_EVENT",
    "SUGGESTION_RECORDED_EVENT",
]
