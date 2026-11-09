"""模型产物与运行环境值对象。"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.modelops.enums import ArtifactFormat, ArtifactVerification
from app.domain.modelops.errors import (
    ModelArtifactInvalidError,
    ModelRuntimeInvalidError,
)
from app.domain.modelops.validation import (
    coerce_enum,
    optional_text,
    sha256_value,
    text_value,
)


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    object_key: str
    sha256: str
    size_bytes: int
    format: str
    verification_status: str = ArtifactVerification.UNVERIFIED

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "object_key",
            text_value(self.object_key, "artifact.object_key", max_length=1024),
        )
        object.__setattr__(self, "sha256", sha256_value(self.sha256, "artifact.sha256"))
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 1
        ):
            raise ModelArtifactInvalidError("artifact.size_bytes 必须是正整数")
        object.__setattr__(
            self, "format", coerce_enum(self.format, ArtifactFormat, "artifact.format").value
        )
        object.__setattr__(
            self,
            "verification_status",
            coerce_enum(
                self.verification_status,
                ArtifactVerification,
                "artifact.verification_status",
            ).value,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "object_key": self.object_key,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "format": self.format,
            "verification_status": self.verification_status,
        }

    @classmethod
    def from_payload(cls, value: object) -> ModelArtifact:
        if not isinstance(value, dict):
            raise ModelArtifactInvalidError("artifact 必须是对象")
        return cls(
            object_key=str(value.get("object_key", "")),
            sha256=str(value.get("sha256", "")),
            size_bytes=value.get("size_bytes"),  # type: ignore[arg-type]
            format=str(value.get("format", "")),
            verification_status=str(
                value.get("verification_status", ArtifactVerification.UNVERIFIED.value)
            ),
        )


@dataclass(frozen=True, slots=True)
class ModelRuntime:
    framework: str
    framework_version: str
    python_version: str | None = None
    cuda_version: str | None = None
    image_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "framework", text_value(self.framework, "runtime.framework", max_length=100)
        )
        object.__setattr__(
            self,
            "framework_version",
            text_value(self.framework_version, "runtime.framework_version", max_length=100),
        )
        object.__setattr__(
            self,
            "python_version",
            optional_text(self.python_version, "runtime.python_version", max_length=32),
        )
        object.__setattr__(
            self,
            "cuda_version",
            optional_text(self.cuda_version, "runtime.cuda_version", max_length=32),
        )
        object.__setattr__(
            self,
            "image_ref",
            optional_text(self.image_ref, "runtime.image_ref", max_length=512),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "framework": self.framework,
            "framework_version": self.framework_version,
            "python_version": self.python_version,
            "cuda_version": self.cuda_version,
            "image_ref": self.image_ref,
        }

    @classmethod
    def from_payload(cls, value: object) -> ModelRuntime:
        if not isinstance(value, dict):
            raise ModelRuntimeInvalidError("runtime 必须是对象")
        return cls(
            framework=str(value.get("framework", "")),
            framework_version=str(value.get("framework_version", "")),
            python_version=value.get("python_version"),  # type: ignore[arg-type]
            cuda_version=value.get("cuda_version"),  # type: ignore[arg-type]
            image_ref=value.get("image_ref"),  # type: ignore[arg-type]
        )
