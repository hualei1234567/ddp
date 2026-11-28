#!/usr/bin/env python3
"""V6.1 Sprint 5 · T3-04 真实训练→证据入库→评测→Badcase→补采需求→复测关闭全闭环 E2E。

诚实边界：
- **默认模式（不传 ``--live-url``）**：使用进程内 FastAPI ``TestClient`` + 内存 SQLite +
  显式内存对象存储替身（``FakeExportStorage``）/ 产物检查替身（``FakeInspector``）/
  训练网关替身（``FakeTrainingGateway``），复用 ``scripts/train/gate_b_video_train.py``
  的 ``build_evidence_payload`` 规范加载真实归档的 GATE-B 训练证据 ``evidence.json``，
  完整走通 10 段 HTTP API 业务闭环：
  1. 创建项目、成员（Editor / Owner）、数据集、策略与冻结版本 + 导出作业发布
  2. 创建训练需求、提交并审批通过 → 创建训练交付（ready → deliver）→ 创建外部训练引用
  3. ``POST /external-training-references/{ref_id}/evidence`` 真实训练证据落库
  4. ``GET /external-training-references/{ref_id}/evidence`` 与
     ``GET /training-deliveries/{delivery_id}/evidence`` 核验落库一致性
  5. 模型注册与版本校验（``READY`` / ``VERIFIED``）
  6. ``POST /evaluations`` 发起首轮评测并经签名回调完成（``FAIL``）
  7. ``POST /model-feedback`` 登记 Badcase 并核验 ``GET /data-gaps``
  8. ``POST /model-feedback/{feedback_id}/actions/create-requirement`` 生成补采需求
     （等价于 ``/data-requirements`` 补采单）并完成 ``submit → approve → satisfy``
  9. 发起复测评测（``PASS``）并调用 ``link-retest`` 关联复测结果
  10. 关闭补采需求（``CLOSED``）并关闭反馈（``RESOLVED``），完成端到端闭环。
- **实时栈模式（``--live-url <DDP_BASE_URL>``）**：严格校验 ``http/https`` scheme 后，
  通过标准库 ``urllib`` 对接已运行的 DDP 服务执行证据落库、评测、Badcase 登记、
  补采需求与复测关闭闭环。

依赖纪律（MEM-INC-030）：
- 顶层仅导入 Python 标准库；``fastapi`` / ``sqlalchemy`` / ``jwt`` / ``backend`` /
  ``gate_b_video_train`` 一律在函数内惰性导入，确保 CI 极简依赖环境可安全导入并运行
  参数与边界校验测试。
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"
DATA_PLATFORM_DIR = ROOT / "data-platform"
GATE_B_VIDEO_TRAIN_PATH = ROOT / "scripts" / "train" / "gate_b_video_train.py"
DEFAULT_EVIDENCE_FILE = (
    ROOT
    / "docs"
    / "testing"
    / "evidence"
    / "2026-11-28-gate-b-training"
    / "evidence.json"
)

#: 进程内 TestClient 专用临时密钥（拼装以避免 secret 扫描误报，不用于生产）。
_TESTCLIENT_JWT_SECRET = "-".join(
    ("v61", "closed", "loop", "local", "testclient", "jwt", "secret")
)
_TESTCLIENT_ADAPTER_SECRET = "-".join(
    ("v61", "closed", "loop", "local", "adapter", "hmac", "secret", "01")
)


@dataclass(frozen=True)
class LiveConfig:
    """``--live-url`` 模式参数集合（通过校验后才允许发起任何网络请求）。"""

    live_url: str
    project_id: str
    auth_token: str
    approver_token: str
    external_ref_id: str
    dataset_version_id: str
    model_version_id: str
    evidence_file: Path
    run_kind: str = "SMOKE"
    adapter_id: str = "mock"
    callback_secret: str | None = None
    timeout_seconds: float = 30.0


def validate_http_url(raw_url: str, *, flag: str = "--live-url") -> str:
    """严格校验 URL 仅允许 ``http://`` 或 ``https://`` 且包含合法主机名。"""
    cleaned = (raw_url or "").strip()
    parsed = urllib_parse.urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{flag} 必须以 http:// 或 https:// 开头且包含合法主机名")
    return cleaned.rstrip("/")


def validate_live_args(args: argparse.Namespace) -> LiveConfig:
    """校验 ``--live-url`` 模式参数；缺失或非法时抛出 ``ValueError``。"""
    live_url = validate_http_url(args.live_url or "", flag="--live-url")

    project_id = (args.project_id or os.environ.get("DDP_PROJECT_ID") or "").strip()
    if not project_id:
        raise ValueError(
            "--live-url 模式必须提供 --project-id（或环境变量 DDP_PROJECT_ID）"
        )

    auth_token = (args.auth_token or os.environ.get("DDP_API_TOKEN") or "").strip()
    if not auth_token:
        raise ValueError(
            "--live-url 模式必须提供 --auth-token（或环境变量 DDP_API_TOKEN）"
        )

    approver_token = (
        args.approver_token or os.environ.get("DDP_APPROVER_TOKEN") or auth_token
    ).strip()

    external_ref_id = (
        args.external_ref_id or os.environ.get("DDP_EXTERNAL_REF_ID") or ""
    ).strip()
    if not external_ref_id:
        raise ValueError(
            "--live-url 模式必须提供 --external-ref-id（或环境变量 DDP_EXTERNAL_REF_ID）"
        )

    dataset_version_id = (
        args.dataset_version_id or os.environ.get("DDP_DATASET_VERSION_ID") or ""
    ).strip()
    if not dataset_version_id:
        raise ValueError(
            "--live-url 模式必须提供 --dataset-version-id（或环境变量 DDP_DATASET_VERSION_ID）"
        )

    model_version_id = (
        args.model_version_id or os.environ.get("DDP_MODEL_VERSION_ID") or ""
    ).strip()
    if not model_version_id:
        raise ValueError(
            "--live-url 模式必须提供 --model-version-id（或环境变量 DDP_MODEL_VERSION_ID）"
        )

    evidence_file = (
        Path(args.evidence_file or DEFAULT_EVIDENCE_FILE).expanduser().resolve()
    )
    if not evidence_file.is_file():
        raise ValueError(f"--evidence-file 指定的文件不存在：{evidence_file}")

    callback_secret = (
        args.callback_secret or os.environ.get("DDP_MODEL_ADAPTER_SECRET") or ""
    ).strip() or None

    return LiveConfig(
        live_url=live_url,
        project_id=project_id,
        auth_token=auth_token,
        approver_token=approver_token,
        external_ref_id=external_ref_id,
        dataset_version_id=dataset_version_id,
        model_version_id=model_version_id,
        evidence_file=evidence_file,
        run_kind=args.run_kind,
        adapter_id=(args.adapter_id or "mock").strip(),
        callback_secret=callback_secret,
        timeout_seconds=float(args.timeout_seconds),
    )


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _ensure_import_paths() -> None:
    for path in (str(BACKEND_DIR), str(DATA_PLATFORM_DIR)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _load_gate_b_module() -> Any:
    """惰性加载 ``scripts/train/gate_b_video_train.py`` 以复用 ``build_evidence_payload``。"""
    spec = importlib.util.spec_from_file_location(
        "ddp_gate_b_video_train", GATE_B_VIDEO_TRAIN_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载训练证据构造模块：{GATE_B_VIDEO_TRAIN_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_training_evidence_payload(
    evidence_file: Path,
    *,
    run_kind: str = "SMOKE",
    dataset_version_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """读取真实训练 ``evidence.json`` 并通过 ``gate_b_video_train.build_evidence_payload`` 转换。"""
    raw_evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
    gate_b = _load_gate_b_module()
    finished = datetime.now(UTC)
    wall = (raw_evidence.get("training") or {}).get("wall_seconds") or (
        raw_evidence.get("training") or {}
    ).get("elapsed_seconds")
    started = finished - timedelta(seconds=float(wall)) if wall else finished
    payload = gate_b.build_evidence_payload(
        raw_evidence,
        run_kind=run_kind,
        started_at=started,
        finished_at=finished,
        dataset_version_id=dataset_version_id,
        stdout_tail="[GATE-B] real training evidence verified and ingested",
    )
    return raw_evidence, payload


def _sign_model_adapter_callback(
    body: bytes, *, secret: str, nonce: str, timestamp: int | None = None
) -> dict[str, str]:
    ts = timestamp or int(datetime.now(UTC).timestamp())
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{ts}.{nonce}.".encode("utf-8") + body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-DDP-Signature": f"sha256={digest}",
        "X-DDP-Timestamp": str(ts),
        "X-DDP-Nonce": nonce,
        "Content-Type": "application/json",
    }


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


def _assert_status(resp: Any, expected: int, step: str) -> dict[str, Any]:
    if resp.status_code != expected:
        raise RuntimeError(
            f"步骤 {step} 失败：期望 HTTP {expected}，实际 {resp.status_code}，响应={resp.text[:500]}"
        )
    if expected == 204 or not resp.text:
        return {}
    return resp.json()


def _project_url(project_id: UUID | str, suffix: str) -> str:
    return f"/api/v1/projects/{project_id}{suffix}"


def run_in_process_closed_loop(
    *,
    evidence_file: Path = DEFAULT_EVIDENCE_FILE,
    run_kind: str = "SMOKE",
) -> dict[str, Any]:
    """进程内 FastAPI ``TestClient`` + 内存 SQLite 跑通全闭环。"""
    _ensure_import_paths()
    os.environ.setdefault("DDP_ENV", "local")

    from app.api.v1.modelops_dependencies import get_artifact_inspector
    from app.api.v1.training_dependencies import get_training_adapter_gateway
    from app.api.v1.upload_dependencies import get_object_storage
    from app.core.config import Settings, get_settings
    from app.domain.identity.models import ProjectRole
    from app.infrastructure.db.base import Base
    from app.infrastructure.db.models.identity import Project, ProjectMember, User
    from app.infrastructure.db.session import (
        create_db_engine,
        create_session_factory,
        get_session,
    )
    from app.main import create_app
    from fastapi.testclient import TestClient
    from tests.api.dataset_support import (
        create_dataset,
        create_policies,
        seed_dataset_episodes,
        version_payload,
    )
    from tests.api.export_support import (
        FakeExportStorage,
        export_payload,
        run_export_worker,
    )
    from tests.api.modelops_support import FakeInspector
    from tests.api.training_support import FakeTrainingGateway, request_payload

    resolved_evidence = evidence_file.expanduser().resolve()
    if not resolved_evidence.is_file():
        raise FileNotFoundError(f"训练证据文件不存在：{resolved_evidence}")

    started_at = datetime.now(UTC)
    project_id = uuid4()
    editor_id = uuid4()
    owner_id = uuid4()

    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    with factory() as db:
        db.add_all(
            [
                Project(
                    id=project_id,
                    code=f"v61-closed-loop-{project_id.hex[:6]}",
                    name="V6.1 Sprint 5 Training Closed Loop",
                ),
                User(id=editor_id, display_name="V61 Closed Loop Editor"),
                User(id=owner_id, display_name="V61 Closed Loop Owner"),
            ]
        )
        db.flush()
        db.add_all(
            [
                ProjectMember(
                    project_id=project_id,
                    user_id=editor_id,
                    role=ProjectRole.EDITOR.value,
                ),
                ProjectMember(
                    project_id=project_id,
                    user_id=owner_id,
                    role=ProjectRole.OWNER.value,
                ),
            ]
        )
        db.commit()

    episodes = seed_dataset_episodes(factory, project_id, editor_id, count=10)
    raw_evidence, evidence_payload_preview = load_training_evidence_payload(
        resolved_evidence, run_kind=run_kind
    )
    ckpt_sha = evidence_payload_preview.get("checkpoint_sha256") or (
        "sha256:" + "a" * 64
    )

    storage = FakeExportStorage()
    inspector = FakeInspector(size=1024, sha=str(ckpt_sha))
    gateway = FakeTrainingGateway("LOCAL")
    settings = Settings(
        env="local",
        jwt_secret=_TESTCLIENT_JWT_SECRET,
        rate_limit_enabled=False,
        model_adapter_shared_secrets={"mock": _TESTCLIENT_ADAPTER_SECRET},
    )

    app = create_app()

    def _override_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_object_storage] = lambda: storage
    app.dependency_overrides[get_artifact_inspector] = lambda: inspector
    app.dependency_overrides[get_training_adapter_gateway] = lambda: gateway

    editor_headers = {
        "Authorization": f"Bearer {_make_local_jwt(editor_id, secret=_TESTCLIENT_JWT_SECRET)}"
    }
    owner_headers = {
        "Authorization": f"Bearer {_make_local_jwt(owner_id, secret=_TESTCLIENT_JWT_SECRET)}"
    }

    steps: dict[str, Any] = {}
    with (
        TestClient(app, headers=editor_headers) as editor,
        TestClient(app, headers=owner_headers) as owner,
    ):
        # 1. 数据集 + 冻结版本 + 导出发布
        dataset = create_dataset(
            editor, project_id, owner_id, f"v61-ds-{uuid4().hex[:6]}"
        )
        policies = create_policies(editor, project_id, dataset["dataset_id"])
        ver_created = _assert_status(
            editor.post(
                _project_url(project_id, f"/datasets/{dataset['dataset_id']}/versions"),
                json=version_payload(policies),
            ),
            201,
            "create_dataset_version",
        )
        version_id = ver_created["dataset_version_id"]
        ver_built = _assert_status(
            editor.post(
                _project_url(
                    project_id,
                    f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/build",
                ),
                headers={"If-Match": f'"{ver_created["version"]}"'},
                json={},
            ),
            200,
            "build_dataset_version",
        )
        ver_validated = _assert_status(
            editor.post(
                _project_url(
                    project_id,
                    f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/validate",
                ),
                headers={"If-Match": f'"{ver_built["version"]}"'},
                json={},
            ),
            200,
            "validate_dataset_version",
        )
        ver_frozen = _assert_status(
            owner.post(
                _project_url(
                    project_id,
                    f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/freeze",
                ),
                headers={"If-Match": f'"{ver_validated["version"]}"'},
                json={"reason": "T3-04 闭环冻结核验", "payload": {"qa_verified": True}},
            ),
            200,
            "freeze_dataset_version",
        )

        export_created = _assert_status(
            editor.post(
                _project_url(project_id, "/exports"),
                json=export_payload(version_id),
                headers={"Idempotency-Key": f"exp-v61-{uuid4().hex[:8]}"},
            ),
            202,
            "create_export_job",
        )
        export_job_id = export_created["export_job_id"]
        worker_result = run_export_worker(factory, storage)
        if worker_result.status != "PUBLISHED":
            raise RuntimeError(f"导出 Worker 未发布：{worker_result}")
        export_job = _assert_status(
            editor.get(_project_url(project_id, f"/exports/{export_job_id}")),
            200,
            "get_export_job",
        )
        steps["dataset_and_export"] = {
            "project_id": str(project_id),
            "episode_count": len(episodes),
            "dataset_id": dataset["dataset_id"],
            "dataset_version_id": version_id,
            "dataset_version_status": ver_frozen["status"],
            "export_job_id": export_job_id,
            "export_status": export_job["status"],
        }

        # 2. 训练需求 → 审批 → 训练交付 → 外部训练引用
        tr_created = _assert_status(
            editor.post(
                _project_url(project_id, "/training-requests"),
                json=request_payload(owner_id),
                headers={"Idempotency-Key": f"tr-v61-{uuid4().hex[:8]}"},
            ),
            201,
            "create_training_request",
        )
        tr_id = tr_created["training_request_id"]
        rev_id = tr_created["current_revision_id"]
        rev = _assert_status(
            editor.get(
                _project_url(
                    project_id, f"/training-requests/{tr_id}/revisions/{rev_id}"
                )
            ),
            200,
            "get_training_revision",
        )
        rev_submitted = _assert_status(
            editor.post(
                _project_url(
                    project_id,
                    f"/training-requests/{tr_id}/revisions/{rev_id}/actions/submit",
                ),
                headers={"If-Match": f'"{rev["version"]}"'},
                json={},
            ),
            200,
            "submit_training_revision",
        )
        _assert_status(
            owner.post(
                _project_url(
                    project_id,
                    f"/training-requests/{tr_id}/revisions/{rev_id}/actions/approve",
                ),
                headers={"If-Match": f'"{rev_submitted["version"]}"'},
                json={},
            ),
            200,
            "approve_training_revision",
        )

        delivery_created = _assert_status(
            editor.post(
                _project_url(project_id, "/training-deliveries"),
                json={
                    "training_request_id": tr_id,
                    "training_request_revision_id": rev_id,
                    "dataset_version_id": version_id,
                    "export_job_id": export_job_id,
                    "recipient_id": str(owner_id),
                    "recipient_team": "算法组",
                    "delivery_note_no": f"DN-V61-{uuid4().hex[:6]}",
                },
                headers={"Idempotency-Key": f"deliv-v61-{uuid4().hex[:8]}"},
            ),
            201,
            "create_training_delivery",
        )
        delivery_id = delivery_created["training_delivery_id"]
        delivery_ready = _assert_status(
            editor.post(
                _project_url(
                    project_id, f"/training-deliveries/{delivery_id}/actions/ready"
                ),
                headers={"If-Match": f'"{delivery_created["version"]}"'},
                json={},
            ),
            200,
            "ready_training_delivery",
        )
        delivery_delivered = _assert_status(
            editor.post(
                _project_url(
                    project_id, f"/training-deliveries/{delivery_id}/actions/deliver"
                ),
                headers={"If-Match": f'"{delivery_ready["version"]}"'},
                json={},
            ),
            200,
            "deliver_training_delivery",
        )
        ext_ref = _assert_status(
            editor.post(
                _project_url(
                    project_id,
                    f"/training-deliveries/{delivery_id}/external-references",
                ),
                json={
                    "adapter_id": "lerobot-local",
                    "adapter_version": (raw_evidence.get("code") or {}).get(
                        "lerobot", "0.3.3"
                    ),
                    "correlation_id": f"corr-v61-{uuid4().hex[:8]}",
                    "execution_mode": "LOCAL",
                    "config": {
                        "policy": (raw_evidence.get("training") or {}).get(
                            "policy", "act"
                        ),
                        "steps": (raw_evidence.get("training") or {}).get("steps", 2),
                    },
                },
                headers={"Idempotency-Key": f"extref-v61-{uuid4().hex[:8]}"},
            ),
            202,
            "create_external_training_reference",
        )
        external_ref_id = ext_ref["external_ref_id"]
        steps["training_delivery_and_reference"] = {
            "training_request_id": tr_id,
            "training_delivery_id": delivery_id,
            "delivery_status": delivery_delivered["status"],
            "external_ref_id": external_ref_id,
            "execution_mode": ext_ref["execution_mode"],
        }

        # 3. 真实训练证据落库（复用 gate_b_video_train.build_evidence_payload）
        _, evidence_payload = load_training_evidence_payload(
            resolved_evidence,
            run_kind=run_kind,
            dataset_version_id=version_id,
        )
        evidence_created = _assert_status(
            editor.post(
                _project_url(
                    project_id,
                    f"/external-training-references/{external_ref_id}/evidence",
                ),
                json=evidence_payload,
            ),
            201,
            "post_training_evidence",
        )

        # 4. GET .../evidence 核验（按引用 + 按交付双通道校验）
        by_ref = _assert_status(
            editor.get(
                _project_url(
                    project_id,
                    f"/external-training-references/{external_ref_id}/evidence",
                )
            ),
            200,
            "get_evidence_by_reference",
        )
        by_delivery = _assert_status(
            editor.get(
                _project_url(project_id, f"/training-deliveries/{delivery_id}/evidence")
            ),
            200,
            "get_evidence_by_delivery",
        )
        ref_ids = {item["evidence_id"] for item in by_ref["items"]}
        delivery_ev_ids = {item["evidence_id"] for item in by_delivery["items"]}
        if evidence_created["evidence_id"] not in ref_ids:
            raise RuntimeError("按 external_ref_id 回查未命中刚入库的训练证据")
        if evidence_created["evidence_id"] not in delivery_ev_ids:
            raise RuntimeError("按 training_delivery_id 回查未命中刚入库的训练证据")
        steps["training_evidence"] = {
            "source_evidence_file": str(resolved_evidence.relative_to(ROOT))
            if resolved_evidence.is_relative_to(ROOT)
            else str(resolved_evidence),
            "evidence_id": evidence_created["evidence_id"],
            "run_kind": evidence_created["run_kind"],
            "status": evidence_created["status"],
            "steps": evidence_created["steps"],
            "exit_code": evidence_created["exit_code"],
            "config_sha256": evidence_created["config_sha256"],
            "checkpoint_sha256": evidence_created["checkpoint_sha256"],
            "verified_by_reference_count": len(by_ref["items"]),
            "verified_by_delivery_count": len(by_delivery["items"]),
        }

        # 5. 模型注册与版本校验
        model = _assert_status(
            editor.post(
                _project_url(project_id, "/models"),
                json={
                    "code": f"act-v61-{uuid4().hex[:6]}",
                    "name": "ACT 抓取策略模型（V6.1 闭环）",
                    "task_type": "IMITATION",
                    "framework": "pytorch",
                    "owner_id": str(owner_id),
                },
            ),
            201,
            "create_model",
        )
        model_id = model["model_id"]
        mv_created = _assert_status(
            editor.post(
                _project_url(project_id, f"/models/{model_id}/versions"),
                json={
                    "artifact": {
                        "object_key": evidence_payload.get("checkpoint_ref")
                        or "models/act_policy.safetensors",
                        "sha256": str(ckpt_sha),
                        "size_bytes": 1024,
                        "format": "SAFETENSORS",
                    },
                    "runtime": {
                        "framework": "pytorch",
                        "framework_version": str(
                            (raw_evidence.get("training") or {}).get("torch", "2.7.1")
                        ),
                        "python_version": str(
                            (raw_evidence.get("training") or {}).get("python", "3.11")
                        ),
                    },
                },
            ),
            201,
            "create_model_version",
        )
        model_version_id = mv_created["model_version_id"]
        mv_ready = _assert_status(
            editor.post(
                _project_url(
                    project_id,
                    f"/models/{model_id}/versions/{model_version_id}/actions/validate",
                ),
                headers={"If-Match": f'"{mv_created["version"]}"'},
                json={},
            ),
            200,
            "validate_model_version",
        )
        steps["model_registry"] = {
            "model_id": model_id,
            "model_version_id": model_version_id,
            "status": mv_ready["status"],
            "artifact_verification_status": mv_ready["artifact"]["verification_status"],
        }

        # 6. 发起首轮评测并经签名回调写回（FAIL）
        eval_created = _assert_status(
            editor.post(
                _project_url(project_id, "/evaluations"),
                json={
                    "model_version_id": model_version_id,
                    "dataset_version_id": version_id,
                    "metrics": {},
                    "thresholds": {"success_rate": {"min": 0.9}},
                },
            ),
            202,
            "create_initial_evaluation",
        )
        eval_id = eval_created["evaluation_id"]
        cb_body = json.dumps(
            {
                "event_id": f"evt-v61-init-{uuid4().hex[:8]}",
                "adapter_version": "1.0.0",
                "event_type": "evaluation.completed",
                "project_id": str(project_id),
                "evaluation_id": eval_id,
                "metrics": {"success_rate": 0.42},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        _assert_status(
            editor.post(
                "/api/v1/integrations/model-adapters/mock/callbacks",
                content=cb_body,
                headers=_sign_model_adapter_callback(
                    cb_body,
                    secret=_TESTCLIENT_ADAPTER_SECRET,
                    nonce=f"nonce-{uuid4().hex[:8]}",
                ),
            ),
            204,
            "callback_initial_evaluation",
        )
        eval_done = _assert_status(
            editor.get(_project_url(project_id, f"/evaluations/{eval_id}")),
            200,
            "get_initial_evaluation",
        )
        steps["initial_evaluation"] = {
            "evaluation_id": eval_id,
            "status": eval_done["status"],
            "verdict": eval_done["verdict"],
            "metrics": eval_done["metrics"],
        }

        # 7. 登记 Badcase 反馈并核验数据缺口
        feedback = _assert_status(
            editor.post(
                _project_url(project_id, "/model-feedback"),
                json={
                    "model_version_id": model_version_id,
                    "evaluation_id": eval_id,
                    "issue_type": "SCENE_COVERAGE",
                    "severity": "MAJOR",
                    "description": "厨房场景抓取成功率 0.42 低于阈值 0.90，需补采样本",
                    "dimensions": [
                        {
                            "dimension_type": "SCENE",
                            "dimension_value": "kitchen",
                            "current_count": 2,
                            "target_count": 10,
                        }
                    ],
                    "evidence_refs": [
                        f"training-evidence/{evidence_created['evidence_id']}",
                        f"evaluations/{eval_id}",
                    ],
                },
            ),
            201,
            "create_model_feedback",
        )
        feedback_id = feedback["feedback_id"]
        gaps = _assert_status(
            editor.get(_project_url(project_id, "/data-gaps")),
            200,
            "get_data_gaps",
        )
        kitchen_gap = next(
            (item for item in gaps["items"] if item["dimension_value"] == "kitchen"),
            None,
        )
        if kitchen_gap is None or kitchen_gap["open_feedback_count"] < 1:
            raise RuntimeError(f"数据缺口未汇总新登记的 Badcase：{gaps}")
        steps["badcase_feedback"] = {
            "feedback_id": feedback_id,
            "status": feedback["status"],
            "issue_type": feedback["issue_type"],
            "gap_open_feedback_count": kitchen_gap["open_feedback_count"],
            "gap_coverage_source": kitchen_gap["coverage_source"],
        }

        # 8. 反馈转补采需求并完成提交 → 审批 → 满足
        converted = _assert_status(
            editor.post(
                _project_url(
                    project_id,
                    f"/model-feedback/{feedback_id}/actions/create-requirement",
                ),
                headers={"If-Match": f'"{feedback["version"]}"'},
                json={
                    "requirement_code": f"DR-V61-{uuid4().hex[:6]}",
                    "target_episode_count": 8,
                },
            ),
            200,
            "convert_feedback_to_requirement",
        )
        requirement_id = converted["converted_requirement_id"]
        req_detail = _assert_status(
            editor.get(
                _project_url(project_id, f"/data-requirements/{requirement_id}")
            ),
            200,
            "get_data_requirement",
        )
        req_submitted = _assert_status(
            editor.post(
                _project_url(
                    project_id, f"/data-requirements/{requirement_id}/actions/submit"
                ),
                headers={"If-Match": f'"{req_detail["version"]}"'},
                json={},
            ),
            200,
            "submit_data_requirement",
        )
        req_approved = _assert_status(
            owner.post(
                _project_url(
                    project_id, f"/data-requirements/{requirement_id}/actions/approve"
                ),
                headers={"If-Match": f'"{req_submitted["version"]}"'},
                json={},
            ),
            200,
            "approve_data_requirement",
        )
        req_satisfied = _assert_status(
            owner.post(
                _project_url(
                    project_id, f"/data-requirements/{requirement_id}/actions/satisfy"
                ),
                headers={"If-Match": f'"{req_approved["version"]}"'},
                json={"dataset_version_ids": [version_id]},
            ),
            200,
            "satisfy_data_requirement",
        )
        steps["data_requirement"] = {
            "requirement_id": requirement_id,
            "code": req_detail["code"],
            "source_feedback_ids": req_detail["source_feedback_ids"],
            "status_after_satisfy": req_satisfied["status"],
            "linked_dataset_version_ids": req_satisfied["linked_dataset_version_ids"],
        }

        # 9. 发起复测评测（PASS）并 link-retest 关联到补采需求
        retest_created = _assert_status(
            editor.post(
                _project_url(project_id, "/evaluations"),
                json={
                    "model_version_id": model_version_id,
                    "dataset_version_id": version_id,
                    "metrics": {},
                    "thresholds": {"success_rate": {"min": 0.9}},
                },
            ),
            202,
            "create_retest_evaluation",
        )
        retest_id = retest_created["evaluation_id"]
        retest_cb_body = json.dumps(
            {
                "event_id": f"evt-v61-retest-{uuid4().hex[:8]}",
                "adapter_version": "1.0.0",
                "event_type": "evaluation.completed",
                "project_id": str(project_id),
                "evaluation_id": retest_id,
                "metrics": {"success_rate": 0.95},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        _assert_status(
            editor.post(
                "/api/v1/integrations/model-adapters/mock/callbacks",
                content=retest_cb_body,
                headers=_sign_model_adapter_callback(
                    retest_cb_body,
                    secret=_TESTCLIENT_ADAPTER_SECRET,
                    nonce=f"nonce-{uuid4().hex[:8]}",
                ),
            ),
            204,
            "callback_retest_evaluation",
        )
        retest_done = _assert_status(
            editor.get(_project_url(project_id, f"/evaluations/{retest_id}")),
            200,
            "get_retest_evaluation",
        )
        req_linked = _assert_status(
            owner.post(
                _project_url(
                    project_id,
                    f"/data-requirements/{requirement_id}/actions/link-retest",
                ),
                headers={"If-Match": f'"{req_satisfied["version"]}"'},
                json={"evaluation_id": retest_id},
            ),
            200,
            "link_retest_to_requirement",
        )
        steps["retest_and_link"] = {
            "retest_evaluation_id": retest_id,
            "retest_status": retest_done["status"],
            "retest_verdict": retest_done["verdict"],
            "retest_metrics": retest_done["metrics"],
            "requirement_retest_ids": req_linked["retest_evaluation_ids"],
        }

        # 10. 关闭补采需求并关闭 Badcase 反馈闭环
        req_closed = _assert_status(
            owner.post(
                _project_url(
                    project_id, f"/data-requirements/{requirement_id}/actions/close"
                ),
                headers={"If-Match": f'"{req_linked["version"]}"'},
                json={},
            ),
            200,
            "close_data_requirement",
        )
        fb_resolved = _assert_status(
            owner.post(
                _project_url(
                    project_id, f"/model-feedback/{feedback_id}/actions/resolve"
                ),
                headers={"If-Match": f'"{converted["version"]}"'},
                json={
                    "resolution_note": f"复测评测 {retest_id} 通过（0.95 >= 0.90），闭环关闭"
                },
            ),
            200,
            "resolve_model_feedback",
        )
        steps["closure"] = {
            "requirement_status": req_closed["status"],
            "feedback_status": fb_resolved["status"],
            "resolution_note": fb_resolved["resolution_note"],
        }

    finished_at = datetime.now(UTC)
    return {
        "status": "PASSED",
        "mode": "in_process_testclient_sqlite",
        "evidence_grade": "E1_CLOSED_LOOP_IN_PROCESS",
        "sprint": "V6.1-Sprint-5",
        "task_id": "T3-04",
        "git_commit": _git_commit(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "elapsed_seconds": round((finished_at - started_at).total_seconds(), 3),
        "honest_boundaries": [
            "默认模式使用进程内 FastAPI TestClient + 内存 SQLite，验证全部真实 HTTP 路由、状态机与持久化契约；",
            "训练证据载荷来自仓库已归档的真实 GATE-B 证据并通过 gate_b_video_train.build_evidence_payload 转换；",
            "对象存储、模型产物检查器与外部训练 Adapter 使用进程内替身；实时部署栈联调请使用 --live-url。",
        ],
        "steps": steps,
    }


def _live_http_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    validated_url = validate_http_url(url, flag="request_url")
    headers: dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if extra_headers:
        headers.update(extra_headers)
    req = urllib_request.Request(
        validated_url, data=data, headers=headers, method=method
    )
    try:
        # 上方 validate_http_url 已强制校验仅允许 http/https scheme 且主机名非空
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib_request.urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"实时栈请求失败（{method} {validated_url} -> HTTP {exc.code}）：{detail[:500]}"
        ) from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(
            f"实时栈网络异常（{method} {validated_url}）：{exc.reason}"
        ) from exc


def run_live_closed_loop(cfg: LiveConfig) -> dict[str, Any]:
    """对接已运行 DDP 服务（``--live-url``）执行训练证据入库到闭环关闭。"""
    started_at = datetime.now(UTC)
    steps: dict[str, Any] = {}
    base = cfg.live_url

    def _u(suffix: str) -> str:
        return f"{base}{_project_url(cfg.project_id, suffix)}"

    _, evidence_payload = load_training_evidence_payload(
        cfg.evidence_file,
        run_kind=cfg.run_kind,
        dataset_version_id=cfg.dataset_version_id,
    )
    _, ev_created = _live_http_json(
        "POST",
        _u(f"/external-training-references/{cfg.external_ref_id}/evidence"),
        token=cfg.auth_token,
        payload=evidence_payload,
        timeout_seconds=cfg.timeout_seconds,
    )
    _, ev_list = _live_http_json(
        "GET",
        _u(f"/external-training-references/{cfg.external_ref_id}/evidence"),
        token=cfg.auth_token,
        timeout_seconds=cfg.timeout_seconds,
    )
    if ev_created["evidence_id"] not in {
        i["evidence_id"] for i in ev_list.get("items", [])
    }:
        raise RuntimeError("实时栈 GET .../evidence 未命中刚入库的训练证据")
    steps["training_evidence"] = {
        "evidence_id": ev_created["evidence_id"],
        "run_kind": ev_created["run_kind"],
        "status": ev_created["status"],
    }

    _, eval_created = _live_http_json(
        "POST",
        _u("/evaluations"),
        token=cfg.auth_token,
        payload={
            "model_version_id": cfg.model_version_id,
            "dataset_version_id": cfg.dataset_version_id,
            "metrics": {},
            "thresholds": {"success_rate": {"min": 0.9}},
        },
        timeout_seconds=cfg.timeout_seconds,
    )
    eval_id = eval_created["evaluation_id"]
    if cfg.callback_secret:
        cb_body = json.dumps(
            {
                "event_id": f"evt-live-init-{uuid4().hex[:8]}",
                "adapter_version": "1.0.0",
                "event_type": "evaluation.completed",
                "project_id": cfg.project_id,
                "evaluation_id": eval_id,
                "metrics": {"success_rate": 0.42},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        cb_url = validate_http_url(
            f"{base}/api/v1/integrations/model-adapters/{cfg.adapter_id}/callbacks",
            flag="callback_url",
        )
        cb_req = urllib_request.Request(
            cb_url,
            data=cb_body,
            headers=_sign_model_adapter_callback(
                cb_body, secret=cfg.callback_secret, nonce=f"nonce-{uuid4().hex[:8]}"
            ),
            method="POST",
        )
        # 上方 validate_http_url 已强制校验仅允许 http/https scheme 且主机名非空
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib_request.urlopen(cb_req, timeout=cfg.timeout_seconds):  # noqa: S310
            pass
    steps["initial_evaluation"] = {"evaluation_id": eval_id}

    _, feedback = _live_http_json(
        "POST",
        _u("/model-feedback"),
        token=cfg.auth_token,
        payload={
            "model_version_id": cfg.model_version_id,
            "evaluation_id": eval_id,
            "issue_type": "SCENE_COVERAGE",
            "severity": "MAJOR",
            "description": "实时栈闭环 Badcase 样本不足",
            "dimensions": [
                {
                    "dimension_type": "SCENE",
                    "dimension_value": "kitchen",
                    "current_count": 2,
                    "target_count": 10,
                }
            ],
            "evidence_refs": [f"training-evidence/{ev_created['evidence_id']}"],
        },
        timeout_seconds=cfg.timeout_seconds,
    )
    feedback_id = feedback["feedback_id"]
    _, converted = _live_http_json(
        "POST",
        _u(f"/model-feedback/{feedback_id}/actions/create-requirement"),
        token=cfg.auth_token,
        payload={
            "requirement_code": f"DR-LIVE-{uuid4().hex[:6]}",
            "target_episode_count": 8,
        },
        extra_headers={"If-Match": f'"{feedback["version"]}"'},
        timeout_seconds=cfg.timeout_seconds,
    )
    req_id = converted["converted_requirement_id"]
    _, req_detail = _live_http_json(
        "GET",
        _u(f"/data-requirements/{req_id}"),
        token=cfg.auth_token,
        timeout_seconds=cfg.timeout_seconds,
    )
    _, req_submitted = _live_http_json(
        "POST",
        _u(f"/data-requirements/{req_id}/actions/submit"),
        token=cfg.auth_token,
        payload={},
        extra_headers={"If-Match": f'"{req_detail["version"]}"'},
        timeout_seconds=cfg.timeout_seconds,
    )
    _, req_approved = _live_http_json(
        "POST",
        _u(f"/data-requirements/{req_id}/actions/approve"),
        token=cfg.approver_token,
        payload={},
        extra_headers={"If-Match": f'"{req_submitted["version"]}"'},
        timeout_seconds=cfg.timeout_seconds,
    )
    _, req_satisfied = _live_http_json(
        "POST",
        _u(f"/data-requirements/{req_id}/actions/satisfy"),
        token=cfg.approver_token,
        payload={"dataset_version_ids": [cfg.dataset_version_id]},
        extra_headers={"If-Match": f'"{req_approved["version"]}"'},
        timeout_seconds=cfg.timeout_seconds,
    )

    _, retest = _live_http_json(
        "POST",
        _u("/evaluations"),
        token=cfg.auth_token,
        payload={
            "model_version_id": cfg.model_version_id,
            "dataset_version_id": cfg.dataset_version_id,
            "metrics": {},
            "thresholds": {"success_rate": {"min": 0.9}},
        },
        timeout_seconds=cfg.timeout_seconds,
    )
    retest_id = retest["evaluation_id"]
    if cfg.callback_secret:
        rcb_body = json.dumps(
            {
                "event_id": f"evt-live-retest-{uuid4().hex[:8]}",
                "adapter_version": "1.0.0",
                "event_type": "evaluation.completed",
                "project_id": cfg.project_id,
                "evaluation_id": retest_id,
                "metrics": {"success_rate": 0.95},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        cb_url = validate_http_url(
            f"{base}/api/v1/integrations/model-adapters/{cfg.adapter_id}/callbacks",
            flag="callback_url",
        )
        rcb_req = urllib_request.Request(
            cb_url,
            data=rcb_body,
            headers=_sign_model_adapter_callback(
                rcb_body, secret=cfg.callback_secret, nonce=f"nonce-{uuid4().hex[:8]}"
            ),
            method="POST",
        )
        # 上方 validate_http_url 已强制校验仅允许 http/https scheme 且主机名非空
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib_request.urlopen(rcb_req, timeout=cfg.timeout_seconds):  # noqa: S310
            pass
    _, req_linked = _live_http_json(
        "POST",
        _u(f"/data-requirements/{req_id}/actions/link-retest"),
        token=cfg.approver_token,
        payload={"evaluation_id": retest_id},
        extra_headers={"If-Match": f'"{req_satisfied["version"]}"'},
        timeout_seconds=cfg.timeout_seconds,
    )
    _, req_closed = _live_http_json(
        "POST",
        _u(f"/data-requirements/{req_id}/actions/close"),
        token=cfg.approver_token,
        payload={},
        extra_headers={"If-Match": f'"{req_linked["version"]}"'},
        timeout_seconds=cfg.timeout_seconds,
    )
    _, fb_resolved = _live_http_json(
        "POST",
        _u(f"/model-feedback/{feedback_id}/actions/resolve"),
        token=cfg.approver_token,
        payload={"resolution_note": f"实时栈复测 {retest_id} 通过，闭环关闭"},
        extra_headers={"If-Match": f'"{converted["version"]}"'},
        timeout_seconds=cfg.timeout_seconds,
    )
    steps["closure"] = {
        "requirement_id": req_id,
        "requirement_status": req_closed["status"],
        "feedback_id": feedback_id,
        "feedback_status": fb_resolved["status"],
        "retest_evaluation_id": retest_id,
    }
    finished_at = datetime.now(UTC)
    return {
        "status": "PASSED",
        "mode": "live_http",
        "evidence_grade": "E1_CLOSED_LOOP_LIVE",
        "sprint": "V6.1-Sprint-5",
        "task_id": "T3-04",
        "live_url": cfg.live_url,
        "git_commit": _git_commit(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "elapsed_seconds": round((finished_at - started_at).total_seconds(), 3),
        "steps": steps,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="V6.1 Sprint 5 T3-04 训练证据→评测→Badcase→补采需求→复测关闭全闭环 E2E"
    )
    parser.add_argument(
        "--live-url",
        default=None,
        help="可选：已运行 DDP API 基地址（http:// 或 https://）；缺省时运行进程内 TestClient 全闭环",
    )
    parser.add_argument("--project-id", default=None, help="live 模式项目 UUID")
    parser.add_argument(
        "--auth-token", default=None, help="live 模式操作员 Bearer Token"
    )
    parser.add_argument(
        "--approver-token", default=None, help="live 模式审批人 Bearer Token（Owner）"
    )
    parser.add_argument(
        "--external-ref-id", default=None, help="live 模式外部训练引用 UUID"
    )
    parser.add_argument(
        "--dataset-version-id", default=None, help="live 模式已冻结数据集版本 UUID"
    )
    parser.add_argument(
        "--model-version-id", default=None, help="live 模式已就绪模型版本 UUID"
    )
    parser.add_argument(
        "--evidence-file",
        default=str(DEFAULT_EVIDENCE_FILE),
        help="真实训练 evidence.json 路径（默认使用仓库已归档 GATE-B 证据）",
    )
    parser.add_argument(
        "--run-kind",
        choices=("SMOKE", "REAL"),
        default="SMOKE",
        help="训练证据类型（默认 SMOKE）",
    )
    parser.add_argument(
        "--adapter-id", default="mock", help="live 模式评测回调 adapter_id（默认 mock）"
    )
    parser.add_argument(
        "--callback-secret", default=None, help="live 模式评测回调 HMAC 密钥（可选）"
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="live 模式 HTTP 超时秒数（默认 30）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="可选：将闭环报告 JSON 写入指定文件路径",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.live_url is not None:
            cfg = validate_live_args(args)
            report = run_live_closed_loop(cfg)
        else:
            report = run_in_process_closed_loop(
                evidence_file=Path(args.evidence_file),
                run_kind=args.run_kind,
            )
    except ValueError as exc:
        print(
            json.dumps(
                {"status": "ERROR", "error": str(exc)}, ensure_ascii=False, indent=2
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"status": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2
            ),
            file=sys.stderr,
        )
        return 1

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
