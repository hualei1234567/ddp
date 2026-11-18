"""MP4 metadata 只读探测（DDP-V21-N3-004）。

覆盖：有界解析、argv 参数化（无 shell）、损坏/非 MP4/缺 ffprobe/超时/输出
超限的显式失败、错误信息不带绝对路径，以及本机有 FFmpeg 时的真实样例。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from ddp_data.media import (
    MAX_OUTPUT_BYTES,
    MAX_STREAMS,
    Mp4ProbeResult,
    NotMp4Error,
    ProbeFailedError,
    ProbeInputError,
    ProbeOutputLimitError,
    ProbeTimeoutError,
    ProbeUnavailableError,
    probe_mp4,
)
from ddp_data.media import mp4 as mp4_module

FTYP_HEADER = b"\x00\x00\x00\x18ftypmp42"

_PROBE_JSON = json.dumps(
    {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30000/1001",
                "r_frame_rate": "30/1",
                "duration": "12.345",
                "nb_frames": "370",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
            },
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "12.345",
            "size": "999",
        },
    }
).encode()


def _mp4_file(tmp_path: Path, name: str = "sample.mp4") -> Path:
    path = tmp_path / name
    path.write_bytes(FTYP_HEADER + b"\x00" * 64)
    return path


def _completed(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args=["ffprobe"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# --------------------------------------------------------------------------
# 输入校验：非 MP4 / 缺文件 / 目录
# --------------------------------------------------------------------------


def test_rejects_non_mp4_before_invoking_ffprobe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "notes.mp4"
    path.write_bytes(b"not an mp4 at all")

    def _must_not_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail("非 MP4 不应启动 ffprobe")

    monkeypatch.setattr(mp4_module.subprocess, "run", _must_not_run)

    with pytest.raises(NotMp4Error) as excinfo:
        probe_mp4(path)
    assert excinfo.value.code == "not_mp4"
    assert path.name in str(excinfo.value)
    assert str(tmp_path) not in str(excinfo.value)


def test_missing_file_fails_explicitly(tmp_path: Path) -> None:
    missing = tmp_path / "missing.mp4"
    with pytest.raises(ProbeInputError) as excinfo:
        probe_mp4(missing)
    assert excinfo.value.code == "invalid_input"
    assert missing.name in str(excinfo.value)


def test_error_filename_is_sanitized(tmp_path: Path) -> None:
    path = tmp_path / "evil\nname.mp4"
    path.write_bytes(b"not an mp4")

    with pytest.raises(NotMp4Error) as excinfo:
        probe_mp4(path)
    assert "\n" not in str(excinfo.value)
    assert "evil?name.mp4" in str(excinfo.value)


def test_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProbeInputError):
        probe_mp4(tmp_path)


def test_short_header_is_not_mp4(tmp_path: Path) -> None:
    path = tmp_path / "tiny.mp4"
    path.write_bytes(b"ftyp")
    with pytest.raises(NotMp4Error):
        probe_mp4(path)


# --------------------------------------------------------------------------
# ffprobe 可用性
# --------------------------------------------------------------------------


def test_missing_ffprobe_fails_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _mp4_file(tmp_path)
    monkeypatch.setattr(mp4_module.shutil, "which", lambda _name: None)

    with pytest.raises(ProbeUnavailableError) as excinfo:
        probe_mp4(path)
    assert excinfo.value.code == "ffprobe_unavailable"


def test_explicit_ffprobe_path_must_be_executable(tmp_path: Path) -> None:
    path = _mp4_file(tmp_path)
    with pytest.raises(ProbeUnavailableError):
        probe_mp4(path, ffprobe_path=tmp_path / "not-ffprobe")


# --------------------------------------------------------------------------
# 子进程参数与安全边界
# --------------------------------------------------------------------------


def test_command_is_argv_list_with_file_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _mp4_file(tmp_path, name="-i; touch pwned.mp4")
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _completed(stdout=_PROBE_JSON)

    monkeypatch.setattr(mp4_module.subprocess, "run", fake_run)

    probe_mp4(path, ffprobe_path=sys.executable)

    command = captured["command"]
    kwargs = captured["kwargs"]
    assert isinstance(command, list)
    assert command[0] == sys.executable
    # 文件名以 '-' 开头也不会被当成 ffprobe 选项。
    assert command[-1] == f"file:{path.resolve()}"
    assert isinstance(kwargs, dict)
    assert kwargs.get("shell") is not True
    assert kwargs.get("check") is False
    assert kwargs.get("capture_output") is True
    assert kwargs.get("timeout") == mp4_module.DEFAULT_TIMEOUT_SECONDS
    assert not (tmp_path / "pwned.mp4").exists()


# --------------------------------------------------------------------------
# ffprobe 结果解析（有界结构）
# --------------------------------------------------------------------------


def test_parses_bounded_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _mp4_file(tmp_path)
    monkeypatch.setattr(
        mp4_module.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(stdout=_PROBE_JSON),
    )

    result = probe_mp4(path)

    assert isinstance(result, Mp4ProbeResult)
    assert result.duration_ms == 12345
    assert result.fps == 29.97
    assert (result.width, result.height) == (1280, 720)
    assert result.frame_count == 370
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert result.format_name.startswith("mov,mp4")
    assert result.size_bytes == path.stat().st_size
    assert len(result.streams) == 2
    assert result.streams[0].codec_type == "video"
    assert result.streams[0].duration_ms == 12345
    assert result.streams[1].codec_type == "audio"

    metadata = result.as_metadata()
    assert metadata["duration_ms"] == 12345
    assert metadata["stream_count"] == 2
    assert str(path) not in json.dumps(metadata)


def test_missing_optional_fields_stay_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _mp4_file(tmp_path)
    payload = json.dumps(
        {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "nb_frames": "--5",
                    "duration": "N/A",
                }
            ],
            "format": {"format_name": "mov,mp4"},
        }
    ).encode()
    monkeypatch.setattr(
        mp4_module.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(stdout=payload),
    )

    result = probe_mp4(path)

    assert result.duration_ms is None
    assert result.fps is None
    assert result.width is None
    assert result.height is None
    assert result.frame_count is None


def test_duration_and_fps_fall_back_to_video_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _mp4_file(tmp_path)
    payload = json.dumps(
        {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "duration": "2.5",
                    "avg_frame_rate": "0/0",
                    "r_frame_rate": "30/1",
                }
            ],
            "format": {"format_name": "mov,mp4"},
        }
    ).encode()
    monkeypatch.setattr(
        mp4_module.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(stdout=payload),
    )

    result = probe_mp4(path)

    assert result.duration_ms == 2500
    assert result.fps == 30.0


def test_too_many_streams_fails_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _mp4_file(tmp_path)
    payload = json.dumps(
        {"streams": [{"index": index, "codec_type": "data"} for index in range(MAX_STREAMS + 1)]}
    ).encode()
    monkeypatch.setattr(
        mp4_module.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(stdout=payload),
    )

    with pytest.raises(ProbeOutputLimitError):
        probe_mp4(path)


def test_invalid_json_fails_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _mp4_file(tmp_path)
    monkeypatch.setattr(
        mp4_module.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(stdout=b"not json"),
    )

    with pytest.raises(ProbeFailedError):
        probe_mp4(path)


def test_nonzero_exit_message_has_no_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _mp4_file(tmp_path)
    stderr = f"{path}: Invalid data found when processing input".encode()
    monkeypatch.setattr(
        mp4_module.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(stderr=stderr, returncode=1),
    )

    with pytest.raises(ProbeFailedError) as excinfo:
        probe_mp4(path)
    message = str(excinfo.value)
    assert "Invalid data found" in message
    assert str(path.parent) not in message
    assert path.name in message


def test_output_limit_is_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _mp4_file(tmp_path)
    oversized = b"{" + b" " * (MAX_OUTPUT_BYTES + 1) + b"}"
    monkeypatch.setattr(
        mp4_module.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(stdout=oversized),
    )

    with pytest.raises(ProbeOutputLimitError):
        probe_mp4(path)


def test_timeout_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _mp4_file(tmp_path)

    def fake_run(command: list[str], **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(mp4_module.subprocess, "run", fake_run)

    with pytest.raises(ProbeTimeoutError) as excinfo:
        probe_mp4(path, timeout_seconds=0.1)
    assert excinfo.value.code == "probe_timeout"
    assert str(path.parent) not in str(excinfo.value)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), "5"])
def test_invalid_timeout_is_rejected(tmp_path: Path, timeout: object) -> None:
    path = _mp4_file(tmp_path)
    with pytest.raises(ProbeInputError):
        probe_mp4(path, timeout_seconds=timeout)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 真实 FFmpeg 样例（本机缺失时跳过，不伪造通过）
# --------------------------------------------------------------------------

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = pytest.mark.skipif(
    not (_FFMPEG and _FFPROBE), reason="本机未安装 FFmpeg/ffprobe，真实样例用例跳过"
)


@requires_ffmpeg
def test_real_synthetic_mp4(tmp_path: Path) -> None:
    sample = tmp_path / "synthetic.mp4"
    subprocess.run(  # noqa: S603 —— 测试生成，参数固定、不经 shell
        [
            _FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10",
            "-t",
            "0.6",
            "-c:v",
            "mpeg4",
            "-q:v",
            "5",
            str(sample),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )

    result = probe_mp4(sample, ffprobe_path=_FFPROBE)

    assert result.width == 160
    assert result.height == 120
    assert result.fps is not None and abs(result.fps - 10.0) < 0.01
    assert result.duration_ms is not None and 500 <= result.duration_ms <= 800
    assert result.video_codec == "mpeg4"
    assert result.audio_codec is None
    assert any(stream.codec_type == "video" for stream in result.streams)


@requires_ffmpeg
def test_real_damaged_mp4_fails_explicitly(tmp_path: Path) -> None:
    damaged = tmp_path / "damaged.mp4"
    damaged.write_bytes(FTYP_HEADER + b"\x00" * 256)

    with pytest.raises(ProbeFailedError):
        probe_mp4(damaged, ffprobe_path=_FFPROBE)
