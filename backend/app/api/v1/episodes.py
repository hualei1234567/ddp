"""Episode HTTP 路由。

路由只做协议适配：路径/请求体/If-Match/Idempotency-Key → 应用用例，
再把领域实体序列化为契约响应。权限与项目隔离由服务端依赖强制执行。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import get_project_scope, get_write_scope
from app.api.v1.episode_dependencies import EpisodeUseCasesDep
from app.api.v1.episode_schemas import (
    EPISODE_ACTION_MAP,
    EpisodeActionRequest,
    EpisodeCreate,
    EpisodeOut,
    EpisodeRevisionCreate,
)
from app.api.v1.governance_dependencies import EnforceEpisodeCreateQuota
from app.api.v1.preconditions import require_expected_version, require_idempotency_key
from app.api.v1.schemas import PageOut
from app.application.episodes import (
    CreateEpisodeCommand,
    EpisodeActionCommand,
    ReviseEpisodeCommand,
)
from app.core.errors import ValidationFailedError
from app.domain.episodes.filters import EpisodeListFilters
from app.domain.identity.models import ProjectScope

router = APIRouter(prefix="/projects/{project_id}", tags=["episodes"])

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
EpisodeAction = Literal[
    "ready",
    "quality-check",
    "quality-result",
    "accept",
    "reject",
    "complete",
    "archive",
    "invalidate",
]

# 对外动作名 → 应用层具名动作；映射本身定义在 DTO 层，响应里的
# allowed_actions 与这里接受的动作必须来自同一份表。
_ACTION_MAP = {key: value for key, value in EPISODE_ACTION_MAP.items() if key != "ready"}


@router.post("/episodes", response_model=EpisodeOut, status_code=201)
def create_episode(
    payload: EpisodeCreate,
    request: Request,
    response: Response,
    scope: WriteScope,
    use_cases: EpisodeUseCasesDep,
    _quota_guard: EnforceEpisodeCreateQuota,
    idempotency_key: IdempotencyKey,
) -> EpisodeOut:
    episode = use_cases.episodes.create(
        scope,
        CreateEpisodeCommand(
            raw_asset_ids=tuple(payload.raw_asset_ids),
            segmentation=payload.segmentation.to_domain(),
            time_base=payload.time_base,
            calibration_snapshot=tuple(payload.calibration_snapshot),
            canonical_schema_version=payload.canonical_schema_version,
            pipeline_id=payload.pipeline_id,
            pipeline_version=payload.pipeline_version,
            code=payload.code,
            task_id=payload.task_id,
            batch_id=payload.batch_id,
            robot_id=payload.robot_id,
            device_ids=tuple(payload.device_ids),
            sensor_ids=tuple(payload.sensor_ids),
            scene_labels=tuple(payload.scene_labels),
            language_instruction=payload.language_instruction,
            success=payload.success,
            failure_reason=payload.failure_reason,
            ingestion_job_ids=tuple(payload.ingestion_job_ids),
            evidence_role=payload.evidence_role,
        ),
        request_id=_request_id(request),
        http_idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
    )
    response.headers["Idempotent-Replayed"] = (
        "true" if use_cases.episodes.last_replayed else "false"
    )
    response.headers["ETag"] = f'"{episode.version}"'
    return EpisodeOut.from_domain(episode)


@router.get("/episodes", response_model=PageOut[EpisodeOut])
def list_episodes(
    scope: ReadScope,
    use_cases: EpisodeUseCasesDep,
    status: str | None = None,
    task_id: UUID | None = None,
    batch_id: UUID | None = None,
    quality_status: str | None = None,
    annotation_status: str | None = None,
    scene_label: Annotated[list[str] | None, Query(max_length=20)] = None,
    device_id: UUID | None = None,
    robot_id: UUID | None = None,
    camera: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    captured_from: Annotated[datetime | None, Query()] = None,
    captured_to: Annotated[datetime | None, Query()] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[EpisodeOut]:
    """Episode 分页检索。

    ``scene_label`` 可重复传参（``?scene_label=kitchen&scene_label=street``），
    语义是"任一命中"。``camera`` 按 Episode 关联原始资产的机位标识精确匹配。
    其余条件为等值或闭区间。所有条件在仓储层下推为 SQL，不在 HTTP 层取回后过滤。
    """
    page = use_cases.episodes.list_episodes(
        scope,
        status=status,
        task_id=task_id,
        batch_id=batch_id,
        filters=EpisodeListFilters(
            quality_status=quality_status,
            annotation_status=annotation_status,
            scene_labels=tuple(scene_label) if scene_label else None,
            device_id=device_id,
            robot_id=robot_id,
            camera=camera,
            captured_from=captured_from,
            captured_to=captured_to,
        ),
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[EpisodeOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/episodes/{episode_id}", response_model=EpisodeOut)
def get_episode(
    episode_id: UUID,
    response: Response,
    scope: ReadScope,
    use_cases: EpisodeUseCasesDep,
) -> EpisodeOut:
    episode = use_cases.episodes.get_episode(scope, episode_id)
    response.headers["ETag"] = f'"{episode.version}"'
    return EpisodeOut.from_domain(episode)


@router.post("/episodes/{episode_id}/actions/{action}", response_model=EpisodeOut)
def act_on_episode(
    episode_id: UUID,
    action: EpisodeAction,
    request: Request,
    response: Response,
    scope: WriteScope,
    use_cases: EpisodeUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    payload: EpisodeActionRequest | None = None,
) -> EpisodeOut:
    body = payload or EpisodeActionRequest()
    if action == "ready":
        if body.manifest is None or body.frame_count is None:
            raise ValidationFailedError("ready 动作必须提供 manifest 与 frame_count")
        if body.quality_status is not None:
            raise ValidationFailedError("ready 动作不接受 quality_status")
        episode = use_cases.episodes.ready_from_manifest(
            scope,
            episode_id,
            expected_version=expected_version,
            manifest=body.manifest,
            frame_count=body.frame_count,
            request_id=_request_id(request),
            http_idempotency_key=idempotency_key,
            method=request.method,
            path=request.url.path,
        )
    else:
        if body.manifest is not None or body.frame_count is not None:
            raise ValidationFailedError("只有 ready 动作接受 manifest/frame_count")
        episode = use_cases.episodes.apply_action(
            scope,
            episode_id,
            EpisodeActionCommand(
                action=_ACTION_MAP[action],
                expected_version=expected_version,
                reason=body.reason,
                quality_status=body.quality_status,
            ),
            request_id=_request_id(request),
            http_idempotency_key=idempotency_key,
            method=request.method,
            path=request.url.path,
        )
    response.headers["Idempotent-Replayed"] = (
        "true" if use_cases.episodes.last_replayed else "false"
    )
    response.headers["ETag"] = f'"{episode.version}"'
    return EpisodeOut.from_domain(episode)


@router.post(
    "/episodes/{episode_id}/revisions",
    response_model=EpisodeOut,
    status_code=201,
)
def revise_episode(
    episode_id: UUID,
    payload: EpisodeRevisionCreate,
    request: Request,
    response: Response,
    scope: WriteScope,
    use_cases: EpisodeUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
) -> EpisodeOut:
    episode = use_cases.episodes.revise(
        scope,
        episode_id,
        ReviseEpisodeCommand(
            code=payload.code,
            expected_version=expected_version,
        ),
        request_id=_request_id(request),
        http_idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
    )
    response.headers["Idempotent-Replayed"] = (
        "true" if use_cases.episodes.last_replayed else "false"
    )
    response.headers["ETag"] = f'"{episode.version}"'
    return EpisodeOut.from_domain(episode)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None
