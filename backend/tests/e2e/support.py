"""主链端到端测试支撑：真实导入 Worker + 真实业务 API。

**复用审计（不另起平行体系）**

- 身份 / 数据库 / 客户端：``tests.api.conftest`` 的 ``ApiIds``、``make_client``、
  ``seed_identity``；
- 标注链路（Schema / 样本集 / 任务 / 批次 / 提交 / 复核）：``tests.api.annotation_support``；
- 数据集策略与版本载荷：``tests.api.dataset_support``；
- 导出与对象存储替身：``tests.api.export_support``；
- 训练需求 / 修订 / 交付载荷：``tests.api.training_support``。

本模块只补现有支撑没有的两类东西：

1. **HF 导入链路的确定性输入**：HTTP 传输层替身 + 内存对象存储。URL 构造、
   内容签名、大小上限、sha256、Adapter 内容探测、导入状态机、RawAsset
   生命周期仍然全部走生产代码（``_HttpxFetcher`` / ``_parse_metadata`` /
   ``_registry_parser`` / ``ImportRunner`` / ``DataSourceImportWorker``）。
   替身只替换"外网"与"MinIO"这两个外部基础设施。
2. **Episode 就绪所需的 Manifest 组装**：平台当前没有从导入产物生成
   Canonical Manifest 的组件（见验收报告「主链真实状态」），本模块按
   ``app.domain.episodes.manifest`` 的字段要求，用**真实已落库的资产事实**
   （asset_id / object_key / sha256 / size_bytes）组装，不编造业务结论。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from app.api.deps import get_current_principal

from tests.api.dataset_support import url

# 模型平台 Adapter 的 HMAC 密钥：仅测试内使用，显式标注不是真实凭据。
MODEL_ADAPTER_SECRET = "placeholder-model-adapter-secret-0123456789abcdef"

# 导入的确定性输入：两个 Episode、三个机位（一个多机位、一个单机位）。
# 10 条 Episode：1 条多机位（验证多模态流）+ 9 条单机位。
# 条数取 10 不是随意：训练/验证/测试按 8:1:1 划分时，两条数据的划分必然出现
# 空分片（`empty_split_policy=FAIL`），链路会在 build 阶段被真实规则拒绝。
EPISODE_SPECS: tuple[tuple[str, str, str, tuple[tuple[str, float, float], ...]], ...] = tuple(
    [
        (
            "demo_001",
            "Sew lace fabric",
            "sew the lace fabric",
            (("main", 0.0, 5.0), ("wrist", 0.0, 5.0)),
        )
    ]
    + [
        (
            f"demo_{index:03d}",
            f"Manipulation task {index}",
            f"complete manipulation task {index}",
            (("main", 0.0, 5.0 + index),),
        )
        for index in range(2, 11)
    ]
)
EXPECTED_EPISODES = len(EPISODE_SPECS)
EXPECTED_ASSETS = sum(len(spec[3]) for spec in EPISODE_SPECS)


def _mp4_bytes(seed: str) -> bytes:
    """构造内容签名合法的 MP4 字节（offset 4 起必须是 ``ftyp``）。"""
    body = hashlib.sha256(seed.encode("utf-8")).digest() * 64
    return b"\x00\x00\x00\x18ftypmp42" + body + b"\x00" * 16


def metadata_rows() -> list[dict[str, Any]]:
    """``preview/metadata.jsonl`` 的行（generic.episode_jsonl 格式）。"""
    rows: list[dict[str, Any]] = []
    for episode_key, task_name, instruction, cameras in EPISODE_SPECS:
        rows.append(
            {
                "episode_key": episode_key,
                "task_name": task_name,
                "instruction": instruction,
                "episode_seconds": max(item[2] for item in cameras),
                "source_fps": 30.0,
                "environment": "kitchen",
                "setting": "table",
                "media_assets": [
                    {
                        "path": f"videos/{episode_key}_{camera}.mp4",
                        "camera": camera,
                        "excerpt_start_seconds": start,
                        "excerpt_seconds": seconds,
                        "source_fps": 30.0,
                    }
                    for camera, start, seconds in cameras
                ],
            }
        )
    return rows


def expected_media_paths() -> dict[str, tuple[str, str]]:
    """``path -> (episode_key, camera)``，用于对账导入产物。"""
    result: dict[str, tuple[str, str]] = {}
    for row in metadata_rows():
        for asset in row["media_assets"]:
            result[str(asset["path"])] = (str(row["episode_key"]), str(asset["camera"]))
    return result


class MediaTransport:
    """Hugging Face 传输层替身：只替换网络，校验与解析走生产实现。

    显式标注：这不是对业务逻辑的 mock。``fetch_file`` 仍会按 URL 取字节、
    计算 sha256、拒绝超限/空文件/内容与扩展名不符。
    """

    def __init__(self, *, drop_paths: set[str] | None = None) -> None:
        self._drop = set(drop_paths or set())
        files = {f"preview/{path}": _mp4_bytes(path) for path in expected_media_paths()}
        metadata = "\n".join(json.dumps(row, ensure_ascii=False) for row in metadata_rows()).encode(
            "utf-8"
        )
        self._files: dict[str, bytes] = {**files, "preview/metadata.jsonl": metadata}

    def handler(self, request: httpx.Request) -> httpx.Response:
        # 生产 URL：https://huggingface.co/datasets/{repo}/resolve/{rev}/{path}
        marker = "/resolve/"
        if marker not in request.url.path:
            return httpx.Response(404, content=b"not found")
        repo_path = request.url.path.split(marker, 1)[1]
        path = repo_path.split("/", 1)[1] if "/" in repo_path else repo_path
        if path in self._drop:
            return httpx.Response(503, content=b"upstream unavailable")
        content = self._files.get(path)
        if content is None:
            return httpx.Response(404, content=b"not found")
        return httpx.Response(200, content=content)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


class InMemoryObjectStore:
    """MinIO/S3 写入替身（显式标注）；只实现导入链路用到的 ``put_bytes``。"""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_bytes(self, object_key: str, data: bytes, *, content_type: str) -> None:
        self.objects[object_key] = (bytes(data), content_type)

    def put(self, object_key: str, data: bytes, *, content_type: str) -> None:
        self.put_bytes(object_key, data, content_type=content_type)


def install_object_store(monkeypatch: Any, store: InMemoryObjectStore) -> None:
    """把 Worker 内构造的 ``S3MultipartStorage`` 换成本地替身。

    只替换基础设施适配器；``DataSourceObjectWriter``、``build_runner``、
    Worker 流程都保持生产实现。
    """
    from app.workers import datasources as worker_module

    monkeypatch.setattr(worker_module, "S3MultipartStorage", lambda _settings: store)


def run_import_worker(
    factory: Any,
    transport: MediaTransport,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """跑一次真实 ``DataSourceImportWorker.process_once``。"""
    from app.core.config import Settings
    from app.workers.datasources import DataSourceImportWorker

    settings = Settings(env="local")
    client = transport.client()
    try:
        worker = DataSourceImportWorker(factory, settings, client)
        return worker.process_once(now=now or datetime.now(UTC) + timedelta(seconds=1))
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 导入 → Episode → ACCEPTED
# ---------------------------------------------------------------------------


def register_import_chain(
    editor: Any,
    project_id: UUID,
    *,
    code: str,
    sample_limit: int = EXPECTED_EPISODES,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """登记数据源并创建导入任务（不下载数据，事件由 Worker 消费）。"""
    source = editor.post(
        url(project_id, "/data-sources"),
        json={
            "code": code,
            "name": "主链验收数据源",
            "kind": "HUGGINGFACE",
            "repo_id": "ddp/e2e-main-chain",
            "revision": "b" * 40,
            "license": "CC-BY-4.0",
            "access_condition": "PUBLIC",
            "is_gated": False,
            "notes": "端到端链路验收：数据源 → 导入 → RawAsset → Episode",
        },
        headers={"Idempotency-Key": f"ds-{code}"},
    )
    assert source.status_code == 201, source.text
    created = editor.post(
        url(project_id, f"/data-sources/{source.json()['data_source_id']}/imports"),
        json={
            "scope": "SAMPLE",
            "tool_name": "huggingface_hub",
            "tool_version": "0.27.1",
            "sample_limit": sample_limit,
        },
        headers={"Idempotency-Key": f"import-{code}"},
    )
    assert created.status_code == 201, created.text
    return source.json(), created.json()


def materialize_episodes(
    editor: Any,
    project_id: UUID,
    import_id: UUID,
    *,
    scene_labels: list[str],
) -> dict[str, Any]:
    response = editor.post(
        url(project_id, f"/data-source-imports/{import_id}/episodes"),
        json={"scene_labels": scene_labels},
    )
    assert response.status_code == 201, response.text
    return response.json()


def raw_assets_of(client: Any, project_id: UUID, asset_ids: list[UUID]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for asset_id in asset_ids:
        response = client.get(url(project_id, f"/raw-assets/{asset_id}"))
        assert response.status_code == 200, response.text
        assets.append(response.json())
    return assets


def episode_asset_ids(client: Any, project_id: UUID, episode_id: UUID) -> list[UUID]:
    """从血缘图取该 Episode 关联的 RawAsset（唯一的权威关联来源）。

    边方向是 ``RAW_ASSET --SEGMENTED_INTO--> EPISODE``，所以从 Episode 出发
    必须用 ``reverse``（遍历指向当前节点的边）。
    """
    response = client.get(
        f"/api/v1/episodes/{episode_id}/lineage",
        params={"project_id": str(project_id), "direction": "reverse", "depth": 1},
    )
    assert response.status_code == 200, response.text
    asset_ids: list[UUID] = []
    for edge in response.json()["items"]:
        if edge["from_node_type"] == "RAW_ASSET" and edge["relation"] == "SEGMENTED_INTO":
            asset_ids.append(UUID(edge["from_node_id"]))
    assert asset_ids, f"Episode {episode_id} 没有 RAW_ASSET 血缘边"
    return asset_ids


def build_episode_manifest(
    episode: Mapping[str, Any],
    raw_assets: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """按 ``MANIFEST_REQUIRED_KEYS`` 用已落库事实组装 Manifest。

    **这是人工覆盖路径专用**：正常情况下 Manifest 由平台在导入物化时自动生成
    （``app/domain/episodes/readiness.py`` + ``app/application/episodes/readiness.py``），
    人工不再需要组装。保留本函数是为了覆盖"自动判定按规则拒绝、但有据可依"的特例，
    并证明 ``actions/ready`` 这条既有契约仍可用。

    这不是"造业务结论"：每个 stream 的 object_key / sha256 / size_bytes 都来自
    ``GET /raw-assets/{id}`` 的真实响应；quality 如实填 Episode 当前状态
    （导入后为 NOT_CHECKED），不声称通过。
    """
    time_base = dict(episode["time_base"])
    return {
        "schema_version": episode["canonical_schema_version"],
        "episode_id": str(episode["episode_id"]),
        "episode_lineage_id": str(episode["episode_lineage_id"]),
        "episode_version": episode["episode_version"],
        "project_id": str(episode["project_id"]),
        "code": episode["code"],
        "task_id": episode["task_id"],
        "batch_id": episode["batch_id"],
        "robot_id": episode["robot_id"],
        "device_ids": [str(item) for item in episode["device_ids"]],
        "sensor_ids": [str(item) for item in episode["sensor_ids"]],
        "start_time": str(episode["start_time_ns"]),
        "end_time": str(episode["end_time_ns"]),
        "duration_ns": str(episode["duration_ns"]),
        "time_base": time_base,
        "time_alignment": {
            "clock_source": time_base.get("clock_source"),
            "sync_method": time_base.get("sync_method"),
            "sync_status": time_base.get("sync_status"),
        },
        "calibration_snapshot": [dict(item) for item in episode["calibration_snapshot"]],
        "streams": [
            {
                "stream_id": item["asset_id"],
                "object_key": item["object_key"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
                "content_type": item["content_type"],
                "format": item["format"],
            }
            for item in raw_assets
        ],
        "raw_asset_ids": [str(item["asset_id"]) for item in raw_assets],
        "provenance": {
            "pipeline_id": episode["pipeline_id"],
            "pipeline_version": episode["pipeline_version"],
            "source": "data_source_import",
        },
        "quality": {"status": episode["quality_status"], "issues": []},
        "frame_count": episode["frame_count"],
    }


def episode_action(
    client: Any,
    project_id: UUID,
    path: str,
    *,
    version: int,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    expect: int = 200,
) -> Any:
    headers = {"If-Match": f'"{version}"'}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    response = client.post(url(project_id, path), headers=headers, json=payload or {})
    assert response.status_code == expect, response.text
    return response


def advance_episode_to_accepted(
    editor: Any,
    project_id: UUID,
    episode_id: UUID,
    *,
    quality_status: str = "PASS",
    manual_override: bool = False,
) -> dict[str, Any]:
    """READY → 质检 → PASS → ACCEPTED，全部走真实 Episode 用例。

    Episode 的 READY 现在由**平台自己**在导入物化时置上（Canonical Manifest
    生成器，见 ``docs/testing/主链端到端与可追溯性验收-2026-11-17.md`` §6.2 的
    后续实现）。因此这里默认**断言它已经是 READY**，而不是替平台补一步：
    若自动就绪回退成人工步骤，本函数会立刻失败。

    ``manual_override=True`` 只用于"自动判定按规则拒绝、但有据可依"的特例
    （例如多机位导入数据本身不含标定快照）。此时仍须先把真实事实组装成
    Manifest——``build_episode_manifest`` 的每个字段都来自已落库资产，
    ``quality`` 如实填当前状态，不编造结论。

    质检结论的来源见验收文档：先由平台自身的导入质量规则引擎判定（真实规则），
    再由本函数把结论登记到 Episode 上。``quality_status`` 不是随手填的常量。
    """
    current = editor.get(url(project_id, f"/episodes/{episode_id}")).json()
    if not manual_override:
        assert current["status"] == "READY", current
        assert current["manifest_key"] and current["manifest_sha256"], current
        assert current["frame_count"] > 0, current
        assert current["readiness_blockers"] == [], current
        ready = current
    else:
        assert current["status"] == "PENDING", current
        assert current["readiness_blockers"], current
        raw_assets = raw_assets_of(
            editor, project_id, episode_asset_ids(editor, project_id, episode_id)
        )
        manifest = build_episode_manifest(current, raw_assets)
        ready = episode_action(
            editor,
            project_id,
            f"/episodes/{episode_id}/actions/ready",
            version=current["version"],
            payload={"manifest": manifest, "frame_count": current["frame_count"]},
            idempotency_key=f"ready-{episode_id}",
        ).json()
        assert ready["status"] == "READY", ready
    checking = episode_action(
        editor,
        project_id,
        f"/episodes/{episode_id}/actions/quality-check",
        version=ready["version"],
        idempotency_key=f"qc-{episode_id}",
    ).json()
    assert checking["status"] == "QUALITY_CHECKING", checking
    recorded = episode_action(
        editor,
        project_id,
        f"/episodes/{episode_id}/actions/quality-result",
        version=checking["version"],
        payload={"quality_status": quality_status},
        idempotency_key=f"qc-result-{episode_id}",
    ).json()
    assert recorded["quality_status"] == quality_status, recorded
    accepted = episode_action(
        editor,
        project_id,
        f"/episodes/{episode_id}/actions/accept",
        version=recorded["version"],
        idempotency_key=f"accept-{episode_id}",
    ).json()
    assert accepted["status"] == "ACCEPTED", accepted
    return accepted


def approve_annotation(
    editor: Any,
    owner: Any,
    project_id: UUID,
    *,
    schema: Mapping[str, Any],
    sample_set: Mapping[str, Any],
    episode_ids: list[UUID],
    assignee_id: UUID,
    code: str,
) -> dict[str, Any]:
    """任务 → 批次 → 分配 → 提交 → 复核 → 批准（全部走真实 HTTP 用例）。"""
    from tests.api.annotation_support import create_ready_work, create_submitted

    work = create_ready_work(
        editor,
        owner,
        project_id,
        dict(schema),
        dict(sample_set),
        episode_ids[0],
        assignee_id,
        code,
        episode_ids=episode_ids,
        planned_item_count=len(episode_ids),
    )
    submission = create_submitted(
        editor,
        project_id,
        work["assignment"]["assignment_id"],
        episode_ids[0],
        episode_ids=episode_ids,
    )
    review = owner.post(
        url(project_id, f"/annotation-submissions/{submission['submission_id']}/reviews"),
        json={"reviewer_id": str(principal_id(owner))},
    )
    assert review.status_code == 201, review.text
    review_id = review.json()["review_id"]
    claimed = episode_action(
        owner,
        project_id,
        f"/annotation-reviews/{review_id}/actions/claim",
        version=review.json()["version"],
    ).json()
    assert claimed["status"] == "IN_REVIEW", claimed
    approved = episode_action(
        owner,
        project_id,
        f"/annotation-reviews/{review_id}/actions/approve",
        version=claimed["version"],
        # 通过复核必须提供全部通过的检查项（领域规则），空 checklist 会被拒绝。
        payload={"checklist": [{"key": "labels_match", "passed": True, "comment": "逐条核对"}]},
    ).json()
    assert approved["status"] == "APPROVED", approved
    final = editor.get(url(project_id, f"/annotation-submissions/{submission['submission_id']}"))
    assert final.status_code == 200, final.text
    assert final.json()["status"] in {"APPROVED", "RESOLVED_APPROVED"}, final.text
    return {
        "task": work["task"],
        "batch": work["batch"],
        "assignment": work["assignment"],
        "submission": final.json(),
        "review": approved,
    }


def principal_id(client: Any) -> UUID:
    """取客户端被替换的认证主体 ID（仅用于构造请求体里的 reviewer_id）。"""
    return client.app.dependency_overrides[get_current_principal]().user_id


# ---------------------------------------------------------------------------
# 模型闭环载荷（阶段 5 契约的输入构造，不含断言）
# ---------------------------------------------------------------------------


def model_payload(owner_id: UUID, code: str) -> dict[str, Any]:
    return {
        "code": code,
        "name": "主链验收模型",
        "task_type": "IMITATION",
        "framework": "pytorch",
        "owner_id": str(owner_id),
    }


def model_version_payload(*, size_bytes: int = 128, sha256: str) -> dict[str, Any]:
    return {
        "artifact": {
            "object_key": "models/e2e-policy.onnx",
            "sha256": sha256,
            "size_bytes": size_bytes,
            "format": "ONNX",
        },
        "runtime": {
            "framework": "pytorch",
            "framework_version": "2.4.0",
            "python_version": "3.11",
        },
    }


def signed_callback_headers(
    body: bytes,
    *,
    nonce: str,
    secret: str = MODEL_ADAPTER_SECRET,
    timestamp: int | None = None,
) -> dict[str, str]:
    ts = timestamp or int(datetime.now(UTC).timestamp())
    digest = hmac.new(secret.encode(), f"{ts}.{nonce}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "X-DDP-Signature": f"sha256={digest}",
        "X-DDP-Timestamp": str(ts),
        "X-DDP-Nonce": nonce,
        "Content-Type": "application/json",
    }


def post_adapter_callback(
    client: Any,
    payload: dict[str, Any],
    *,
    adapter_id: str = "mock",
) -> Any:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return client.post(
        f"/api/v1/integrations/model-adapters/{adapter_id}/callbacks",
        content=body,
        headers=signed_callback_headers(body, nonce=f"nonce-{uuid4().hex}"),
    )


def iter_episodes(client: Any, project_id: UUID, **params: Any) -> Iterator[dict[str, Any]]:
    response = client.get(url(project_id, "/episodes"), params={"limit": 100, **params})
    assert response.status_code == 200, response.text
    yield from response.json()["items"]


__all__ = [
    "EPISODE_SPECS",
    "EXPECTED_ASSETS",
    "EXPECTED_EPISODES",
    "InMemoryObjectStore",
    "MODEL_ADAPTER_SECRET",
    "MediaTransport",
    "advance_episode_to_accepted",
    "approve_annotation",
    "build_episode_manifest",
    "episode_action",
    "episode_asset_ids",
    "expected_media_paths",
    "install_object_store",
    "iter_episodes",
    "materialize_episodes",
    "metadata_rows",
    "model_payload",
    "model_version_payload",
    "post_adapter_callback",
    "principal_id",
    "raw_assets_of",
    "register_import_chain",
    "run_import_worker",
    "signed_callback_headers",
    "url",
]
