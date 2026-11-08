# DDP-309 staging 校验闭环证据

> 后续更新：2026-11-09 已切换为真实 MinIO 并复跑通过，见
> `docs/testing/evidence/2026-11-09-ddp-310/`；本文件保留 moto 阶段的原始记录。

- 日期：2026-11-09（本地时间跨日，任务日期 2026-11-08）
- 任务：DDP-309 RawAsset 校验 Worker
- 基线：`6b42d1f`（Worker 合入）+ `17b873f`（S3 端点可配置）
- 环境：staging Ubuntu 笔记本 `xxx.xxx.xxx.xxx`，PostgreSQL 16 + API/Worker 容器
- 对象存储：**moto 5.2.5 S3 兼容服务**（`ddp-s3.service`，:9000），**不是 MinIO**
  - 原因：本机与镜像源均无法拉取 `minio/minio`（403/超时），见 DDP-307 证据
  - 限制：moto 为内存存储，重启丢对象；预签名 PUT 必须带 `Content-Type`，否则 Flask 会把 body 当表单；
    因此本证据只证明协议与 Worker 闭环，MinIO 专属行为仍为 `BLOCKED`
- 身份：staging 一次性种子项目/用户（`staging E2E`），仅用于本证据；令牌运行期用 `.env` 密钥现签，不落盘

## 1. 命令

```bash
# 迁移与健康
cd backend && .venv/bin/alembic current
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health

# Worker 启动
docker logs --tail 3 ddp-worker

# 真实 HTTP 上传闭环（脚本读取 .env 现签 JWT，不打印令牌）
.venv/bin/python docs/testing/evidence/2026-11-08-ddp-309/staging_e2e_upload.py
```

## 2. 原始输出

```text
$ alembic current
0010 (head)

$ curl health
200

$ docker logs --tail 3 ddp-worker
{"ts": "...", "level": "INFO", "logger": "ddp.worker.outbox", "message": "RawAsset 校验 Worker 已启动 poll_interval=1.0 max_attempts=5"}

$ python staging_e2e_upload.py
create 201
part_url 200
put 200
ack 200
complete 202
asset_initial VALIDATING dedup False
asset_final VALID
sha_match True size 9
```

## 3. 结论

- 上传会话、分片预签名 PUT、ack、complete、outbox、Worker 流式重算 Hash/大小、
  `VALIDATING → VALID`、RawAsset 查询在 staging 全链路打通。
- 同一对象 SHA-256 与登记值一致，大小一致。
- 未验证：MinIO 真实行为、并发 complete（DDP-308 已报 FAIL，DDP-310 修复中）、大文件吞吐。

## 4. 回滚

- 停止 `ddp-s3.service`，恢复 `DDP_S3_*` 指向真实 MinIO 后重建 API/Worker。
- staging 种子数据位于 `projects/users/project_members` 固定 UUID，可按 ID 删除（需备份）。
