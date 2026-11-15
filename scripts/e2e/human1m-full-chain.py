"""NVIDIA Human-1M：10 条完整 episode 的全链路端到端验收。

与 ``human1m-chain.py`` 的关系
-----------------------------
``human1m-chain.py`` 是"链路能不能跑通"的冒烟脚本（固定取全量 15 条 preview），
用于日常回归。本脚本是**带验收口径的复验**：

1. 明确锁定 **10 条完整 episode**（``scope=SAMPLE, sample_limit=10``），
   抽样确定性取 metadata 前 10 条，不做随机抽样；
2. 逐条校验"完整"——每条 episode 的**全部机位**都必须落库，不允许半条；
3. 用官方 ``SHA256SUMS.txt`` 做**独立交叉校验**（不只信自己算的 hash），
   并用官方 ``metadata.jsonl`` 的 ``view`` 字段独立推导期望机位，
   与脚本内写死的人工期望值互为对照，防止"期望值写错导致假通过"；
4. 真实 ``curl -r 0-1023`` 验证 HTTP 206（httpx 对该 MinIO 构建的 keep-alive 不稳）；
5. 回读 MinIO 对象重新计算 sha256，证明"库里的 hash"与"存储里的字节"一致，
   而不是只信第一遍下载时的内存结果；
6. 输出机器可核对的统计，便于写入证据 README。

为什么不改 ``human1m-chain.py``：那个脚本是冒烟基线，改动它会改变既有回归口径；
验收需要更强的断言与不同的输出形状，另起一个脚本比塞条件分支更清晰。

真实执行，不 mock：真实 Hugging Face 下载 → 真实 PostgreSQL → 真实 MinIO。

用法::

    backend/.venv/bin/python scripts/e2e/human1m-full-chain.py

前置::

    docker compose up -d postgres minio
    backend/.venv/bin/alembic upgrade head
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "data-platform"))


def _apply_default_env() -> None:
    """设置本脚本需要的默认环境变量。

    **必须在 main() 里调用，不能在模块顶层做。** 顶层 ``os.environ.setdefault``
    会污染任何导入本模块的进程——``tests/test_human1m_full_chain.py`` 在收集阶段
    ``exec_module`` 本文件，就把 ``DDP_RATE_LIMIT_ENABLED=false`` 写进了测试进程，
    导致其后所有限流用例静默失效（表现为「单独跑绿、全量跑红」）。
    """
    os.environ.setdefault("DDP_ENV", "local")
    os.environ.setdefault(
        "DDP_DATABASE_URL", "postgresql+psycopg://ddp:ddp@localhost:55432/ddp"
    )
    os.environ.setdefault("DDP_S3_ENDPOINT", "http://127.0.0.1:9000")
    os.environ.setdefault("DDP_S3_ACCESS_KEY", "minioadmin")
    os.environ.setdefault("DDP_S3_SECRET_KEY", "minioadmin")
    os.environ.setdefault("DDP_S3_BUCKET", "ddp-raw")
    # 验收脚本用 TestClient 直连应用；限流中间件对同一 IP 的高频写请求会误伤，
    # 与本脚本要验证的链路无关，显式关闭而不是靠"恰好没触发"。
    os.environ.setdefault("DDP_RATE_LIMIT_ENABLED", "false")


from uuid import uuid4  # noqa: E402

import httpx  # noqa: E402
from app.application.datasources import (  # noqa: E402
    DataSourceUseCases,
    NewDataSource,
    NewDataSourceImport,
)
from app.application.datasources.import_runner import ImportRunner  # noqa: E402
from app.application.datasources.reviews import DataReviewUseCases  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.domain.datasources.review import ReviewDecision  # noqa: E402
from app.domain.identity.models import ProjectScope  # noqa: E402
from app.infrastructure.db.models.identity import (  # noqa: E402
    Project,
    ProjectMember,
    User,
)
from app.infrastructure.db.session import (  # noqa: E402
    create_db_engine,
    create_session_factory,
)
from app.infrastructure.repositories.collection_audit import (  # noqa: E402
    SqlOutboxRepository,
)
from app.infrastructure.repositories.dataset_audit import (  # noqa: E402
    SqlDatasetAuditRepository,
)
from app.infrastructure.repositories.datasources import (  # noqa: E402
    SqlDataReviewRepository,
    SqlDataSourceImportRepository,
    SqlDataSourceRepository,
    SqlMediaAssetRepository,
)
from app.infrastructure.repositories.idempotency import (  # noqa: E402
    SqlIdempotencyRepository,
)
from app.infrastructure.storage.data_source_objects import (  # noqa: E402
    DataSourceObjectReader,
    DataSourceObjectWriter,
)
from app.infrastructure.storage.s3 import S3MultipartStorage  # noqa: E402
from app.workers.datasources import _HttpxFetcher, _parse_metadata  # noqa: E402
from ddp_data.lerobot import parse_preview_metadata  # noqa: E402

REPO_ID = "nvidia/PhysicalAI-Human-1M-100K"
REVISION = "7e312e654818d1e0d3ebec3b78e50ee833b429f8"
# 验收口径：10 条完整 episode（确定性取 metadata 前 10 条）。
ACCEPTANCE_EPISODES = 10
# 官方校验和清单；用同一 revision 取，避免"校验的是别处版本"。
CHECKSUM_MANIFEST = "SHA256SUMS.txt"
METADATA_PATH = "preview/metadata.jsonl"

# 人工期望值（第二重判据）。与官方 metadata 推导出的机位集合互为对照：
# 任一侧改动都会让脚本失败，从而避免"期望值写错 → 假通过"。
EXPECTED_CAMERAS: dict[str, set[str]] = {
    "demo_001": {"main", "right", "stereo"},
    "demo_002": {"main", "right", "stereo"},
    "demo_003": {"main", "right", "stereo"},
    "demo_004": {"main", "right", "stereo"},
    "demo_005": {"main", "right", "stereo", "wrist_left", "wrist_right"},
    "demo_007": {"main", "right", "stereo", "wrist_left", "wrist_right"},
    "demo_008": {"main", "right", "stereo", "wrist_left", "wrist_right"},
    "demo_009": {"main"},
    "demo_011": {"main"},
    "demo_012": {"main"},
}
EXPECTED_ASSETS = sum(len(cameras) for cameras in EXPECTED_CAMERAS.values())


def step(n: int, text: str) -> None:
    print(f"\n[{n}] {text}")


def fail(message: str) -> int:
    print(f"    [FAIL] {message}")
    return 1


def _official_url(path: str) -> str:
    """按已登记 revision 构造官方下载 URL（绝不使用 main）。"""
    return f"https://huggingface.co/datasets/{REPO_ID}/resolve/{REVISION}/{path}"


def fetch_official(path: str, *, timeout: float = 120.0) -> bytes:
    """直连官方仓库取一个文件；失败即中止，不用缓存或本地副本顶替。"""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(_official_url(path))
        response.raise_for_status()
        return response.content


def parse_checksum_manifest(text: str) -> dict[str, str]:
    """解析 ``SHA256SUMS.txt``：``<sha256>  <相对路径>``，返回 路径 → hash。"""
    entries: dict[str, str] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"SHA256SUMS 第 {lineno} 行格式非法：{line[:120]}")
        entries[parts[1]] = parts[0]
    if not entries:
        raise ValueError("SHA256SUMS 为空")
    return entries


def main() -> int:
    _apply_default_env()
    settings = get_settings()
    engine = create_db_engine(
        settings.database_url,
        pool_size=4,
        max_overflow=0,
        pool_timeout_seconds=10,
        connect_timeout_seconds=10,
    )
    factory = create_session_factory(engine)
    submitter_id, reviewer_id = uuid4(), uuid4()
    project_id = uuid4()

    if not REVISION.isalnum() or len(REVISION) != 40:
        return fail(f"revision 不是 40 位 sha：{REVISION}")

    step(1, "创建项目与两个用户（提交人 / 审核人）")
    with factory() as s:
        s.add(
            Project(
                id=project_id,
                code=f"e2e10-{project_id.hex[:6]}",
                name="Human-1M 10 条验收",
            )
        )
        s.add(User(id=submitter_id, display_name="导入提交人"))
        s.add(User(id=reviewer_id, display_name="独立审核人"))
        s.flush()
        # 提交人是 editor，审核人是 owner：权限也不同，避免"同一身份双开"。
        s.add(ProjectMember(project_id=project_id, user_id=submitter_id, role="editor"))
        s.add(ProjectMember(project_id=project_id, user_id=reviewer_id, role="owner"))
        s.commit()
    submitter = ProjectScope(project_id, actor_id=submitter_id)
    reviewer = ProjectScope(project_id, actor_id=reviewer_id)
    print(f"    project={project_id}")
    print(f"    submitter={submitter_id}（editor） reviewer={reviewer_id}（owner）")

    step(2, f"登记数据源：{REPO_ID} @ {REVISION}")
    with factory() as s:
        sources = DataSourceUseCases(
            sources=SqlDataSourceRepository(s),
            imports=SqlDataSourceImportRepository(s),
            audit=SqlDatasetAuditRepository(s),
            outbox=SqlOutboxRepository(s),
            idempotency=SqlIdempotencyRepository(s),
            uow=s,
        )
        source = sources.create_source(
            submitter,
            NewDataSource(
                code=f"hf-human1m10-{project_id.hex[:6]}",
                name="NVIDIA PhysicalAI Human-1M（10 条完整 episode 验收）",
                repo_id=REPO_ID,
                revision=REVISION,
                license="CC-BY-4.0",
                access_condition="PUBLIC",
                is_gated=False,
                dataset_card_url=f"https://huggingface.co/datasets/{REPO_ID}",
                notes="公开 preview：15 demo / 39 视频；本次验收口径 = 前 10 条 episode",
            ),
            path=f"/api/v1/projects/{project_id}/data-sources",
            request_id="e2e10-source",
        )
        job = sources.create_import(
            submitter,
            source.data_source_id,
            NewDataSourceImport(
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=ACCEPTANCE_EPISODES,
            ),
            path=(
                f"/api/v1/projects/{project_id}"
                f"/data-sources/{source.data_source_id}/imports"
            ),
            request_id="e2e10-import",
        )
        source_id, import_id = source.data_source_id, job.import_id
    print(f"    source={source_id}")
    print(
        f"    import={import_id} 状态={job.status} scope={job.scope} limit={job.sample_limit}"
    )

    step(3, "取官方基准：metadata 前 10 条 + SHA256SUMS 清单（独立于被测链路）")
    metadata_bytes = fetch_official(METADATA_PATH)
    official_episodes = parse_preview_metadata(_parse_metadata(metadata_bytes))
    official_first10 = official_episodes[:ACCEPTANCE_EPISODES]
    derived_cameras = {
        episode.episode_key: {asset.camera for asset in episode.media_assets}
        for episode in official_first10
    }
    manifest_text = fetch_official(CHECKSUM_MANIFEST).decode("utf-8")
    manifest = parse_checksum_manifest(manifest_text)
    print(
        f"    metadata={len(metadata_bytes):,} 字节，共 {len(official_episodes)} 条 episode；"
        f"SHA256SUMS {len(manifest)} 条"
    )
    print(f"    官方顺序前 {ACCEPTANCE_EPISODES} 条：{list(derived_cameras)}")
    if list(derived_cameras) != list(EXPECTED_CAMERAS):
        return fail(
            f"metadata 前 10 条与人工期望不一致：官方={list(derived_cameras)} "
            f"期望={list(EXPECTED_CAMERAS)}"
        )
    for key, cameras in derived_cameras.items():
        if cameras != EXPECTED_CAMERAS[key]:
            return fail(
                f"{key} 官方机位与人工期望不一致：官方={sorted(cameras)} "
                f"期望={sorted(EXPECTED_CAMERAS[key])}"
            )
    expected_total = sum(len(cameras) for cameras in derived_cameras.values())
    print(
        f"    → 双判据一致：{len(derived_cameras)} 条 episode / {expected_total} 个资产"
    )
    if expected_total != EXPECTED_ASSETS:
        return fail(f"资产数期望不一致：{expected_total} != {EXPECTED_ASSETS}")

    step(4, "真实下载 → 内容签名校验 → sha256 → 存 MinIO → 建媒体资产")
    with httpx.Client(timeout=180.0) as client, factory() as s:
        runner = ImportRunner(
            sources=SqlDataSourceRepository(s),
            imports=SqlDataSourceImportRepository(s),
            assets=SqlMediaAssetRepository(s),
            audit=SqlDatasetAuditRepository(s),
            outbox=SqlOutboxRepository(s),
            storage=DataSourceObjectWriter(S3MultipartStorage(settings)),
            uow=s,
        )
        outcome = runner.execute(
            submitter,
            import_id,
            fetch=_HttpxFetcher(client),
            parse_metadata=_parse_metadata,
            parse_episodes=parse_preview_metadata,
        )
    print(
        f"    episodes={outcome.episode_count} assets={outcome.asset_count} "
        f"bytes={outcome.total_bytes:,}"
    )
    if outcome.episode_count != ACCEPTANCE_EPISODES:
        return fail(
            f"期望 {ACCEPTANCE_EPISODES} 条 episode，实际 {outcome.episode_count}"
        )
    if outcome.asset_count != EXPECTED_ASSETS:
        return fail(f"期望 {EXPECTED_ASSETS} 个资产，实际 {outcome.asset_count}")

    step(5, "完整性校验：每条 episode 的全部机位都必须落库（不允许半条）")
    with factory() as s:
        assets = SqlMediaAssetRepository(s).list_by_import(
            submitter, import_id, limit=1000
        )
    by_episode: dict[str, list[str]] = {}
    for asset in assets:
        by_episode.setdefault(asset.episode_key, []).append(asset.camera)
    if set(by_episode) != set(EXPECTED_CAMERAS):
        return fail(f"episode 集合不符：{sorted(by_episode)}")
    for key in sorted(by_episode):
        got, want = set(by_episode[key]), EXPECTED_CAMERAS[key]
        if got != want:
            return fail(f"{key} 机位不完整：得到 {sorted(got)}，期望 {sorted(want)}")
        print(f"    {key}: {len(got)} 机位 {sorted(got)}")
    if len(assets) != EXPECTED_ASSETS:
        return fail(f"资产数不符：库里 {len(assets)}，期望 {EXPECTED_ASSETS}")
    print(f"    → {len(by_episode)} 条 episode 全部机位齐备，共 {len(assets)} 个资产")

    step(6, "独立交叉校验：官方 SHA256SUMS + MinIO 对象回读")
    mismatched_manifest: list[str] = []
    missing_manifest: list[str] = []
    for asset in assets:
        # 官方清单位于 preview/ 下，资产 path 是 videos/xxx.mp4。
        manifest_key = f"preview/{asset.path}"
        official = manifest.get(manifest_key)
        if official is None:
            missing_manifest.append(manifest_key)
            continue
        if f"sha256:{official}" != asset.sha256:
            mismatched_manifest.append(manifest_key)
    if missing_manifest:
        return fail(
            f"SHA256SUMS 未覆盖 {len(missing_manifest)} 个资产：{missing_manifest[:3]}"
        )
    if mismatched_manifest:
        return fail(f"与官方 SHA256SUMS 不一致：{mismatched_manifest[:3]}")
    print(f"    [PASS] {len(assets)} 个资产的 sha256 与官方 SHA256SUMS 全部一致")

    storage = S3MultipartStorage(settings)
    reread_bytes = 0
    storage_problems: list[str] = []
    for asset in assets:
        size = storage.object_size(asset.object_key)
        if size != asset.size_bytes:
            storage_problems.append(
                f"{asset.path} 大小 {size} != 库内 {asset.size_bytes}"
            )
            continue
        digest = hashlib.sha256()
        for chunk in storage.stream_object(asset.object_key, chunk_size=1024 * 1024):
            digest.update(chunk)
            reread_bytes += len(chunk)
        if f"sha256:{digest.hexdigest()}" != asset.sha256:
            storage_problems.append(f"{asset.path} 回读 hash 与库内不一致")
        if asset.content_type != "video/mp4":
            storage_problems.append(f"{asset.path} content_type={asset.content_type}")
    if storage_problems:
        return fail(f"MinIO 对象校验失败：{storage_problems[:3]}")
    print(
        f"    [PASS] {len(assets)} 个对象回读 {reread_bytes:,} 字节，"
        "大小 / sha256 / content_type 与库内记录一致"
    )

    step(7, "质量报告（规则版本 / 样本范围 / 逐条 PASS-FAIL）")
    with factory() as s:
        reviews = DataReviewUseCases(
            reviews=SqlDataReviewRepository(s),
            imports=SqlDataSourceImportRepository(s),
            assets=SqlMediaAssetRepository(s),
            audit=SqlDatasetAuditRepository(s),
            outbox=SqlOutboxRepository(s),
            uow=s,
        )
        report = reviews.quality_report(submitter, import_id)
    print(
        f"    规则={report.rule_version} 范围={report.sample_scope} "
        f"episodes={report.total_episodes} assets={report.total_assets} 通过={report.passed}"
    )
    for finding in report.findings:
        mark = "PASS" if finding.passed else "FAIL"
        print(f"      [{mark}] {finding.rule_id}: {finding.detail}")
    if report.sample_scope != "SAMPLE":
        return fail(f"质量报告样本范围应为 SAMPLE，实际 {report.sample_scope}")
    if report.total_episodes != ACCEPTANCE_EPISODES:
        return fail(
            f"质量报告 episode 数 {report.total_episodes} != {ACCEPTANCE_EPISODES}"
        )
    if report.total_assets != EXPECTED_ASSETS:
        return fail(f"质量报告资产数 {report.total_assets} != {EXPECTED_ASSETS}")
    if not report.passed:
        print("    质量报告未通过，停止后续审核")
        return 1

    step(8, "人工审核：提交人不得自审（先证明被拒绝，再由独立审核人裁决）")
    with factory() as s:
        reviews = DataReviewUseCases(
            reviews=SqlDataReviewRepository(s),
            imports=SqlDataSourceImportRepository(s),
            assets=SqlMediaAssetRepository(s),
            audit=SqlDatasetAuditRepository(s),
            outbox=SqlOutboxRepository(s),
            uow=s,
        )
        review = reviews.ensure_review(submitter, import_id, request_id="e2e10-review")
        print(f"    审核记录已建 提交人={review.submitted_by} 状态={review.status}")
        self_review_blocked = False
        try:
            reviews.decide(
                submitter,
                import_id,
                ReviewDecision.ACCEPT,
                reason="自审尝试（应被拒绝）",
                expected_version=review.row_version,
                request_id="e2e10-self-review",
            )
        except Exception as exc:  # noqa: BLE001 - 只为验证职责分离生效
            self_review_blocked = True
            print(f"    [OK] 自审被拒绝：{type(exc).__name__}: {exc}")
        if not self_review_blocked:
            return fail("提交人自审未被拒绝，职责分离失效")

        decided = reviews.decide(
            reviewer,
            import_id,
            ReviewDecision.ACCEPT,
            reason=(
                f"前 {ACCEPTANCE_EPISODES} 条 episode 全机位齐备、"
                "内容签名与 sha256 校验通过、质量规则全部 PASS"
            ),
            expected_version=review.row_version,
            request_id="e2e10-decide",
        )
    if decided.reviewer_id != reviewer_id:
        return fail("审核人不是独立审核人，职责分离失效")
    print(
        f"    审核状态={decided.status} 裁决={decided.decision} 审核人={decided.reviewer_id}"
    )

    step(9, "预签名播放 URL + 真实 curl -r 0-1023 验证 HTTP 206")
    with factory() as s:
        main_asset = next(a for a in assets if a.camera == "main")
        url = DataSourceObjectReader(S3MultipartStorage(settings)).presign_get(
            main_asset.object_key, expires_seconds=900
        )
    print(f"    主视角资产={main_asset.path} size={main_asset.size_bytes:,}")
    print(f"    预签名含签名参数={'X-Amz-Signature=' in url}")
    curl = subprocess.run(  # noqa: S603 - 固定参数，不拼接外部输入
        [
            "curl",
            "-sS",
            "-o",
            "/dev/null",
            "-D",
            "-",
            "-r",
            "0-1023",
            "--max-time",
            "60",
            url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    headers = [
        line
        for line in curl.stdout.splitlines()
        if line.lower().startswith(("http/", "content-range", "content-length"))
    ]
    for line in headers:
        print(f"      {line}")
    status_ok = any(
        " 206 " in line for line in headers if line.upper().startswith("HTTP/")
    )
    if not status_ok:
        return fail(f"curl Range 未返回 206（stderr={curl.stderr[:200]}）")
    expected_range = f"bytes 0-1023/{main_asset.size_bytes}"
    range_ok = any(
        line.lower().startswith("content-range")
        and line.split(":", 1)[1].strip() == expected_range
        for line in headers
    )
    if not range_ok:
        return fail(f"Content-Range 不符：期望 {expected_range}")
    print(f"    [OK] 服务端按 Range 返回 206 Partial Content（{expected_range}）")

    step(10, "走真实 HTTP 路由复核（前端页面用的端点）")
    from app.infrastructure.db.session import get_session  # noqa: E402
    from app.main import create_app  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402

    os.environ["DDP_LOCAL_HEADER_AUTH"] = "true"
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_session] = _override_session(factory)
    with TestClient(app) as client:
        base = f"/api/v1/projects/{project_id}"
        headers_in = {"X-User-Id": str(submitter_id)}
        report_resp = client.get(
            f"{base}/data-source-imports/{import_id}/quality-report", headers=headers_in
        )
        review_resp = client.get(
            f"{base}/data-source-imports/{import_id}/review", headers=headers_in
        )
        assets_resp = client.get(
            f"{base}/data-source-imports/{import_id}/assets?limit=1000",
            headers=headers_in,
        )
        playback_resp = client.post(
            f"{base}/media-assets/{main_asset.asset_id}/playback", headers=headers_in
        )
    report_json = report_resp.json()
    review_json = review_resp.json()
    assets_json = assets_resp.json()
    playback_json = playback_resp.json()
    print(
        f"    质量报告 HTTP={report_resp.status_code} rule_version={report_json.get('rule_version')} "
        f"passed={report_json.get('passed')} episodes={report_json.get('total_episodes')} "
        f"assets={report_json.get('total_assets')}"
    )
    print(
        f"    审核记录 HTTP={review_resp.status_code} status={review_json.get('status')} "
        f"decision={review_json.get('decision')} version={review_json.get('version')}"
    )
    print(
        f"    资产列表 HTTP={assets_resp.status_code} count={len(assets_json.get('items', []))}"
    )
    print(
        f"    播放 URL HTTP={playback_resp.status_code} "
        f"ttl={playback_json.get('expires_in_seconds')}s "
        f"size={playback_json.get('size_bytes'):,}"
    )
    if report_resp.status_code != 200 or review_resp.status_code != 200:
        return fail("HTTP 复核失败（质量报告或审核记录）")
    if assets_resp.status_code != 200 or playback_resp.status_code != 200:
        return fail("HTTP 复核失败（资产列表或播放 URL）")
    if report_json.get("rule_version") != report.rule_version:
        return fail("规则版本与用例层不一致")
    if report_json.get("sample_scope") != "SAMPLE":
        return fail("HTTP 返回的样本范围不是 SAMPLE")
    if review_json.get("status") != str(decided.status):
        return fail("审核状态与用例层不一致")
    if review_json.get("reviewer_id") != str(reviewer_id):
        return fail("HTTP 返回的审核人与独立审核人不一致")
    if len(assets_json.get("items", [])) != len(assets):
        return fail("HTTP 资产数量与用例层不一致")
    if playback_json.get("size_bytes") != main_asset.size_bytes:
        return fail("HTTP 播放 URL 的 size 与库内资产不一致")

    step(11, "汇总")
    with factory() as s:
        final = SqlDataSourceImportRepository(s).get(submitter, import_id)
    if final.imported_row_count != ACCEPTANCE_EPISODES:
        return fail(
            f"库内 row_count={final.imported_row_count} 不等于 {ACCEPTANCE_EPISODES}"
        )
    if final.imported_file_count != EXPECTED_ASSETS:
        return fail(
            f"库内 file_count={final.imported_file_count} 不等于 {EXPECTED_ASSETS}"
        )
    total_bytes = sum(asset.size_bytes for asset in assets)
    if total_bytes != outcome.total_bytes:
        return fail(f"字节合计不符：库内 {total_bytes} vs 导入 {outcome.total_bytes}")
    print(f"    导入状态     = {final.status}")
    print(
        f"    episode / 资产 = {final.imported_row_count} / {final.imported_file_count}"
    )
    print(f"    总字节       = {total_bytes:,}")
    print(
        f"    质量规则     = {report.rule_version}（{len(report.findings)} 条全部 PASS）"
    )
    print(f"    审核结论     = {decided.status} / {decided.decision}")
    print("    独立校验     = 官方 SHA256SUMS PASS / MinIO 回读 PASS")
    print("    206 验证     = PASS")
    print(
        f"    证据锚点     = project={project_id} import={import_id} source={source_id}"
    )
    print("\n验收口径 = 10 条完整 episode：全链路通过。")
    return 0


def _override_session(factory):
    """给 TestClient 注入与脚本相同的 session factory。"""

    def _dependency():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return _dependency


if __name__ == "__main__":
    raise SystemExit(main())
