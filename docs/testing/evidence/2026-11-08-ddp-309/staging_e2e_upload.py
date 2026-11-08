"""staging E2E：创建上传会话 → 分片 → complete → Worker 校验为 VALID。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
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
    content = b"\x89MCAP0\r\nX"
    sha = f"sha256:{hashlib.sha256(content).hexdigest()}"
    headers = {"Authorization": f"Bearer {mint_token()}"}
    base = f"/api/v1/projects/{PROJECT}"
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=headers, timeout=30) as client:
        created = client.post(
            f"{base}/upload-sessions",
            json={
                "original_filename": "staging-e2e.mcap",
                "declared_content_type": "application/x-mcap",
                "declared_size_bytes": len(content),
                "format_hint": "MCAP",
                "expected_sha256": sha,
            },
            headers={"Idempotency-Key": f"e2e-{int(time.time())}"},
        )
        print("create", created.status_code)
        created.raise_for_status()
        session = created.json()
        session_id = session["upload_session_id"]
        version = int(created.headers.get("ETag", '"1"').strip('"'))

        part_url = client.post(f"{base}/upload-sessions/{session_id}/parts/1/url")
        print("part_url", part_url.status_code)
        part_url.raise_for_status()
        signed = part_url.json()

        put_headers = {**signed["headers"], "Content-Type": "application/octet-stream"}
        put = httpx.put(signed["url"], headers=put_headers, content=content, timeout=30)
        print("put", put.status_code)
        put.raise_for_status()
        etag = (put.headers.get("ETag") or "").strip('"')

        ack = client.post(
            f"{base}/upload-sessions/{session_id}/parts/1/ack",
            json={"etag": etag, "size_bytes": len(content), "sha256": sha},
        )
        print("ack", ack.status_code)
        ack.raise_for_status()

        current = client.get(f"{base}/upload-sessions/{session_id}")
        current.raise_for_status()
        version = int(current.headers.get('ETag', '"%s"' % version).strip('"'))
        completed = client.post(
            f"{base}/upload-sessions/{session_id}/complete",
            json={
                "expected_sha256": sha,
                "expected_size_bytes": len(content),
                "parts": [
                    {
                        "part_number": 1,
                        "etag": etag,
                        "size_bytes": len(content),
                        "sha256": sha,
                    }
                ],
            },
            headers={"If-Match": f'"{version}"', "Idempotency-Key": f"e2e-c-{int(time.time())}"},
        )
        print("complete", completed.status_code)
        completed.raise_for_status()
        result = completed.json()
        asset_id = result["raw_asset"]["asset_id"]
        print("asset_initial", result["raw_asset"]["status"], "dedup", result["deduplicated"])

        status = None
        for _ in range(30):
            detail = client.get(f"{base}/raw-assets/{asset_id}")
            detail.raise_for_status()
            status = detail.json()["status"]
            if status != "VALIDATING":
                break
            time.sleep(1)
        final = detail.json()
        print("asset_final", status)
        print("sha_match", final["sha256"] == sha, "size", final["size_bytes"])
        return 0 if status == "VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
