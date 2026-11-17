"""阶段 5 模型闭环 API 测试共享构造器：模型/版本、客户端、反馈。

抽出来供缺口覆盖等阶段 5 API 测试共用，避免把 ``test_modelops_api.py`` 继续
推向巨型文件；行为与原文件内的私有辅助完全一致（调用点只用别名导入）。
"""

from __future__ import annotations

from uuid import uuid4

from app.api.v1.modelops_dependencies import get_artifact_inspector
from app.application.modelops.ports import ArtifactInspector
from app.core.config import Settings

from tests.api.conftest import make_client
from tests.api.dataset_support import url

SHA = "sha256:" + "a" * 64
SECRET = "placeholder-model-adapter-secret-0123456789abcdef"


class FakeInspector(ArtifactInspector):
    """按预定大小与 Hash 回显的产物检查器；不访问对象存储。"""

    def __init__(self, size: int, sha: str = SHA) -> None:
        self.size = size
        self.sha = sha

    def object_size(self, object_key: str) -> int | None:
        return self.size

    def object_sha256(self, object_key: str) -> str | None:
        return self.sha


def model_payload(owner_id, code: str | None = None) -> dict[str, object]:
    return {
        "code": code or f"model-{uuid4().hex[:8]}",
        "name": "抓取策略模型",
        "task_type": "IMITATION",
        "framework": "pytorch",
        "owner_id": str(owner_id),
    }


def version_payload(size: int = 128) -> dict[str, object]:
    return {
        "artifact": {
            "object_key": "models/policy.onnx",
            "sha256": SHA,
            "size_bytes": size,
            "format": "ONNX",
        },
        "runtime": {
            "framework": "pytorch",
            "framework_version": "2.4.0",
            "python_version": "3.11",
        },
    }


def settings() -> Settings:
    return Settings(env="local", model_adapter_shared_secrets={"mock": SECRET})


def client(factory, user_id, inspector: FakeInspector, *, settings_override=None):
    client = make_client(factory, user_id, settings_override=settings_override or settings())
    client.app.dependency_overrides[get_artifact_inspector] = lambda: inspector
    return client


def create_model(client, project_id, owner_id) -> dict[str, object]:
    response = client.post(url(project_id, "/models"), json=model_payload(owner_id))
    assert response.status_code == 201, response.text
    return response.json()


def ready_version(client, project_id, model_id, size: int = 128) -> dict[str, object]:
    created = client.post(
        url(project_id, f"/models/{model_id}/versions"), json=version_payload(size)
    )
    assert created.status_code == 201, created.text
    validated = client.post(
        url(
            project_id,
            f"/models/{model_id}/versions/{created.json()['model_version_id']}/actions/validate",
        ),
        headers={"If-Match": f'"{created.json()["version"]}"'},
        json={},
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "READY"
    return validated.json()


def feedback_with_dimension(client, project_id, model_version_id, **dimension) -> dict:
    response = client.post(
        url(project_id, "/model-feedback"),
        json={
            "model_version_id": model_version_id,
            "issue_type": "SCENE_COVERAGE",
            "severity": "MAJOR",
            "description": "覆盖不足",
            "dimensions": [dimension],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


__all__ = [
    "SECRET",
    "SHA",
    "FakeInspector",
    "client",
    "create_model",
    "feedback_with_dimension",
    "model_payload",
    "ready_version",
    "settings",
    "version_payload",
]
