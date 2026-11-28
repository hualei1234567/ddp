"""Alertmanager → alert-receiver 告警闭环端到端演练脚本。

## 演练什么

构造真实 Alertmanager v4 Webhook payload，投递至本地拉起的真实 ``alert-receiver``
实例（默认）或已运行的实例（``--target-url``），端到端断言以下闭环判据：

1. ``GET /health`` 探针返回 200；
2. 签名与鉴权守卫：缺令牌 / 错签名返回 401，合法 Bearer + HMAC-SHA256 返回 200；
3. 告警落盘：``firing`` 告警真实写入 ``alerts.ndjson``，每行一条合法 JSON；
4. 去重处理：窗口内重复 ``firing`` 告警返回 ``status=deduplicated, stored=false``，
   不重复落盘；
5. 恢复事件处理：同 ``groupKey`` / ``fingerprint`` 的 ``resolved`` 恢复告警正常落盘，
   ``GET /alerts`` 与 ``GET /stats`` 及落盘行数三方计数一致。

## 依赖约束（MEM-INC-030）

本脚本顶层**仅导入 Python 标准库**，可在仅装 ``pytest + pyyaml`` 的最小 CI 环境中
直接导入并测试。

## 用法

```bash
# 本地自拉起 alert-receiver 全闭环演练
python3 scripts/ops/alerting_e2e_drill.py \
  --out docs/testing/evidence/2026-11-28-observability-closure/alerting-drill-result.json

# 对接已运行的本地 alert-receiver 实例（如 compose monitoring profile）
python3 scripts/ops/alerting_e2e_drill.py --target-url http://127.0.0.1:9194
```
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
ALERT_RECEIVER_ROOT = REPO / "services" / "alert-receiver"
SIGNATURE_HEADER = "X-Alertmanager-Signature"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def compute_hmac_signature(secret: str, body: bytes) -> str:
    """按 alert-receiver 同一算法计算 ``X-Alertmanager-Signature``。"""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_alertmanager_v4_payload(
    *,
    alertname: str = "DdpApiDown",
    status: str = "firing",
    severity: str = "critical",
    component: str = "api",
    fingerprint: str = "e2e-drill-fp-0001",
    summary: str = "演练：DDP API 抓取失败超过 2 分钟",
    description: str = "由 alerting_e2e_drill.py 构造的 Alertmanager v4 演练载荷",
    starts_at: str | None = None,
    ends_at: str | None = None,
) -> dict[str, Any]:
    """构造标准 Alertmanager v4 Webhook payload。"""
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    resolved_starts_at = starts_at or now_iso
    if ends_at is not None:
        resolved_ends_at = ends_at
    elif status == "resolved":
        resolved_ends_at = now_iso
    else:
        resolved_ends_at = "0001-01-01T00:00:00Z"

    labels = {
        "alertname": alertname,
        "severity": severity,
        "component": component,
        "service": "ddp-api",
        "env": "drill",
    }
    annotations = {
        "summary": summary,
        "description": description,
    }
    group_key = f'{{}}/{{severity="{severity}"}}:{{alertname="{alertname}"}}'
    return {
        "version": "4",
        "groupKey": group_key,
        "truncatedAlerts": 0,
        "status": status,
        "receiver": "default-webhook",
        "groupLabels": {"alertname": alertname},
        "commonLabels": labels,
        "commonAnnotations": annotations,
        "externalURL": "http://127.0.0.1:9193",
        "alerts": [
            {
                "status": status,
                "labels": labels,
                "annotations": annotations,
                "startsAt": resolved_starts_at,
                "endsAt": resolved_ends_at,
                "generatorURL": "http://127.0.0.1:9190/graph?g0.expr=up%7Bjob%3D%22ddp-api%22%7D%3D%3D0",
                "fingerprint": fingerprint,
            }
        ],
    }


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    """发起标准库 HTTP 请求并解析 JSON 响应（非 2xx 不抛异常，返回状态码）。"""
    req_headers = dict(headers or {})
    if body is not None and "Content-Type" not in req_headers:
        req_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def _ensure_loopback_url(url: str) -> None:
    """安全护栏：默认只允许投递至本机回环地址。"""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise ValueError(f"拒绝向非回环地址执行演练：host={host!r}")


def _load_receiver_build_server() -> Any:
    """惰性加载 services/alert-receiver 的 build_server，并在加载后还原 sys.path 与 sys.modules。

    注意：``services/alert-receiver/app`` 与 ``backend/app`` 共享顶层包名 ``app``，
    若不还原 ``sys.modules["app*"]``，同一 pytest 进程内后续加载 ``backend/app`` 的测试
    会报 ``ModuleNotFoundError: No module named 'app.api'``（见 MEM-INC-032）。
    """
    service_root = str(ALERT_RECEIVER_ROOT)
    saved_path = list(sys.path)
    saved_app_modules = {
        k: v for k, v in sys.modules.items() if k == "app" or k.startswith("app.")
    }
    try:
        if service_root in sys.path:
            sys.path.remove(service_root)
        sys.path.insert(0, service_root)
        for mod_name in list(saved_app_modules):
            sys.modules.pop(mod_name, None)

        from app.main import build_server

        return build_server
    finally:
        for mod_name in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
            sys.modules.pop(mod_name, None)
        sys.modules.update(saved_app_modules)
        sys.path[:] = saved_path


def run_alerting_drill(
    *,
    target_url: str | None = None,
    bearer_token: str | None = None,
    hmac_secret: str | None = None,
    dedup_window_seconds: float = 60.0,
    allow_non_loopback: bool = False,
) -> dict[str, Any]:
    """执行 Alertmanager → alert-receiver 端到端闭环演练并返回结构化报告。"""
    embedded = target_url is None
    if target_url is not None and not allow_non_loopback:
        _ensure_loopback_url(target_url)

    effective_bearer = bearer_token
    effective_hmac = hmac_secret
    if embedded:
        effective_bearer = effective_bearer or secrets.token_hex(16)
        effective_hmac = effective_hmac or secrets.token_hex(16)

    httpd: Any = None
    thread: threading.Thread | None = None
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    ndjson_path: Path | None = None

    try:
        if embedded:
            temp_dir = tempfile.TemporaryDirectory(prefix="ddp-alert-drill-")
            data_dir = Path(temp_dir.name)
            build_server = _load_receiver_build_server()
            httpd = build_server(
                host="127.0.0.1",
                port=0,
                data_dir=data_dir,
                bearer_token=effective_bearer,
                hmac_secret=effective_hmac,
                dedup_window_seconds=dedup_window_seconds,
            )
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            host, port = httpd.server_address[0], httpd.server_address[1]
            base_url = f"http://{host}:{port}"
            ndjson_path = data_dir / "alerts.ndjson"
        else:
            assert target_url is not None
            base_url = target_url.rstrip("/")

        checks: dict[str, Any] = {}

        # 1. 健康检查
        health_status, health_body = _http_json(f"{base_url}/health")
        assert health_status == 200, f"/health 返回 {health_status}: {health_body}"
        assert health_body.get("status") == "ok", f"/health 状态异常: {health_body}"
        checks["health"] = {
            "status_code": health_status,
            "service": health_body.get("service"),
        }

        # 记录基线统计（外部实例可能已有历史数据）
        _, baseline_stats = _http_json(f"{base_url}/stats")
        baseline_total = int(baseline_stats.get("total", 0))
        baseline_firing = int(baseline_stats.get("firing", 0))
        baseline_resolved = int(baseline_stats.get("resolved", 0))

        fingerprint = f"drill-{secrets.token_hex(6)}"
        firing_payload = build_alertmanager_v4_payload(
            alertname="DdpApiDown",
            status="firing",
            fingerprint=fingerprint,
        )
        firing_bytes = json.dumps(firing_payload, ensure_ascii=False).encode("utf-8")

        def _signed_headers(raw_body: bytes) -> dict[str, str]:
            hdrs: dict[str, str] = {"Content-Type": "application/json"}
            if effective_bearer:
                hdrs["Authorization"] = f"Bearer {effective_bearer}"
            if effective_hmac:
                hdrs[SIGNATURE_HEADER] = compute_hmac_signature(
                    effective_hmac, raw_body
                )
            return hdrs

        # 2. 鉴权与签名守卫校验（仅在配置了对应凭证时）
        if effective_bearer:
            unauth_code, _ = _http_json(
                f"{base_url}/alerts", method="POST", body=firing_bytes, headers={}
            )
            assert unauth_code == 401, f"缺 Bearer 令牌应返回 401，实际 {unauth_code}"
            checks["auth_missing_bearer_rejected"] = {"status_code": unauth_code}

        if effective_hmac:
            bad_headers = {
                "Content-Type": "application/json",
                SIGNATURE_HEADER: "sha256=" + "0" * 64,
            }
            if effective_bearer:
                bad_headers["Authorization"] = f"Bearer {effective_bearer}"
            bad_sig_code, _ = _http_json(
                f"{base_url}/alerts",
                method="POST",
                body=firing_bytes,
                headers=bad_headers,
            )
            assert bad_sig_code == 401, f"错误 HMAC 签名应返回 401，实际 {bad_sig_code}"
            checks["auth_invalid_hmac_rejected"] = {"status_code": bad_sig_code}

        # 3. 首次 firing 告警投递与落盘
        s_fire, b_fire = _http_json(
            f"{base_url}/alerts",
            method="POST",
            body=firing_bytes,
            headers=_signed_headers(firing_bytes),
        )
        assert (
            s_fire == 200 and b_fire.get("stored") is True
        ), f"firing 投递失败: {s_fire} {b_fire}"
        checks["firing_accepted"] = {"status_code": s_fire, "response": b_fire}

        # 4. 重复 firing 告警去重验证
        s_dup, b_dup = _http_json(
            f"{base_url}/alerts",
            method="POST",
            body=firing_bytes,
            headers=_signed_headers(firing_bytes),
        )
        assert s_dup == 200, f"重复 firing 请求异常: {s_dup} {b_dup}"
        if embedded and dedup_window_seconds > 0:
            assert b_dup == {
                "status": "deduplicated",
                "stored": False,
            }, f"预期去重，实际 {b_dup}"
        checks["duplicate_firing"] = {"status_code": s_dup, "response": b_dup}

        # 5. resolved 恢复事件投递
        resolved_payload = build_alertmanager_v4_payload(
            alertname="DdpApiDown",
            status="resolved",
            fingerprint=fingerprint,
            starts_at=firing_payload["alerts"][0]["startsAt"],
        )
        resolved_bytes = json.dumps(resolved_payload, ensure_ascii=False).encode(
            "utf-8"
        )
        s_res, b_res = _http_json(
            f"{base_url}/alerts",
            method="POST",
            body=resolved_bytes,
            headers=_signed_headers(resolved_bytes),
        )
        assert (
            s_res == 200 and b_res.get("stored") is True
        ), f"resolved 投递失败: {s_res} {b_res}"
        checks["resolved_accepted"] = {"status_code": s_res, "response": b_res}

        # 6. 查询与落盘交叉核对
        s_list, listing = _http_json(f"{base_url}/alerts")
        s_stats, after_stats = _http_json(f"{base_url}/stats")
        assert s_list == 200 and s_stats == 200

        expected_firing_delta = 1 if (embedded and dedup_window_seconds > 0) else 2
        expected_total_delta = expected_firing_delta + 1
        actual_total_delta = int(after_stats["total"]) - baseline_total
        actual_firing_delta = int(after_stats["firing"]) - baseline_firing
        actual_resolved_delta = int(after_stats["resolved"]) - baseline_resolved

        assert (
            actual_total_delta == expected_total_delta
        ), f"total 增量不符: expected={expected_total_delta}, actual={actual_total_delta}"
        assert actual_firing_delta == expected_firing_delta
        assert actual_resolved_delta == 1
        assert listing["alerts"][0]["status"] == "resolved"
        assert listing["alerts"][0]["alerts"][0]["fingerprint"] == fingerprint

        disk_lines: int | None = None
        if ndjson_path is not None:
            raw_lines = [
                line
                for line in ndjson_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for line in raw_lines:
                parsed_line = json.loads(line)
                assert isinstance(parsed_line, dict)
            disk_lines = len(raw_lines)
            assert disk_lines == after_stats["total"] == listing["count"]

        checks["cross_check"] = {
            "stats_after": after_stats,
            "listing_count": listing["count"],
            "disk_ndjson_lines": disk_lines,
            "firing_delta": actual_firing_delta,
            "resolved_delta": actual_resolved_delta,
            "total_delta": actual_total_delta,
        }

        return {
            "passed": True,
            "mode": "embedded" if embedded else "external",
            "target_url": base_url,
            "executed_at": datetime.now(UTC).isoformat(),
            "security": {
                "bearer_auth_enabled": bool(effective_bearer),
                "hmac_signature_enabled": bool(effective_hmac),
                "dedup_window_seconds": dedup_window_seconds if embedded else None,
            },
            "fingerprint": fingerprint,
            "checks": checks,
        }
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=5)
        if temp_dir is not None:
            temp_dir.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Alertmanager → alert-receiver 告警闭环演练"
    )
    parser.add_argument(
        "--target-url",
        default=None,
        help="已运行的 alert-receiver 地址（省略则自动拉起本地临时实例）",
    )
    parser.add_argument(
        "--dedup-window-seconds",
        type=float,
        default=60.0,
        help="本地自拉起模式下的去重窗口秒数（默认 60）",
    )
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="允许投递至非回环地址（默认拒绝）",
    )
    parser.add_argument("--out", default="", help="演练报告 JSON 输出路径")
    args = parser.parse_args(argv)

    result = run_alerting_drill(
        target_url=args.target_url,
        dedup_window_seconds=args.dedup_window_seconds,
        allow_non_loopback=args.allow_non_loopback,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"演练结果已写入 {out_path}", file=sys.stderr)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
