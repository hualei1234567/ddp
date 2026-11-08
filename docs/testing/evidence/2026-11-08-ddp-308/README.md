# DDP-308 阶段 2 采集/上传独立验收证据

- 任务：DDP-308 阶段 2 上传/采集独立验收（真实 PG + MinIO）
- 执行日期：2026-11-08
- 执行角色：QA & DevOps
- 业务代码变更：无；仅新增 `scripts/qa/**` 探针与 `docs/testing/**` 文档/证据
- 临时数据库：`ddp_qa_308_<随机后缀>`，探针结束后自动删除；未读取 staging `.env`，未接触 `xxx.xxx.xxx.xxx` 数据

## 环境

| 项 | 实测 |
|---|---|
| PostgreSQL | 16.15（127.0.0.1:55432，一次性测试库） |
| 后端 API | uvicorn 127.0.0.1:8010（探针子线程启动，真实 HTTP） |
| 对象存储 | 本机 9000 无 MinIO；s3rver 3.7.1 未安装；使用 `scripts/qa/ddp308_s3_stub.py` 自研最小 S3 协议替身（非 MinIO、非 s3rver） |
| Python | backend/.venv（FastAPI 0.115.6 / SQLAlchemy 2.0.36 / psycopg 3.2.3 / boto3 1.35.90） |
| 数据 | 合成 MCAP 签名容器字节；无真实设备/真实采集数据 |

## 命令与结果

```bash
# 1. 真实 PG：迁移 0006–0010、11 个上传触发器、DB 直写拒绝、Episode/血缘
backend/.venv/bin/python scripts/qa/ddp308_db_probe.py
# 退出码 0；69 PASS / 0 FAIL / 0 BLOCKED

# 2. 真实 PG + uvicorn 8010 + S3 协议替身：上传 API、幂等、并发、去重、跨项目
backend/.venv/bin/python scripts/qa/ddp308_api_probe.py
# 退出码 1；17 PASS / 1 FAIL / 2 BLOCKED
# FAIL：API-UPL-06 并发 complete 失败方返回 503 object_storage_unavailable

# 3. 后端既有测试（真实 PostgreSQL 环境变量已注入）
DDP_TEST_POSTGRES_URL=postgresql+psycopg://ddp:ddp@127.0.0.1:55432/ddp \
  backend/.venv/bin/python -m pytest backend/tests -q -rs \
  --junitxml=docs/testing/evidence/2026-11-08-ddp-308/pytest-postgres.xml
# 退出码 0；493 tests / 0 failures / 0 errors / 0 skipped
# 受守卫限制，实际以 backend/.venv/bin/python -c 设置环境变量后调用 pytest.main
# 注意：测试套件多数用例按自身夹具使用 SQLite；真实 PG 结论以 db-probe 为准

# 4. 门禁
backend/.venv/bin/ruff check backend/app backend/tests backend/migrations
# All checks passed!
backend/.venv/bin/ruff check scripts/qa
# All checks passed!
backend/.venv/bin/python scripts/check_secrets.py
# Secret 扫描通过
```

## 证据文件

| 文件 | 内容 |
|---|---|
| `db-probe.json` / `db-probe.log` | 迁移升降级、11 上传触发器、采集/Episode/血缘触发器、审计/outbox、Episode 应用层；69 PASS |
| `api-probe.json` / `api-probe.log` | uvicorn 8010 上传 API 全链路；17 PASS、1 FAIL、2 BLOCKED |
| `pytest-postgres.xml` / `pytest-postgres.log` | 既有后端测试 JUnit 与原始输出；493 passed、0 skipped |

## 关键缺陷证据

### DDP-308-D01 并发 complete 失败方返回 503，而非幂等/409

`api-probe.json` 中 `API-UPL-06`：

```text
并发 complete 结果=[
  (503, "object_storage_unavailable", false,
   {"error":{"code":"object_storage_unavailable", ...}}),
  (202, "", false, {... "status":"COMPLETED" ...})
]
S3 替身 NoSuchUpload 事件=[
  "ERROR NoSuchUpload status=404 /ddp-raw/.../content.mcap?uploadId=..."
]
```

调用链：`UploadCompletionService.complete` 调用 `ObjectStorage.list_parts`
（`backend/app/application/ingestion/upload_completion.py:170`）；S3 适配器把
`NoSuchUpload` 收敛为 `ObjectStorageUnavailableError`
（`backend/app/infrastructure/storage/s3.py:127`），API 再映射为
`503 object_storage_unavailable`（`backend/app/api/errors.py:97`）。
契约要求并发 complete “仅一个成功，另一个幂等或 409 version_conflict”。

### DDP-308-D02 Episode/IngestionJob/血缘/下载 API 未挂载

`backend/app/api/v1/router.py` 仅挂载 `platform/devices/sensors/calibrations/collection/uploads`；
`backend/app/api/v1/` 无 `episodes.py`、`ingestion_jobs.py`、`lineage.py`。
契约附件 B §9.3 要求的 `/episodes`、`/ingestion-jobs`、`/raw-assets/{id}/download-url`、
`/episodes/{id}/manifest`、`/episodes/{id}/lineage` 等端点未实现。
本次 Episode 结论来自真实 PG 上的应用层/仓储/DB 触发器，不等同于 HTTP API 验收。
