"""供应商模块：供应商 / 到货批次 / 片段 / 终审 / 验收台账。

覆盖的状态路径
--------------
- 供应商：``DRAFT → ACTIVE → SUSPENDED → ACTIVE``（合作中）；
  另一条 ``DRAFT → ACTIVE → TERMINATED``（已终止，终止要求 Owner）；
- 到货批次一：``ANNOUNCED → IN_TRANSIT → ARRIVED → SCREENING → finalize``
  结果由片段统计决定，这里是 ``PARTIALLY_ACCEPTED``（2 通过 + 1 拒收）→ ``CLOSED``；
- 到货批次二：``… → SCREENING``，片段走 ``NEED_MORE → resubmit → ACCEPT``，
  终审 ``ACCEPTED``；
- 到货批次三：``… → SCREENING → REJECTED → REWORK → SCREENING → finalize``，
  补件后 ``PARTIALLY_ACCEPTED`` → ``CLOSED``；
- 片段：``REGISTERED → PENDING_REVIEW → ACCEPTED/REJECTED/NEED_MORE``。

职责分离
--------
- 片段登记人不得裁决自己登记的片段：主身份登记、第二身份裁决；
- 终审是高风险放行，要求项目 Owner：主身份执行。

关于 RawAsset
-------------
终审会拒绝「已通过但缺 RawAsset 引用」的片段
（backend/app/application/suppliers/inbound.py:294-300），因此通过的片段必须
引用真实 RawAsset。正常路径是走上传会话把演示文件写进对象存储；当前治理配额
存在一处缺陷（OBJECT_STORAGE_BYTES 用量被 Postgres 的 numeric 类型带回，
``ResourceUsage`` 判定为非整数），任何上传都会 422。脚本显式识别该场景并复用
项目内已有 RawAsset，同时打印 DEGRADED，不伪造 ID。
"""

from __future__ import annotations

import hashlib
import http.client
import socket
import urllib.parse
from typing import Any

from seed.client import Client, Context, SeedFailure, expect, idempotency_key, if_match

GROUP = "suppliers"

_SEGMENT_BYTES = b'{"seed": "ddp-supplier-segment", "note": "demo inbound segment"}\n'


def _create(client: Client, path: str, body: dict[str, Any], label: str) -> dict[str, Any]:
    payload = expect(label, client.call("POST", path, body=body), 201)
    assert isinstance(payload, dict)
    return payload


def _read(client: Client, path: str, label: str) -> dict[str, Any]:
    payload = expect(label, client.call("GET", path), 200)
    assert isinstance(payload, dict)
    return payload


def _act(
    client: Client,
    path: str,
    action: str,
    version: int,
    label: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = expect(
        label,
        client.call(
            "POST",
            f"{path}/actions/{action}",
            body=dict(body or {}),
            headers=if_match(version),
        ),
        200,
    )
    assert isinstance(payload, dict)
    return payload


def _blocked(label: str, reason: str) -> None:
    print(f"  ! BLOCKED {label}：{reason}")


def _put_object(url: str, headers: dict[str, str], data: bytes, label: str) -> str:
    """预签名 URL 的主机名是容器网络服务名；保持 Host、只改 TCP 目标。"""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        socket.getaddrinfo(host, port)
        target = host
    except socket.gaierror:
        target = "127.0.0.1"
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    factory = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = factory(target, port, timeout=120)
    try:
        conn.request("PUT", path, body=data, headers={**headers, "Host": parts.netloc})
        response = conn.getresponse()
        response.read()
        if response.status != 200:
            raise SeedFailure(f"{label}：上传字节失败，状态 {response.status}")
        etag = response.getheader("ETag")
    finally:
        conn.close()
    if not etag:
        raise SeedFailure(f"{label}：对象存储未返回 ETag")
    return etag.strip('"')


def _storage_quota_configured(client: Client, base: str) -> bool:
    quotas = _read(client, f"{base}/quotas", "读取资源配额")
    return any(
        str(item.get("resource_kind")) == "OBJECT_STORAGE_BYTES"
        for item in (quotas.get("items") or [])
    )


def _upload_asset(client: Client, ctx: Context, base: str, label: str) -> str:
    """上传一个演示片段文件，返回 RawAsset ID。"""
    digest = "sha256:" + hashlib.sha256(_SEGMENT_BYTES).hexdigest()
    status, session = client.call(
        "POST",
        f"{base}/upload-sessions",
        body={
            "original_filename": f"segment-{ctx.stamp}.json",
            "declared_content_type": "application/json",
            "declared_size_bytes": len(_SEGMENT_BYTES),
            "expected_sha256": digest,
            "format_hint": "JSON",
        },
        headers={"Idempotency-Key": idempotency_key()},
    )
    expect(f"{label}创建上传会话", (status, session), 201)
    assert isinstance(session, dict)
    session_id = str(session["upload_session_id"])
    part_path = f"{base}/upload-sessions/{session_id}/parts/1"
    part = expect(
        f"{label}申请分片地址",
        client.call("POST", f"{part_path}/url", body={"sha256": digest}),
        200,
    )
    assert isinstance(part, dict)
    etag = _put_object(part["url"], dict(part.get("headers") or {}), _SEGMENT_BYTES, label)
    expect(
        f"{label}登记分片",
        client.call(
            "POST",
            f"{part_path}/ack",
            body={"etag": etag, "size_bytes": len(_SEGMENT_BYTES), "sha256": digest},
        ),
        200,
    )
    current = _read(client, f"{base}/upload-sessions/{session_id}", f"{label}读取上传会话")
    completed = expect(
        f"{label}完成上传",
        client.call(
            "POST",
            f"{base}/upload-sessions/{session_id}/complete",
            body={
                "expected_sha256": digest,
                "expected_size_bytes": len(_SEGMENT_BYTES),
                "parts": [
                    {
                        "part_number": 1,
                        "etag": etag,
                        "size_bytes": len(_SEGMENT_BYTES),
                        "sha256": digest,
                    }
                ],
                "metadata": {"seed_stamp": ctx.stamp},
            },
            headers={
                "If-Match": if_match(int(current["version"]))["If-Match"],
                "Idempotency-Key": idempotency_key(),
            },
        ),
        202,
    )
    assert isinstance(completed, dict)
    asset = completed.get("raw_asset") or {}
    if not asset.get("asset_id"):
        raise SeedFailure(f"{label}：上传完成但没有返回 RawAsset")
    return str(asset["asset_id"])


def _asset_pool(client: Client, ctx: Context, base: str, wanted: int) -> list[str]:
    """准备若干 RawAsset ID；上传不可用时回退到项目内已有资产。"""
    ids: list[str] = []
    try:
        for index in range(wanted):
            ids.append(_upload_asset(client, ctx, base, f"上传片段资产 #{index + 1}"))
        return ids
    except SeedFailure as exc:
        if "实际 422" not in str(exc) or not _storage_quota_configured(client, base):
            raise
        print("  ! DEGRADED 上传片段资产：治理配额缺陷使上传不可用，" "复用项目内已有 RawAsset")
    assets = _read(client, f"{base}/raw-assets?limit=100", "读取已有原始资产")
    existing = [
        str(item["asset_id"]) for item in (assets.get("items") or []) if item.get("asset_id")
    ]
    if not existing:
        raise SeedFailure("上传被治理配额挡住，且项目内没有可复用的 RawAsset，无法终审通过片段")
    return [existing[index % len(existing)] for index in range(wanted)]


def _advance_batch(
    client: Client,
    base: str,
    batch: dict[str, Any],
) -> dict[str, Any]:
    path = f"{base}/inbound-batches/{batch['batch_id']}"
    batch = _act(client, path, "dispatch", batch["version"], "批次发运")
    batch = _act(client, path, "arrive", batch["version"], "批次到货")
    return _act(client, path, "start-screening", batch["version"], "批次开始筛查")


def _add_segment(
    client: Client,
    base: str,
    batch_id: str,
    code: str,
    *,
    raw_asset_id: str | None,
    task_name: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "segment_code": code,
        "duration_ns": 30_000_000_000,
        "task_name": task_name,
        "diagnostics": {"frame_drop_rate": 0.004, "camera_missing_frames": 0},
    }
    if raw_asset_id is not None:
        body["raw_asset_id"] = raw_asset_id
    return _create(
        client,
        f"{base}/inbound-batches/{batch_id}/segments",
        body,
        f"登记片段 {code}",
    )


def _review_then_decide(
    ctx: Context,
    client: Client,
    base: str,
    batch_id: str,
    segment: dict[str, Any],
    *,
    decision: str,
    reason: str | None = None,
    queue: bool = True,
) -> dict[str, Any]:
    reviewer = ctx.as_("reviewer")
    path = f"{base}/inbound-batches/{batch_id}/segments/{segment['segment_id']}"
    if queue:
        # resubmit 已经把片段送回 PENDING_REVIEW，不能再 queue-review 一次。
        segment = _act(client, path, "queue-review", segment["version"], "片段提交审核")
    body: dict[str, Any] = {"decision": decision}
    if reason is not None:
        body["reason"] = reason
    payload = expect(
        f"片段裁决 {decision}",
        reviewer.call(
            "POST",
            f"{path}/decision",
            body=body,
            headers=if_match(segment["version"]),
        ),
        200,
    )
    assert isinstance(payload, dict)
    return payload


def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"
    stamp = ctx.stamp

    # --- 供应商 ---
    supplier = _create(
        client,
        f"{base}/suppliers",
        {
            "code": f"sup-{stamp}-east",
            "name": "华东数据采集服务商（合作中）",
            "category": "DATA_COLLECTION",
            "contact_name": "王工",
            "contact_email": "east-demo@example.com",
            "external_ref": f"CRM-{stamp}-E",
            "notes": "承接家庭场景双臂抓取外采，按批次到货验收。",
        },
        "创建供应商（合作中）",
    )
    supplier_id = ctx.remember(GROUP, "supplier_id", str(supplier["supplier_id"]))
    supplier_path = f"{base}/suppliers/{supplier_id}"
    supplier = _act(client, supplier_path, "activate", supplier["version"], "供应商准入")
    supplier = _act(
        client,
        supplier_path,
        "suspend",
        supplier["version"],
        "供应商暂停合作",
        {"reason": "资质年审材料待补充"},
    )
    supplier = _act(
        client,
        supplier_path,
        "resume",
        supplier["version"],
        "供应商恢复合作",
        {"reason": "资质材料已补齐"},
    )
    ctx.remember(GROUP, "supplier_status", str(supplier.get("status", "")))

    terminated = _create(
        client,
        f"{base}/suppliers",
        {
            "code": f"sup-{stamp}-south",
            "name": "华南标注服务商（已终止）",
            "category": "ANNOTATION",
            "contact_name": "李工",
            "contact_email": "south-demo@example.com",
            "external_ref": f"CRM-{stamp}-S",
            "notes": "连续两批验收不合格，合作终止。",
        },
        "创建供应商（待终止）",
    )
    terminated_path = f"{base}/suppliers/{terminated['supplier_id']}"
    terminated = _act(
        client, terminated_path, "activate", terminated["version"], "待终止供应商准入"
    )
    _act(
        client,
        terminated_path,
        "terminate",
        terminated["version"],
        "终止供应商合作",
        {"reason": "连续两批一次通过率低于合同阈值"},
    )

    # --- 批次一：2 通过 + 1 拒收 → PARTIALLY_ACCEPTED → CLOSED ---
    assets = _asset_pool(client, ctx, base, 2)
    batch1 = _create(
        client,
        f"{base}/inbound-batches",
        {
            "supplier_id": supplier_id,
            "batch_code": f"IB-{stamp}-01",
            "title": "厨房抓取外采第一批",
            "planned_segment_count": 3,
            "planned_arrival_date": "2026-11-18T02:00:00Z",
            "storage_prefix": f"supplier/{stamp}/batch-01",
            "notes": "含 1 个需当场复查的遮挡片段。",
        },
        "登记到货批次一",
    )
    batch1_id = ctx.remember(GROUP, "batch_id", str(batch1["batch_id"]))
    batch1 = _advance_batch(client, base, batch1)
    seg_a = _add_segment(
        client, base, batch1_id, f"seg-{stamp}-a1", raw_asset_id=assets[0], task_name="kitchen/pick"
    )
    _review_then_decide(ctx, client, base, batch1_id, seg_a, decision="ACCEPT")
    seg_b = _add_segment(
        client,
        base,
        batch1_id,
        f"seg-{stamp}-a2",
        raw_asset_id=assets[1],
        task_name="kitchen/place",
    )
    _review_then_decide(ctx, client, base, batch1_id, seg_b, decision="ACCEPT")
    seg_c = _add_segment(
        client, base, batch1_id, f"seg-{stamp}-a3", raw_asset_id=None, task_name="kitchen/reach"
    )
    _review_then_decide(
        ctx,
        client,
        base,
        batch1_id,
        seg_c,
        decision="REJECT",
        reason="画面被围裙遮挡，关键帧不可用",
    )
    batch1 = expect(
        "批次一终审",
        client.call(
            "POST",
            f"{base}/inbound-batches/{batch1_id}/finalize",
            body={"reason": "片段已全部裁决"},
            headers=if_match(batch1["version"]),
        ),
        200,
    )
    assert isinstance(batch1, dict)
    ctx.remember(GROUP, "batch1_status", str(batch1.get("status", "")))
    batch1 = _act(
        client,
        f"{base}/inbound-batches/{batch1_id}",
        "close",
        batch1["version"],
        "批次一结算关闭",
    )

    # --- 批次二：NEED_MORE → resubmit → ACCEPT → ACCEPTED ---
    batch2 = _create(
        client,
        f"{base}/inbound-batches",
        {
            "supplier_id": supplier_id,
            "batch_code": f"IB-{stamp}-02",
            "title": "客厅导航外采第二批",
            "planned_segment_count": 2,
            "planned_arrival_date": "2026-11-19T02:00:00Z",
            "storage_prefix": f"supplier/{stamp}/batch-02",
            "notes": "首个片段需补件后重新提交。",
        },
        "登记到货批次二",
    )
    batch2_id = ctx.remember(GROUP, "batch2_id", str(batch2["batch_id"]))
    batch2 = _advance_batch(client, base, batch2)
    seg_d = _add_segment(
        client, base, batch2_id, f"seg-{stamp}-b1", raw_asset_id=assets[0], task_name="living/nav"
    )
    seg_d = _review_then_decide(
        ctx,
        client,
        base,
        batch2_id,
        seg_d,
        decision="NEED_MORE",
        reason="片段末尾 2 秒被裁掉，请补全",
    )
    seg_d_path = f"{base}/inbound-batches/{batch2_id}/segments/{seg_d['segment_id']}"
    seg_d = _act(client, seg_d_path, "resubmit", seg_d["version"], "片段补件重新提交")
    _review_then_decide(ctx, client, base, batch2_id, seg_d, decision="ACCEPT", queue=False)
    seg_e = _add_segment(
        client, base, batch2_id, f"seg-{stamp}-b2", raw_asset_id=assets[1], task_name="living/avoid"
    )
    _review_then_decide(ctx, client, base, batch2_id, seg_e, decision="ACCEPT")
    batch2 = expect(
        "批次二终审",
        client.call(
            "POST",
            f"{base}/inbound-batches/{batch2_id}/finalize",
            body={"reason": "补件片段已通过"},
            headers=if_match(batch2["version"]),
        ),
        200,
    )
    assert isinstance(batch2, dict)
    ctx.remember(GROUP, "batch2_status", str(batch2.get("status", "")))

    # --- 批次三：全部拒收 → REJECTED → REWORK → SCREENING → PARTIALLY_ACCEPTED ---
    batch3 = _create(
        client,
        f"{base}/inbound-batches",
        {
            "supplier_id": supplier_id,
            "batch_code": f"IB-{stamp}-03",
            "title": "阳台场景外采第三批（返工）",
            "planned_segment_count": 1,
            "planned_arrival_date": "2026-11-20T02:00:00Z",
            "storage_prefix": f"supplier/{stamp}/batch-03",
            "notes": "首批全部拒收，供应商补件后重新送检。",
        },
        "登记到货批次三",
    )
    batch3_id = ctx.remember(GROUP, "batch3_id", str(batch3["batch_id"]))
    batch3 = _advance_batch(client, base, batch3)
    seg_f = _add_segment(
        client, base, batch3_id, f"seg-{stamp}-c1", raw_asset_id=None, task_name="balcony/pick"
    )
    _review_then_decide(
        ctx, client, base, batch3_id, seg_f, decision="REJECT", reason="时码与设备时钟偏差超过 2 秒"
    )
    batch3 = expect(
        "批次三首次终审",
        client.call(
            "POST",
            f"{base}/inbound-batches/{batch3_id}/finalize",
            body={"reason": "首个片段不可用"},
            headers=if_match(batch3["version"]),
        ),
        200,
    )
    assert isinstance(batch3, dict)
    batch3_path = f"{base}/inbound-batches/{batch3_id}"
    batch3 = _act(
        client,
        batch3_path,
        "rework",
        batch3["version"],
        "批次返工",
        {"reason": "要求供应商重新校时后补送"},
    )
    batch3 = _act(client, batch3_path, "start-screening", batch3["version"], "批次返工后重新筛查")
    seg_g = _add_segment(
        client, base, batch3_id, f"seg-{stamp}-c2", raw_asset_id=assets[0], task_name="balcony/pick"
    )
    _review_then_decide(ctx, client, base, batch3_id, seg_g, decision="ACCEPT")
    batch3 = expect(
        "批次三返工终审",
        client.call(
            "POST",
            f"{base}/inbound-batches/{batch3_id}/finalize",
            body={"reason": "补件片段已通过"},
            headers=if_match(batch3["version"]),
        ),
        200,
    )
    assert isinstance(batch3, dict)
    ctx.remember(GROUP, "batch3_status", str(batch3.get("status", "")))
    _act(client, batch3_path, "close", batch3["version"], "批次三结算关闭")

    # --- 读回校验：台账 / 绩效 / 横向汇总 ---
    ledger = _read(
        client,
        f"{base}/acceptance-ledger?limit=100",
        "读取验收台账",
    )
    entries = ledger.get("items") or []
    if not entries:
        raise SeedFailure("验收台账为空：批次与片段动作应当逐条留痕")
    ctx.remember(GROUP, "ledger_entries", str(len(entries)))

    metrics = _read(client, f"{base}/suppliers/{supplier_id}/metrics", "读取供应商绩效")
    portfolio = _read(client, f"{base}/suppliers/portfolio", "读取供应商横向汇总")
    if int(portfolio["totals"]["total_suppliers"]) < 2:
        raise SeedFailure("供应商横向汇总少于 2 家")
    batches = _read(client, f"{base}/inbound-batches?limit=100", "读回到货批次列表")
    if len(batches.get("items") or []) < 3:
        raise SeedFailure("到货批次少于 3 个")

    print(
        "  供应商闭环：供应商 2 / 批次 3 / 台账 "
        f"{len(entries)} 条 / 单供应商已裁决片段 {metrics['decided_segments']}"
    )
