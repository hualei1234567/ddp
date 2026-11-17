"""导出模块：导出任务 / 动作 / 产物清单 / 产物下载 URL。

覆盖的状态路径
--------------
- 导出任务 A：``QUEUED → RUNNING → SUCCEEDED``，由 ``export-worker`` 真实执行
  （写对象存储并回读校验 Hash），HTTP 线程只做创建与轮询；
- 导出任务 B：``QUEUED → CANCELLED``（取消必须给理由）；
- 负向契约：对已成功任务执行 ``retry`` 必须被拒绝（``409 state_transition_invalid``）。

产物与下载
----------
``GET /exports/{id}/artifacts`` 返回逐文件与汇总 SHA256；下载接口返回短期签名
URL。本地 compose 的对象存储端点只在容器网络内可解析（``http://minio:9000``），
种子进程按 ``Host: minio:9000`` + 回环地址连接已发布的端口，真正下载一次并核对
大小与 SHA256——只校验 URL 字符串不能证明产物可下载。

前置条件
--------
导出只接受 ``FROZEN`` 且有效性为 ``VALID`` 的版本。若项目里没有冻结版本，
说明 datasets 模块尚未跑通（或上游 Episode 不合格），模块如实失败并给出下一步。
"""

from __future__ import annotations

import hashlib
import http.client
import socket
import time
from typing import Any
from urllib.parse import quote, urlsplit

from seed.client import Client, Context, SeedFailure, expect, idempotency_key, if_match

GROUP = "exports"

_FORMAT = "LE_ROBOT"
_FORMAT_VERSION = "ddp.lerobot/1.0.0"
_EXPORTER_ID = "ddp.lerobot-metadata"
_EXPORTER_VERSION = "1.0.0"
_MAPPING_VERSION = "ddp.lerobot/1.0.0"
_TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED"})
_WAIT_SECONDS = 60.0
_LOCAL_ENDPOINT_HOST = "127.0.0.1"


def _ok(label: str, result: tuple[int, Any], want: int = 200) -> Any:
    payload = expect(label, result, want)
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label}：响应不是 JSON 对象：{payload!r}")
    return payload


def _get_object_storage(label: str, client: Client, path: str, *, attempts: int = 5) -> Any:
    """读取依赖对象存储的接口。

    对象存储短暂不可用（``503 object_storage_unavailable``）是可恢复的外部
    依赖故障，按有界退避重试；超过次数仍失败就如实报错，不降级成"空产物"。
    """
    delay = 2.0
    for attempt in range(attempts):
        status, payload = client.call("GET", path)
        if status == 200:
            if not isinstance(payload, dict):
                raise SeedFailure(f"{label}：响应不是 JSON 对象：{payload!r}")
            return payload
        if status != 503 or attempt == attempts - 1:
            raise SeedFailure(f"{label}：期望 200，实际 {status}\n" f"  响应：{str(payload)[:400]}")
        print(f"  [等待] {label}：对象存储暂不可用，{delay:.0f}s 后重试")
        time.sleep(delay)
        delay = min(delay * 1.5, 10.0)
    raise AssertionError("不可达")  # pragma: no cover - 循环必然返回或抛错


def _page(client: Client, path: str, label: str, *, limit: int = 100) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        separator = "&" if "?" in path else "?"
        query = f"{separator}limit={limit}"
        if cursor:
            query += f"&cursor={cursor}"
        payload = _ok(label, client.call("GET", f"{path}{query}"))
        page_items = payload.get("items")
        if not isinstance(page_items, list):
            raise SeedFailure(f"{label}：响应缺少 items")
        items.extend(page_items)
        cursor = payload.get("next_cursor")
        if not cursor:
            return items


def _latest_frozen_version(client: Client, base: str) -> tuple[str, dict[str, Any]]:
    """找出项目里最新的 FROZEN 版本；导出只接受已冻结且有效的版本。"""
    frozen: list[tuple[str, dict[str, Any]]] = []
    for dataset in _page(client, f"{base}/datasets", "列出数据集"):
        dataset_id = str(dataset["dataset_id"])
        versions = _page(
            client,
            f"{base}/datasets/{dataset_id}/versions?status=FROZEN",
            f"列出数据集 {dataset_id} 的冻结版本",
        )
        frozen.extend((dataset_id, version) for version in versions)
    if not frozen:
        raise SeedFailure(
            "项目里没有 FROZEN 数据集版本，导出无法受理："
            "先跑 datasets 模块把版本构建/校验/冻结走通（需要上游 Episode 合格）"
        )
    dataset_id, version = max(frozen, key=lambda item: str(item[1].get("created_at") or ""))
    if version.get("validity_status") != "VALID":
        raise SeedFailure(f"最新冻结版本有效性不是 VALID：{version.get('validity_status')}")
    return dataset_id, version


def _create_job(
    client: Client, base: str, version_id: str, label: str, *, key: str
) -> dict[str, Any]:
    return _ok(
        label,
        client.call(
            "POST",
            f"{base}/exports",
            body={
                "dataset_version_id": version_id,
                "format": _FORMAT,
                "format_version": _FORMAT_VERSION,
                "exporter_id": _EXPORTER_ID,
                "exporter_version": _EXPORTER_VERSION,
                "mapping_version": _MAPPING_VERSION,
                "config": {"include_sidecar": True},
                "timeout_seconds": 600,
                "max_attempts": 3,
            },
            headers={"Idempotency-Key": key},
        ),
        202,
    )


def _wait_terminal(client: Client, job_path: str, label: str) -> dict[str, Any]:
    deadline = time.monotonic() + _WAIT_SECONDS
    while True:
        job = _ok(label, client.call("GET", job_path))
        if job.get("status") in _TERMINAL_STATUSES:
            return job
        if time.monotonic() >= deadline:
            raise SeedFailure(
                f"{label}：等待 {int(_WAIT_SECONDS)}s 后仍为 {job.get('status')}，"
                "导出 Worker 未推进（BLOCKED：检查 export-worker 容器与 outbox 事件）"
            )
        time.sleep(1.0)


def _fetch_presigned(url: str) -> bytes:
    """下载预签名 URL。

    本地 compose 的端点主机名（``minio``）只在容器网络内可解析，因此当直接解析
    失败时，改为连接回环地址的已发布端口，同时保留原始 ``Host`` 头——S3 签名
    覆盖 Host，改写 Host 会让签名失效。
    """
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname:
        raise SeedFailure(f"下载 URL 不是可用的 http 地址：{url[:120]}")
    port = parts.port or 80
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    headers = {"Host": parts.netloc}
    try:
        connection = http.client.HTTPConnection(parts.hostname, port, timeout=20)
        connection.connect()
    except socket.gaierror:
        connection = http.client.HTTPConnection(_LOCAL_ENDPOINT_HOST, port, timeout=20)
    try:
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        body = response.read()
        status = response.status
    finally:
        connection.close()
    if status != 200:
        raise SeedFailure(f"产物下载失败：HTTP {status}")
    return body


def _verify_artifact(client: Client, base: str, job_id: str, manifest: dict[str, Any]) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise SeedFailure("产物清单为空")
    entry = artifacts[0]
    path = str(entry.get("path") or "")
    if not path:
        raise SeedFailure("产物清单缺少 path")
    download = _get_object_storage(
        "获取产物下载地址",
        client,
        f"{base}/exports/{job_id}/artifacts/{quote(path, safe='/')}/download",
    )
    for field in ("download_url", "download_url_expires_at", "sha256", "size_bytes"):
        if not download.get(field):
            raise SeedFailure(f"下载地址响应缺少 {field}：{download}")
    body = _fetch_presigned(str(download["download_url"]))
    if len(body) != int(download["size_bytes"]):
        raise SeedFailure(
            f"产物大小与清单不一致：下载 {len(body)} != 清单 {download['size_bytes']}"
        )
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    if digest != download["sha256"]:
        raise SeedFailure(f"产物 Hash 校验失败：{digest} != {download['sha256']}")
    print(f"  产物 {path} 下载校验通过（{len(body)} bytes）")


def _cancel_job(client: Client, job_path: str, job: dict[str, Any], label: str) -> dict[str, Any]:
    """取消任务。若 Worker 已抢先跑完，取消会 409；此时如实记录真实终态。"""
    result = client.call(
        "POST",
        f"{job_path}/actions/cancel",
        body={"reason": "演示：提交后发现导出范围有误，取消该任务"},
        headers=if_match(int(job["version"])),
    )
    status, payload = result
    if status == 200:
        cancelled = expect(label, result, 200)
    elif status == 409:
        current = _ok(f"{label}（取消冲突后复查）", client.call("GET", job_path))
        if current.get("status") != "SUCCEEDED":
            raise SeedFailure(
                f"{label}：取消返回 409，且任务状态为 {current.get('status')}，"
                f"不是可解释的竞态：{payload}"
            )
        print("  [说明] 取消请求与 Worker 完成发生竞态，任务已 SUCCEEDED，按真实终态记录")
        cancelled = current
    else:
        raise SeedFailure(f"{label}：期望 200/409，实际 {status}\n  响应：{payload}")
    return cancelled


def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"

    dataset_id, version = _latest_frozen_version(client, base)
    version_id = ctx.remember(GROUP, "dataset_version_id", str(version["dataset_version_id"]))
    print(f"  使用冻结版本 {version_id}（数据集 {dataset_id}）")

    # --- 任务 A：QUEUED → RUNNING → SUCCEEDED（export-worker 真实执行） ---
    job = _create_job(
        client,
        base,
        version_id,
        "创建导出任务（LeRobot 元数据）",
        key=idempotency_key(),
    )
    job_id = ctx.remember(GROUP, "export_job_id", str(job["export_job_id"]))
    job_path = f"{base}/exports/{job_id}"
    if job.get("status") != "QUEUED":
        raise SeedFailure(f"新建导出任务应为 QUEUED，实际 {job.get('status')}")
    finished = _wait_terminal(client, job_path, "等待导出任务完成")
    if finished.get("status") != "SUCCEEDED":
        raise SeedFailure(
            f"导出任务未成功：status={finished.get('status')} "
            f"error={finished.get('error_code')} {finished.get('error_summary')}"
        )
    ctx.remember(GROUP, "export_status", str(finished["status"]))

    manifest = _get_object_storage("读取产物清单", client, f"{job_path}/artifacts")
    if int(manifest.get("artifact_count", 0)) < 1:
        raise SeedFailure("产物清单为空")
    ctx.remember(GROUP, "artifact_count", str(manifest["artifact_count"]))
    _verify_artifact(client, base, job_id, manifest)

    # 终态不可重试：成功任务 retry 必须被拒绝（负向契约）。
    _ok(
        "成功任务 retry 应被拒绝",
        client.call(
            "POST",
            f"{job_path}/actions/retry",
            headers=if_match(int(finished["version"])),
        ),
        409,
    )

    # --- 任务 B：QUEUED → CANCELLED ---
    second = _create_job(
        client,
        base,
        version_id,
        "创建导出任务（待取消）",
        key=idempotency_key(),
    )
    second_id = ctx.remember(GROUP, "cancelled_export_job_id", str(second["export_job_id"]))
    cancelled = _cancel_job(client, f"{base}/exports/{second_id}", second, "取消导出任务")
    ctx.remember(GROUP, "cancelled_export_status", str(cancelled.get("status")))
    pending = _ok("读取第二导出任务", client.call("GET", f"{base}/exports/{second_id}"))
    if pending.get("status") not in {"CANCELLED", "SUCCEEDED"}:
        raise SeedFailure(f"第二导出任务终态异常：{pending.get('status')}")

    # 未成功任务不可取产物清单（负向契约）。
    if pending.get("status") == "CANCELLED":
        _ok(
            "已取消任务取产物清单应被拒绝",
            client.call("GET", f"{base}/exports/{second_id}/artifacts"),
            409,
        )
