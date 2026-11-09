"""阶段 5 本地真实 PostgreSQL 链路验证（受控数据，非 staging）。

覆盖：模型 → 版本产物校验 → 发布 → 评测回调 → 反馈 → 数据缺口 → 数据需求
→ 满足 → 复测评测 → 关闭 → 推理任务回调；并检查跨项目隔离与回调重放。

说明：
- 数据库为本地 PostgreSQL 16（Alembic 0021），应用通过 TestClient 走真实路由、
  Repository、SQL 与 PostgreSQL 守卫；不是 SQLite。
- 模型产物为 `[SYNTHETIC]` 本地文件：当前环境无法启动 MinIO/Docker 镜像下载，
  产物存在性/大小校验由显式标注的本地文件 inspector 提供，真实 S3 头校验 `[未验证]`。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DDP_ENV", "local")
os.environ.setdefault(
    "DDP_DATABASE_URL", "postgresql+psycopg://hualei@127.0.0.1:5432/ddp_stage5_e2e"
)
os.environ.setdefault("DDP_JWT_SECRET", "local-dev-secret-EXAMPLE-NOT-A-REAL-KEY")
os.environ.setdefault("DDP_S3_ACCESS_KEY", "test-access-key")
os.environ.setdefault("DDP_S3_SECRET_KEY", "test-secret-key-0123456789abcdef")

from sqlalchemy import select  # noqa: E402

from app.api.v1.modelops_dependencies import get_artifact_inspector  # noqa: E402
from app.application.modelops.ports import ArtifactInspector  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.domain.identity.models import ProjectRole  # noqa: E402
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent  # noqa: E402
from app.infrastructure.db.models.identity import Project, ProjectMember, User  # noqa: E402
from app.infrastructure.db.session import create_db_engine, create_session_factory  # noqa: E402
from tests.api.conftest import ApiIds, make_client  # noqa: E402
from tests.api.dataset_support import url  # noqa: E402
from tests.api.export_support import build_frozen_version  # noqa: E402

PROJECT = UUID("00000000-0000-4000-8000-000000000002")
OWNER = UUID("00000000-0000-4000-8000-000000000001")
EDITOR = UUID("00000000-0000-4000-8000-000000000003")
OUTSIDER = UUID("00000000-0000-4000-8000-000000000099")
SECRET = "stage5-local-adapter-secret-0123456789"
ARTIFACT = Path("/tmp/ddp-stage5-artifact.onnx")
IDS = ApiIds(
    project_a=PROJECT,
    project_b=UUID("00000000-0000-4000-8000-000000000012"),
    editor_a=EDITOR,
    owner_a=OWNER,
    viewer_a=UUID("00000000-0000-4000-8000-000000000004"),
    outsider=OUTSIDER,
)

results: list[dict[str, object]] = []


def record(step: str, status: int, detail: object = "") -> None:
    results.append({"step": step, "status": status, "detail": detail})
    print(f"[{step}] HTTP {status} {detail}")


class LocalFileInspector(ArtifactInspector):
    """[SYNTHETIC] 以本地文件大小代替 S3 HEAD；真实对象存储校验未在本轮覆盖。"""

    def object_size(self, object_key: str) -> int | None:
        return ARTIFACT.stat().st_size if ARTIFACT.exists() else None

    def object_sha256(self, object_key: str) -> str | None:
        if not ARTIFACT.exists():
            return None
        return "sha256:" + hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()


def seed_identity(factory) -> None:
    with factory() as session:
        if session.get(Project, PROJECT) is None:
            session.add_all(
                [
                    Project(id=PROJECT, code="stage5-e2e", name="阶段 5 验收项目"),
                    Project(
                        id=IDS.project_b, code="stage5-e2e-b", name="阶段 5 隔离项目"
                    ),
                ]
            )
            for user_id, name in [
                (OWNER, "项目负责人"),
                (EDITOR, "编辑者"),
                (IDS.viewer_a, "只读用户"),
                (OUTSIDER, "项目外用户"),
            ]:
                if session.get(User, user_id) is None:
                    session.add(User(id=user_id, display_name=name))
            session.flush()
            session.add_all(
                [
                    ProjectMember(
                        project_id=PROJECT,
                        user_id=OWNER,
                        role=ProjectRole.OWNER.value,
                    ),
                    ProjectMember(
                        project_id=PROJECT,
                        user_id=EDITOR,
                        role=ProjectRole.EDITOR.value,
                    ),
                    ProjectMember(
                        project_id=PROJECT,
                        user_id=IDS.viewer_a,
                        role=ProjectRole.VIEWER.value,
                    ),
                ]
            )
            session.commit()


def signed_headers(body: bytes, *, nonce: str) -> dict[str, str]:
    ts = int(datetime.now(UTC).timestamp())
    digest = hmac.new(
        SECRET.encode(), f"{ts}.{nonce}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return {
        "X-DDP-Signature": f"sha256={digest}",
        "X-DDP-Timestamp": str(ts),
        "X-DDP-Nonce": nonce,
        "Content-Type": "application/json",
    }


def callback(client, payload: dict[str, object], *, nonce: str | None = None):
    body = json.dumps(payload, separators=(",", ":")).encode()
    return client.post(
        "/api/v1/integrations/model-adapters/mock/callbacks",
        content=body,
        headers=signed_headers(body, nonce=nonce or f"nonce-{uuid4().hex[:8]}"),
    )


def main() -> int:
    engine = create_db_engine(
        os.environ["DDP_DATABASE_URL"],
        pool_size=5,
        max_overflow=2,
        pool_timeout_seconds=10,
        connect_timeout_seconds=10,
    )
    factory = create_session_factory(engine)
    seed_identity(factory)

    settings = Settings(env="local", model_adapter_shared_secrets={"mock": SECRET})
    editor = make_client(factory, EDITOR, settings_override=settings)
    owner = make_client(factory, OWNER, settings_override=settings)
    outsider = make_client(factory, OUTSIDER, settings_override=settings)
    inspector = LocalFileInspector()
    for client in (editor, owner, outsider):
        client.app.dependency_overrides[get_artifact_inspector] = lambda: inspector

    ARTIFACT.write_bytes(bytes(range(256)) * 16)
    sha = "sha256:" + hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
    size = ARTIFACT.stat().st_size

    context = build_frozen_version(factory, IDS)
    version_id = str(context["version_id"])
    record("冻结数据集版本", 200, {"dataset_version_id": version_id})

    created_model = editor.post(
        url(PROJECT, "/models"),
        json={
            "code": f"ACT-{uuid4().hex[:6]}",
            "name": "阶段 5 抓取策略模型",
            "task_type": "IMITATION",
            "framework": "pytorch",
            "owner_id": str(OWNER),
        },
    )
    assert created_model.status_code == 201, created_model.text
    model = created_model.json()
    record("创建模型", 201, {"model_id": model["model_id"], "status": model["status"]})

    created_version = editor.post(
        url(PROJECT, f"/models/{model['model_id']}/versions"),
        json={
            "artifact": {
                "object_key": "models/stage5-e2e/artifact.onnx",
                "sha256": sha,
                "size_bytes": size,
                "format": "ONNX",
            },
            "runtime": {
                "framework": "pytorch",
                "framework_version": "2.4.0",
                "python_version": "3.11",
            },
            "dataset_version_ids": [version_id],
            "metrics_summary": {"train_loss": 0.12},
        },
    )
    assert created_version.status_code == 201, created_version.text
    version = created_version.json()
    record(
        "创建模型版本",
        201,
        {"model_version_id": version["model_version_id"], "artifact_size": size},
    )

    validated = editor.post(
        url(
            PROJECT,
            f"/models/{model['model_id']}/versions/{version['model_version_id']}/actions/validate",
        ),
        headers={"If-Match": f'"{version["version"]}"'},
        json={},
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "READY"
    record("产物校验→READY", 200, {"status": "READY"})

    published = owner.post(
        url(
            PROJECT,
            f"/models/{model['model_id']}/versions/{version['model_version_id']}/actions/publish",
        ),
        headers={"If-Match": f'"{validated.json()["version"]}"'},
        json={},
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "RELEASED"
    record("发布版本→RELEASED", 200, {"released_at": published.json()["released_at"]})

    republish = owner.post(
        url(
            PROJECT,
            f"/models/{model['model_id']}/versions/{version['model_version_id']}/actions/publish",
        ),
        headers={"If-Match": f'"{published.json()["version"]}"'},
        json={},
    )
    assert republish.status_code == 409, republish.text
    record("重复发布被拒绝", 409, republish.json()["error"]["code"])

    forbidden = outsider.get(url(PROJECT, f"/models/{model['model_id']}"))
    assert forbidden.status_code in {403, 404}, forbidden.text
    record("跨项目用户读取被拒绝", forbidden.status_code, "隔离生效")

    created_eval = editor.post(
        url(PROJECT, "/evaluations"),
        json={
            "model_version_id": version["model_version_id"],
            "dataset_version_id": version_id,
            "metrics": {},
            "thresholds": {"success_rate": {"min": 0.9}},
        },
    )
    assert created_eval.status_code == 202, created_eval.text
    evaluation_id = created_eval.json()["evaluation_id"]
    record("评测入队", 202, {"evaluation_id": evaluation_id, "status": "QUEUED"})

    fail_cb = callback(
        editor,
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "evaluation.completed",
            "project_id": str(PROJECT),
            "evaluation_id": evaluation_id,
            "metrics": {"success_rate": 0.42},
        },
    )
    assert fail_cb.status_code == 204, fail_cb.text
    eval_body = editor.get(url(PROJECT, f"/evaluations/{evaluation_id}")).json()
    assert eval_body["verdict"] == "FAIL", eval_body
    record(
        "评测回调→FAIL",
        204,
        {"status": eval_body["status"], "verdict": eval_body["verdict"]},
    )

    feedback = editor.post(
        url(PROJECT, "/model-feedback"),
        json={
            "model_version_id": version["model_version_id"],
            "evaluation_id": evaluation_id,
            "issue_type": "SCENE_COVERAGE",
            "severity": "MAJOR",
            "description": "厨房场景抓取成功率仅 0.42，低于阈值 0.9",
            "dimensions": [
                {
                    "dimension_type": "SCENE",
                    "dimension_value": "kitchen",
                    "current_count": 2,
                    "target_count": 10,
                }
            ],
            "evidence_refs": ["models/stage5-e2e/artifact.onnx"],
        },
    )
    assert feedback.status_code == 201, feedback.text
    feedback_body = feedback.json()
    record("登记模型反馈", 201, {"feedback_id": feedback_body["feedback_id"]})

    gaps = editor.get(url(PROJECT, "/data-gaps")).json()
    kitchen = next(i for i in gaps["items"] if i["dimension_value"] == "kitchen")
    assert kitchen["suggested_episode_count"] == 8, kitchen
    record("数据缺口聚合", 200, {"suggested_episode_count": 8})

    converted = editor.post(
        url(
            PROJECT,
            f"/model-feedback/{feedback_body['feedback_id']}/actions/create-requirement",
        ),
        headers={"If-Match": f'"{feedback_body["version"]}"'},
        json={
            "requirement_code": f"DR-{uuid4().hex[:6]}",
            "requirement_title": "补采厨房场景抓取数据",
            "target_episode_count": 8,
        },
    )
    assert converted.status_code == 200, converted.text
    requirement_id = converted.json()["converted_requirement_id"]
    record(
        "反馈转数据需求",
        200,
        {"requirement_id": requirement_id, "feedback_status": converted.json()["status"]},
    )

    requirement = editor.get(url(PROJECT, f"/data-requirements/{requirement_id}")).json()
    assert requirement["source_feedback_ids"] == [feedback_body["feedback_id"]]
    submitted = editor.post(
        url(PROJECT, f"/data-requirements/{requirement_id}/actions/submit"),
        headers={"If-Match": f'"{requirement["version"]}"'},
        json={},
    )
    assert submitted.status_code == 200, submitted.text
    self_approve = editor.post(
        url(PROJECT, f"/data-requirements/{requirement_id}/actions/approve"),
        headers={"If-Match": f'"{submitted.json()["version"]}"'},
        json={},
    )
    assert self_approve.status_code == 403, self_approve.text
    record("提交人自批被拒绝", 403, "职责分离生效")
    approved = owner.post(
        url(PROJECT, f"/data-requirements/{requirement_id}/actions/approve"),
        headers={"If-Match": f'"{submitted.json()["version"]}"'},
        json={},
    )
    assert approved.status_code == 200, approved.text
    record("负责人批准需求", 200, {"status": approved.json()["status"]})

    unsatisfied = owner.post(
        url(PROJECT, f"/data-requirements/{requirement_id}/actions/satisfy"),
        headers={"If-Match": f'"{approved.json()["version"]}"'},
        json={"dataset_version_ids": []},
    )
    assert unsatisfied.status_code == 409, unsatisfied.text
    record("未关联冻结版本不能满足", 409, unsatisfied.json()["error"]["code"])

    satisfied = owner.post(
        url(PROJECT, f"/data-requirements/{requirement_id}/actions/satisfy"),
        headers={"If-Match": f'"{approved.json()["version"]}"'},
        json={"dataset_version_ids": [version_id]},
    )
    assert satisfied.status_code == 200, satisfied.text
    assert satisfied.json()["status"] == "SATISFIED"
    record("关联冻结版本→SATISFIED", 200, {"status": "SATISFIED"})

    retest = editor.post(
        url(PROJECT, "/evaluations"),
        json={
            "model_version_id": version["model_version_id"],
            "dataset_version_id": version_id,
            "metrics": {},
            "thresholds": {"success_rate": {"min": 0.9}},
        },
    )
    assert retest.status_code == 202, retest.text
    retest_id = retest.json()["evaluation_id"]
    pass_cb = callback(
        editor,
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "evaluation.completed",
            "project_id": str(PROJECT),
            "evaluation_id": retest_id,
            "metrics": {"success_rate": 0.95},
        },
    )
    assert pass_cb.status_code == 204, pass_cb.text
    retest_body = editor.get(url(PROJECT, f"/evaluations/{retest_id}")).json()
    assert retest_body["verdict"] == "PASS", retest_body
    record("复测评测→PASS", 204, {"verdict": "PASS"})

    linked = owner.post(
        url(PROJECT, f"/data-requirements/{requirement_id}/actions/link-retest"),
        headers={"If-Match": f'"{satisfied.json()["version"]}"'},
        json={"evaluation_id": retest_id},
    )
    assert linked.status_code == 200, linked.text
    record("关联复测评测", 200, {"retest_evaluation_ids": [retest_id]})

    closed = owner.post(
        url(PROJECT, f"/data-requirements/{requirement_id}/actions/close"),
        headers={"If-Match": f'"{linked.json()["version"]}"'},
        json={"reason": "复测通过，关闭需求"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"
    record("关闭数据需求", 200, {"status": "CLOSED"})

    task = editor.post(
        url(PROJECT, "/inference-tasks"),
        json={
            "model_version_id": version["model_version_id"],
            "episode_ids": [str(uuid4())],
        },
    )
    assert task.status_code == 202, task.text
    task_id = task.json()["task_id"]
    task_event_id = f"evt-{uuid4().hex[:8]}"
    task_payload = {
        "event_id": task_event_id,
        "adapter_version": "1.0.0",
        "event_type": "inference.completed",
        "project_id": str(PROJECT),
        "task_id": task_id,
        "result_ref": {
            "object_key": "inference/stage5-e2e/result.json",
            "sha256": sha,
        },
        "metrics": {"latency_ms": 12.5},
    }
    task_cb = callback(editor, task_payload)
    assert task_cb.status_code == 204, task_cb.text
    task_body = editor.get(url(PROJECT, f"/inference-tasks/{task_id}")).json()
    assert task_body["status"] == "SUCCEEDED", task_body
    record("推理任务回调→SUCCEEDED", 204, {"metrics": task_body["metrics"]})

    duplicate = callback(editor, task_payload)
    assert duplicate.status_code == 204, duplicate.text
    record("同一 event_id 同内容回调幂等", 204, "IDEMPOTENT")

    replay = callback(editor, {**task_payload, "metrics": {"latency_ms": 99.0}})
    assert replay.status_code == 409, replay.text
    record("同一 event_id 不同内容被拒绝", 409, replay.json()["error"]["code"])

    with factory() as session:
        audit_count = len(
            session.scalars(
                select(AuditLog).where(AuditLog.project_id == PROJECT)
            ).all()
        )
        outbox_count = len(
            session.scalars(select(OutboxEvent).where(OutboxEvent.project_id == PROJECT)).all()
        )
    record("审计与 Outbox 落库", 200, {"audit_logs": audit_count, "outbox": outbox_count})
    assert audit_count > 0 and outbox_count > 0

    engine.dispose()
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "database": "PostgreSQL 16.15 local (alembic 0021)",
        "artifact": "[SYNTHETIC] local file; S3 HEAD not covered",
        "steps": results,
    }
    out = Path(__file__).with_name("summary.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"\n全部阶段 5 链路步骤通过，证据写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
