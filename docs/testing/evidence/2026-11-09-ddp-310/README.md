# DDP-310 阶段 2 整改与真实 MinIO 证据

- 日期：2026-11-09
- 基线：`f234305`（DDP-310 合入）
- 环境：staging Ubuntu 笔记本，PostgreSQL 16，API/Worker 容器
- 对象存储：**真实 MinIO**，从 `RELEASE.2024-08-17T01-24-54Z`（commit `72cff79c8a7c`）
  源码构建（Go 1.22.5），`ddp-minio.service`，数据目录 `/var/lib/ddp-minio`，`:9000`
- 说明：此前 DDP-309 证据使用的 moto 已被真实 MinIO 取代；MinIO 只接受 SigV4，
  DDP-310 已让后端强制 `signature_version=s3v4` 与 path-style。

## 1. 命令

```bash
cd backend && .venv/bin/alembic current
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9000/minio/health/live
docker logs --tail 3 ddp-worker
.venv/bin/python docs/testing/evidence/2026-11-09-ddp-310/staging_accept_310.py
```

## 2. 原始输出

```text
$ alembic current
0010 (head)

$ curl health
200
$ curl minio/health/live
200

$ docker logs --tail 3 ddp-worker
{"ts": "...", "level": "INFO", "logger": "ddp.worker.outbox", "message": "RawAsset 校验 Worker 已启动 poll_interval=1.0 max_attempts=5"}

$ python staging_accept_310.py
concurrent_complete [202, 409]
session_status COMPLETED
asset_status VALID sha_match True
download_url 200 True
episode_create 422 {"error":{"code":"calibration_not_active","message":"引用的标定记录未激活",...}}
```

## 3. 结论

- DDP-308-D01 已修复：并发 `complete` 返回 `[202, 409]`，无 503，且只产生一个可用资产。
- 真实 MinIO 下预签名 PUT、ListParts、complete、Worker 流式重算、`VALIDATING → VALID` 全链路通过。
- `download-url` 返回 200 且不落 URL 明文审计（单测覆盖）。
- Episode API 已挂载；422 为项目未配置激活标定的合法领域拒绝，不是路由缺失。
- 未验证：真实设备数据、弱网、性能、恢复演练；由 DDP-311 QA 复验与后续专项覆盖。

## 4. 回滚

- 代码回滚：`git revert f234305`；MinIO 可停 `ddp-minio.service`，对象数据保留在 `/var/lib/ddp-minio`。
- staging `.env` 中 `DDP_S3_*` 与 MinIO root 凭据一致；不得提交 `.env`。
