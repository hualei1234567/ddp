"""DatasetVersion、条目、预览、报告与 Manifest HTTP 路由。"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_project_context
from app.api.v1.dataset_dependencies import (
    ActionName,
    DatasetUseCasesDep,
    ExpectedVersion,
    IdempotencyKey,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.dataset_schemas import (
    DatasetVersionCreate,
    SelectionPreviewRequest,
    VersionActionRequest,
    item_out,
    leakage_out,
    manifest_out,
    preview_out,
    quality_out,
    version_out,
)
from app.application.datasets.commands import (
    ActionCommand,
    LeakageWaiverCommand,
    NewDatasetVersion,
    PreviewSelection,
)
from app.core.errors import ForbiddenError
from app.domain.datasets.state_machines import allowed_version_actions
from app.domain.identity.models import ProjectContext

router = APIRouter(prefix="/projects/{project_id}/datasets", tags=["dataset-versions"])


@router.get("/{dataset_id}/versions")
def list_versions(
    dataset_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
    status: str | None = None,
    validity_status: str | None = None,
    leakage_status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = use_cases.versions.list(
        scope,
        dataset_id,
        status=status,
        validity_status=validity_status,
        leakage_status=leakage_status,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.dataset_version_id)
    return {
        "items": [
            version_out(item, allowed_actions=allowed_version_actions(item.status))
            for item in page.items
        ],
        "next_cursor": page.next_cursor,
    }


@router.post("/{dataset_id}/versions", status_code=201)
def create_version(
    dataset_id: UUID,
    payload: DatasetVersionCreate,
    request: Request,
    scope: WriteScope,
    use_cases: DatasetUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    version = use_cases.versions.create(
        scope,
        dataset_id,
        NewDatasetVersion(
            selection_rule_version=payload.selection_rule_version,
            selection_filters=payload.selection_filters,
            dedup_policy_id=payload.dedup_policy_id,
            dedup_policy_version=payload.dedup_policy_version,
            split_policy_id=payload.split_policy_id,
            split_policy_version=payload.split_policy_version,
            leakage_policy_id=payload.leakage_policy_id,
            leakage_policy_version=payload.leakage_policy_version,
            quality_policy_id=payload.quality_policy_id,
            quality_policy_version=payload.quality_policy_version,
            supersedes_version_id=payload.supersedes_version_id,
            build_now=payload.build_now,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return version_out(version, allowed_actions=allowed_version_actions(version.status))


@router.get("/{dataset_id}/versions/{version_id}")
def get_version(
    dataset_id: UUID,
    version_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
) -> dict[str, Any]:
    version = use_cases.versions.get(scope, dataset_id, version_id)
    return version_out(version, allowed_actions=allowed_version_actions(version.status))


@router.post("/{dataset_id}/versions/{version_id}/actions/{action}")
def act_on_version(
    dataset_id: UUID,
    version_id: UUID,
    action: ActionName,
    request: Request,
    scope: WriteScope,
    use_cases: DatasetUseCasesDep,
    context: Annotated[ProjectContext, Depends(get_project_context)],
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: VersionActionRequest | None = None,
) -> dict[str, Any]:
    if action == "archive" and not (
        context.is_platform_admin or context.role is not None and context.role.value == "owner"
    ):
        raise ForbiddenError("归档要求项目负责人权限")
    body = payload or VersionActionRequest()
    version = use_cases.versions.apply_action(
        scope,
        dataset_id,
        version_id,
        action,
        expected_version=expected_version,
        reason=body.reason,
        command=ActionCommand(
            reason=body.reason,
            comment=body.comment,
            evidence_refs=tuple(body.evidence_refs),
            payload=dict(body.payload or {}),
        ),
        waivers=tuple(
            LeakageWaiverCommand(
                rule=item.rule,
                reason=item.reason,
                impact=item.impact,
                expires_at=item.expires_at,
            )
            for item in body.waivers
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return version_out(version, allowed_actions=allowed_version_actions(version.status))


@router.post("/{dataset_id}/versions/{version_id}/selection-preview")
def preview_selection(
    dataset_id: UUID,
    version_id: UUID,
    payload: SelectionPreviewRequest,
    request: Request,
    scope: WriteScope,
    use_cases: DatasetUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    preview = use_cases.versions.preview(
        scope,
        dataset_id,
        version_id,
        PreviewSelection(
            selection_filters=payload.selection_filters,
            dedup_policy_id=payload.dedup_policy_id,
            dedup_policy_version=payload.dedup_policy_version,
            split_policy_id=payload.split_policy_id,
            split_policy_version=payload.split_policy_version,
            leakage_policy_id=payload.leakage_policy_id,
            leakage_policy_version=payload.leakage_policy_version,
            quality_policy_id=payload.quality_policy_id,
            quality_policy_version=payload.quality_policy_version,
            sample_limit=payload.sample_limit,
        ),
        request_id=request_id(request),
    )
    return preview_out(preview)


@router.get("/{dataset_id}/versions/{version_id}/items")
def list_items(
    dataset_id: UUID,
    version_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
    split: str | None = None,
    sample_role: str | None = None,
    episode_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = use_cases.versions.list_items(
        scope,
        dataset_id,
        version_id,
        split=split,
        sample_role=sample_role,
        episode_id=episode_id,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.dataset_item_id)
    return {
        "items": [item_out(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.get("/{dataset_id}/versions/{version_id}/items/{item_id}")
def get_item(
    dataset_id: UUID,
    version_id: UUID,
    item_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
) -> dict[str, Any]:
    return item_out(use_cases.versions.get_item(scope, dataset_id, version_id, item_id))


@router.get("/{dataset_id}/versions/{version_id}/quality-report")
def get_quality_report(
    dataset_id: UUID,
    version_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
) -> dict[str, Any]:
    return quality_out(use_cases.versions.quality_report(scope, dataset_id, version_id))


@router.get("/{dataset_id}/versions/{version_id}/leakage-report")
def get_leakage_report(
    dataset_id: UUID,
    version_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
) -> dict[str, Any]:
    return leakage_out(use_cases.versions.leakage_report(scope, dataset_id, version_id))


@router.get("/{dataset_id}/versions/{version_id}/manifest")
def get_manifest(
    dataset_id: UUID,
    version_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
    inline: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    manifest = use_cases.versions.manifest(scope, dataset_id, version_id)
    # 当前阶段对象存储适配未接入：始终内联返回，并明确 hash 供客户端校验。
    return manifest_out(manifest, inline=inline, download_url=None)
