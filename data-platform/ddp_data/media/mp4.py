"""MP4 metadata 只读探测（DDP-V21-N3-004）。

**为什么需要这一层**

平台此前对视频只有 ``ftyp`` 魔数识别（``domain/ingestion/validation.py``），
能判断“这是不是 MP4”，但拿不到时长、帧率、分辨率与编码。导入链路因此只能
依赖元数据里**声明**的 ``source_fps``，无法用文件事实对账。

**为什么用 ffprobe 子进程**

容器解析（moov/trak/stbl 等）属于高成熟度能力，手写解析器既慢又容易在损坏
文件上出安全边界问题。仓库已声明 FFmpeg/ffprobe 为部署前提，这里只做一层
**受控只读调用**，不引入新 Python 依赖，也不把 FFmpeg 依赖混进后端 venv。

**安全与边界**

- 参数以 ``argv`` 列表传递，不经 shell；输入统一加 ``file:`` 前缀，避免以
  ``-`` 开头的文件名被 ffprobe 当作选项（命令注入面）。
- 只读文件前 12 字节做 ``ftyp`` 签名校验，**不把整个文件读入内存**；其余由
  ffprobe 自行按需读取。
- 子进程带超时；``subprocess.run`` 在超时时会 kill 并回收子进程，避免僵尸。
- 输出通过 ``-show_entries`` 限定字段，并设字节上限与 stream 条数上限；
  超限显式失败，而不是截断后冒充完整结果。
- 错误信息只含文件名，不带绝对路径；ffprobe stderr 会先脱敏再截断。
- 损坏/非 MP4/缺 ffprobe/超时/输出超限全部抛显式异常，**绝不返回空结果**。
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

DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 120.0
MAX_OUTPUT_BYTES = 256 * 1024
MAX_STREAMS = 32
MAX_TEXT_LENGTH = 64
MAX_REASONABLE_FPS = 1000.0
_FTYP_OFFSET = 4
_FTYP = b"ftyp"
_HEADER_BYTES = 12
_ERROR_TEXT_LIMIT = 300
_DISPLAY_NAME_LIMIT = 128
_SHOW_ENTRIES = (
    "format=format_name,duration:"
    "stream=index,codec_type,codec_name,width,height,"
    "r_frame_rate,avg_frame_rate,duration,nb_frames"
)


class MediaProbeError(Exception):
    """媒体探测失败基类；调用方必须显式处理。"""

    code = "media_probe_failed"


class ProbeInputError(MediaProbeError):
    """输入文件不存在、不可读或不是普通文件。"""

    code = "invalid_input"


class NotMp4Error(MediaProbeError):
    """内容缺少 MP4 ``ftyp`` 签名。"""

    code = "not_mp4"


class ProbeUnavailableError(MediaProbeError):
    """环境缺少可执行的 ffprobe。"""

    code = "ffprobe_unavailable"


class ProbeTimeoutError(MediaProbeError):
    """ffprobe 超过给定超时。"""

    code = "probe_timeout"


class ProbeOutputLimitError(MediaProbeError):
    """ffprobe 输出或 stream 数超过有界上限。"""

    code = "probe_output_too_large"


class ProbeFailedError(MediaProbeError):
    """ffprobe 非零退出、输出非法或文件损坏。"""

    code = "probe_failed"


@dataclass(frozen=True, slots=True)
class MediaStreamInfo:
    """单条 stream 的有界摘要；缺失字段保持 ``None``，不补默认值。"""

    index: int | None
    codec_type: str | None
    codec_name: str | None
    width: int | None
    height: int | None
    fps: float | None
    duration_ms: int | None
    frame_count: int | None


@dataclass(frozen=True, slots=True)
class Mp4ProbeResult:
    """MP4 只读探测结果。

    ``duration_ms`` / ``fps`` / ``width`` / ``height`` / ``frame_count`` 取
    **首条视频流**；文件没有视频流时为 ``None``，而不是 0。``duration_ms``
    优先取容器时长，缺失时回退到首条视频流时长。
    """

    duration_ms: int | None
    fps: float | None
    width: int | None
    height: int | None
    frame_count: int | None
    video_codec: str | None
    audio_codec: str | None
    format_name: str | None
    size_bytes: int | None
    streams: tuple[MediaStreamInfo, ...]

    def as_metadata(self) -> dict[str, object]:
        """有界、可 JSON 序列化的摘要；不含路径与 ffprobe 原始输出。

        调用方可把它嵌进 ``RawAsset.raw_metadata``（如 ``{"media": ...}``），
        与既有的导入来源留痕并存，无需新增数据库列。
        """
        return {
            "format_name": self.format_name,
            "duration_ms": self.duration_ms,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "frame_count": self.frame_count,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "stream_count": len(self.streams),
        }


def probe_mp4(
    path: str | os.PathLike[str],
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ffprobe_path: str | os.PathLike[str] | None = None,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> Mp4ProbeResult:
    """只读探测一个本地 MP4 文件。

    参数：

    - ``path``：本地文件路径；不读取整个文件，也不接受 URL。
    - ``timeout_seconds``：子进程超时，默认 5 秒，上限 120 秒。
    - ``ffprobe_path``：可执行路径；默认从 ``PATH`` 解析 ``ffprobe``。
    - ``max_output_bytes``：stdout/stderr 字节上限（防超大输出）。

    失败一律抛 :class:`MediaProbeError` 子类；成功返回有界结果。
    """
    timeout = _validate_timeout(timeout_seconds)
    output_limit = _validate_output_limit(max_output_bytes)
    resolved = _resolve_media_file(path)
    _assert_mp4_signature(resolved)
    executable = _resolve_ffprobe(ffprobe_path)

    command = [
        executable,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        _SHOW_ENTRIES,
        "-i",
        f"file:{resolved}",
    ]
    try:
        completed = subprocess.run(  # noqa: S603 —— 参数列表调用，不经 shell
            command,
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeTimeoutError(f"ffprobe 在 {timeout:g} 秒内未完成，已终止") from exc
    except FileNotFoundError as exc:
        raise ProbeUnavailableError("ffprobe 不存在；请检查 FFmpeg 安装") from exc
    except OSError as exc:
        raise ProbeFailedError("ffprobe 无法启动") from exc
    except subprocess.SubprocessError as exc:
        raise ProbeFailedError("ffprobe 执行失败") from exc

    if len(completed.stdout) > output_limit or len(completed.stderr) > output_limit:
        raise ProbeOutputLimitError(f"ffprobe 输出超过上限 {output_limit} 字节")
    if completed.returncode != 0:
        raise ProbeFailedError(
            _failure_message(completed.returncode, completed.stderr, resolved)
        )

    payload = _parse_payload(completed.stdout)
    return _build_result(payload, resolved)


def _validate_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProbeInputError("timeout_seconds 必须是数字")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ProbeInputError("timeout_seconds 必须大于 0")
    if timeout > MAX_TIMEOUT_SECONDS:
        raise ProbeInputError(f"timeout_seconds 不得超过 {MAX_TIMEOUT_SECONDS:g} 秒")
    return timeout


def _validate_output_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProbeInputError("max_output_bytes 必须是整数")
    if value <= 0:
        raise ProbeInputError("max_output_bytes 必须大于 0")
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
        raise ProbeInputError("媒体路径必须是字符串或 Path") from exc
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ProbeInputError(f"媒体文件不存在：{_display_name(candidate)}") from exc
    except OSError as exc:
        raise ProbeInputError(f"无法访问媒体文件：{_display_name(candidate)}") from exc
    if not resolved.is_file():
        raise ProbeInputError(f"媒体路径不是普通文件：{_display_name(candidate)}")
    return resolved


def _assert_mp4_signature(path: Path) -> None:
    """只读前 12 字节；不把整个文件载入内存。"""
    try:
        with path.open("rb") as handle:
            header = handle.read(_HEADER_BYTES)
    except OSError as exc:
        raise ProbeInputError(f"无法读取媒体文件：{path.name}") from exc
    if header[_FTYP_OFFSET : _FTYP_OFFSET + len(_FTYP)] != _FTYP:
        raise NotMp4Error(f"{_display_name(path)} 不是 MP4：前 12 字节缺少 ftyp 签名")


def _resolve_ffprobe(ffprobe_path: str | os.PathLike[str] | None) -> str:
    if ffprobe_path is not None:
        candidate = Path(ffprobe_path)
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise ProbeUnavailableError("指定的 ffprobe 不存在或不可执行")
        return str(candidate)
    found = shutil.which("ffprobe")
    if not found:
        raise ProbeUnavailableError("未找到 ffprobe；MP4 metadata 探测需要 FFmpeg/ffprobe")
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
        raise ProbeFailedError("ffprobe 输出不是合法 JSON") from exc
    if not isinstance(payload, Mapping):
        raise ProbeFailedError("ffprobe 输出必须是 JSON 对象")
    return payload


def _build_result(payload: Mapping[str, object], resolved: Path) -> Mp4ProbeResult:
    format_info = payload.get("format")
    if format_info is None:
        format_info = {}
    if not isinstance(format_info, Mapping):
        raise ProbeFailedError("ffprobe 输出的 format 必须是对象")

    raw_streams = payload.get("streams")
    if raw_streams is None:
        raw_streams = []
    if not isinstance(raw_streams, list):
        raise ProbeFailedError("ffprobe 输出的 streams 必须是数组")
    if len(raw_streams) > MAX_STREAMS:
        raise ProbeOutputLimitError(
            f"ffprobe 报告的 stream 数 {len(raw_streams)} 超过上限 {MAX_STREAMS}"
        )

    streams = tuple(_stream_info(item) for item in raw_streams)
    video = next((stream for stream in streams if stream.codec_type == "video"), None)
    audio = next((stream for stream in streams if stream.codec_type == "audio"), None)

    duration_ms = _as_seconds_ms(format_info.get("duration"))
    if duration_ms is None and video is not None:
        duration_ms = video.duration_ms

    return Mp4ProbeResult(
        duration_ms=duration_ms,
        fps=video.fps if video is not None else None,
        width=video.width if video is not None else None,
        height=video.height if video is not None else None,
        frame_count=video.frame_count if video is not None else None,
        video_codec=video.codec_name if video is not None else None,
        audio_codec=audio.codec_name if audio is not None else None,
        format_name=_as_text(format_info.get("format_name")),
        size_bytes=_file_size(resolved),
        streams=streams,
    )


def _stream_info(item: object) -> MediaStreamInfo:
    if not isinstance(item, Mapping):
        raise ProbeFailedError("ffprobe 输出的 stream 元素必须是对象")
    return MediaStreamInfo(
        index=_as_int(item.get("index"), minimum=0),
        codec_type=_as_text(item.get("codec_type")),
        codec_name=_as_text(item.get("codec_name")),
        width=_as_int(item.get("width"), minimum=1),
        height=_as_int(item.get("height"), minimum=1),
        fps=_first_fps(item.get("avg_frame_rate"), item.get("r_frame_rate")),
        duration_ms=_as_seconds_ms(item.get("duration")),
        frame_count=_as_int(item.get("nb_frames"), minimum=0),
    )


def _first_fps(primary: object, fallback: object) -> float | None:
    fps = _as_fps(primary)
    return fps if fps is not None else _as_fps(fallback)


def _as_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text[:MAX_TEXT_LENGTH]


def _as_int(value: object, *, minimum: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        # ffprobe 的 nb_frames 等字段可能以字符串输出；解析失败即视为缺失。
        try:
            number = float(value.strip())
        except ValueError:
            return None
    elif isinstance(value, int | float):
        number = float(value)
    else:
        return None
    if not math.isfinite(number) or number != int(number) or number < minimum:
        return None
    return int(number)


def _as_seconds_ms(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return int(round(seconds * 1000))


def _as_fps(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        result = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if "/" in text:
            numerator, _, denominator = text.partition("/")
            try:
                top = float(numerator)
                bottom = float(denominator)
            except ValueError:
                return None
            if bottom == 0:
                return None
            result = top / bottom
        else:
            try:
                result = float(text)
            except ValueError:
                return None
    else:
        return None
    if not math.isfinite(result) or result <= 0 or result > MAX_REASONABLE_FPS:
        return None
    return round(result, 3)


def _file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_OUTPUT_BYTES",
    "MAX_STREAMS",
    "MediaProbeError",
    "Mp4ProbeResult",
    "NotMp4Error",
    "ProbeFailedError",
    "ProbeInputError",
    "ProbeOutputLimitError",
    "ProbeTimeoutError",
    "ProbeUnavailableError",
    "MediaStreamInfo",
    "probe_mp4",
]
