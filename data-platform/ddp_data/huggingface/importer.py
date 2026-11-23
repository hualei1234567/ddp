"""从 Hugging Face 拉取小样本文件：流式下载 + 内容校验。

设计边界（`docs/ddp/07` §1/§5）：

- **流式**：按块读取并同时计算 sha256，不在内存里攒整个文件；
- **只读已登记 revision**：URL 里带 commit sha，不读 ``main``；
- **校验内容而非后缀**：MP4 必须真的有 ``ftyp`` box，JSON 必须能解析；
  扩展名不匹配一律拒绝；
- **失败留痕**：任何异常向上抛，调用方把错误写进导入任务，不静默跳过。

本模块不依赖 FastAPI / SQLAlchemy；``HttpFetcher`` 是协议，生产用 httpx，
测试注入 fake，因此单元测试不联网。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Protocol

# 单个文件大小上限：小样本导入不应出现超大单文件。
# NVIDIA Human-1M 的预览视频约 0.7–3MB，留足余量但拒绝异常大文件。
DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
_CHUNK = 1024 * 1024


class ImportError_(Exception):
    """导入过程中的可预期失败；调用方应记为导入失败并保留原因。"""


@dataclass(frozen=True, slots=True)
class FetchedFile:
    """已完成下载与校验的文件。"""

    path: str
    size_bytes: int
    sha256: str
    content_type: str
    data: bytes


class HttpFetcher(Protocol):
    """最小下载协议；生产实现走 httpx，测试注入 fake。"""

    def stream(self, url: str) -> Iterator[bytes]:
        """按块产出响应体；HTTP 非 2xx 应抛异常。"""
        ...


# 允许导入的文件类型 → 内容签名判定。
# 用内容签名而非后缀，是因为"改个扩展名"是最常见的绕过手段。
def _looks_like_mp4(head: bytes) -> bool:
    # ISO BMFF：4 字节 size + 'ftyp' at offset 4。
    return len(head) >= 12 and head[4:8] == b"ftyp"


def _looks_like_jsonl(head: bytes) -> bool:
    text = head.lstrip()
    return text.startswith(b"{") or text.startswith(b"[")


def _looks_like_parquet(head: bytes) -> bool:
    return head.startswith(b"PAR1")


_ALLOWED_SUFFIX = {
    ".mp4": ("video/mp4", _looks_like_mp4),
    ".jsonl": ("application/x-ndjson", _looks_like_jsonl),
    ".json": ("application/json", _looks_like_jsonl),
    ".parquet": ("application/vnd.apache.parquet", _looks_like_parquet),
    ".md": ("text/markdown", lambda head: True),
    ".txt": ("text/plain", lambda head: True),
}


def resolve_url(repo_id: str, revision: str, path: str) -> str:
    """构造带 commit sha 的下载 URL；绝不使用 main。"""
    if len(revision) != 40 or not all(c in "0123456789abcdef" for c in revision):
        raise ImportError_(f"revision 必须是 40 位 commit sha，得到 {revision!r}")
    return f"https://huggingface.co/datasets/{repo_id}/resolve/{revision}/{path}"


def fetch_file(
    fetcher: HttpFetcher,
    *,
    repo_id: str,
    revision: str,
    path: str,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    on_progress: Callable[[int, int], None] | None = None,
) -> FetchedFile:
    """下载单个文件并校验类型、大小与内容签名。"""
    suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    spec = _ALLOWED_SUFFIX.get(suffix)
    if spec is None:
        raise ImportError_(
            f"不支持的文件类型：{path}（允许：{sorted(_ALLOWED_SUFFIX)}）"
        )
    content_type, check = spec

    url = resolve_url(repo_id, revision, path)
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    for chunk in fetcher.stream(url):
        total += len(chunk)
        if total > max_bytes:
            # 边下边判，不等到下完才发现超限——避免被超大文件拖垮。
            raise ImportError_(f"文件超过上限 {max_bytes} 字节：{path}")
        digest.update(chunk)
        chunks.append(chunk)
        if on_progress is not None:
            on_progress(total, -1)

    if total == 0:
        raise ImportError_(f"文件为空：{path}")

    data = b"".join(chunks)
    if not check(data[:64]):
        raise ImportError_(
            f"文件内容与扩展名不符（{suffix}）：{path}；" "拒绝导入可能被改名的文件"
        )

    return FetchedFile(
        path=path,
        size_bytes=total,
        sha256="sha256:" + digest.hexdigest(),
        content_type=content_type,
        data=data,
    )


def load_metadata_jsonl(
    files: dict[str, FetchedFile], path: str
) -> list[dict[str, object]]:
    """解析 metadata.jsonl；空行跳过，非对象行视为格式错误。"""
    item = files.get(path)
    if item is None:
        raise ImportError_(f"缺少元数据文件：{path}")
    rows: list[dict[str, object]] = []
    for lineno, line in enumerate(item.data.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ImportError_(f"{path}:{lineno} 不是合法 JSON：{exc}") from exc
        if not isinstance(value, dict):
            raise ImportError_(f"{path}:{lineno} 必须是 JSON 对象")
        rows.append(value)
    if not rows:
        raise ImportError_(f"{path} 不含任何记录")
    return rows
