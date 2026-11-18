#!/usr/bin/env python3
"""数据资产生命周期端到端：上传 → 校验 → 隔离 → 放行 → 归档 → 墓碑。

## 为什么单独有这个脚本

原始资产的状态机一直有 ``post-hoc-quarantine`` / ``release-quarantine-or-revalidate`` /
``reject-quarantined`` / ``archive`` / ``tombstone`` 五条边，但此前**没有任何
HTTP 入口**：被隔离的资产永远放不出来，资产也无法归档或墓碑。界面只能展示
"隔离原因""墓碑时间"这些用户永远造不出来的状态。

本脚本用真实上传会话 + 真实校验 worker 走完整条链，每一步都用服务端返回的
`allowed_actions` 决定下一步能不能做——不绕过状态机、不直接改库。

## 用法

    backend/.venv/bin/python scripts/qa/raw_asset_lifecycle_smoke.py

前置：compose 栈在跑（api / worker / minio），`.env` 里有 `DDP_WEB_DEV_AUTH`。

退出码：0 全部通过；1 某一步不符合预期（打印端点、状态码、响应体）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from seed.client import (  # noqa: E402
    Client,
    Context,
    SeedFailure,
    expect,
    idempotency_key,
    if_match,
    upload_object,
)
from seed.run import _load_env, _project, _token  # noqa: E402


def _get(client: Client, path: str, label: str) -> dict[str, Any]:
    payload = expect(label, client.call("GET", path), 200)
    assert isinstance(payload, dict)
    return payload


def _act(
    client: Client,
    base: str,
    asset_id: str,
    action: str,
    version: int,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = expect(
        f"动作 {action}",
        client.call(
            "POST",
            f"{base}/raw-assets/{asset_id}/actions/{action}",
            body=body or {},
            headers={**if_match(version), "Idempotency-Key": idempotency_key()},
        ),
        200,
    )
    assert isinstance(payload, dict)
    return payload


def _wait_status(
    client: Client,
    base: str,
    asset_id: str,
    wanted: set[str],
    *,
    timeout: float = 180,
    label: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _get(client, f"{base}/raw-assets/{asset_id}", "读取资产")
        if str(last.get("status")) in wanted:
            return last
        time.sleep(2)
    raise SeedFailure(f"{label}：等待 {wanted} 超时，最后状态 {last.get('status')}")


def _download_status(
    client: Client, project_id: str, asset_id: str
) -> tuple[int, dict[str, Any]]:
    status, payload = client.call(
        "GET",
        f"/api/v1/raw-assets/{asset_id}/download-url?project_id={project_id}",
    )
    assert isinstance(payload, dict)
    return status, payload


def run(client: Client, project_id: str, stamp: str) -> None:
    base = f"/api/v1/projects/{project_id}"
    ctx = Context(
        base_url=client._base,
        token="",
        project_id=project_id,
        user_id="",
        stamp=stamp,
    )
    # 内容带时间戳，避免命中内容去重（去重会复用历史资产，干扰状态链）。
    content = json.dumps(
        {"seed": "ddp-raw-asset-lifecycle", "stamp": stamp}, ensure_ascii=False
    ).encode()
    uploaded = upload_object(
        client,
        ctx,
        filename=f"lifecycle-{stamp}.json",
        content=content,
        content_type="application/json",
        format_hint="JSON",
        label="上传生命周期验证资产",
    )
    asset_id = str(uploaded["raw_asset_id"])
    if not asset_id or asset_id == "None":
        raise SeedFailure("完成上传没有返回 raw_asset")
    print(f"  ✓ 资产已上传：{asset_id}")

    asset = _wait_status(
        client, base, asset_id, {"VALID", "QUARANTINED", "REJECTED"}, label="等待校验"
    )
    if asset["status"] != "VALID":
        raise SeedFailure(f"新上传资产未校验通过：{asset['status']}")
    print(f"  ✓ 服务端校验通过：allowed_actions={asset['allowed_actions']}")

    quarantined = _act(
        client,
        base,
        asset_id,
        "post-hoc-quarantine",
        int(asset["version"]),
        body={"reason": "全链路验证：模拟事后发现隐私画面", "quarantine_codes": ["metadata_conflict"]},
    )
    if quarantined["status"] != "QUARANTINED":
        raise SeedFailure(f"事后隔离未生效：{quarantined['status']}")
    status, payload = _download_status(client, project_id, asset_id)
    if status != 422:
        raise SeedFailure(f"隔离资产应禁止下载，实际 {status} {payload}")
    print("  ✓ 事后隔离生效，下载被拒（422）")

    released = _act(
        client, base, asset_id, "release-quarantine-or-revalidate", int(quarantined["version"])
    )
    if released["status"] != "VALIDATING" or released["quarantine_codes"]:
        raise SeedFailure(f"隔离放行未清空结论：{released}")
    print("  ✓ 隔离放行：回到 VALIDATING 且旧隔离结论已清空")
    asset = _wait_status(client, base, asset_id, {"VALID"}, label="等待重新校验")
    print(f"  ✓ 重新校验通过：{asset['status']}")

    archived = _act(client, base, asset_id, "archive", int(asset["version"]))
    if archived["status"] != "ARCHIVED" or not archived["archived_at"]:
        raise SeedFailure(f"归档未生效：{archived}")
    status, _ = _download_status(client, project_id, asset_id)
    if status != 200:
        raise SeedFailure(f"归档资产应仍可下载，实际 {status}")
    print("  ✓ 归档生效，仍可下载（正常生命周期终点）")

    tombstoned = _act(
        client,
        base,
        asset_id,
        "tombstone",
        int(archived["version"]),
        body={"reason": "全链路验证：来源数据被撤回"},
    )
    if tombstoned["status"] != "TOMBSTONED" or tombstoned["allowed_actions"]:
        raise SeedFailure(f"墓碑未生效或仍有动作：{tombstoned}")
    status, payload = _download_status(client, project_id, asset_id)
    if status != 422:
        raise SeedFailure(f"墓碑资产应禁止下载，实际 {status} {payload}")
    print("  ✓ 墓碑生效：只剩元数据与血缘，下载被拒（422）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="数据资产生命周期端到端")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    env = _load_env()
    client = Client(args.api_base, _token(env))
    project_id = _project(client, env)
    stamp = time.strftime("%Y%m%d%H%M%S")
    print(f"项目 {project_id} / 批次 {stamp}\n")
    try:
        run(client, project_id, stamp)
    except SeedFailure as exc:
        print(f"\n✗ 数据资产生命周期失败：{exc}")
        return 1
    print("\n数据资产生命周期闭环通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
