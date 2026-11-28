"""probe_frames 纯函数测试（DDP V6.1 / T1-02）。

**测试策略**

- 解析逻辑、参数校验、上限与脱敏全部走**纯函数路径**：用仓库自带的假
  ``ffprobe`` 脚本（壳脚本回放预置 JSON）替代真实 ffprobe，既不依赖网络
  也不依赖本机 FFmpeg，可在任何环境稳定重放。
- 边界用真实文件：
  - ``docs/testing/fixtures/stage2/samples/cameras/cam_front.mp4``（21KB
    空壳 MP4，ftyp 合法但无流无帧）作为"无流"负样本，走真实 ffprobe。
  - lerobot-pusht AV1 Golden Sample（真实 25650 帧逐帧 pts_time）为可选
    用例，路径经 ``DDP_TEST_REAL_VIDEO`` 注入，未注入自动跳过，不把
    6.6MB 缓存文件假设成所有环境都存在。
- 不 mock ffprobe 内部实现：超时用 ``sleep`` 假 ffprobe 实测触发，损坏
  输出用假 ffprobe 回放非 JSON/越界数据实测触发。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

# 仓库把 data-platform 作为独立测试根（无顶层 pyproject 导出包），从仓库
# 根直接运行 pytest 时补一次 sys.path，保证 `ddp_data` 可导入。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG_ROOT = _REPO_ROOT / "data-platform"
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from ddp_data.media.video_decode import (  # noqa: E402
    DEFAULT_MAX_FRAMES,
    FFprobeUnavailableError,
    FrameInfo,
    VideoInputError,
    VideoProbeError,
    VideoProbeFailedError,
    VideoProbeOutputLimitError,
    VideoProbeTimeoutError,
    probe_frames,
)

# 21KB 空壳 MP4：ftyp 签名合法，但无任何流、无帧——"无流"负样本。
_EMPTY_MP4 = (
    _REPO_ROOT
    / "docs/testing/fixtures/stage2/samples/cameras/cam_front.mp4"
)
_REAL_VIDEO_ENV = "DDP_TEST_REAL_VIDEO"


# ---------------------------------------------------------------------------
# 假 ffprobe：壳脚本回放预置 JSON / 模拟行为，隔离真实子进程行为。
# ---------------------------------------------------------------------------

_FAKE_TEMPLATE = """#!{python}
import json
import sys
mode = {mode!r}
if mode == "sleep":
    import time
    time.sleep(60)
if mode == "raw":
    sys.stdout.write({raw!r})
    sys.exit({exit_code})
if mode == "err":
    sys.stderr.write({raw!r})
    sys.exit({exit_code})
data = json.load(open({fixture!r}))
print(json.dumps(data))
"""


def _install_fake_ffprobe(
    tmp_path: Path,
    *,
    mode: str,
    payload: dict[str, object] | None = None,
    raw: str | None = None,
    exit_code: int = 0,
) -> str:
    """生成一个可执行的假 ffprobe，返回其路径。"""
    fixture = tmp_path / "fake_probe_payload.json"
    if payload is not None:
        fixture.write_text(json.dumps(payload), encoding="utf-8")
    script = tmp_path / "fake-ffprobe"
    script.write_text(
        _FAKE_TEMPLATE.format(
            python=sys.executable,
            mode=mode,
            raw=raw if raw is not None else "",
            fixture=str(fixture),
            exit_code=exit_code,
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(script)


def _frames_payload(pts_times: list[str]) -> dict[str, object]:
    return {
        "frames": [
            {"media_type": "video", "pts_time": value} for value in pts_times
        ]
    }


# ---------------------------------------------------------------------------
# 解析逻辑（假 ffprobe 回放）
# ---------------------------------------------------------------------------


class TestParsing:
    def test_basic_pts_times_preserved_as_raw(self, tmp_path: Path) -> None:
        """逐帧 pts_time 原样解析：原文与 float 并存，index 连续编号。"""
        ffprobe = _install_fake_ffprobe(
            tmp_path,
            mode="json",
            payload=_frames_payload(["0.000000", "0.100000", "0.200000"]),
        )
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)

        frames = probe_frames(target, ffprobe_path=ffprobe)

        assert [f.pts_time for f in frames] == [0.0, 0.1, 0.2]
        assert [f.pts_time_raw for f in frames] == [
            "0.000000",
            "0.100000",
            "0.200000",
        ]
        assert [f.index for f in frames] == [0, 1, 2]
        assert all(f.media_type == "video" for f in frames)
        assert all(isinstance(f, FrameInfo) for f in frames)

    def test_non_uniform_pts_not_normalized(self, tmp_path: Path) -> None:
        """不等间距 pts_time 原样保留：不重排、不补齐、不改精度。"""
        raw_times = ["0.000000", "0.033000", "0.100000", "0.400000"]
        ffprobe = _install_fake_ffprobe(
            tmp_path, mode="json", payload=_frames_payload(raw_times)
        )
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)

        frames = probe_frames(target, ffprobe_path=ffprobe)

        assert [f.pts_time_raw for f in frames] == raw_times
        assert [f.pts_time for f in frames] == [float(t) for t in raw_times]

    def test_negative_and_na_pts_time(self, tmp_path: Path) -> None:
        """负值（B 帧/编辑列表）保留；``N/A``/缺失转 None，不抛错。"""
        payload = {
            "frames": [
                {"media_type": "video", "pts_time": "-0.040000"},
                {"media_type": "video", "pts_time": "N/A"},
                {"media_type": "video"},
            ]
        }
        ffprobe = _install_fake_ffprobe(tmp_path, mode="json", payload=payload)
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)

        frames = probe_frames(target, ffprobe_path=ffprobe)

        assert frames[0].pts_time == -0.04
        assert frames[0].pts_time_raw == "-0.040000"
        assert frames[1].pts_time is None
        assert frames[1].pts_time_raw == "N/A"
        assert frames[2].pts_time is None
        assert frames[2].pts_time_raw is None

    def test_non_video_entries_skipped(self, tmp_path: Path) -> None:
        """混入的非 video 条目跳过，video 帧序号保持连续。"""
        payload = {
            "frames": [
                {"media_type": "audio", "pts_time": "0.000000"},
                {"media_type": "video", "pts_time": "0.000000"},
                {"media_type": "video", "pts_time": "0.100000"},
            ]
        }
        ffprobe = _install_fake_ffprobe(tmp_path, mode="json", payload=payload)
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)

        frames = probe_frames(target, ffprobe_path=ffprobe)

        assert [f.index for f in frames] == [0, 1]
        assert [f.pts_time for f in frames] == [0.0, 0.1]

    def test_empty_frames_returns_empty_list(self, tmp_path: Path) -> None:
        """ffprobe 成功但 0 帧（无流文件）返回空列表而非报错。"""
        ffprobe = _install_fake_ffprobe(tmp_path, mode="json", payload={"frames": []})
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)

        assert probe_frames(target, ffprobe_path=ffprobe) == []


# ---------------------------------------------------------------------------
# 参数校验与安全边界（纯输入校验 + 假 ffprobe 行为）
# ---------------------------------------------------------------------------


class TestValidation:
    def test_missing_file_raises_input_error(self, tmp_path: Path) -> None:
        with pytest.raises(VideoInputError, match="不存在"):
            probe_frames(tmp_path / "nope.mp4", ffprobe_path="/bin/true")

    def test_directory_input_raises_input_error(self, tmp_path: Path) -> None:
        with pytest.raises(VideoInputError, match="不是普通文件"):
            probe_frames(tmp_path, ffprobe_path="/bin/true")

    def test_bad_timeout_values(self, tmp_path: Path) -> None:
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)
        for bad in (0, -1.0, 1e9, float("nan"), True, "5"):
            with pytest.raises(VideoInputError):
                probe_frames(target, timeout=bad, ffprobe_path="/bin/true")

    def test_bad_max_frames_values(self, tmp_path: Path) -> None:
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)
        for bad in (0, -10, 1.5, True, "100"):
            with pytest.raises(VideoInputError):
                probe_frames(target, max_frames=bad, ffprobe_path="/bin/true")

    def test_bad_max_output_bytes(self, tmp_path: Path) -> None:
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)
        for bad in (0, -1, 2.5, False):
            with pytest.raises(VideoInputError):
                probe_frames(target, max_output_bytes=bad, ffprobe_path="/bin/true")

    def test_all_errors_share_base(self, tmp_path: Path) -> None:
        """失败统一走 VideoProbeError 基类，调用方可一次性捕获。"""
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)
        with pytest.raises(VideoProbeError):
            probe_frames(target, timeout=0, ffprobe_path="/bin/true")

    def test_missing_ffprobe_binary(self, tmp_path: Path) -> None:
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)
        with pytest.raises(FFprobeUnavailableError):
            probe_frames(
                target, ffprobe_path=tmp_path / "definitely-not-here"
            )

    def test_tiny_real_mp4_no_streams(self) -> None:
        """真实空壳 MP4（合法 ftyp、无流无帧）：成功返回空列表。"""
        if not _EMPTY_MP4.is_file():  # pragma: no cover —— 仓库固定 fixture
            pytest.fail(f"仓库 fixture 缺失：{_EMPTY_MP4.name}")
        ffprobe = _which_or_skip("ffprobe")
        frames = probe_frames(_EMPTY_MP4, ffprobe_path=ffprobe)
        assert frames == []


def _which_or_skip(name: str) -> str:
    found = shutil.which(name)
    if not found:
        pytest.skip(f"环境未安装 {name}")
    assert found is not None
    return found


# ---------------------------------------------------------------------------
# 子进程行为边界（假 ffprobe 实测触发）
# ---------------------------------------------------------------------------


class TestProcessBoundaries:
    def test_timeout_kills_probe(self, tmp_path: Path) -> None:
        """假 ffprobe sleep(60)，timeout=1 → 显式超时异常而非挂死。"""
        ffprobe = _install_fake_ffprobe(tmp_path, mode="sleep")
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)

        with pytest.raises(VideoProbeTimeoutError, match="秒内未完成"):
            probe_frames(target, timeout=1, ffprobe_path=ffprobe)

    def test_nonzero_exit_sanitizes_path(self, tmp_path: Path) -> None:
        """非零退出：错误信息含文件名，不含绝对路径（脱敏）。"""
        raw = (
            "[mov,mp4,m4a,3gp,3gpp,mj2 @ 0x0] error decoding\n"
            f"file:{tmp_path / 'clip.mp4'}: Invalid data found\n"
        )
        ffprobe = _install_fake_ffprobe(
            tmp_path, mode="err", raw=raw, exit_code=1
        )
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)

        with pytest.raises(VideoProbeFailedError) as excinfo:
            probe_frames(target, ffprobe_path=ffprobe)
        message = str(excinfo.value)
        assert "clip.mp4" in message
        assert str(tmp_path) not in message
        assert "file:" + str(target) not in message

    def test_invalid_json_output(self, tmp_path: Path) -> None:
        """stdout 不是合法 JSON → 显式失败，不返回半结果。"""
        ffprobe = _install_fake_ffprobe(tmp_path, mode="raw", raw="{oops")
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)

        with pytest.raises(VideoProbeFailedError, match="不是合法 JSON"):
            probe_frames(target, ffprobe_path=ffprobe)

    def test_frame_limit_rejected_not_truncated(self, tmp_path: Path) -> None:
        """max_frames=2 但有 3 帧 → 超限异常，绝不静默截断成 2 帧。"""
        ffprobe = _install_fake_ffprobe(
            tmp_path,
            mode="json",
            payload=_frames_payload(["0.0", "0.1", "0.2"]),
        )
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)

        with pytest.raises(VideoProbeOutputLimitError, match="超过上限 2"):
            probe_frames(target, max_frames=2, ffprobe_path=ffprobe)

    def test_output_byte_limit(self, tmp_path: Path) -> None:
        """stdout 字节数超过 max_output_bytes → 显式超限。"""
        ffprobe = _install_fake_ffprobe(
            tmp_path, mode="json", payload=_frames_payload(["0.0", "0.1"])
        )
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"\x00" * 16)

        with pytest.raises(VideoProbeOutputLimitError, match="字节"):
            probe_frames(target, max_output_bytes=16, ffprobe_path=ffprobe)

    def test_default_max_frames_is_bounded(self) -> None:
        """默认上限必须有界（防无界逐帧输出撑爆内存）。"""
        assert 0 < DEFAULT_MAX_FRAMES <= 1_000_000


# ---------------------------------------------------------------------------
# 真实 Golden Sample（可选：DDP_TEST_REAL_VIDEO 注入路径才跑）
# ---------------------------------------------------------------------------


class TestRealGoldenSample:
    @pytest.mark.skipif(
        not os.environ.get(_REAL_VIDEO_ENV),
        reason="未设置 DDP_TEST_REAL_VIDEO，跳过真实 AV1 Golden Sample",
    )
    def test_golden_sample_frame_count_and_pts(self) -> None:
        """lerobot-pusht AV1 样本：25650 帧，pts_time 0.0 起步、单调不减。"""
        video = Path(os.environ[_REAL_VIDEO_ENV])
        ffprobe = _which_or_skip("ffprobe")
        frames = probe_frames(video, ffprobe_path=ffprobe, timeout=120)

        assert len(frames) == 25650
        assert frames[0].pts_time == 0.0
        assert frames[-1].pts_time == pytest.approx(2564.9, abs=1e-6)
        times = [f.pts_time for f in frames]
        assert all(t is not None for t in times)
        assert all(a <= b for a, b in zip(times, times[1:]))
