"""HF 导入器测试：不联网，用 fake fetcher 注入字节。"""

from __future__ import annotations

import pytest
from ddp_data.huggingface.importer import (
    DEFAULT_MAX_FILE_BYTES,
    ImportError_,
    fetch_file,
    resolve_url,
)

_SHA = "0123456789abcdef0123456789abcdef01234567"
_MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 100


class FakeFetcher:
    def __init__(self, payload: bytes | Exception, *, chunk: int = 32) -> None:
        self._payload = payload
        self._chunk = chunk
        self.urls: list[str] = []

    def stream(self, url: str):
        self.urls.append(url)
        if isinstance(self._payload, Exception):
            raise self._payload
        for i in range(0, len(self._payload), self._chunk):
            yield self._payload[i : i + self._chunk]


def test_downloads_and_hashes_mp4() -> None:
    fetcher = FakeFetcher(_MP4)

    result = fetch_file(fetcher, repo_id="o/n", revision=_SHA, path="videos/a.mp4")

    assert result.size_bytes == len(_MP4)
    assert result.content_type == "video/mp4"
    assert result.sha256.startswith("sha256:")
    assert len(result.sha256) == 71


def test_url_uses_commit_sha_not_branch() -> None:
    fetcher = FakeFetcher(_MP4)

    fetch_file(fetcher, repo_id="o/n", revision=_SHA, path="videos/a.mp4")

    assert _SHA in fetcher.urls[0]
    assert "/main/" not in fetcher.urls[0]


def test_branch_name_revision_is_rejected() -> None:
    with pytest.raises(ImportError_, match="40 位 commit sha"):
        resolve_url("o/n", "main", "videos/a.mp4")


def test_renamed_file_is_rejected_by_content_signature() -> None:
    """改扩展名是最常见的绕过手段；必须按内容签名判定。"""
    fetcher = FakeFetcher(b"not really an mp4 file at all")

    with pytest.raises(ImportError_, match="内容与扩展名不符"):
        fetch_file(fetcher, repo_id="o/n", revision=_SHA, path="videos/a.mp4")


def test_unsupported_extension_is_rejected() -> None:
    with pytest.raises(ImportError_, match="不支持的文件类型"):
        fetch_file(FakeFetcher(_MP4), repo_id="o/n", revision=_SHA, path="x.exe")


def test_oversized_file_is_rejected_while_streaming() -> None:
    big = b"\x00\x00\x00\x20ftyp" + b"\x00" * 5000

    with pytest.raises(ImportError_, match="超过上限"):
        fetch_file(
            FakeFetcher(big, chunk=100),
            repo_id="o/n",
            revision=_SHA,
            path="videos/a.mp4",
            max_bytes=1000,
        )


def test_default_limit_is_documented() -> None:
    """小样本导入不应出现超大单文件；上限必须显式可查。"""
    assert DEFAULT_MAX_FILE_BYTES == 64 * 1024 * 1024


def test_empty_file_is_rejected() -> None:
    with pytest.raises(ImportError_, match="文件为空"):
        fetch_file(FakeFetcher(b""), repo_id="o/n", revision=_SHA, path="videos/a.mp4")


def test_jsonl_content_signature() -> None:
    fetcher = FakeFetcher(b'{"example_id": "demo_001"}\n')

    result = fetch_file(fetcher, repo_id="o/n", revision=_SHA, path="preview/metadata.jsonl")

    assert result.content_type == "application/x-ndjson"


def test_fetch_error_propagates() -> None:
    with pytest.raises(RuntimeError, match="404"):
        fetch_file(
            FakeFetcher(RuntimeError("404 not found")),
            repo_id="o/n",
            revision=_SHA,
            path="videos/a.mp4",
        )
