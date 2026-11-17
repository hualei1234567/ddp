"""环境变量配置。地址、密钥、路径一律不硬编码。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.errors import ConfigurationError

DEFAULT_TRAINING_SECRET = ""
DEFAULT_MODEL_SECRET = ""


def _text(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def _integer(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是整数") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} 不得小于 {minimum}")
    return value


def _number(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是数字") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} 不得小于 {minimum}")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} 必须是布尔值")


def _prefix(name: str, default: str) -> str:
    return _text(name, default).strip("/")


@dataclass(frozen=True, slots=True)
class Settings:
    api_base_url: str = "http://localhost:8000"
    public_base_url: str = "http://localhost:9100"
    training_adapter_id: str = "local-training"
    training_adapter_version: str = "1.0.0"
    training_secret: str = DEFAULT_TRAINING_SECRET
    model_adapter_id: str = "local-model"
    model_adapter_version: str = "1.0.0"
    model_secret: str = DEFAULT_MODEL_SECRET
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "ddp-raw"
    s3_region: str = "us-east-1"
    export_prefix: str = "exports"
    model_prefix: str = "models"
    evaluation_prefix: str = "evaluations"
    inference_prefix: str = "inference"
    ffmpeg_path: str = "/opt/homebrew/bin/ffmpeg"
    ffprobe_path: str = "/opt/homebrew/bin/ffprobe"
    frames_per_video: int = 8
    image_size: int = 32
    max_frames: int = 200_000
    max_concurrent_runs: int = 2
    max_pending_runs: int = 32
    max_video_bytes: int = 512 * 1024 * 1024
    max_manifest_bytes: int = 64 * 1024 * 1024
    run_timeout_seconds: int = 1800
    ffmpeg_timeout_seconds: int = 120
    http_timeout_seconds: float = 10.0
    callback_max_attempts: int = 6
    callback_initial_delay_seconds: float = 1.0
    callback_max_delay_seconds: float = 30.0
    allow_single_model_fallback: bool = True
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        settings = cls(
            api_base_url=_text("DDP_API_BASE_URL", defaults.api_base_url).rstrip("/"),
            public_base_url=_text(
                "LOCAL_ADAPTER_PUBLIC_BASE_URL", defaults.public_base_url
            ).rstrip("/"),
            training_adapter_id=_text(
                "LOCAL_ADAPTER_TRAINING_ADAPTER_ID", defaults.training_adapter_id
            ),
            training_adapter_version=_text(
                "LOCAL_ADAPTER_TRAINING_ADAPTER_VERSION",
                defaults.training_adapter_version,
            ),
            training_secret=_text(
                "LOCAL_ADAPTER_TRAINING_SECRET", DEFAULT_TRAINING_SECRET
            ),
            model_adapter_id=_text(
                "LOCAL_ADAPTER_MODEL_ADAPTER_ID", defaults.model_adapter_id
            ),
            model_adapter_version=_text(
                "LOCAL_ADAPTER_MODEL_ADAPTER_VERSION", defaults.model_adapter_version
            ),
            model_secret=_text("LOCAL_ADAPTER_MODEL_SECRET", DEFAULT_MODEL_SECRET),
            s3_endpoint=_text("DDP_S3_ENDPOINT", defaults.s3_endpoint),
            s3_access_key=_text("DDP_S3_ACCESS_KEY", defaults.s3_access_key),
            s3_secret_key=_text("DDP_S3_SECRET_KEY", defaults.s3_secret_key),
            s3_bucket=_text("DDP_S3_BUCKET", defaults.s3_bucket),
            s3_region=_text("DDP_S3_REGION", defaults.s3_region),
            export_prefix=_prefix(
                "LOCAL_ADAPTER_EXPORT_PREFIX", defaults.export_prefix
            ),
            model_prefix=_prefix("LOCAL_ADAPTER_MODEL_PREFIX", defaults.model_prefix),
            evaluation_prefix=_prefix(
                "LOCAL_ADAPTER_EVALUATION_PREFIX", defaults.evaluation_prefix
            ),
            inference_prefix=_prefix(
                "LOCAL_ADAPTER_INFERENCE_PREFIX", defaults.inference_prefix
            ),
            ffmpeg_path=_text("LOCAL_ADAPTER_FFMPEG_PATH", defaults.ffmpeg_path),
            ffprobe_path=_text("LOCAL_ADAPTER_FFPROBE_PATH", defaults.ffprobe_path),
            frames_per_video=_integer(
                "LOCAL_ADAPTER_FRAMES_PER_VIDEO", defaults.frames_per_video
            ),
            image_size=_integer(
                "LOCAL_ADAPTER_IMAGE_SIZE", defaults.image_size, minimum=8
            ),
            max_frames=_integer("LOCAL_ADAPTER_MAX_FRAMES", defaults.max_frames),
            max_concurrent_runs=_integer(
                "LOCAL_ADAPTER_MAX_CONCURRENT_RUNS", defaults.max_concurrent_runs
            ),
            max_pending_runs=_integer(
                "LOCAL_ADAPTER_MAX_PENDING_RUNS", defaults.max_pending_runs
            ),
            max_video_bytes=_integer(
                "LOCAL_ADAPTER_MAX_VIDEO_BYTES", defaults.max_video_bytes
            ),
            max_manifest_bytes=_integer(
                "LOCAL_ADAPTER_MAX_MANIFEST_BYTES", defaults.max_manifest_bytes
            ),
            run_timeout_seconds=_integer(
                "LOCAL_ADAPTER_RUN_TIMEOUT_SECONDS", defaults.run_timeout_seconds
            ),
            ffmpeg_timeout_seconds=_integer(
                "LOCAL_ADAPTER_FFMPEG_TIMEOUT_SECONDS", defaults.ffmpeg_timeout_seconds
            ),
            http_timeout_seconds=_number(
                "LOCAL_ADAPTER_HTTP_TIMEOUT_SECONDS", defaults.http_timeout_seconds
            ),
            callback_max_attempts=_integer(
                "LOCAL_ADAPTER_CALLBACK_MAX_ATTEMPTS", defaults.callback_max_attempts
            ),
            callback_initial_delay_seconds=_number(
                "LOCAL_ADAPTER_CALLBACK_INITIAL_DELAY_SECONDS",
                defaults.callback_initial_delay_seconds,
            ),
            callback_max_delay_seconds=_number(
                "LOCAL_ADAPTER_CALLBACK_MAX_DELAY_SECONDS",
                defaults.callback_max_delay_seconds,
            ),
            allow_single_model_fallback=_boolean(
                "LOCAL_ADAPTER_ALLOW_SINGLE_MODEL_FALLBACK",
                defaults.allow_single_model_fallback,
            ),
            log_level=_text("LOCAL_ADAPTER_LOG_LEVEL", defaults.log_level).upper(),
        )
        missing = [
            name
            for name, value in (
                ("LOCAL_ADAPTER_TRAINING_SECRET", settings.training_secret),
                ("LOCAL_ADAPTER_MODEL_SECRET", settings.model_secret),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                "缺少回调共享密钥："
                + "、".join(missing)
                + "；必须与 DDP 配置的密钥一致"
            )
        return settings


__all__ = ["Settings"]
