"""ffmpeg 抽帧：缩放到 N×N 灰度，返回原始像素字节。

为什么用 ffmpeg 子进程而不是 OpenCV：镜像里只装 ffmpeg，解码能力由成熟工具
承担；本服务不重复实现容器/编解码。每路视频按均匀时间点抽帧，失败显式抛错，
不返回空数组掩盖问题。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.errors import FrameExtractionError

logger = logging.getLogger("ddp.local_adapter.frames")


@dataclass(frozen=True, slots=True)
class ExtractedFrames:
    frames: tuple[bytes, ...]
    sampling: str
    duration_seconds: float | None


def ffmpeg_version(path: str) -> str | None:
    executable = shutil.which(path) or (path if Path(path).is_file() else None)
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    first = completed.stdout.splitlines()[0] if completed.stdout else ""
    return first.strip() or "unknown"


def _probe_duration(path: Path, ffprobe_path: str, timeout: int) -> float | None:
    executable = shutil.which(ffprobe_path) or (
        ffprobe_path if Path(ffprobe_path).is_file() else None
    )
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        value = float(completed.stdout.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _run_ffmpeg(command: list[str], *, timeout: int) -> bytes:
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise FrameExtractionError(
            "ffmpeg_unavailable", "未找到 ffmpeg 可执行文件"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FrameExtractionError("ffmpeg_timeout", "ffmpeg 抽帧超时") from exc
    except OSError as exc:
        raise FrameExtractionError("ffmpeg_failed", f"无法启动 ffmpeg：{exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")[-500:]
        raise FrameExtractionError(
            "ffmpeg_failed", f"ffmpeg 返回 {completed.returncode}：{stderr}"
        )
    return completed.stdout


def extract_frames(
    path: Path,
    *,
    ffmpeg_path: str,
    ffprobe_path: str,
    count: int,
    size: int,
    timeout: int,
) -> ExtractedFrames:
    if count < 1 or size < 8:
        raise FrameExtractionError("ffmpeg_config_invalid", "抽帧数量或尺寸配置非法")
    expected = size * size
    duration = _probe_duration(path, ffprobe_path, timeout)
    filter_expr = f"scale={size}:{size}:flags=area,format=gray"
    frames: list[bytes] = []
    if duration is not None:
        for index in range(count):
            timestamp = duration * (index + 0.5) / count
            command = [
                ffmpeg_path,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                filter_expr,
                "-f",
                "rawvideo",
                "pipe:1",
            ]
            payload = _run_ffmpeg(command, timeout=timeout)
            if len(payload) != expected:
                raise FrameExtractionError(
                    "frame_size_mismatch",
                    f"抽帧字节数 {len(payload)} != {expected}（{path.name}）",
                )
            frames.append(payload)
        return ExtractedFrames(
            frames=tuple(frames), sampling="evenly_spaced", duration_seconds=duration
        )

    # 时长探测失败时退回「解码前 N 帧」；仍然真实解码，不伪造。
    command = [
        ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-frames:v",
        str(count),
        "-vf",
        filter_expr,
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    payload = _run_ffmpeg(command, timeout=timeout)
    if not payload:
        raise FrameExtractionError(
            "no_frames_decoded", f"视频未解码出任何帧：{path.name}"
        )
    if len(payload) % expected != 0:
        raise FrameExtractionError(
            "frame_size_mismatch",
            f"抽帧总字节数不是 {expected} 的整数倍（{path.name}）",
        )
    for offset in range(0, len(payload), expected):
        frames.append(payload[offset : offset + expected])
    logger.warning("ffprobe 未取得时长，%s 退回首 %s 帧采样", path.name, len(frames))
    return ExtractedFrames(
        frames=tuple(frames),
        sampling="first_frames_fallback",
        duration_seconds=None,
    )


__all__ = ["ExtractedFrames", "extract_frames", "ffmpeg_version"]
