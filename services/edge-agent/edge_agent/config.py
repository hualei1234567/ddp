"""Agent 配置：TOML / 映射加载 + 校验。所有路径与阈值都在这里集中声明并可覆盖。

默认值只是“配置默认”，部署时由配置文件覆盖；业务代码不得在别处写死阈值/路径。
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from edge_agent.errors import ConfigError
from edge_agent.preflight.models import CheckStatus


def _reject_unknown(section: str, data: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"配置段 [{section}] 含未知键：{sorted(unknown)}")


def _path(value: Any, where: str) -> Path:
    if not isinstance(value, str | Path) or not str(value):
        raise ConfigError(f"{where} 必须是非空路径")
    return Path(value).expanduser().resolve()


def _require(data: Mapping[str, Any], key: str, section: str) -> Any:
    if key not in data:
        raise ConfigError(f"配置段 [{section}] 缺少必填项 {key}")
    return data[key]


@dataclass(frozen=True)
class SpoolConfig:
    root: Path
    db_path: Path | None = None
    # 默认不删除 Spool 内的副本；开启后也只允许删除已拿到服务端回执的 UPLOADED 条目。
    allow_purge_uploaded: bool = False
    read_chunk_bytes: int = 1 << 20

    def __post_init__(self) -> None:
        if self.read_chunk_bytes <= 0:
            raise ConfigError("spool.read_chunk_bytes 必须 > 0")

    @property
    def state_db_path(self) -> Path:
        return self.db_path if self.db_path is not None else self.root / "state.sqlite3"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> SpoolConfig:
        _reject_unknown("spool", data, {f.name for f in fields(cls)})
        kwargs: dict[str, Any] = {"root": _path(_require(data, "root", "spool"), "spool.root")}
        if data.get("db_path") is not None:
            kwargs["db_path"] = _path(data["db_path"], "spool.db_path")
        for key in ("allow_purge_uploaded", "read_chunk_bytes"):
            if key in data:
                kwargs[key] = data[key]
        return cls(**kwargs)


@dataclass(frozen=True)
class ExportSource:
    name: str
    root: Path

    def __post_init__(self) -> None:
        if not self.name:
            raise ConfigError("adapter.sources[].name 不能为空")


@dataclass(frozen=True)
class AdapterConfig:
    sources: tuple[ExportSource, ...] = ()
    # 文件大小/mtime 在该窗口内无变化才视为稳定（避免读到写入中的文件）。
    stability_window_s: float = 30.0
    ignored_suffixes: tuple[str, ...] = (".tmp", ".part", ".partial", ".crdownload", ".swp", "~")
    sidecar_suffix: str = ".meta.json"
    follow_symlinks: bool = False

    def __post_init__(self) -> None:
        if self.stability_window_s < 0:
            raise ConfigError("adapter.stability_window_s 必须 >= 0")
        names = [s.name for s in self.sources]
        if len(names) != len(set(names)):
            raise ConfigError("adapter.sources 的 name 必须唯一")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AdapterConfig:
        _reject_unknown("adapter", data, {f.name for f in fields(cls)})
        kwargs: dict[str, Any] = {}
        sources = []
        for raw in data.get("sources", ()):
            _reject_unknown("adapter.sources", raw, {"name", "root"})
            sources.append(
                ExportSource(
                    name=_require(raw, "name", "adapter.sources"),
                    root=_path(_require(raw, "root", "adapter.sources"), "adapter.sources.root"),
                )
            )
        kwargs["sources"] = tuple(sources)
        for key in ("stability_window_s", "sidecar_suffix", "follow_symlinks"):
            if key in data:
                kwargs[key] = data[key]
        if "ignored_suffixes" in data:
            kwargs["ignored_suffixes"] = tuple(data["ignored_suffixes"])
        return cls(**kwargs)


@dataclass(frozen=True)
class UploadConfig:
    url_refresh_margin_s: float = 30.0  # 签名 URL 距过期不足该值即提前重签
    max_attempts: int = 5  # 单个远端调用的最大尝试次数（含首次）
    backoff_base_s: float = 1.0
    backoff_max_s: float = 30.0
    max_session_recreations: int = 1  # 服务端 Session 失效后允许的重建次数
    part_size_hint_bytes: int | None = None  # 向服务端提示的分片大小；以服务端回执为准
    verify_hash_before_upload: bool = True  # 首次建会话前重算 Spool 文件 Hash
    read_chunk_bytes: int = 1 << 20

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ConfigError("upload.max_attempts 必须 >= 1")
        if self.backoff_base_s < 0 or self.backoff_max_s < self.backoff_base_s:
            raise ConfigError("upload.backoff 配置非法")
        if self.url_refresh_margin_s < 0 or self.max_session_recreations < 0:
            raise ConfigError("upload 配置含负数")
        if self.part_size_hint_bytes is not None and self.part_size_hint_bytes <= 0:
            raise ConfigError("upload.part_size_hint_bytes 必须 > 0")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> UploadConfig:
        _reject_unknown("upload", data, {f.name for f in fields(cls)})
        return cls(**dict(data))


@dataclass(frozen=True)
class PreflightThresholds:
    min_free_bytes_block: int = 5 * 1024**3
    min_free_bytes_warn: int = 20 * 1024**3
    clock_skew_warn_ms: int = 100
    clock_skew_block_ms: int = 1000
    min_agent_version: str = "0.1.0"
    recommended_agent_version: str | None = None
    network_unreachable_status: CheckStatus = CheckStatus.BLOCKED
    clock_unavailable_status: CheckStatus = CheckStatus.WARN
    residue_status: CheckStatus = CheckStatus.WARN  # 其他 Session 遗留未上传数据

    def __post_init__(self) -> None:
        if not 0 <= self.min_free_bytes_block <= self.min_free_bytes_warn:
            raise ConfigError("preflight 磁盘阈值需满足 0 <= block <= warn")
        if not 0 <= self.clock_skew_warn_ms <= self.clock_skew_block_ms:
            raise ConfigError("preflight 时钟阈值需满足 0 <= warn <= block")
        for name in ("network_unreachable_status", "clock_unavailable_status", "residue_status"):
            if getattr(self, name) not in (CheckStatus.WARN, CheckStatus.BLOCKED):
                raise ConfigError(f"preflight.{name} 只能是 WARN 或 BLOCKED")
        for name in ("min_agent_version", "recommended_agent_version"):
            value = getattr(self, name)
            if value is not None:
                try:
                    Version(value)
                except InvalidVersion as exc:
                    raise ConfigError(f"preflight.{name} 不是合法版本号：{value}") from exc

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> PreflightThresholds:
        _reject_unknown("preflight", data, {f.name for f in fields(cls)})
        kwargs = dict(data)
        for key in ("network_unreachable_status", "clock_unavailable_status", "residue_status"):
            if key in kwargs:
                kwargs[key] = CheckStatus(kwargs[key])
        return cls(**kwargs)


@dataclass(frozen=True)
class AgentConfig:
    agent_id: str
    spool: SpoolConfig
    adapter: AdapterConfig = field(default_factory=AdapterConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)
    preflight: PreflightThresholds = field(default_factory=PreflightThresholds)

    def __post_init__(self) -> None:
        if not self.agent_id:
            raise ConfigError("agent_id 不能为空")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AgentConfig:
        _reject_unknown("root", data, {"agent_id", "spool", "adapter", "upload", "preflight"})
        return cls(
            agent_id=_require(data, "agent_id", "root"),
            spool=SpoolConfig.from_mapping(_require(data, "spool", "root")),
            adapter=AdapterConfig.from_mapping(data.get("adapter", {})),
            upload=UploadConfig.from_mapping(data.get("upload", {})),
            preflight=PreflightThresholds.from_mapping(data.get("preflight", {})),
        )

    @classmethod
    def from_toml(cls, path: Path) -> AgentConfig:
        try:
            with path.open("rb") as handle:
                return cls.from_mapping(tomllib.load(handle))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"无法读取配置文件 {path}：{exc}") from exc
