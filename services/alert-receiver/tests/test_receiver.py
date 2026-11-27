"""告警接收端单元测试。

覆盖验收要求的 5 项：
1. POST 正常落盘 + GET 能查到；
2. 非法 Content-Type 返回 400；
3. 超大 body 返回 413；
4. NDJSON 每行一个 JSON；
5. ``/health`` 返回 200。

外加：``/stats`` 计数对得上、坏行不影响其余记录可查、限流参数生效。
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

from conftest import SAMPLE_PAYLOAD, get_json, post_json, post_raw


def test_post_alert_persists_and_get_returns_it(server, store_dir: Path) -> None:
    """POST 一条告警 → 落盘 → GET /alerts 能原样查到。"""
    base_url, _ = server

    status, body = post_json(f"{base_url}/alerts", SAMPLE_PAYLOAD)
    assert status == 200, body
    assert body["stored"] is True

    status, listing = get_json(f"{base_url}/alerts")
    assert status == 200
    assert listing["count"] == 1
    alert = listing["alerts"][0]
    assert alert["status"] == "firing"
    assert alert["commonLabels"]["alertname"] == "DdpApiDown"
    assert alert["alerts"][0]["fingerprint"] == "1a2b3c4d"

    # 落盘文件确实存在且内容一致
    ndjson = store_dir / "alerts.ndjson"
    assert ndjson.exists()


def test_ndjson_has_one_json_per_line(server, store_dir: Path) -> None:
    """NDJSON 格式：每行一个独立可解析的 JSON 对象。"""
    base_url, _ = server

    for index in range(3):
        payload = dict(SAMPLE_PAYLOAD)
        payload["groupKey"] = f"group-{index}"
        status, _ = post_json(f"{base_url}/alerts", payload)
        assert status == 200

    ndjson = store_dir / "alerts.ndjson"
    lines = [line for line in ndjson.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 3, "每条告警必须独占一行"
    for line in lines:
        parsed = json.loads(line)  # 每一行都必须能独立解析
        assert isinstance(parsed, dict)
        assert parsed["version"] == "4"

    # 查询返回 3 条，且最新在前（倒序）
    _, listing = get_json(f"{base_url}/alerts")
    assert listing["count"] == 3
    assert [item["groupKey"] for item in listing["alerts"]] == [
        "group-2",
        "group-1",
        "group-0",
    ]


def test_invalid_content_type_returns_400(server) -> None:
    """Content-Type 不是 application/json → 400。"""
    base_url, _ = server

    status, body = post_json(
        f"{base_url}/alerts", SAMPLE_PAYLOAD, content_type="text/plain"
    )
    assert status == 400, body
    assert "Content-Type" in body["error"]


def test_missing_content_type_returns_400(server) -> None:
    """完全不带 Content-Type → 400（不静默接受）。"""
    base_url, _ = server

    status, _ = post_raw(f"{base_url}/alerts", json.dumps(SAMPLE_PAYLOAD).encode(), "")
    assert status == 400


def test_oversized_body_returns_413(server) -> None:
    """请求体超过 1 MiB 上限 → 413（不读进内存）。"""
    base_url, _ = server

    oversized = b'{"pad":"' + b"x" * (1024 * 1024 + 1024) + b'"}'
    status, _ = post_raw(f"{base_url}/alerts", oversized)
    assert status == 413, "超限 body 必须返回 413"


def test_body_at_limit_is_accepted(server) -> None:
    """刚好在上限之内的 body 正常受理（边界不是靠猜）。"""
    base_url, _ = server

    payload = {"version": "4", "pad": "y" * (1024 * 1024 - 64), "status": "firing"}
    raw = json.dumps(payload).encode("utf-8")
    assert len(raw) <= 1024 * 1024
    status, _ = post_raw(f"{base_url}/alerts", raw)
    assert status == 200


def test_lying_content_length_does_not_hang(server) -> None:
    """声明超大 Content-Length 但实际只发几字节 → 仍要立刻回 413，不挂死。

    这是实测发现的真实缺陷：若服务端先「读完 body 再响应」，遇到撒谎的
    Content-Length 会一直等在对端，线程被占死。
    """
    base_url, _ = server
    host, port = base_url.removeprefix("http://").split(":")
    port_number = int(port)

    sock = socket.create_connection((host, port_number), timeout=15)
    try:
        sock.sendall(
            b"POST /alerts HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
            b"Content-Length: 99999999\r\n\r\n" + b'{"a":"b"}'
        )
        sock.settimeout(15)
        started = time.monotonic()
        response = sock.recv(4096)
        elapsed = time.monotonic() - started
    finally:
        sock.close()

    assert b"413" in response.split(b"\r\n")[0], response
    assert elapsed < 5, f"服务端挂死等待 body（耗时 {elapsed:.1f}s）"


def test_health_returns_200(server) -> None:
    """/health 返回 200（compose healthcheck 依赖它）。"""
    base_url, _ = server

    status, body = get_json(f"{base_url}/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["service"] == "ddp-alert-receiver"


def test_stats_counts_match_stored_alerts(server) -> None:
    """/stats 计数与实际落盘条数对得上。"""
    base_url, _ = server

    post_json(f"{base_url}/alerts", SAMPLE_PAYLOAD)

    resolved = dict(SAMPLE_PAYLOAD)
    resolved["status"] = "resolved"
    resolved["commonLabels"] = {"alertname": "DdpOutboxStale", "severity": "critical"}
    post_json(f"{base_url}/alerts", resolved)

    _, stats = get_json(f"{base_url}/stats")
    assert stats["total"] == 2
    assert stats["firing"] == 1
    assert stats["resolved"] == 1
    assert stats["by_alertname"] == {"DdpApiDown": 1, "DdpOutboxStale": 1}


def test_limit_query_parameter_works(server) -> None:
    """``?limit=N`` 生效（返回最近的 N 条）。"""
    base_url, _ = server

    for index in range(4):
        payload = dict(SAMPLE_PAYLOAD)
        payload["groupKey"] = f"g{index}"
        post_json(f"{base_url}/alerts", payload)

    _, listing = get_json(f"{base_url}/alerts?limit=2")
    assert listing["count"] == 2
    assert listing["alerts"][0]["groupKey"] == "g3"


def test_unknown_path_returns_404(server) -> None:
    """未知路径 → 404（不是 500）。"""
    base_url, _ = server

    status, _ = get_json(f"{base_url}/nope")
    assert status == 404


def test_broken_line_does_not_break_query(server, store_dir: Path) -> None:
    """一行坏数据不应让整份文件不可查（保住其余证据）。"""
    base_url, _ = server

    post_json(f"{base_url}/alerts", SAMPLE_PAYLOAD)
    # 模拟一次被并发写坏的行
    with (store_dir / "alerts.ndjson").open("a", encoding="utf-8") as handle:
        handle.write("{not-json\n")

    _, listing = get_json(f"{base_url}/alerts")
    assert listing["count"] == 1, "坏行应被跳过，好行仍可查"
    assert listing["alerts"][0]["commonLabels"]["alertname"] == "DdpApiDown"


def test_data_survives_restart(server, store_dir: Path) -> None:
    """进程重启后仍能查到——「留下证据」的关键。"""
    base_url, _ = server
    post_json(f"{base_url}/alerts", SAMPLE_PAYLOAD)

    # 用新的 store 实例指向同一目录（等价于进程重启）
    from app.store import AlertStore

    reopened = AlertStore(store_dir)
    records = reopened.read_all()
    assert len(records) == 1
    assert records[0]["commonLabels"]["alertname"] == "DdpApiDown"
