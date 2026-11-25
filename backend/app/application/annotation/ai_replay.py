"""AI 建议的幂等重放批次重建与身份解析（从 ``ai_suggestions.py`` 分出）。

分出理由：该文件贴着 500 行评审线，而"按身份重建批次"是自洽的纯函数
职责——输入请求载荷与建议列表，输出首次创建的那一批。它与状态机、
权限、记账都没有共享状态。

身份函数（``identity_of`` / ``uuid_value``）从用例模块挪到这里作为唯一
权威实现：落库前查重、幂等重放、批次重建三处引用同一份定义，不复制。
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from app.domain.annotation.ai_suggestion import AiSuggestion
from app.domain.annotation.errors import DomainValidationError, ResourceNotFoundError

REPLAY_PREFIX = "submission:"


def identity_of(
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


def uuid_value(value: object, field_name: str) -> UUID:
    """载荷里的 UUID 只接受真正的 UUID 或 UUID 字符串，其余显式失败。"""
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise DomainValidationError(f"{field_name} 不是合法 UUID") from exc
    raise DomainValidationError(f"{field_name} 必须是 UUID")


def uuid_ref(response_ref: str | None, resource: str) -> UUID:
    if response_ref is None:
        raise ResourceNotFoundError(resource, response_ref)
    return uuid_value(response_ref, resource)


def submission_ref(response_ref: str | None) -> UUID:
    if response_ref is None or not response_ref.startswith(REPLAY_PREFIX):
        raise ResourceNotFoundError("标注提交", response_ref)
    return uuid_value(response_ref.removeprefix(REPLAY_PREFIX), "标注提交")


def replay_batch(
    scope: object,
    *,
    list_for_submission: object,
    submission_id: UUID,
    payloads: tuple[Mapping[str, object], ...],
) -> list[AiSuggestion]:
    """按**请求体里的建议身份**重建首次创建的批次。

    幂等键作用域是 ``(project, actor, key)``，重放时请求体已被
    ``request_fingerprint`` 校验为与首次一致，因此可据此确定身份集合。
    结果按身份集合过滤，而不是按提交取全量——后者会把后续用别的幂等键
    追加的建议一并返回（同一提交多模型对比是设计上支持的用法）。

    身份缺失时抛 404 而不是返回空列表：说明幂等记录指向的建议已被清理，
    静默返回空会让客户端误以为"登记成功但一条都没有"。

    ``list_for_submission`` 是仓储的查询方法（依赖注入而非 import），
    读取范围仍由调用方的 ``scope`` 决定。
    """
    payload_identities = [
        identity_of(
            uuid_value(item.get("annotation_id"), "annotation_id"),
            item.get("label_key"),
            item.get("model_id"),
            item.get("model_version"),
            item.get("prompt_version"),
        )
        for item in payloads
    ]
    by_identity = {
        identity_of(
            suggestion.annotation_id,
            suggestion.label_key,
            suggestion.model_id,
            suggestion.model_version,
            suggestion.prompt_version,
        ): suggestion
        for suggestion in list_for_submission(scope, submission_id)  # type: ignore[operator]
    }
    # 按请求体顺序输出，与首次响应的顺序一致；仓储的排序键是数据库列序，
    # 不是请求顺序，直接复用会让重放响应与首次在多项时对不上。
    try:
        return [by_identity[identity] for identity in payload_identities]
    except KeyError as exc:
        raise ResourceNotFoundError("AI 建议", submission_id) from exc


__all__ = [
    "REPLAY_PREFIX",
    "identity_of",
    "replay_batch",
    "submission_ref",
    "uuid_ref",
    "uuid_value",
]
