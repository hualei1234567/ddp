"""Episode 列表的多维检索条件。

检索条件只有在 SQL 层下推才有意义：把条件带回答题再在内存里筛，
会让分页游标失真（取回 N 条、过滤后剩 0 条）。因此本模块只负责
**把请求参数归一成合法值**并给出稳定语义，不承担任何取数逻辑。

语义约定（与 ``SelectionFilters`` 的候选门禁保持一致）：

- ``scene_labels``：任一命中即可（Episode 的 ``scene_labels`` 与条件求交集非空）；
- ``device_id``：Episode 的 ``device_ids`` 必须包含该设备；
- ``camera``：任一关联原始资产的来源事实 ``raw_metadata.camera`` 精确命中即可；
  机位标识是导入链写入的 Canonical 事实（``import_runner._import_metadata``），
  与自动就绪判定同源。上传类资产没有该事实，因此不会命中任何机位条件；
- ``captured_from`` / ``captured_to``：闭区间，按 Episode 所关联原始资产
  ``captured_at`` 的**最早值**判定；无捕获时间的 Episode 不满足任何时间条件。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.domain.episodes.enums import EpisodeAnnotationStatus, EpisodeQualityStatus
from app.domain.episodes.errors import DomainValidationError

_MAX_SCENE_LABELS = 20
_MAX_SCENE_LABEL_LENGTH = 100
# 与 ``MediaAsset.camera`` 的列宽一致（``app/domain/datasources/media.py`` 的
# ``CAMERA_MAX``）。这里不复用那个常量：Episode 检索只依赖已落库事实，
# 不应把数据源域的实现细节带进检索语义。
_MAX_CAMERA_LENGTH = 64


def _enum(value: object, enum_type: type, field_name: str) -> object:
    if value is None:
        return None
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)  # type: ignore[attr-defined]
        raise DomainValidationError(f"{field_name} 取值非法，允许值：{allowed}") from exc


def _aware(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{field_name} 必须是带时区的 datetime")
    return value.astimezone(UTC)


def _labels(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items: tuple[object, ...] = (value,)
    elif isinstance(value, list | tuple):
        items = tuple(value)
    else:
        raise DomainValidationError("scene_label 必须是字符串列表")
    result: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise DomainValidationError("scene_label 必须是字符串列表")
        text = item.strip()
        if not text:
            raise DomainValidationError("scene_label 不得为空串")
        if len(text) > _MAX_SCENE_LABEL_LENGTH:
            raise DomainValidationError(
                f"scene_label 长度不得超过 {_MAX_SCENE_LABEL_LENGTH} 个字符"
            )
        if text not in result:
            result.append(text)
    if len(result) > _MAX_SCENE_LABELS:
        raise DomainValidationError(f"scene_label 最多 {_MAX_SCENE_LABELS} 个不同取值")
    return tuple(result)


def _uuid(value: object, field_name: str) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise DomainValidationError(f"{field_name} 必须是 UUID") from exc
    raise DomainValidationError(f"{field_name} 必须是 UUID")


def _camera(value: object) -> str | None:
    """机位标识归一：去空白、拒绝空串与超长；不做大小写折叠。

    camera 是外部数据集声明的机位名（如 ``main`` / ``wrist``），大小写属于
    标识本身，静默改写会让筛选条件永远查不到数据。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise DomainValidationError("camera 必须是字符串")
    text = value.strip()
    if not text:
        raise DomainValidationError("camera 不得为空串")
    if len(text) > _MAX_CAMERA_LENGTH:
        raise DomainValidationError(f"camera 长度不得超过 {_MAX_CAMERA_LENGTH} 个字符")
    return text


@dataclass(frozen=True, slots=True)
class EpisodeListFilters:
    """Episode 列表的多维过滤条件；全部为空表示不做额外限制。"""

    quality_status: EpisodeQualityStatus | str | None = None
    annotation_status: EpisodeAnnotationStatus | str | None = None
    scene_labels: tuple[str, ...] | list[str] | str | None = None
    device_id: UUID | str | None = None
    robot_id: UUID | str | None = None
    camera: str | None = None
    captured_from: datetime | None = None
    captured_to: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "quality_status",
            _enum(self.quality_status, EpisodeQualityStatus, "quality_status"),
        )
        object.__setattr__(
            self,
            "annotation_status",
            _enum(self.annotation_status, EpisodeAnnotationStatus, "annotation_status"),
        )
        object.__setattr__(self, "scene_labels", _labels(self.scene_labels))
        object.__setattr__(self, "device_id", _uuid(self.device_id, "device_id"))
        object.__setattr__(self, "robot_id", _uuid(self.robot_id, "robot_id"))
        object.__setattr__(self, "camera", _camera(self.camera))
        captured_from = _aware(self.captured_from, "captured_from")
        captured_to = _aware(self.captured_to, "captured_to")
        object.__setattr__(self, "captured_from", captured_from)
        object.__setattr__(self, "captured_to", captured_to)
        if captured_from is not None and captured_to is not None and captured_to < captured_from:
            raise DomainValidationError("captured_to 不得早于 captured_from")

    @property
    def is_empty(self) -> bool:
        """是否没有任何额外条件（用于调用方跳过无意义的谓词拼接）。"""
        return all(
            value is None or value == ()
            for value in (
                self.quality_status,
                self.annotation_status,
                self.scene_labels,
                self.device_id,
                self.robot_id,
                self.camera,
                self.captured_from,
                self.captured_to,
            )
        )
