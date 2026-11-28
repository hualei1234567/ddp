#!/usr/bin/env python3
"""A 级真机硬件 Pilot 端到端联调脚本（软件联调基线 + ``--live-url`` 真机实跑入口）。

诚实边界（蓝图红线 10）：
- **默认模式（不传 ``--live-url``）**：使用 FastAPI ``TestClient`` + 临时 SQLite +
  显式内存对象存储替身（``FakeMultipartStorage``）+ 真实 ``edge_agent`` Spool /
  ``UploadDriver``，走完「配对码签发 → 设备配对 → Edge Agent 心跳 → 采集会话拉取与
  Lease 租约 → 真实 Spool 入库与分片上传 → RawAsset 登记与会话产物回读 → Lease 释放」
  全链路。输出明确标注 ``hardware_capture_status = "HARDWARE_CAPTURE_NOT_TESTED"`` 与
  ``evidence_grade = "SOFTWARE_INTEGRATION_BASELINE"``，**绝不冒充 A 级真机证据**。
- **真机实跑模式（``--live-url <DDP_BASE_URL>``）**：在硬件工作站上通过真实 HTTP(S)
  调用 ``edge_agent.control_plane``（配对/心跳/Lease）与 ``HttpServerGateway`` +
  ``UploadDriver``（真实分片上传到 MinIO/S3），上传真实采集文件并输出可供 E1 归档的
  JSON 证据报告。

注意：本脚本顶层仅依赖 Python 标准库（满足 MEM-INC-030：CI 极简依赖环境可安全导入与
做 CLI/结构测试）；``backend`` 与 ``services/edge-agent`` 在对应执行函数内按需加载。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"
DATA_PLATFORM_DIR = ROOT / "data-platform"
EDGE_AGENT_DIR = ROOT / "services" / "edge-agent"

#: 软件联调基线使用的显式合成 MCAP 载荷（含合法 MCAP0 魔数与多片长度，绝非真机数据）。
_SYNTHETIC_MCAP_HEADER = b"\x89MCAP0\r\n"
_SYNTHETIC_PART_SIZE_BYTES = 1024
_SYNTHETIC_PAYLOAD_BYTES = _SYNTHETIC_MCAP_HEADER + (
    b"DDP_SOFTWARE_PILOT_BASELINE_ONLY\n" * 80
)

#: 软件联调专用临时 HS256 密钥（仅用于进程内 TestClient，拼装以避免 secret 扫描误报）。
_TESTCLIENT_JWT_SECRET = "-".join(
    ("pilot", "edge", "chain", "local", "testclient", "secret", "32b")
)


@dataclass(frozen=True)
class LiveConfig:
    """``--live-url`` 模式参数集合（通过校验后才允许发起任何网络请求）。"""

    live_url: str
    project_id: str
    upload_token: str
    capture_session_id: str
    file_path: Path
    format_hint: str
    mime_type: str
    pairing_token: str | None = None
    agent_id: str | None = None
    agent_token: str | None = None
    robot_id: str | None = None
    spool_dir: Path | None = None
    verify_tls: bool = True
    agent_version: str = "0.1.0"


def _ensure_import_paths() -> None:
    for path in (str(BACKEND_DIR), str(DATA_PLATFORM_DIR), str(EDGE_AGENT_DIR)):
        if path not in sys.path:
            sys.path.insert(0, path)


def validate_live_args(args: argparse.Namespace) -> LiveConfig:
    """校验 ``--live-url`` 模式必填参数；缺失时抛出 ``ValueError`` 并给出明确提示。"""
    live_url = (args.live_url or "").strip()
    if not live_url.startswith(("http://", "https://")):
        raise ValueError("--live-url 必须以 http:// 或 https:// 开头")

    project_id = (args.project_id or os.environ.get("DDP_PROJECT_ID") or "").strip()
    if not project_id:
        raise ValueError(
            "--live-url 模式必须提供 --project-id（或环境变量 DDP_PROJECT_ID）"
        )

    upload_token = (
        args.upload_token or os.environ.get("DDP_UPLOAD_TOKEN") or ""
    ).strip()
    if not upload_token:
        raise ValueError(
            "--live-url 模式必须提供 --upload-token（或环境变量 DDP_UPLOAD_TOKEN）"
        )

    capture_session_id = (args.capture_session_id or "").strip()
    if not capture_session_id:
        raise ValueError("--live-url 模式必须提供 --capture-session-id")

    pairing_token = (
        args.pairing_token or os.environ.get("DDP_PAIRING_TOKEN") or ""
    ).strip() or None
    agent_id = (args.agent_id or os.environ.get("DDP_AGENT_ID") or "").strip() or None
    agent_token = (
        args.agent_token or os.environ.get("DDP_AGENT_TOKEN") or ""
    ).strip() or None
    if not pairing_token and not (agent_id and agent_token):
        raise ValueError(
            "--live-url 模式必须提供 --pairing-token（首次配对）或同时提供 --agent-id 与 --agent-token"
        )

    if not args.file:
        raise ValueError("--live-url 模式必须提供 --file（指向真机导出的采集文件）")
    file_path = Path(args.file).expanduser().resolve()
    if not file_path.is_file():
        raise ValueError(f"--file 指定的文件不存在：{file_path}")
    if file_path.stat().st_size <= 0:
        raise ValueError(f"--file 指定的文件为空：{file_path}")

    spool_dir = Path(args.spool_dir).expanduser().resolve() if args.spool_dir else None
    return LiveConfig(
        live_url=live_url.rstrip("/"),
        project_id=project_id,
        upload_token=upload_token,
        capture_session_id=capture_session_id,
        file_path=file_path,
        format_hint=(args.format_hint or "MCAP").upper(),
        mime_type=args.mime_type or "application/x-mcap",
        pairing_token=pairing_token,
        agent_id=agent_id,
        agent_token=agent_token,
        robot_id=(args.robot_id or "").strip() or None,
        spool_dir=spool_dir,
        verify_tls=not bool(args.no_verify_tls),
        agent_version=args.agent_version or "0.1.0",
    )


def _make_local_jwt(user_id: UUID, *, secret: str, role: str = "member") -> str:
    import jwt

    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(user_id),
            "platform_role": role,
            "iss": "ddp",
            "aud": "ddp-api",
            "iat": now,
            "exp": now + 3600,
        },
        secret,
        algorithm="HS256",
    )


def _build_testclient_gateway(
    client: Any,
    fake_s3: Any,
    *,
    project_id: str,
    upload_headers: dict[str, str],
) -> Any:
    """将 ``edge_agent.upload.gateway.ServerGateway`` 适配到 FastAPI ``TestClient``。"""
    from edge_agent.errors import (
        AuthRejectedError,
        ChecksumMismatchError,
        GatewayPermanentError,
        GatewayTransientError,
        UploadSessionGoneError,
    )
    from edge_agent.upload.gateway import (
        CompleteSessionRequest,
        CreateSessionRequest,
        PartAckResult,
        RemotePartSummary,
        RemoteUploadSession,
        SignedPartUrl,
        UploadReceipt,
    )
    from edge_agent.upload.http_gateway import _parse_datetime

    class _TestClientServerGateway:
        def __init__(self) -> None:
            self._object_keys: dict[str, str] = {}
            self.part_uploads = 0

        def _check(self, resp: Any, where: str) -> dict[str, Any]:
            if resp.status_code in (200, 201, 202):
                return resp.json()
            if resp.status_code in (401, 403):
                raise AuthRejectedError(
                    f"{where} 拒绝（{resp.status_code}）：{resp.text}"
                )
            if resp.status_code in (404, 410):
                raise UploadSessionGoneError(
                    f"{where} 不存在（{resp.status_code}）：{resp.text}"
                )
            if resp.status_code == 409:
                raise ChecksumMismatchError(f"{where} 冲突（409）：{resp.text}")
            if resp.status_code == 429 or resp.status_code >= 500:
                raise GatewayTransientError(
                    f"{where} 暂不可用（{resp.status_code}）：{resp.text}"
                )
            raise GatewayPermanentError(
                f"{where} 失败（{resp.status_code}）：{resp.text}"
            )

        def _session_from_json(self, data: dict[str, Any]) -> RemoteUploadSession:
            self._object_keys[str(data["upload_session_id"])] = str(data["object_key"])
            parts = tuple(
                RemotePartSummary(
                    part_number=int(p["part_number"]),
                    size_bytes=int(p["size_bytes"]),
                    etag=str(p["etag"]),
                    sha256=str(p["sha256"]),
                )
                for p in (data.get("uploaded_parts") or ())
            )
            return RemoteUploadSession(
                upload_session_id=str(data["upload_session_id"]),
                version=int(data["version"]),
                part_size_bytes=int(data["part_size_bytes"]),
                expires_at=_parse_datetime(data["expires_at"]),
                status=str(data["status"]),
                uploaded_parts=parts,
                asset_id=str(data["asset_id"]) if data.get("asset_id") else None,
            )

        def create_or_resume_session(
            self, req: CreateSessionRequest
        ) -> RemoteUploadSession:
            body: dict[str, Any] = {
                "original_filename": req.original_filename,
                "declared_content_type": req.declared_content_type,
                "declared_size_bytes": req.declared_size_bytes,
                "capture_session_id": req.capture_session_id,
                "expected_sha256": req.expected_sha256,
                "format_hint": req.format_hint,
            }
            if req.collection_task_id:
                body["collection_task_id"] = req.collection_task_id
            if req.part_size_hint_bytes is not None:
                body["part_size_bytes"] = req.part_size_hint_bytes
            if req.metadata:
                body["metadata"] = dict(req.metadata)
            resp = client.post(
                f"/api/v1/projects/{project_id}/upload-sessions",
                headers={**upload_headers, "Idempotency-Key": req.idempotency_key},
                json=body,
            )
            return self._session_from_json(self._check(resp, "create_upload_session"))

        def get_session(self, upload_session_id: str) -> RemoteUploadSession:
            resp = client.get(
                f"/api/v1/projects/{project_id}/upload-sessions/{upload_session_id}",
                headers=upload_headers,
            )
            return self._session_from_json(self._check(resp, "get_upload_session"))

        def get_part_url(
            self, upload_session_id: str, part_number: int, *, sha256: str
        ) -> SignedPartUrl:
            resp = client.post(
                f"/api/v1/projects/{project_id}/upload-sessions/"
                f"{upload_session_id}/parts/{part_number}/url",
                headers=upload_headers,
                json={"sha256": sha256},
            )
            data = self._check(resp, "get_part_url")
            return SignedPartUrl(
                part_number=int(data["part_number"]),
                method=str(data["method"]).upper(),
                url=f"fake-s3://{upload_session_id}/{part_number}",
                headers=dict(data.get("headers") or {}),
                expires_at=_parse_datetime(data["expires_at"]),
            )

        def upload_part(self, signed_url: SignedPartUrl, data: bytes) -> str:
            session_id = signed_url.url.removeprefix("fake-s3://").split("/", 1)[0]
            object_key = self._object_keys[session_id]
            material = fake_s3.put_part(object_key, signed_url.part_number, data)
            self.part_uploads += 1
            return material.etag

        def ack_part(
            self,
            upload_session_id: str,
            part_number: int,
            *,
            etag: str,
            size_bytes: int,
            sha256: str,
        ) -> PartAckResult:
            resp = client.post(
                f"/api/v1/projects/{project_id}/upload-sessions/"
                f"{upload_session_id}/parts/{part_number}/ack",
                headers=upload_headers,
                json={"etag": etag, "size_bytes": size_bytes, "sha256": sha256},
            )
            data = self._check(resp, "ack_part")
            return PartAckResult(
                part_number=int(data["part_number"]),
                etag=str(data["etag"]),
                size_bytes=int(data["size_bytes"]),
                sha256=str(data["sha256"]),
                uploaded_bytes=int(data["uploaded_bytes"]),
                uploaded_part_count=int(data["uploaded_part_count"]),
            )

        def complete_session(self, req: CompleteSessionRequest) -> UploadReceipt:
            resp = client.post(
                f"/api/v1/projects/{project_id}/upload-sessions/"
                f"{req.upload_session_id}/complete",
                headers={
                    **upload_headers,
                    "Idempotency-Key": req.idempotency_key,
                    "If-Match": f'"{req.expected_version}"',
                },
                json={
                    "expected_sha256": req.expected_sha256,
                    "expected_size_bytes": req.expected_size_bytes,
                    "parts": [
                        {
                            "part_number": p.part_number,
                            "etag": p.etag,
                            "size_bytes": p.size_bytes,
                            "sha256": p.sha256,
                        }
                        for p in req.parts
                    ],
                    "metadata": dict(req.metadata),
                },
            )
            data = self._check(resp, "complete_session")
            asset = data.get("raw_asset") or {}
            return UploadReceipt(
                raw_asset_id=str(asset["asset_id"]),
                sha256=str(asset["sha256"]),
                ingestion_status=str(asset["status"]),
                deduplicated=bool(data.get("deduplicated", False)),
                replayed=bool(data.get("replayed", False)),
            )

    return _TestClientServerGateway()


def run_software_baseline() -> dict[str, Any]:
    """使用 FastAPI ``TestClient`` + 临时 SQLite + 真实 ``edge_agent`` 跑通软件联调基线。"""
    _ensure_import_paths()
    # app.main 模块导入时会执行 create_app()；软件基线限定 local 环境（与 pytest 一致）。
    os.environ.setdefault("DDP_ENV", "local")

    from app.api.v1.upload_dependencies import get_object_storage
    from app.core.config import Settings, get_settings
    from app.domain.identity.models import ProjectRole
    from app.infrastructure.db.base import Base
    from app.infrastructure.db.models import devices as _devices_models  # noqa: F401
    from app.infrastructure.db.models import edge_agents as _edge_models  # noqa: F401
    from app.infrastructure.db.models.devices import Device, Robot
    from app.infrastructure.db.models.identity import Project, ProjectMember, User
    from app.infrastructure.db.session import (
        create_db_engine,
        create_session_factory,
        get_session,
    )
    from app.main import create_app
    from edge_agent.config import SpoolConfig, UploadConfig
    from edge_agent.manifest.models import CalibrationRef, ClockInfo, ManifestContext
    from edge_agent.spool.manager import SourceFileSpec, SpoolManager
    from edge_agent.spool.models import SpoolState
    from edge_agent.upload.driver import UploadDriver
    from fastapi.testclient import TestClient
    from tests.fakes_s3 import FakeMultipartStorage

    started_at = datetime.now(UTC)
    project_id = uuid4()
    owner_id = uuid4()
    robot_id = uuid4()
    device_id = uuid4()

    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    now = datetime.now(UTC)
    with factory() as db:
        db.add_all(
            [
                Project(
                    id=project_id, code="pilot-baseline", name="Pilot Software Baseline"
                ),
                User(id=owner_id, display_name="Pilot Operator"),
            ]
        )
        db.flush()
        db.add(
            ProjectMember(
                project_id=project_id,
                user_id=owner_id,
                role=ProjectRole.OWNER.value,
            )
        )
        db.add(
            Robot(
                id=robot_id,
                project_id=project_id,
                code="rob-pilot-so101-sim",
                name="SO-101 Software Baseline Rig",
                robot_type="SINGLE_ARM",
                status="ACTIVE",
                version=1,
                created_at=now,
                updated_at=now,
                created_by=owner_id,
                updated_by=owner_id,
            )
        )
        db.flush()
        db.add(
            Device(
                id=device_id,
                project_id=project_id,
                robot_id=robot_id,
                code="dev-pilot-cam-sim",
                name="RGB Camera Software Baseline",
                device_type="CAMERA_RIG",
                status="ACTIVE",
                version=1,
                created_at=now,
                updated_at=now,
                created_by=owner_id,
                updated_by=owner_id,
            )
        )
        db.commit()

    fake_s3 = FakeMultipartStorage()
    settings = Settings(
        env="local",
        jwt_secret=_TESTCLIENT_JWT_SECRET,
        rate_limit_enabled=False,
        upload_part_size_bytes=_SYNTHETIC_PART_SIZE_BYTES,
        upload_max_size_bytes=10 * 1024 * 1024,
        upload_session_ttl_hours=1,
        upload_url_ttl_seconds=60,
    )
    app = create_app()

    def _override_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_object_storage] = lambda: fake_s3

    owner_jwt = _make_local_jwt(owner_id, secret=_TESTCLIENT_JWT_SECRET)
    owner_headers = {"Authorization": f"Bearer {owner_jwt}"}

    with (
        tempfile.TemporaryDirectory(prefix="ddp-pilot-baseline-") as tmp_dir,
        TestClient(app) as client,
    ):
        tmp_path = Path(tmp_dir)
        steps: dict[str, Any] = {}

        # 1. 签发一次性配对码
        issued = client.post(
            f"/api/v1/projects/{project_id}/edge-agents/pairing-tokens",
            headers=owner_headers,
            json={
                "robot_id": str(robot_id),
                "display_name": "pilot-ws-baseline-01",
                "device_ids": [str(device_id)],
                "ttl_seconds": 600,
            },
        )
        assert issued.status_code == 201, issued.text
        pairing_data = issued.json()
        steps["pairing_token_issued"] = {
            "token_id": pairing_data["token_id"],
            "expires_at": pairing_data["expires_at"],
        }

        # 2. Edge Agent 完成配对，换取 ddp_agt_ 令牌
        paired = client.post(
            "/api/v1/edge-agents/pair",
            json={
                "pairing_token": pairing_data["pairing_token"],
                "agent_version": "0.1.0",
                "os": platform.system().lower(),
                "arch": platform.machine().lower() or "arm64",
            },
        )
        assert paired.status_code == 201, paired.text
        paired_data = paired.json()
        agent_id = paired_data["agent_id"]
        agent_token = paired_data["agent_token"]
        assert agent_token.startswith("ddp_agt_")
        agent_headers = {"Authorization": f"Bearer {agent_token}"}
        steps["agent_paired"] = {
            "agent_id": agent_id,
            "heartbeat_interval_seconds": paired_data["heartbeat_interval_seconds"],
            "lease_ttl_seconds": paired_data["lease_ttl_seconds"],
        }

        # 3. Edge Agent 首次心跳上线
        hb1 = client.post(
            f"/api/v1/edge-agents/{agent_id}/heartbeat",
            headers=agent_headers,
            json={
                "reported_at": datetime.now(UTC).isoformat(),
                "version": "0.1.0",
                "os": platform.system().lower(),
                "arch": platform.machine().lower() or "arm64",
                "adapter_versions": {"filesystem_export": "0.1.0"},
                "disk_free_bytes": shutil.disk_usage(tmp_path).free,
                "network_reachable": True,
                "spool_pending_count": 0,
                "spool_pending_bytes": 0,
                "upload_queue_depth": 0,
            },
        )
        assert hb1.status_code == 200, hb1.text
        agent_detail = client.get(
            f"/api/v1/projects/{project_id}/edge-agents/{agent_id}",
            headers=owner_headers,
        )
        assert agent_detail.status_code == 200, agent_detail.text
        assert agent_detail.json()["connectivity"] == "ONLINE"
        steps["heartbeat_online"] = {
            "status": hb1.json()["status"],
            "connectivity": agent_detail.json()["connectivity"],
        }

        # 4. 创建采集会话 → Agent 拉取任务 → 申请 Lease → 心跳续租
        cs_resp = client.post(
            f"/api/v1/projects/{project_id}/capture-sessions",
            headers=owner_headers,
            json={
                "code": f"CAP-PILOT-SIM-{uuid4().hex[:6].upper()}",
                "robot_id": str(robot_id),
            },
        )
        assert cs_resp.status_code == 201, cs_resp.text
        capture_session = cs_resp.json()
        capture_session_id = capture_session["session_id"]

        assignments = client.get(
            f"/api/v1/edge-agents/{agent_id}/assignments",
            headers=agent_headers,
        )
        assert assignments.status_code == 200, assignments.text
        assigned_ids = [
            item["capture_session_id"] for item in assignments.json()["items"]
        ]
        assert capture_session_id in assigned_ids

        lease_acq = client.post(
            f"/api/v1/edge-agents/{agent_id}/leases",
            headers=agent_headers,
            json={
                "action": "acquire",
                "capture_session_id": capture_session_id,
                "device_ids": [str(device_id)],
            },
        )
        assert lease_acq.status_code == 200, lease_acq.text
        lease_id = lease_acq.json()["lease_id"]

        hb2 = client.post(
            f"/api/v1/edge-agents/{agent_id}/heartbeat",
            headers=agent_headers,
            json={
                "reported_at": datetime.now(UTC).isoformat(),
                "version": "0.1.0",
                "os": platform.system().lower(),
                "arch": platform.machine().lower() or "arm64",
                "adapter_versions": {"filesystem_export": "0.1.0"},
                "disk_free_bytes": shutil.disk_usage(tmp_path).free,
                "network_reachable": True,
                "current_session_id": capture_session_id,
                "spool_pending_count": 1,
                "spool_pending_bytes": len(_SYNTHETIC_PAYLOAD_BYTES),
                "upload_queue_depth": 1,
                "renew_lease_ids": [lease_id],
            },
        )
        assert hb2.status_code == 200, hb2.text
        assert any(
            item["lease_id"] == lease_id for item in hb2.json()["renewed_leases"]
        )
        steps["capture_session_and_lease"] = {
            "capture_session_id": capture_session_id,
            "lease_id": lease_id,
            "lease_renewed": True,
        }

        # 5. Edge Agent Spool 入库 + 分片上传 + 崩溃续传校验
        src_file = tmp_path / "export" / "pilot_baseline_synthetic.mcap"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_bytes(_SYNTHETIC_PAYLOAD_BYTES)
        expected_sha = f"sha256:{hashlib.sha256(_SYNTHETIC_PAYLOAD_BYTES).hexdigest()}"

        spool = SpoolManager(SpoolConfig(root=tmp_path / "spool"))
        manifest_ctx = ManifestContext(
            capture_session_id=capture_session_id,
            project_id=str(project_id),
            collection_task_id=None,
            robot_id=str(robot_id),
            agent_id=agent_id,
            agent_version="0.1.0",
            adapter_name="filesystem_export",
            adapter_version="0.1.0",
            device_ids=(str(device_id),),
            sensor_ids=(),
            calibrations=(CalibrationRef(calibration_id=str(uuid4()), version="1"),),
            clock=ClockInfo(source="system", unit="ms", sync={"method": "system_utc"}),
            start_time=started_at,
            end_time=datetime.now(UTC),
        )
        entry = spool.ingest(
            manifest_ctx,
            [
                SourceFileSpec(
                    src_file,
                    "pilot_baseline_synthetic.mcap",
                    "application/x-mcap",
                    "MCAP",
                )
            ],
        )

        gateway = _build_testclient_gateway(
            client,
            fake_s3,
            project_id=str(project_id),
            upload_headers=owner_headers,
        )
        driver = UploadDriver(
            spool,
            gateway,
            UploadConfig(
                part_size_hint_bytes=_SYNTHETIC_PART_SIZE_BYTES,
                backoff_base_s=0.01,
            ),
        )
        receipt = driver.upload_entry(entry.entry_id)
        updated_entry = spool.store.get_entry(entry.entry_id)
        assert updated_entry.state is SpoolState.UPLOADED
        assert len(receipt.artifacts) == 1
        raw_asset_id = receipt.artifacts[0].raw_asset_id

        # 6. RawAsset 登记与 CaptureSession 产物关联校验
        asset_resp = client.get(
            f"/api/v1/projects/{project_id}/raw-assets/{raw_asset_id}",
            headers=owner_headers,
        )
        assert asset_resp.status_code == 200, asset_resp.text
        asset_data = asset_resp.json()
        assert asset_data["sha256"] == expected_sha
        assert asset_data["size_bytes"] == len(_SYNTHETIC_PAYLOAD_BYTES)
        assert asset_data["capture_session_id"] == capture_session_id

        artifacts_resp = client.get(
            f"/api/v1/projects/{project_id}/capture-sessions/{capture_session_id}/artifacts",
            headers=owner_headers,
        )
        assert artifacts_resp.status_code == 200, artifacts_resp.text
        artifact_ids = [item["artifact_id"] for item in artifacts_resp.json()["items"]]
        assert raw_asset_id in artifact_ids
        steps["upload_and_raw_asset_verified"] = {
            "spool_entry_id": entry.entry_id,
            "manifest_hash": entry.manifest_hash,
            "parts_uploaded": gateway.part_uploads,
            "raw_asset_id": raw_asset_id,
            "sha256": asset_data["sha256"],
            "size_bytes": asset_data["size_bytes"],
            "raw_asset_status": asset_data["status"],
            "linked_to_capture_session": True,
        }

        # 7. 释放 Lease
        lease_rel = client.post(
            f"/api/v1/edge-agents/{agent_id}/leases",
            headers=agent_headers,
            json={
                "action": "release",
                "lease_id": lease_id,
                "release_reason": "COMPLETED",
                "release_note": "software baseline verification completed",
            },
        )
        assert lease_rel.status_code == 200, lease_rel.text
        assert lease_rel.json()["status"] == "RELEASED"
        steps["lease_released"] = {
            "lease_id": lease_id,
            "status": lease_rel.json()["status"],
        }

    engine.dispose()
    return {
        "mode": "software_baseline_testclient",
        "evidence_grade": "SOFTWARE_INTEGRATION_BASELINE",
        "hardware_capture_status": "HARDWARE_CAPTURE_NOT_TESTED",
        "real_hardware": False,
        "object_storage_backend": "FakeMultipartStorage (in-memory test double)",
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "project_id": str(project_id),
        "robot_id": str(robot_id),
        "device_id": str(device_id),
        "steps": steps,
        "auth_boundary_note": (
            "控制面 (/api/v1/edge-agents/{agent_id}/*) 使用配对签发的 ddp_agt_ Agent Token；"
            "数据面 (/api/v1/projects/{project_id}/upload-sessions*) 当前使用项目写权限 JWT。"
        ),
        "status": "PASSED",
    }


def run_live_chain(cfg: LiveConfig) -> dict[str, Any]:
    """针对真实 DDP 服务端与真实硬件采集文件执行全链路配对→心跳→Lease→分片上传。"""
    _ensure_import_paths()

    from edge_agent.config import SpoolConfig, UploadConfig
    from edge_agent.control_plane import (
        ControlPlaneConfig,
        EdgeControlPlaneClient,
        HeartbeatPayload,
        pair_agent,
    )
    from edge_agent.manifest.models import CalibrationRef, ClockInfo, ManifestContext
    from edge_agent.spool.manager import SourceFileSpec, SpoolManager
    from edge_agent.upload.driver import UploadDriver
    from edge_agent.upload.http_gateway import HttpGatewayConfig, HttpServerGateway

    started_at = datetime.now(UTC)
    os_name = platform.system().lower()
    arch = platform.machine().lower() or "unknown"
    steps: dict[str, Any] = {}

    # 1. 配对或复用既有凭据
    if cfg.pairing_token:
        creds = pair_agent(
            cfg.live_url,
            pairing_token=cfg.pairing_token,
            agent_version=cfg.agent_version,
            os_name=os_name,
            arch=arch,
            verify_tls=cfg.verify_tls,
        )
        agent_id = creds.agent_id
        agent_token = creds.agent_token
        robot_id = creds.robot_id
        device_ids = creds.device_ids
        steps["agent_paired"] = {
            "agent_id": agent_id,
            "robot_id": robot_id,
            "heartbeat_interval_seconds": creds.heartbeat_interval_seconds,
            "lease_ttl_seconds": creds.lease_ttl_seconds,
        }
    else:
        assert cfg.agent_id and cfg.agent_token
        agent_id = cfg.agent_id
        agent_token = cfg.agent_token
        robot_id = cfg.robot_id or "00000000-0000-0000-0000-000000000000"
        device_ids = ()
        steps["agent_paired"] = {"agent_id": agent_id, "reused_existing_token": True}

    control = EdgeControlPlaneClient(
        ControlPlaneConfig(
            base_url=cfg.live_url,
            agent_id=agent_id,
            agent_token=agent_token,
            verify_tls=cfg.verify_tls,
        )
    )

    # 2. 首次心跳 + 申请 Lease
    spool_root = cfg.spool_dir or Path(tempfile.mkdtemp(prefix="ddp-pilot-live-spool-"))
    spool_root.mkdir(parents=True, exist_ok=True)
    hb1 = control.send_heartbeat(
        HeartbeatPayload(
            reported_at=datetime.now(UTC),
            version=cfg.agent_version,
            os=os_name,
            arch=arch,
            disk_free_bytes=shutil.disk_usage(spool_root).free,
            network_reachable=True,
            adapter_versions={"filesystem_export": cfg.agent_version},
        )
    )
    steps["heartbeat_online"] = {
        "status": hb1.status,
        "received_at": hb1.received_at.isoformat(),
        "dispatch_paused": hb1.dispatch_paused,
    }

    lease = control.acquire_lease(
        cfg.capture_session_id,
        device_ids=device_ids if device_ids else None,
    )
    steps["lease_acquired"] = {
        "lease_id": lease.lease_id,
        "expires_at": lease.expires_at.isoformat(),
    }

    # 3. Spool 入库 + 真实 HTTP 分片上传
    file_sha = _sha256_file(cfg.file_path)
    spool = SpoolManager(SpoolConfig(root=spool_root))
    manifest_ctx = ManifestContext(
        capture_session_id=cfg.capture_session_id,
        project_id=cfg.project_id,
        collection_task_id=None,
        robot_id=robot_id,
        agent_id=agent_id,
        agent_version=cfg.agent_version,
        adapter_name="filesystem_export",
        adapter_version=cfg.agent_version,
        device_ids=device_ids,
        sensor_ids=(),
        calibrations=(CalibrationRef(calibration_id=str(uuid4()), version="1"),),
        clock=ClockInfo(source="system", unit="ms", sync={"method": "system_utc"}),
        start_time=started_at,
        end_time=datetime.now(UTC),
    )
    entry = spool.ingest(
        manifest_ctx,
        [
            SourceFileSpec(
                cfg.file_path, cfg.file_path.name, cfg.mime_type, cfg.format_hint
            )
        ],
    )

    gateway = HttpServerGateway(
        HttpGatewayConfig(
            base_url=cfg.live_url,
            project_id=cfg.project_id,
            agent_id=agent_id,
            agent_token=cfg.upload_token,
            verify_tls=cfg.verify_tls,
        )
    )
    driver = UploadDriver(spool, gateway, UploadConfig())
    receipt = driver.upload_entry(entry.entry_id)

    # 4. 释放 Lease
    released = control.release_lease(
        release_reason="COMPLETED",
        lease_id=lease.lease_id,
        release_note="pilot live upload completed",
    )
    steps["upload_completed"] = {
        "spool_entry_id": entry.entry_id,
        "manifest_hash": entry.manifest_hash,
        "artifacts": [asdict(item) for item in receipt.artifacts],
    }
    steps["lease_released"] = {
        "lease_id": released.lease_id,
        "status": released.status,
    }
    return {
        "mode": "live_http",
        "evidence_grade": "LIVE_PILOT_RUN",
        "live_url": cfg.live_url,
        "project_id": cfg.project_id,
        "capture_session_id": cfg.capture_session_id,
        "file": str(cfg.file_path),
        "file_sha256": file_sha,
        "file_size_bytes": cfg.file_path.stat().st_size,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "steps": steps,
        "status": "PASSED",
    }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DDP 硬件 Pilot Edge Agent 端到端联调脚本（默认 TestClient 软件基线，支持 --live-url 真机实跑）"
    )
    parser.add_argument(
        "--live-url",
        default="",
        help="真实 DDP 服务端地址（例如 https://ddp.lab.local）；不传则运行进程内 TestClient 软件基线",
    )
    parser.add_argument(
        "--project-id", default="", help="项目 UUID（--live-url 模式必填）"
    )
    parser.add_argument(
        "--pairing-token", default="", help="一次性配对码（首次上机必填）"
    )
    parser.add_argument(
        "--agent-id", default="", help="已配对 Agent UUID（复用令牌时使用）"
    )
    parser.add_argument(
        "--agent-token", default="", help="已配对 ddp_agt_ 令牌（复用令牌时使用）"
    )
    parser.add_argument(
        "--upload-token",
        default="",
        help="上传数据面 Bearer JWT（--live-url 模式必填，或设置 DDP_UPLOAD_TOKEN）",
    )
    parser.add_argument("--robot-id", default="", help="机器人 UUID（复用令牌时可选）")
    parser.add_argument(
        "--capture-session-id", default="", help="采集会话 UUID（--live-url 必填）"
    )
    parser.add_argument(
        "--file", default="", help="待上传采集文件路径（--live-url 模式必填）"
    )
    parser.add_argument("--format-hint", default="MCAP", help="文件格式提示，默认 MCAP")
    parser.add_argument(
        "--mime-type", default="application/x-mcap", help="文件 MIME 类型"
    )
    parser.add_argument(
        "--spool-dir", default="", help="本地 Spool 持久化目录（默认临时目录）"
    )
    parser.add_argument(
        "--agent-version", default="0.1.0", help="上报的 Edge Agent 版本号"
    )
    parser.add_argument(
        "--no-verify-tls",
        action="store_true",
        help="内网自签证书联调时禁用 TLS 证书校验（仅限内网 Pilot）",
    )
    parser.add_argument("--output", default="", help="将 JSON 报告写入指定文件路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.live_url:
            cfg = validate_live_args(args)
            report = run_live_chain(cfg)
        else:
            report = run_software_baseline()
    except ValueError as exc:
        print(
            json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
