"""耐久文件系统原语：fsync、SHA256、原子 rename。

Hash 使用标准库 ``hashlib.file_digest``，不手写分块循环。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from edge_agent.errors import SpoolIntegrityError


def sha256_file(path: Path) -> str:
    """返回文件内容的 SHA256 十六进制（小写）。"""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def sha256_range(path: Path, offset: int, length: int, chunk_bytes: int) -> str:
    """计算文件 [offset, offset+length) 的 SHA256（分片 Hash）。"""
    digest = hashlib.sha256()
    remaining = length
    with path.open("rb") as handle:
        handle.seek(offset)
        while remaining > 0:
            block = handle.read(min(chunk_bytes, remaining))
            if not block:
                raise SpoolIntegrityError(f"文件在读取分片时被截断：{path}")
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_dir(path: Path) -> None:
    """fsync 目录，使其中的创建 / rename 落盘。"""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def copy_file_durable(src: Path, dst: Path) -> None:
    """复制并 fsync 目标文件及其父目录。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    fsync_file(dst)
    fsync_dir(dst.parent)


def write_bytes_durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    fsync_dir(path.parent)


def atomic_rename_dir(src: Path, dst: Path) -> None:
    """同一文件系统内原子重命名目录；目标已存在视为完整性错误（不覆盖）。"""
    if dst.exists():
        raise SpoolIntegrityError(f"目标目录已存在，拒绝覆盖：{dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.rename(src, dst)
    fsync_dir(dst.parent)
    fsync_dir(src.parent)
