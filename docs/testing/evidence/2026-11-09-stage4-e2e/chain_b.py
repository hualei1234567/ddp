"""阶段 4 staging 链路（续）：导出产物→交付→验收。"""

from __future__ import annotations

import json
import sys
from uuid import uuid4

import httpx

BASE = "http://127.0.0.1:18000/api/v1"
PROJECT = "00000000-0000-4000-8000-000000000002"
DATASET = "888cc1f9-239a-42c1-ac0e-3ca22ddd80be"
OWNER = "00000000-0000-4000-8000-000000000001"
OWNER_TOKEN = sys.argv[1]
EDITOR_TOKEN = sys.argv[2]
EXPORT_ID = sys.argv[3]


def client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
        trust_env=False,
    )


def call(step, method, path, *, token, **kwargs):
    with client(token) as c:
        response = c.request(method, path, **kwargs)
    print(f"[{step}] {response.status_code}")
    if response.status_code >= 400:
        print(response.text)
        raise SystemExit(1)
    return response.json() if response.content else None


editor = client(EDITOR_TOKEN)
owner = client(OWNER_TOKEN)

versions = editor.get(f"/projects/{PROJECT}/datasets/{DATASET}/versions").json()["items"]
frozen = [v for v in versions if v["status"] == "FROZEN"]
frozen.sort(key=lambda v: v["dataset_version"], reverse=True)
version = frozen[0]
VID = version["dataset_version_id"]
print("frozen version:", VID, version["dataset_version"], version["manifest_sha256"])

artifacts = editor.get(f"/projects/{PROJECT}/exports/{EXPORT_ID}/artifacts").json()
print("artifacts:", len(artifacts["artifacts"]), artifacts["aggregate_sha256"])

first = artifacts["artifacts"][0]
download = editor.get(
    f"/projects/{PROJECT}/exports/{EXPORT_ID}/artifacts/{first['path']}/download"
).json()
print("download url:", download["download_url"][:80], "expires", download["download_url_expires_at"])

requests = editor.get(f"/projects/{PROJECT}/training-requests").json()["items"]
target = [r for r in requests if r["title"] == "staging 全链路训练需求"]
target.sort(key=lambda r: r["created_at"], reverse=True)
training = target[0]
REQ_ID = training["training_request_id"]
rev = editor.get(
    f"/projects/{PROJECT}/training-requests/{REQ_ID}/revisions/{training['current_revision_id']}"
).json()
print("revision:", rev["revision_no"], rev["status"])

delivery = call(
    "创建训练交付",
    "POST",
    f"/projects/{PROJECT}/training-deliveries",
    token=EDITOR_TOKEN,
    json={
        "training_request_id": REQ_ID,
        "training_request_revision_id": rev["revision_id"],
        "dataset_version_id": VID,
        "export_job_id": EXPORT_ID,
        "recipient_id": OWNER,
        "recipient_team": "算法组",
        "delivery_note_no": f"DN-E2E-{uuid4().hex[:6]}",
    },
    headers={"Idempotency-Key": f"e2e-dn-{uuid4().hex[:8]}"},
)
DID = delivery["training_delivery_id"]
print("delivery:", DID, delivery["status"], delivery["item_count"], delivery["split_counts"])

delivery = call(
    "交付置为 READY",
    "POST",
    f"/projects/{PROJECT}/training-deliveries/{DID}/actions/ready",
    token=EDITOR_TOKEN,
    headers={"If-Match": f'"{delivery["version"]}"'},
    json={},
)
delivery = call(
    "标记交付完成",
    "POST",
    f"/projects/{PROJECT}/training-deliveries/{DID}/actions/deliver",
    token=EDITOR_TOKEN,
    headers={"If-Match": f'"{delivery["version"]}"'},
    json={},
)
delivery = call(
    "验收交付",
    "POST",
    f"/projects/{PROJECT}/training-deliveries/{DID}/actions/accept",
    token=OWNER_TOKEN,
    headers={"If-Match": f'"{delivery["version"]}"'},
    json={"acceptance_opinion": "staging 全链路验收通过"},
)
print("delivery final:", delivery["status"], delivery["accepted_at"])

final_request = owner.get(f"/projects/{PROJECT}/training-requests/{REQ_ID}").json()
print("request final:", final_request["status"])

summary = {
    "dataset_version_id": VID,
    "export_job_id": EXPORT_ID,
    "training_request_id": REQ_ID,
    "delivery_id": DID,
    "delivery_status": delivery["status"],
    "request_status": final_request["status"],
    "artifact_count": len(artifacts["artifacts"]),
    "aggregate_sha256": artifacts["aggregate_sha256"],
    "download_url_issued": bool(download["download_url"]),
}
open("/tmp/stage4_chain_summary.json", "w").write(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False))
