"""staging 验收：唯一内容上传、并发 complete、下载 URL、Episode API 冒烟。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path("/home/user/vscodeworkspace/DataPlatform")
ENV: dict[str, str] = {}
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, _, value = line.partition("=")
        ENV[key.strip()] = value.strip()

PROJECT = "00000000-0000-4000-8000-000000000002"
USER = "00000000-0000-4000-8000-000000000001"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_token() -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": USER,
        "platform_role": "member",
        "iss": ENV.get("DDP_JWT_ISSUER", "ddp"),
        "aud": ENV.get("DDP_JWT_AUDIENCE", "ddp-api"),
        "iat": now,
        "exp": now + 600,
    }
    signing_input = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(payload).encode())}"
    signature = hmac.new(
        ENV["DDP_JWT_SECRET"].encode(), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{b64url(signature)}"


def main() -> int:
    marker = f"DDP310-{int(time.time())}".encode()
    content = b"\x89MCAP0\r\n" + marker
    sha = f"sha256:{hashlib.sha256(content).hexdigest()}"
    headers = {"Authorization": f"Bearer {mint_token()}"}
    base = f"/api/v1/projects/{PROJECT}"
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=headers, timeout=30) as client:
        created = client.post(
            f"{base}/upload-sessions",
            json={
                "original_filename": "staging-ddp310.mcap",
                "declared_content_type": "application/x-mcap",
                "declared_size_bytes": len(content),
                "format_hint": "MCAP",
                "expected_sha256": sha,
            },
            headers={"Idempotency-Key": f"d310-{int(time.time())}"},
        )
        created.raise_for_status()
        session = created.json()
        sid = session["upload_session_id"]

        url = client.post(f"{base}/upload-sessions/{sid}/parts/1/url").json()
        put = httpx.put(
            url["url"],
            headers={**url["headers"], "Content-Type": "application/octet-stream"},
            content=content,
            timeout=30,
        )
        put.raise_for_status()
        etag = (put.headers.get("ETag") or "").strip('"')
        ack = client.post(
            f"{base}/upload-sessions/{sid}/parts/1/ack",
            json={"etag": etag, "size_bytes": len(content), "sha256": sha},
        )
        ack.raise_for_status()

        current = client.get(f"{base}/upload-sessions/{sid}")
        version = int(current.headers["ETag"].strip('"'))
        body = {
            "expected_sha256": sha,
            "expected_size_bytes": len(content),
            "parts": [
                {"part_number": 1, "etag": etag, "size_bytes": len(content), "sha256": sha}
            ],
        }

        def complete(index: int) -> int:
            response = client.post(
                f"{base}/upload-sessions/{sid}/complete",
                json=body,
                headers={
                    "If-Match": f'"{version}"',
                    "Idempotency-Key": f"d310-c-{index}-{int(time.time())}",
                },
            )
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(complete, (1, 2)))
        print("concurrent_complete", statuses)

        asset_id = None
        for _ in range(30):
            detail = client.get(f"{base}/upload-sessions/{sid}").json()
            asset_id = detail.get("asset_id")
            if detail["status"] == "COMPLETED" and asset_id:
                break
            time.sleep(1)
        print("session_status", detail["status"])

        for _ in range(30):
            asset = client.get(f"{base}/raw-assets/{asset_id}").json()
            if asset["status"] != "VALIDATING":
                break
            time.sleep(1)
        print("asset_status", asset["status"], "sha_match", asset["sha256"] == sha)

        download = client.get(f"{base}/../../raw-assets/{asset_id}/download-url?project_id={PROJECT}")
        print("download_url", download.status_code, "expires_at" in download.text)

        episode = client.post(
            f"{base}/episodes",
            json={
                "raw_asset_ids": [asset_id],
                "segmentation": {
                    "method": "MANUAL_TIME_RANGE",
                    "rule_version": "manual-v1",
                    "start_time_ns": int(time.time() * 1_000_000),
                    "end_time_ns": int(time.time() * 1_000_000) + 1_000_000,
                },
                "time_base": {
                    "time_domain": "UTC",
                    "clock_source": "NTP",
                    "sync_method": "NTP",
                    "sync_status": "VERIFIED",
                },
                "canonical_schema_version": "canonical-1.0.0",
                "pipeline_id": "manual",
                "pipeline_version": "1.0.0",
            },
            headers={"Idempotency-Key": f"d310-ep-{int(time.time())}"},
        )
        print("episode_create", episode.status_code)
        return 0 if statuses in ([202, 202], [202, 409]) and asset["status"] == "VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
