"""Dataset、策略与状态动作 HTTP 路由。"""

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
    DatasetActionRequest,
    DatasetCreate,
    DatasetPatch,
    DedupPolicyCreate,
    LeakagePolicyCreate,
    QualityPolicyCreate,
    SplitPolicyCreate,
    dataset_out,
    policy_out,
)
from app.application.datasets.commands import (
    NewDataset,
    NewDedupPolicy,
    NewLeakagePolicy,
    NewQualityPolicy,
    NewSplitPolicy,
)
from app.core.errors import ForbiddenError
from app.domain.datasets.state_machines import allowed_dataset_actions
from app.domain.identity.models import ProjectContext

router = APIRouter(prefix="/projects/{project_id}/datasets", tags=["datasets"])


@router.get("")
def list_datasets(
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
    status: str | None = None,
    purpose: str | None = None,
    usage_policy: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = use_cases.datasets.list(
        scope,
        status=status,
        purpose=purpose,
        usage_policy=usage_policy,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.dataset_id)
    return {
        "items": [
            dataset_out(item, allowed_actions=allowed_dataset_actions(item.status))
            for item in page.items
        ],
        "next_cursor": page.next_cursor,
    }


@router.post("", status_code=201)
def create_dataset(
    payload: DatasetCreate,
    request: Request,
    scope: WriteScope,
    use_cases: DatasetUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    dataset = use_cases.datasets.create(
        scope,
        NewDataset(
            code=payload.code,
            name=payload.name,
            purpose=payload.purpose,
            owner_id=payload.owner_id,
            usage_policy=payload.usage_policy,
            description=payload.description,
            tags=tuple(payload.tags),
            retention_policy=payload.retention_policy,
            default_dedup_policy_id=payload.default_dedup_policy_id,
            default_split_policy_id=payload.default_split_policy_id,
            default_leakage_policy_id=payload.default_leakage_policy_id,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return dataset_out(dataset, allowed_actions=allowed_dataset_actions(dataset.status))


@router.get("/{dataset_id}")
def get_dataset(
    dataset_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
) -> dict[str, Any]:
    dataset = use_cases.datasets.get(scope, dataset_id)
    return dataset_out(dataset, allowed_actions=allowed_dataset_actions(dataset.status))


@router.patch("/{dataset_id}")
def update_dataset(
    dataset_id: UUID,
    payload: DatasetPatch,
    request: Request,
    scope: WriteScope,
    use_cases: DatasetUseCasesDep,
    expected_version: ExpectedVersion,
) -> dict[str, Any]:
    dataset = use_cases.datasets.update(
        scope,
        dataset_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
        request_id=request_id(request),
    )
    return dataset_out(dataset, allowed_actions=allowed_dataset_actions(dataset.status))


@router.post("/{dataset_id}/actions/{action}")
def act_on_dataset(
    dataset_id: UUID,
    action: ActionName,
    request: Request,
    scope: WriteScope,
    use_cases: DatasetUseCasesDep,
    context: Annotated[ProjectContext, Depends(get_project_context)],
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: DatasetActionRequest | None = None,
) -> dict[str, Any]:
    if action in {"archive", "restore"} and not (
        context.is_platform_admin or context.role is not None and context.role.value == "owner"
    ):
        raise ForbiddenError("归档/恢复要求项目负责人权限")
    dataset = use_cases.datasets.apply_action(
        scope,
        dataset_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return dataset_out(dataset, allowed_actions=allowed_dataset_actions(dataset.status))


def _policy_page(items: list[Any], limit: int, id_attr: str) -> dict[str, Any]:
    page = build_page(items, limit=limit, id_of=lambda item: getattr(item, id_attr))
    return {
        "items": [policy_out(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.get("/{dataset_id}/dedup-policies")
def list_dedup_policies(
    dataset_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = use_cases.policies.list_dedup(scope, dataset_id, limit=fetch_limit(limit))
    return _policy_page(items, limit, "dedup_policy_id")


@router.post("/{dataset_id}/dedup-policies", status_code=201)
def create_dedup_policy(
    dataset_id: UUID,
    payload: DedupPolicyCreate,
    request: Request,
    scope: WriteScope,
    use_cases: DatasetUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    policy = use_cases.policies.create_dedup(
        scope,
        dataset_id,
        NewDedupPolicy(
            exact_keys=tuple(payload.exact_keys),
            near_duplicate_enabled=payload.near_duplicate_enabled,
            near_duplicate_enforcement=payload.near_duplicate_enforcement,
            retain_rule=payload.retain_rule,
            name=payload.name,
            similarity_method=payload.similarity_method,
            similarity_threshold=payload.similarity_threshold,
            similarity_min_cluster_size=payload.similarity_min_cluster_size,
            grouping_keys=tuple(payload.grouping_keys),
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return policy_out(policy)


@router.get("/{dataset_id}/dedup-policies/{policy_id}")
def get_dedup_policy(
    dataset_id: UUID,
    policy_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
) -> dict[str, Any]:
    return policy_out(use_cases.policies.get_dedup(scope, dataset_id, policy_id))


@router.get("/{dataset_id}/split-policies")
def list_split_policies(
    dataset_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = use_cases.policies.list_split(scope, dataset_id, limit=fetch_limit(limit))
    return _policy_page(items, limit, "split_policy_id")


@router.post("/{dataset_id}/split-policies", status_code=201)
def create_split_policy(
    dataset_id: UUID,
    payload: SplitPolicyCreate,
    request: Request,
    scope: WriteScope,
    use_cases: DatasetUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    policy = use_cases.policies.create_split(
        scope,
        dataset_id,
        NewSplitPolicy(
            strategy=payload.strategy,
            seed=payload.seed,
            group_by=tuple(payload.group_by),
            empty_split_policy=payload.empty_split_policy,
            min_item_count=payload.min_item_count,
            min_duration_ns=payload.min_duration_ns,
            rounding_rule=payload.rounding_rule,
            name=payload.name,
            train_ratio=payload.train_ratio,
            validation_ratio=payload.validation_ratio,
            test_ratio=payload.test_ratio,
            train_count=payload.train_count,
            validation_count=payload.validation_count,
            test_count=payload.test_count,
            stratify_by=tuple(payload.stratify_by),
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return policy_out(policy)


@router.get("/{dataset_id}/split-policies/{policy_id}")
def get_split_policy(
    dataset_id: UUID,
    policy_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
) -> dict[str, Any]:
    return policy_out(use_cases.policies.get_split(scope, dataset_id, policy_id))


@router.get("/{dataset_id}/leakage-policies")
def list_leakage_policies(
    dataset_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = use_cases.policies.list_leakage(scope, dataset_id, limit=fetch_limit(limit))
    return _policy_page(items, limit, "leakage_policy_id")


@router.post("/{dataset_id}/leakage-policies", status_code=201)
def create_leakage_policy(
    dataset_id: UUID,
    payload: LeakagePolicyCreate,
    request: Request,
    scope: WriteScope,
    use_cases: DatasetUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    policy = use_cases.policies.create_leakage(
        scope,
        dataset_id,
        NewLeakagePolicy(
            hard_rules=tuple(payload.hard_rules),
            soft_rules=tuple(payload.soft_rules),
            waiver_allowed_rules=tuple(payload.waiver_allowed_rules),
            name=payload.name,
            similarity_grouping_enabled=payload.similarity_grouping_enabled,
            scene_grouping_enabled=payload.scene_grouping_enabled,
            batch_grouping_enabled=payload.batch_grouping_enabled,
            temporal_overlap_enabled=payload.temporal_overlap_enabled,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return policy_out(policy)


@router.get("/{dataset_id}/leakage-policies/{policy_id}")
def get_leakage_policy(
    dataset_id: UUID,
    policy_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
) -> dict[str, Any]:
    return policy_out(use_cases.policies.get_leakage(scope, dataset_id, policy_id))


@router.get("/{dataset_id}/quality-policies")
def list_quality_policies(
    dataset_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = use_cases.policies.list_quality(scope, dataset_id, limit=fetch_limit(limit))
    return _policy_page(items, limit, "quality_policy_id")


@router.post("/{dataset_id}/quality-policies", status_code=201)
def create_quality_policy(
    dataset_id: UUID,
    payload: QualityPolicyCreate,
    request: Request,
    scope: WriteScope,
    use_cases: DatasetUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    policy = use_cases.policies.create_quality(
        scope,
        dataset_id,
        NewQualityPolicy(
            min_selected_count=payload.min_selected_count,
            allow_warn_quality=payload.allow_warn_quality,
            require_leakage_pass=payload.require_leakage_pass,
            name=payload.name,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return policy_out(policy)


@router.get("/{dataset_id}/quality-policies/{policy_id}")
def get_quality_policy(
    dataset_id: UUID,
    policy_id: UUID,
    scope: ReadScope,
    use_cases: DatasetUseCasesDep,
) -> dict[str, Any]:
    return policy_out(use_cases.policies.get_quality(scope, dataset_id, policy_id))
