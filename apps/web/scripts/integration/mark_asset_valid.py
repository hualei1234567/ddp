#!/usr/bin/env python3
"""DDP-307 集成测试夹具：模拟 VALIDATE Worker 完成首资产校验。

仅用于覆盖后端 dedup 分支：真实上传完成后的 RawAsset 为 VALIDATING，
而 dedup 只匹配 VALID/QUARANTINED/ARCHIVED。本脚本按 Worker 的权威迁移
把指定资产置为 VALID，只修改本任务临时 SQLite，不修改 backend 代码，
也不伪造上传字节或 Hash。
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.infrastructure.db.models.ingestion import RawAssetModel


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法：mark_asset_valid.py <asset_id>")
    database_url = os.environ.get("DDP_DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("缺少 DDP_DATABASE_URL")
    if "staging" in database_url.lower() or "xxx.xxx.xxx.xxx" in database_url:
        raise SystemExit("拒绝使用 staging 数据库")

    try:
        asset_id = UUID(sys.argv[1])
    except ValueError as exc:
        raise SystemExit(f"asset_id 不是合法 UUID：{exc}") from exc

    engine = create_engine(database_url)
    with Session(engine) as session:
        asset = session.get(RawAssetModel, asset_id)
        if asset is None:
            raise SystemExit("资产不存在")
        if asset.status != "VALIDATING":
            print(f"资产状态为 {asset.status}，无需迁移")
            return
        now = datetime.now(UTC)
        asset.status = "VALID"
        asset.verified_at = now
        asset.updated_at = now
        asset.version += 1
        session.commit()
        print(f"资产 {asset_id} VALIDATING -> VALID（version={asset.version}）")


if __name__ == "__main__":
    main()
