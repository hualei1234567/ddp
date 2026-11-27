"""接收端测试夹具：在随机端口起一个真实 HTTP 服务（不用 Mock）。"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

# 服务代码在 services/alert-receiver/app 下；把它加进 sys.path 才能 import app.*
SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.main import build_server  # noqa: E402 - 需先改 sys.path


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    """每个测试一个独立落盘目录（隔离证据文件）。"""
    target = tmp_path / "alerts"
    target.mkdir()
    return target


@pytest.fixture
def server(store_dir: Path):
    """在随机端口上启动真实接收端，测试结束关闭。"""
    httpd = build_server(host="127.0.0.1", port=0, data_dir=store_dir)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}", httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def post_json(url: str, payload: dict[str, Any], content_type: str = "application/json"):
    """POST 一个 JSON 到接收端（返回 urllib 响应）。"""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": content_type}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def get_json(url: str):
    """GET 一个 JSON（返回 urllib 响应）。

    错误状态码（如 404）也返回而非抛异常——测试要断言的是状态码本身。
    """
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def post_raw(url: str, body: bytes, content_type: str = "application/json"):
    """POST 原始字节（用于测超大 body）。"""
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": content_type}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


#: 一条真实的 Alertmanager webhook payload（v4 格式）
SAMPLE_PAYLOAD: dict[str, Any] = {
    "version": "4",
    "groupKey": "{}:{alertname=\"DdpApiDown\"}",
    "status": "firing",
    "receiver": "default-webhook",
    "groupLabels": {"alertname": "DdpApiDown"},
    "commonLabels": {
        "alertname": "DdpApiDown",
        "severity": "critical",
        "component": "api",
    },
    "commonAnnotations": {
        "summary": "DDP API 抓取失败超过 2 分钟",
        "description": "检查 api 容器是否存活",
    },
    "externalURL": "http://localhost:9193",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "DdpApiDown",
                "severity": "critical",
                "component": "api",
            },
            "annotations": {
                "summary": "DDP API 抓取失败超过 2 分钟",
                "description": "检查 api 容器是否存活",
            },
            "startsAt": "2026-11-27T10:00:00.000Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://localhost:9190/graph?g0.expr=up%7Bjob%3D%22ddp-api%22%7D",
            "fingerprint": "1a2b3c4d",
        }
    ],
}
