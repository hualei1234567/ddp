"""导入产出的媒体资产：一个相机的一段视频在对象存储中的引用。

数据库只存元数据、引用、Hash 与状态；文件本体在对象存储（`CLAUDE.md`「数据与安全」）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.datasources.errors import DomainValidationError

CAMERA_MAX = 64
PATH_MAX = 1024
TASK_MAX = 200


@dataclass(frozen=True, slots=True)
class MediaAsset:
    asset_id: UUID
    project_id: UUID
    data_source_id: UUID
    import_id: UUID
    episode_key: str
    path: str
    camera: str
    object_key: str
    size_bytes: int
    sha256: str
    content_type: str
    task_name: str
    created_at: datetime
    excerpt_start_seconds: float | None = None
    excerpt_seconds: float | None = None
    source_fps: float | None = None

    def __post_init__(self) -> None:
        for name, limit in (
            ("episode_key", PATH_MAX),
            ("path", PATH_MAX),
            ("camera", CAMERA_MAX),
            ("object_key", PATH_MAX),
            ("content_type", 128),
            ("task_name", TASK_MAX),
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(f"{name} 不能为空")
            if len(value) > limit:
                raise DomainValidationError(f"{name} 超过 {limit} 字符")
        if self.size_bytes <= 0:
            raise DomainValidationError("size_bytes 必须大于 0")
        # Hash 必须带算法前缀且长度固定：解析器写错也能立刻发现。
        if not self.sha256.startswith("sha256:") or len(self.sha256) != 71:
            raise DomainValidationError("sha256 必须是 'sha256:' + 64 位十六进制")
        for name in ("excerpt_start_seconds", "excerpt_seconds", "source_fps"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise DomainValidationError(f"{name} 不能为负")
