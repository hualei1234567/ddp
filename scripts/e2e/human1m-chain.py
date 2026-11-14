"""NVIDIA Human-1M 全链路端到端验证。

真实执行（不 mock）：真实 Hugging Face 下载 → 真实 PostgreSQL → 真实 MinIO。
链路：登记数据源 → 导入任务 → 下载校验 → 解析 → 媒体资产 → 质量报告 → 人工审核。

用法：
    backend/.venv/bin/python scripts/e2e/human1m-chain.py

前置：
    docker compose up -d postgres minio
    backend/.venv/bin/alembic upgrade head
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "data-platform"))
os.environ.setdefault("DDP_ENV", "local")
os.environ.setdefault("DDP_DATABASE_URL", "postgresql+psycopg://ddp:ddp@localhost:55432/ddp")
os.environ.setdefault("DDP_S3_ENDPOINT", "http://127.0.0.1:9000")
os.environ.setdefault("DDP_S3_ACCESS_KEY", "minioadmin")
os.environ.setdefault("DDP_S3_SECRET_KEY", "minioadmin")
os.environ.setdefault("DDP_S3_BUCKET", "ddp-raw")

from datetime import UTC, datetime  # noqa: E402
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


def step(n: int, text: str) -> None:
    print(f"\n[{n}] {text}")


def main() -> int:
    settings = get_settings()
    engine = create_db_engine(
        settings.database_url,
        pool_size=4,
        max_overflow=0,
        pool_timeout_seconds=10,
        connect_timeout_seconds=10,
    )
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    submitter_id, reviewer_id = uuid4(), uuid4()
    project_id = uuid4()

    step(1, "创建项目与两个用户（提交人 / 审核人）")
    with factory() as s:
        s.add(Project(id=project_id, code=f"e2e-{project_id.hex[:6]}", name="Human-1M 全链路"))
        s.add(User(id=submitter_id, display_name="导入提交人"))
        s.add(User(id=reviewer_id, display_name="独立审核人"))
        s.flush()
        s.add(ProjectMember(project_id=project_id, user_id=submitter_id, role="editor"))
        s.add(ProjectMember(project_id=project_id, user_id=reviewer_id, role="owner"))
        s.commit()
    submitter = ProjectScope(project_id, actor_id=submitter_id)
    reviewer = ProjectScope(project_id, actor_id=reviewer_id)
    print(f"    project={project_id} submitter={submitter_id} reviewer={reviewer_id}")

    step(2, f"登记数据源 {REPO_ID} @ {REVISION[:12]}…")
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
                code=f"hf-human1m-{project_id.hex[:6]}",
                name="NVIDIA PhysicalAI Human-1M (preview)",
                repo_id=REPO_ID,
                revision=REVISION,
                license="CC-BY-4.0",
                access_condition="PUBLIC",
                is_gated=False,
                dataset_card_url=f"https://huggingface.co/datasets/{REPO_ID}",
                notes="公开预览：15 demo / 39 视频；全量需 NVIDIA 授权",
            ),
            path=f"/api/v1/projects/{project_id}/data-sources",
            request_id="e2e-source",
        )
        job = sources.create_import(
            submitter,
            source.data_source_id,
            NewDataSourceImport(
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=15,
            ),
            path=f"/api/v1/projects/{project_id}/data-sources/{source.data_source_id}/imports",
            request_id="e2e-import",
        )
        source_id, import_id = source.data_source_id, job.import_id
    print(f"    source={source_id} import={import_id} 状态={job.status}")

    step(3, "真实下载 → 内容校验 → 存 MinIO → 建媒体资产")
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

    step(4, "质量报告（规则版本 / 样本范围 / 逐条结果）")
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
    print(f"    规则={report.rule_version} 范围={report.sample_scope} 通过={report.passed}")
    for finding in report.findings:
        print(f"      [{'PASS' if finding.passed else 'FAIL'}] {finding.rule_id}: {finding.detail}")
    if not report.passed:
        print("    质量报告未通过，停止后续审核")
        return 1

    step(5, "人工审核（提交人不得自审，由独立审核人裁决）")
    with factory() as s:
        reviews = DataReviewUseCases(
            reviews=SqlDataReviewRepository(s),
            imports=SqlDataSourceImportRepository(s),
            assets=SqlMediaAssetRepository(s),
            audit=SqlDatasetAuditRepository(s),
            outbox=SqlOutboxRepository(s),
            uow=s,
        )
        review = reviews.ensure_review(submitter, import_id, request_id="e2e-review")
        decided = reviews.decide(
            reviewer,
            import_id,
            ReviewDecision.ACCEPT,
            reason="样本完整、机位齐全、校验和全部合法",
            expected_version=review.row_version,
            request_id="e2e-decide",
        )
    print(f"    审核状态={decided.status} 裁决={decided.decision} 审核人={decided.reviewer_id}")

    step(6, "播放能力验证（预签名 URL + Range 请求）")
    with factory() as s:
        assets = SqlMediaAssetRepository(s).list_by_import(submitter, import_id, limit=1000)
        main_asset = next(a for a in assets if a.camera == "main")
        url = DataSourceObjectReader(S3MultipartStorage(settings)).presign_get(
            main_asset.object_key, expires_seconds=900
        )
    print(f"    资产 = {len(assets)}，主视角 = {main_asset.path}")
    print(f"    预签名 URL 含签名参数 = {'X-Amz-Signature=' in url}")
    print(f"    → 可用 curl -r 0-1023 '{url[:48]}…' 验证 HTTP 206")

    step(7, "汇总")
    with factory() as s:
        final = SqlDataSourceImportRepository(s).get(submitter, import_id)
    print(f"    导入状态 = {final.status}")
    print(f"    片段 / 资产 = {final.imported_row_count} / {final.imported_file_count}")
    print(f"    质量通过 = {report.passed}（规则 {report.rule_version}）")
    print(f"    审核结论 = {decided.status}")
    print("\n全链路验证完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
