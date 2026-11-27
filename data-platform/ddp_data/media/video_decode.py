"""视频逐帧 pts_time 只读探测（DDP V6.1 / T1-02）。

**为什么只读不写**

本模块属于数据准入/对账层（fixture 准入与 ffprobe 实测），只负责把容器里
**实测**的逐帧时间线（``pts_time``）以有界、可 JSON 序列化的形式读出来：

- 平台对原始资产的要求是不可变：导入链路只登记事实，不修改媒体本体。
  转码、重封装、重打时间戳都是"写"操作，必须走独立的派生资产流程并留下
  溯源记录，而不是在探测层顺手做掉。
- ``ffmpeg -f null -`` 这类纯解码校验属于集成级准入检查（T1-01 的
  evidence 登记），同样不在此处；本模块只调用 ffprobe 逐帧读取。
- ``pts_time`` **保持原始值返回，不做归一化**：不按时基换算、不按 fps
  重排、不补齐缺帧、不改小数精度。归一化是下游帧对齐层的职责；探测层
  一旦"顺手修数据"，上游就无法区分文件事实与推断事实。

**安全模式（镜像 mp4.py 的受控子进程约定）**

- 参数以 ``argv`` 列表传递，不经 shell；输入统一加 ``file:`` 前缀，
  避免以 ``-`` 开头的路径被 ffprobe 当作选项（命令注入面）。
- 子进程带超时，超时由 ``subprocess.run`` kill 并回收，避免僵尸；
  stdin 接 ``DEVNULL``，防止 ffprobe 等待输入。
- stdout/stderr 设字节上限；``-show_entries frame=media_type,pts_time``
  只取两个字段，配合 ``max_frames`` 上限，超限显式失败而不是截断后
  冒充完整结果。
- 错误信息只含文件名，绝对路径先脱敏再截断。
- 失败一律抛显式异常，绝不返回空列表冒充成功。
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_FRAMES = 100_000
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_TEXT_LENGTH = 64
_ERROR_TEXT_LIMIT = 300
_DISPLAY_NAME_LIMIT = 128
_SHOW_ENTRIES = "frame=media_type,pts_time"
_VIDEO_MEDIA_TYPE = "video"


class VideoProbeError(Exception):
    """视频逐帧探测失败基类；调用方必须显式处理。"""

    code = "video_probe_failed"


class VideoInputError(VideoProbeError):
    """输入路径、超时或上限参数非法。"""

    code = "invalid_input"


class FFprobeUnavailableError(VideoProbeError):
    """环境缺少可执行的 ffprobe。"""

    code = "ffprobe_unavailable"


class VideoProbeTimeoutError(VideoProbeError):
    """ffprobe 超过给定超时。"""

    code = "probe_timeout"


class VideoProbeOutputLimitError(VideoProbeError):
    """ffprobe 输出字节或逐帧数超过有界上限。"""

    code = "probe_output_too_large"


class VideoProbeFailedError(VideoProbeError):
    """ffprobe 非零退出、输出非法或文件损坏。"""

    code = "probe_failed"


@dataclass(frozen=True, slots=True)
class FrameInfo:
    """单帧的有界事实。

    - ``index``：该帧在返回列表中的序号（0 起，等于视频流内的读取顺序）。
    - ``pts_time``：原始 ``pts_time`` 十进制字符串直接转 float；**未做任何
      时基换算、取整或重排**，非等间距原样保留。缺失/非法（如 ``N/A``）为
      ``None``，不补默认值。
    - ``pts_time_raw``：ffprobe 输出的原文，供对账与存证。
    """

    index: int
    media_type: str | None
    pts_time: float | None
    pts_time_raw: str | None


def probe_frames(
    video_path: str | os.PathLike[str],
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ffprobe_path: str | os.PathLike[str] | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> list[FrameInfo]:
    """只读探测一个本地视频文件的逐帧 ``pts_time``。

    参数：

    - ``video_path``：本地文件路径；不读取整个文件，也不接受 URL。
    - ``max_frames``：逐帧数上限（默认 10 万）；超出显式失败，不截断。
    - ``timeout``：子进程超时秒数，默认 30，上限 600（逐帧读取比容器级
      探测重，故上限放宽，仍必须有界）。
    - ``ffprobe_path``：可执行路径；默认从 ``PATH`` 解析 ``ffprobe``。
    - ``max_output_bytes``：stdout/stderr 字节上限（防超大输出）。

    失败一律抛 :class:`VideoProbeError` 子类；成功返回逐帧列表。
    """
    timeout_seconds = _validate_timeout(timeout)
    frame_limit = _validate_frame_limit(max_frames)
    output_limit = _validate_output_limit(max_output_bytes)
    resolved = _resolve_media_file(video_path)
    executable = _resolve_ffprobe(ffprobe_path)

    command = [
        executable,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_frames",
        "-show_entries",
        _SHOW_ENTRIES,
        "-select_streams",
        "v:0",
        "-i",
        f"file:{resolved}",
    ]
    try:
        completed = subprocess.run(  # noqa: S603 —— 参数列表调用，不经 shell
            command,
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoProbeTimeoutError(
            f"ffprobe 在 {timeout_seconds:g} 秒内未完成，已终止"
        ) from exc
    except FileNotFoundError as exc:
        raise FFprobeUnavailableError("ffprobe 不存在；请检查 FFmpeg 安装") from exc
    except OSError as exc:
        raise VideoProbeFailedError("ffprobe 无法启动") from exc
    except subprocess.SubprocessError as exc:
        raise VideoProbeFailedError("ffprobe 执行失败") from exc

    if len(completed.stdout) > output_limit or len(completed.stderr) > output_limit:
        raise VideoProbeOutputLimitError(
            f"ffprobe 输出超过上限 {output_limit} 字节"
        )
    if completed.returncode != 0:
        raise VideoProbeFailedError(
            _failure_message(completed.returncode, completed.stderr, resolved)
        )

    payload = _parse_payload(completed.stdout)
    return _build_frames(payload, resolved, frame_limit)


def _validate_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise VideoInputError("timeout 必须是数字")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise VideoInputError("timeout 必须大于 0")
    if timeout > MAX_TIMEOUT_SECONDS:
        raise VideoInputError(f"timeout 不得超过 {MAX_TIMEOUT_SECONDS:g} 秒")
    return timeout


def _validate_frame_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VideoInputError("max_frames 必须是整数")
    if value <= 0:
        raise VideoInputError("max_frames 必须大于 0")
    return value


def _validate_output_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VideoInputError("max_output_bytes 必须是整数")
    if value <= 0:
        raise VideoInputError("max_output_bytes 必须大于 0")
    return value


def _display_name(path: Path) -> str:
    """错误信息里只留可打印文件名；控制字符替换掉，避免日志注入。"""
    name = path.name or "?"
    cleaned = "".join(char if char.isprintable() else "?" for char in name)
    return cleaned[:_DISPLAY_NAME_LIMIT]


def _resolve_media_file(path: str | os.PathLike[str]) -> Path:
    try:
        candidate = Path(path)
    except TypeError as exc:
        raise VideoInputError("视频路径必须是字符串或 Path") from exc
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise VideoInputError(f"视频文件不存在：{_display_name(candidate)}") from exc
    except OSError as exc:
        raise VideoInputError(f"无法访问视频文件：{_display_name(candidate)}") from exc
    if not resolved.is_file():
        raise VideoInputError(f"视频路径不是普通文件：{_display_name(candidate)}")
    return resolved


def _resolve_ffprobe(ffprobe_path: str | os.PathLike[str] | None) -> str:
    if ffprobe_path is not None:
        candidate = Path(ffprobe_path)
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise FFprobeUnavailableError("指定的 ffprobe 不存在或不可执行")
        return str(candidate)
    found = shutil.which("ffprobe")
    if not found:
        raise FFprobeUnavailableError(
            "未找到 ffprobe；视频逐帧探测需要 FFmpeg/ffprobe"
        )
    return found


def _failure_message(returncode: int, stderr: bytes, resolved: Path) -> str:
    text = _sanitize(stderr.decode("utf-8", errors="replace"), resolved)
    text = " ".join(text.split())
    if not text:
        return f"ffprobe 返回 {returncode}，未输出错误详情"
    return f"ffprobe 返回 {returncode}：{text[:_ERROR_TEXT_LIMIT]}"


def _sanitize(text: str, resolved: Path) -> str:
    """去掉绝对路径，只保留文件名；避免把本机目录写进错误信息。"""
    replacements = [f"file:{resolved}", str(resolved)]
    parent = str(resolved.parent)
    if len(parent) > 1:
        replacements.append(parent)
    for raw in replacements:
        text = text.replace(raw, _display_name(resolved))
    return text


def _parse_payload(stdout: bytes) -> Mapping[str, object]:
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VideoProbeFailedError("ffprobe 输出不是合法 JSON") from exc
    if not isinstance(payload, Mapping):
        raise VideoProbeFailedError("ffprobe 输出必须是 JSON 对象")
    return payload


def _build_frames(
    payload: Mapping[str, object],
    resolved: Path,
    frame_limit: int,
) -> list[FrameInfo]:
    raw_frames = payload.get("frames")
    if raw_frames is None:
        raw_frames = []
    if not isinstance(raw_frames, list):
        raise VideoProbeFailedError("ffprobe 输出的 frames 必须是数组")

    frames: list[FrameInfo] = []
    for item in raw_frames:
        if not isinstance(item, Mapping):
            raise VideoProbeFailedError("ffprobe 输出的 frame 元素必须是对象")
        media_type = _as_text(item.get("media_type"))
        # -select_streams v:0 已过滤到视频流；此处兜底跳过混入的其它类型。
        if media_type is not None and media_type != _VIDEO_MEDIA_TYPE:
            continue
        raw_pts = _as_text(item.get("pts_time"))
        frames.append(
            FrameInfo(
                index=len(frames),
                media_type=media_type,
                pts_time=_as_pts_time(raw_pts),
                pts_time_raw=raw_pts,
            )
        )
        if len(frames) > frame_limit:
            raise VideoProbeOutputLimitError(
                f"{_display_name(resolved)} 的视频帧数超过上限 {frame_limit}，"
                "请调大 max_frames；不做静默截断"
            )
    return frames


def _as_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text[:MAX_TEXT_LENGTH]


def _as_pts_time(raw: str | None) -> float | None:
    """原始十进制字符串转 float；缺失或非法（如 ``N/A``）返回 ``None``。

    只做类型转换，不换算时基、不取整、不改符号：pts_time 可为负
    （B 帧/编辑列表），负值同样是文件事实，原样保留。
    """
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if not math.isfinite(seconds):
        return None
    return seconds


__all__ = [
    "DEFAULT_MAX_FRAMES",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "FFprobeUnavailableError",
    "FrameInfo",
    "VideoInputError",
    "VideoProbeError",
    "VideoProbeFailedError",
    "VideoProbeOutputLimitError",
    "VideoProbeTimeoutError",
    "probe_frames",
]
