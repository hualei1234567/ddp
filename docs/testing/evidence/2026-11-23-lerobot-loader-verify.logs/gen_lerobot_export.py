# -*- coding: utf-8 -*-
"""V4.1 FR-034/035 步骤 2：用既有测试工厂生成 LE_ROBOT 导出产物到 /tmp/lerobot-dataset/。

参考 backend/tests/unit/test_export_domain.py 的 make_job/make_version/make_item 写法，
把每个 ExportArtifactSpec.data 按其 path 落盘，并写出 aggregate 与 manifest 备查。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 让 tests 可导入

from app.domain.datasets.enums import Split  # noqa: E402
from app.domain.datasets.exporting import (  # noqa: E402
    aggregate_sha256,
    build_artifact_manifest,
    build_export_artifacts,
)
from tests.unit.test_export_domain import make_item, make_job, make_version  # noqa: E402

ROOT = Path("/tmp/lerobot-dataset")


def main() -> int:
    now = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
    job = make_job()  # LE_ROBOT / ddp.lerobot/1.0.0
    version = make_version()
    # 3 个 Episode：与既有单测同构（train/validation/test 各 1）
    items = (
        make_item(0, Split.TRAIN),
        make_item(1, Split.VALIDATION),
        make_item(2, Split.TEST),
    )
    artifacts = build_export_artifacts(job, version, items, created_at=now)

    if ROOT.exists():
        import shutil

        shutil.rmtree(ROOT)
    for artifact in artifacts:
        target = ROOT / artifact.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(artifact.data)
        print(f"written: {artifact.path} ({len(artifact.data)} bytes)")

    aggregate = aggregate_sha256(artifacts)
    manifest = build_artifact_manifest(job, artifacts, aggregate=aggregate)
    (ROOT.parent / "lerobot-aggregate.txt").write_text(aggregate + "\n", encoding="utf-8")
    (ROOT.parent / "lerobot-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"aggregate_sha256: {aggregate}")
    print(f"artifact_count: {manifest['artifact_count']}")
    info = json.loads(next(a for a in artifacts if a.path == "meta/info.json").data)
    print("info keys:", sorted(info.keys()))
    print("total_episodes:", info.get("total_episodes"), "total_frames:", info.get("total_frames"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
