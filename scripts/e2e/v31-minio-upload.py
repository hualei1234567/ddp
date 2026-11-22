"""V3.1 真实 MinIO 上传验收：分片上传 → 会话归属 → 下载签名。

与 `test_capture_session_upload_linkage.py` 的关系
---------------------------------------------------
单元/API 测试用 `FakeMultipartStorage` 只替代 S3 **传输层**，证明业务链路
正确。本脚本补齐另一半：把存储换成真实 `S3MultipartStorage` 打到本机
MinIO，验证「预签名 URL 真的能 PUT、ETag/校验和真的能回读、Complete 真的
能合并、下载签名真的能 GET 到字节、库里的 sha256 与存储里的字节一致」。

真实执行，不 mock：真实 PostgreSQL → 真实 MinIO → 真实 HTTP 路由（TestClient）。

前置::

    docker compose up -d postgres minio
    backend/.venv/bin/alembic upgrade head

用法::

    backend/.venv/bin/python scripts/e2e/v31-minio-upload.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
# ``app.main`` 的导入链会到达 ``ddp_data``（导入物化引用 Canonical 版本常量），
# 而 data-platform 不装进 backend 的 venv；与 ``scripts/check.sh`` 的
# ``PYTHONPATH=backend:data-platform`` 保持一致。
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "data-platform"))


def _apply_default_env() -> None:
    """设置本脚本需要的默认环境变量。

    **必须在 main() 里调用，不能在模块顶层做**：顶层 `setdefault` 会污染任何
    导入本模块的进程（历史教训见 `human1m-full-chain.py` 的同名注释）。
    """
    os.environ.setdefault("DDP_ENV", "local")
    os.environ.setdefault(
        "DDP_DATABASE_URL", "postgresql+psycopg://ddp:ddp@localhost:55432/ddp"
    )
    os.environ.setdefault("DDP_S3_ENDPOINT", "http://127.0.0.1:9000")
    os.environ.setdefault("DDP_S3_ACCESS_KEY", "minioadmin")
    os.environ.setdefault("DDP_S3_SECRET_KEY", "minioadmin")
    os.environ.setdefault("DDP_S3_BUCKET", "ddp-raw")
    os.environ.setdefault("DDP_LOCAL_HEADER_AUTH", "true")
    os.environ.setdefault("DDP_RATE_LIMIT_ENABLED", "false")
    # 按部署配置取分片大小，不在这里另写一套阈值；显式设为 5 MiB 是因为
    # MinIO 要求除最后一片外每片至少 5 MiB，默认 16 MiB 会让 12 MiB 内容
    # 退化成单分片，测不到多分片合并。
    os.environ.setdefault("DDP_UPLOAD_PART_SIZE_BYTES", str(5 * 1024 * 1024))


_apply_default_env()

import httpx  # noqa: E402
from app.api.v1.upload_dependencies import get_object_storage  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.domain.identity.models import ProjectRole  # noqa: E402
from app.infrastructure.db.models.identity import (  # noqa: E402
    Project,
    ProjectMember,
    User,
)
from app.infrastructure.db.session import (  # noqa: E402
    create_db_engine,
    create_session_factory,
    get_session,
)
from app.infrastructure.storage.s3 import S3MultipartStorage  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

# 12 MiB 确定性内容 + 5 MiB 分片 = 3 个分片：既满足 MinIO 对非末片 ≥ 5 MiB
# 的要求，又能在十几秒内跑完，并真正验证多分片合并。
PART_SIZE = 5 * 1024 * 1024
# 内容签名必须是真实 MCAP magic：服务端按字节判型，不看扩展名。
# 声明 `format_hint=MCAP` 却不给 magic 会被 `file_signature_mismatch` 拒绝。
_MCAP_MAGIC = b"\x89MCAP0\r\n"
CONTENT = _MCAP_MAGIC + bytes(range(256)) * (
    (12 * 1024 * 1024 - len(_MCAP_MAGIC)) // 256
)

_EXPECTED_SHA256 = hashlib.sha256(CONTENT).hexdigest()


def fail(message: str) -> int:
    print(f"[FAIL] {message}")
    return 1


def ok(message: str) -> None:
    print(f"[OK] {message}")


def step(index: int, title: str) -> None:
    print(f"\n[{index}] {title}")


def _seed_local_identity(factory: sessionmaker[Session]) -> tuple[UUID, UUID]:
    """真实 PG 里写入一个项目与一个成员，返回 (project_id, user_id)。

    不用测试夹具的 SQLite：本脚本要验证的就是「真实数据库 + 真实对象存储」
    的组合，任何一侧换成内存替身都会失去验收价值。
    """
    project_id, user_id = uuid4(), uuid4()
    with factory() as session:
        session.add(
            Project(
                id=project_id, code=f"v31-{project_id.hex[:8]}", name="V31 验收项目"
            )
        )
        session.add(User(id=user_id, display_name="V31 验收用户"))
        session.flush()
        session.add(
            ProjectMember(
                project_id=project_id,
                user_id=user_id,
                role=ProjectRole.OWNER.value,
            )
        )
        session.commit()
    return project_id, user_id


def _put_parts(
    client: httpx.Client, session_payload: dict[str, object]
) -> list[dict[str, object]]:
    """按预签名 URL 真实 PUT 每个分片，返回 ack 需要的材料。"""
    materials: list[dict[str, object]] = []
    total_parts = (len(CONTENT) + PART_SIZE - 1) // PART_SIZE
    for index in range(1, total_parts + 1):
        chunk = CONTENT[(index - 1) * PART_SIZE : index * PART_SIZE]
        url_resp = client.post(
            f"/api/v1/projects/{session_payload['project_id']}/upload-sessions/"
            f"{session_payload['upload_session_id']}/parts/{index}/url"
        )
        if url_resp.status_code != 200:
            raise RuntimeError(f"分片 {index} 预签名失败：{url_resp.text}")
        signed = url_resp.json()

        # trust_env=False：本机 http_proxy 变量会把 127.0.0.1 的预签名请求
        # 送到代理，预签名 URL 的 Host 与签名不匹配会直接失败。
        put = httpx.put(
            signed["url"],
            content=chunk,
            headers=signed.get("headers") or {},
            timeout=60.0,
            trust_env=False,
        )
        if put.status_code not in (200, 201):
            raise RuntimeError(
                f"分片 {index} PUT 到 MinIO 失败：{put.status_code} {put.text[:200]}"
            )
        etag = put.headers.get("ETag") or put.headers.get("etag")
        if not etag:
            raise RuntimeError(
                f"分片 {index} 响应缺少 ETag（MinIO 未返回，无法完成 multipart）"
            )
        materials.append(
            {
                "part_number": index,
                "etag": etag,
                "size_bytes": len(chunk),
                "sha256": f"sha256:{hashlib.sha256(chunk).hexdigest()}",
            }
        )
    return materials


def main() -> int:
    settings = get_settings()
    print("=" * 72)
    print("V3.1 真实 MinIO 上传验收")
    print(f"  endpoint = {settings.s3_endpoint}")
    print(f"  bucket   = {settings.s3_bucket}")
    print(f"  database = {settings.database_url.split('@')[-1]}")
    print("=" * 72)

    engine = create_db_engine(
        settings.database_url,
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=10,
        connect_timeout_seconds=10,
    )
    # 不调用 `Base.metadata.create_all`：真实库由 Alembic 维护，重复建表会触发
    # 已存在的触发器（calibration_profiles 等），把"基础设施未就绪"伪装成脚本 bug。
    factory = create_session_factory(engine)
    storage = S3MultipartStorage(settings)

    step(0, "真实存储连通性")
    try:
        storage.put_bytes("v31-probe/hello.txt", b"v31", content_type="text/plain")
        read = storage.read_range("v31-probe/hello.txt", start=0, length=3)
    except Exception as exc:  # noqa: BLE001 - 验收脚本需要看到原始失败原因
        return fail(f"MinIO 不可用：{exc}")
    if read != b"v31":
        return fail(f"MinIO 回读不一致：{read!r}")
    ok("真实 MinIO put_bytes / read_range 可用")

    project_id, user_id = _seed_local_identity(factory)
    print(f"    项目 = {project_id}")
    print(f"    用户 = {user_id}")

    from app.main import create_app  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402

    def override_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_object_storage] = lambda: storage

    report: dict[str, object] = {"project_id": str(project_id), "user_id": str(user_id)}
    try:
        with TestClient(app) as client:
            client.headers.update({"X-User-Id": str(user_id)})

            step(1, "创建采集会话")
            capture = client.post(
                f"/api/v1/projects/{project_id}/capture-sessions",
                json={"code": "v31-minio-001"},
            )
            if capture.status_code != 201:
                return fail(f"创建采集会话失败：{capture.status_code} {capture.text}")
            capture_session_id = capture.json()["session_id"]
            ok(f"采集会话 = {capture_session_id}")

            step(2, "创建上传会话（带采集会话归属）")
            created = client.post(
                f"/api/v1/projects/{project_id}/upload-sessions",
                json={
                    "original_filename": "v31-minio.bin",
                    "declared_content_type": "application/octet-stream",
                    "declared_size_bytes": len(CONTENT),
                    "format_hint": "MCAP",
                    "expected_sha256": f"sha256:{_EXPECTED_SHA256}",
                    "capture_session_id": capture_session_id,
                },
            )
            if created.status_code != 201:
                return fail(f"创建上传会话失败：{created.status_code} {created.text}")
            upload = created.json()
            upload["project_id"] = str(project_id)
            ok(
                f"上传会话 = {upload['upload_session_id']}，object_key = {upload['object_key']}"
            )

            step(3, "真实 PUT 每个分片到 MinIO，并回写 ack")
            materials = _put_parts(client, upload)
            ok(f"{len(materials)} 个分片已真实写入 MinIO")

            for item in materials:
                ack = client.post(
                    f"/api/v1/projects/{project_id}/upload-sessions/"
                    f"{upload['upload_session_id']}/parts/{item['part_number']}/ack",
                    json={
                        "etag": item["etag"],
                        "size_bytes": item["size_bytes"],
                        "sha256": item["sha256"],
                    },
                )
                if ack.status_code != 200:
                    return fail(
                        f"分片 {item['part_number']} ack 失败：{ack.status_code} {ack.text}"
                    )
            ok("全部分片 ack 通过（服务端按真实 ListParts 校验 ETag 与校验和）")

            step(4, "完成 multipart 上传")
            current = client.get(
                f"/api/v1/projects/{project_id}/upload-sessions/{upload['upload_session_id']}"
            )
            if current.status_code != 200:
                return fail(f"重读上传会话失败：{current.text}")
            completed = client.post(
                f"/api/v1/projects/{project_id}/upload-sessions/"
                f"{upload['upload_session_id']}/complete",
                json={
                    "expected_sha256": f"sha256:{_EXPECTED_SHA256}",
                    "expected_size_bytes": len(CONTENT),
                    "parts": [
                        {
                            "part_number": item["part_number"],
                            "etag": item["etag"],
                            "size_bytes": item["size_bytes"],
                            "sha256": item["sha256"],
                        }
                        for item in materials
                    ],
                },
                headers={"If-Match": f'"{current.json()["version"]}"'},
            )
            if completed.status_code != 202:
                return fail(f"完成上传失败：{completed.status_code} {completed.text}")
            body = completed.json()
            asset = body["raw_asset"]
            if asset is None:
                return fail("完成上传后未产生 RawAsset")
            ok(f"RawAsset = {asset['asset_id']}，deduplicated = {body['deduplicated']}")

            step(5, "校验采集会话归属已落库（V31-001）")
            fetched = client.get(
                f"/api/v1/projects/{project_id}/raw-assets/{asset['asset_id']}"
            )
            if fetched.status_code != 200:
                return fail(f"回读 RawAsset 失败：{fetched.text}")
            if fetched.json()["capture_session_id"] != capture_session_id:
                return fail(
                    "RawAsset.capture_session_id 与采集会话不一致："
                    f"{fetched.json()['capture_session_id']} != {capture_session_id}"
                )
            artifacts = client.get(
                f"/api/v1/projects/{project_id}/capture-sessions/{capture_session_id}/artifacts"
            )
            if artifacts.status_code != 200:
                return fail(f"读取会话产物失败：{artifacts.text}")
            items = artifacts.json()["items"]
            if not any(item["artifact_id"] == asset["asset_id"] for item in items):
                return fail(f"会话产物列表缺少本资产：{items}")
            ok("采集会话产物列表可读回该 RawAsset")

            step(6, "真实回读 MinIO 对象，比对字节与 sha256")
            stored = storage.read_range(
                upload["object_key"], start=0, length=len(CONTENT)
            )
            if stored != CONTENT:
                return fail("MinIO 中的对象内容与本地上传内容不一致")
            stored_sha = hashlib.sha256(stored).hexdigest()
            if stored_sha != _EXPECTED_SHA256:
                return fail(f"MinIO 对象 sha256 不一致：{stored_sha}")
            if fetched.json()["sha256"].removeprefix("sha256:") != _EXPECTED_SHA256:
                return fail(f"库里 sha256 与真实字节不一致：{fetched.json()['sha256']}")
            ok(f"对象字节与 sha256 一致（{len(stored):,} bytes）")

            step(7, "跑真实 outbox 校验 Worker，把资产推进到 VALID")
            from app.workers.outbox import OutboxWorker  # noqa: E402

            worker = OutboxWorker(factory, settings, storage)
            for _ in range(5):
                result = worker.process_once()
                if asset["asset_id"] and getattr(result, "event_id", None) is None:
                    break
            validated = client.get(
                f"/api/v1/projects/{project_id}/raw-assets/{asset['asset_id']}"
            )
            if validated.status_code != 200:
                return fail(f"校验后回读 RawAsset 失败：{validated.text}")
            if validated.json()["status"] != "VALID":
                return fail(
                    "资产未通过校验（状态 "
                    f"{validated.json()['status']}"
                    f"，隔离码 {validated.json().get('quarantine_codes')}）"
                )
            ok("outbox 校验 Worker 真实运行，资产状态 = VALID")

            step(8, "下载签名 URL 真实 GET")
            signed = client.get(
                f"/api/v1/raw-assets/{asset['asset_id']}/download-url"
                f"?project_id={project_id}"
            )
            if signed.status_code != 200:
                return fail(f"签名下载地址失败：{signed.status_code} {signed.text}")
            download = httpx.get(signed.json()["url"], timeout=60.0, trust_env=False)
            if download.status_code != 200:
                return fail(f"签名 URL GET 失败：{download.status_code}")
            if hashlib.sha256(download.content).hexdigest() != _EXPECTED_SHA256:
                return fail("签名 URL 下载内容 sha256 不一致")
            ok(f"签名 URL 下载 {len(download.content):,} bytes，sha256 一致")

            step(9, "分段 Range 读取（播放器拖拽依赖）")
            ranged = httpx.get(
                signed.json()["url"],
                headers={"Range": "bytes=1024-2047"},
                timeout=60.0,
                trust_env=False,
            )
            if ranged.status_code != 206:
                return fail(f"Range 请求未返回 206：{ranged.status_code}")
            if ranged.content != CONTENT[1024:2048]:
                return fail("Range 返回内容与源字节不一致")
            ok("Range 206 Partial Content 正确")

            report.update(
                {
                    "capture_session_id": capture_session_id,
                    "upload_session_id": upload["upload_session_id"],
                    "asset_id": asset["asset_id"],
                    "object_key": upload["object_key"],
                    "sha256": _EXPECTED_SHA256,
                    "size_bytes": len(CONTENT),
                }
            )
    finally:
        engine.dispose()

    print("\n" + "=" * 72)
    print("验收结果 = 真实 MinIO 全链路通过")
    for key, value in report.items():
        print(f"    {key:<20} = {value}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
