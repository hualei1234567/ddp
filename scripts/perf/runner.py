"""HTTP 测量执行器与 API 进程生命周期。

统计口径由 :mod:`perf.core` 统一；本模块只负责并发发请求、记录耗时与错误，
并管理一个**只指向临时库**的 uvicorn 子进程。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from perf.core import summarize


@dataclass(frozen=True, slots=True)
class RequestSpec:
    label: str
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    expected_status: int = 200


@dataclass(slots=True)
class Sample:
    label: str
    seconds: float
    status: int | None
    error: str | None


@dataclass(slots=True)
class RunResult:
    summary: dict[str, float | int]
    labels: dict[str, dict[str, float | int]]
    wall_seconds: float
    requests: int
    concurrency: int
    errors: list[str]

    def as_report(self) -> dict[str, Any]:
        return {
            "concurrency": self.concurrency,
            "requests": self.requests,
            "wall_seconds": round(self.wall_seconds, 4),
            "summary": self.summary,
            "labels": self.labels,
            "sample_errors": self.errors[:10],
        }


def bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def run_requests(
    specs: Sequence[RequestSpec],
    *,
    concurrency: int,
    requests: int,
    warmup: int = 3,
    timeout_seconds: float = 15.0,
) -> RunResult:
    """按固定变体序列发请求，返回聚合统计。

    - 每个 worker 独立 ``httpx.Client``，避免共享连接池掩盖并发问题；
    - warmup 请求单独成批、不计入统计；
    - 非期望状态码与传输异常都算错误：基线测成功路径，非 2xx 是"坏"不是"慢"。
    """
    if not specs:
        raise ValueError("场景至少需要一个请求变体")
    if concurrency < 1:
        raise ValueError("并发度必须 >= 1")
    if requests < 1:
        raise ValueError("请求数必须 >= 1")
    variants = list(specs)
    warmup_tasks = [variants[index % len(variants)] for index in range(warmup * concurrency)]
    measured_tasks = [variants[index % len(variants)] for index in range(requests)]
    if warmup_tasks:
        _run_batch(warmup_tasks, concurrency=concurrency, timeout_seconds=timeout_seconds)
    samples, wall_seconds = _run_batch(
        measured_tasks, concurrency=concurrency, timeout_seconds=timeout_seconds
    )

    def _summaries(group: Sequence[Sample]) -> dict[str, float | int]:
        return summarize(
            [sample.seconds * 1000 for sample in group],
            wall_seconds=wall_seconds,
            error_count=sum(1 for sample in group if sample.error),
        )

    by_label: dict[str, list[Sample]] = {}
    for sample in samples:
        by_label.setdefault(sample.label, []).append(sample)
    return RunResult(
        summary=_summaries(samples),
        labels={label: _summaries(group) for label, group in sorted(by_label.items())},
        wall_seconds=wall_seconds,
        requests=len(samples),
        concurrency=concurrency,
        errors=[f"{sample.label}: {sample.error}" for sample in samples if sample.error],
    )


def _run_batch(
    tasks: Sequence[RequestSpec],
    *,
    concurrency: int,
    timeout_seconds: float,
) -> tuple[list[Sample], float]:
    """并发执行一批请求；返回样本与从统一起跑到全部结束的墙钟时间。"""
    samples: list[Sample] = []
    failures: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(concurrency + 1)

    def worker(index: int) -> None:
        assigned = tasks[index::concurrency]
        local: list[Sample] = []
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=4)
        with httpx.Client(timeout=timeout_seconds, limits=limits) as client:
            barrier.wait()
            for spec in assigned:
                started = time.perf_counter()
                try:
                    response = client.request(spec.method, spec.url, headers=spec.headers)
                    elapsed = time.perf_counter() - started
                    error = None
                    if response.status_code != spec.expected_status:
                        detail = response.text[:200].replace("\n", " ")
                        error = (
                            f"HTTP {response.status_code}（期望 {spec.expected_status}）：{detail}"
                        )
                    local.append(Sample(spec.label, elapsed, response.status_code, error))
                except httpx.HTTPError as exc:
                    local.append(
                        Sample(
                            spec.label,
                            time.perf_counter() - started,
                            None,
                            f"{type(exc).__name__}: {exc}",
                        )
                    )
                except BaseException as exc:  # 编程错误必须上抛，不能变成"少测了几条"
                    with lock:
                        failures.append(exc)
                    break
        with lock:
            samples.extend(local)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(concurrency)]
    for thread in threads:
        thread.start()
    barrier.wait()
    wall_started = time.perf_counter()
    for thread in threads:
        thread.join()
    wall_seconds = time.perf_counter() - wall_started
    if failures:
        raise failures[0]
    if len(samples) != len(tasks):
        raise RuntimeError(f"样本数 {len(samples)} 与请求数 {len(tasks)} 不一致")
    return samples, wall_seconds


def fetch_playback_urls(
    base_url: str,
    token: str,
    project_id: str,
    asset_ids: Sequence[str],
    *,
    timeout_seconds: float = 15.0,
) -> list[str]:
    headers = bearer_headers(token)
    urls: list[str] = []
    with httpx.Client(timeout=timeout_seconds) as client:
        for asset_id in asset_ids:
            response = client.post(
                f"{base_url.rstrip('/')}/api/v1/projects/{project_id}"
                f"/media-assets/{asset_id}/playback",
                headers=headers,
            )
            if response.status_code != 200:
                raise RuntimeError(f"签发播放 URL 失败：HTTP {response.status_code}")
            payload = response.json()
            url = payload.get("url")
            if not isinstance(url, str) or not url:
                raise RuntimeError("播放 URL 响应缺少 url")
            urls.append(url)
    return urls


def build_range_specs(
    urls: Sequence[str],
    *,
    object_size: int,
    chunk_size: int,
    requests: int,
) -> list[RequestSpec]:
    """按首段/中段/尾段循环构造 Range 请求；每次 64 KiB。"""
    if not urls:
        raise ValueError("Range 场景需要至少一个播放 URL")
    if chunk_size < 1 or object_size < chunk_size:
        raise ValueError("对象大小必须 >= 分片大小")
    offsets = (0, object_size // 2, object_size - chunk_size)
    labels = ("head", "middle", "tail")
    specs: list[RequestSpec] = []
    for index in range(requests):
        offset = offsets[index % len(offsets)]
        url = urls[index % len(urls)]
        specs.append(
            RequestSpec(
                label=labels[index % len(labels)],
                method="GET",
                url=url,
                headers={"Range": f"bytes={offset}-{offset + chunk_size - 1}"},
                expected_status=206,
            )
        )
    return specs


def verify_range(
    url: str, *, object_size: int, chunk_size: int, timeout_seconds: float = 15.0
) -> None:
    """测量前先验证 206 + Content-Range 总长，避免把"坏路径"当"慢路径"。"""
    with httpx.Client(timeout=timeout_seconds) as client:
        # URL 来自压测目标（--base-url）或服务端返回的预签名播放地址；本地压测工具。
        # nosemgrep: ddp-ssrf-unvalidated-url —— 非服务端出站面，目标由操作者显式指定
        response = client.get(url, headers={"Range": f"bytes=0-{chunk_size - 1}"})
    if response.status_code != 206:
        raise RuntimeError(f"Range 校验失败：HTTP {response.status_code}")
    content_range = response.headers.get("content-range", "")
    expected = f"bytes 0-{chunk_size - 1}/{object_size}"
    if content_range != expected:
        raise RuntimeError(f"Range 校验失败：Content-Range={content_range!r}，期望 {expected!r}")
    if len(response.content) != chunk_size:
        raise RuntimeError(f"Range 校验失败：实际 {len(response.content)} 字节")


def find_free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ApiServer:
    """为一次基线运行启动的临时 API 进程；只连传入的临时库。"""

    def __init__(self, *, backend_dir: Path, dsn: str, port: int) -> None:
        self._backend_dir = backend_dir
        self._dsn = dsn
        self.port = find_free_port(port)
        self._process: subprocess.Popen[bytes] | None = None
        self._log_path: Path | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, *, timeout_seconds: float = 40.0) -> None:
        env = dict(os.environ)
        env.update(
            {
                "DDP_ENV": "local",
                "DDP_DATABASE_URL": self._dsn,
                # 基线测的是端点本身，不是限流器；限流会制造与数据规模无关的 429。
                "DDP_RATE_LIMIT_ENABLED": "false",
                "DDP_LOG_LEVEL": "WARNING",
            }
        )
        handle = tempfile.NamedTemporaryFile(prefix="ddp-perf-api-", suffix=".log", delete=False)
        self._log_path = Path(handle.name)
        handle.close()
        log = self._log_path.open("wb")
        try:
            self._process = subprocess.Popen(  # noqa: S603 - 固定参数，不接受外部输入
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.port),
                    "--log-level",
                    "warning",
                ],
                cwd=str(self._backend_dir),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        finally:
            log.close()
        self._wait_healthy(timeout_seconds=timeout_seconds)

    def _wait_healthy(self, *, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                detail = self.tail_log()
                raise RuntimeError(f"API 启动失败（exit={self._process.returncode}）：{detail}")
            try:
                response = httpx.get(f"{self.base_url}/health", timeout=1.0)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"API 健康检查超时（{timeout_seconds}s）：{self.tail_log()}")

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        self._process = None

    def tail_log(self, lines: int = 20) -> str:
        if self._log_path is None or not self._log_path.exists():
            return ""
        content = self._log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-lines:])

    def cleanup_log(self) -> None:
        if self._log_path is not None and self._log_path.exists():
            self._log_path.unlink(missing_ok=True)
