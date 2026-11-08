# DDP-311 证据包说明（2026-11-09）

- 任务：DDP-311「阶段 2 复验 v1.1（DDP-308-D01/D02 关闭 + 真实 MinIO）」
- 被测基线：`fcbec76`（含 DDP-310 `f234305`）
- 报告：`docs/testing/阶段2采集上传验收报告-2026-11-09-v1.1.md`
- 说明：项目约定证据归档在 `docs/testing/evidence/`；本目录即任务描述中的 `evidence/2026-11-09-ddp-311/`。

## 1. 文件清单

| 文件 | 内容 |
|---|---|
| `run.log` | 完整验收探针逐项输出（68 PASS / 1 FAIL / 0 BLOCKED） |
| `run.json` | 逐项结构化结果、环境信息、汇总 |
| `state.json` | 功能验收中的资源 ID 与响应摘要（不含下载 URL 明文） |
| `api.log` | uvicorn 真实 HTTP 访问日志 |
| `minio.log` | 真实 MinIO 服务端日志 |
| `worker-valid.log` | Worker 正常校验事件（outcome=VALID） |
| `worker-tampered.log` | Worker 内容篡改校验事件（outcome=QUARANTINED） |
| `worker-concurrent.log` | 并发 complete 后 Worker 校验日志 |
| `minio-smoke.log` | 真实 MinIO 预签名/分片/下载冒烟日志 |
| `gates-311.log` | ruff、Secret 扫描、pytest 命令与结果 |
| `pytest-311.log` | 真实 PostgreSQL 环境下 pytest 输出 |
| `pytest-311.xml` | JUnit 结果：526 tests / 0 failures / 0 errors / 0 skipped |
| `run_ddp311.py` | 主验收探针（可复现） |
| `run_gates.py` | 回归门禁脚本（ruff + secret + pytest） |
| `probe_minio_smoke.py` | 真实 MinIO 冒烟探针 |
| `probe_env.py` / `probe_db.py` | 环境与数据库只读探测 |
| `check_cleanup.py` | 收尾检查：临时数据库、端口、目录均已清理 |

## 2. 复现命令

```bash
backend/.venv/bin/python docs/testing/evidence/2026-11-09-ddp-311/run_ddp311.py
backend/.venv/bin/python docs/testing/evidence/2026-11-09-ddp-311/run_gates.py
backend/.venv/bin/python docs/testing/evidence/2026-11-09-ddp-311/probe_minio_smoke.py
```

说明：

- `run_ddp311.py` 退出码为 1，唯一原因是 `AUDIT-DOWNLOAD-REQUEST-ID` 记录到一个真实 P2 缺陷；
  验收标准中的 D01、D02、MinIO、Worker、迁移/触发器全部 PASS。
- 探针自动创建并删除一次性 PostgreSQL 数据库；真实 MinIO 使用 `/usr/local/bin/minio`、
  临时数据目录、`127.0.0.1:9100`，退出时终止进程并删除目录。
- 未读取 staging `.env`，未访问 `xxx.xxx.xxx.xxx`，未修改业务代码/迁移/契约/配置。

## 3. 关键实测值

- PostgreSQL：16.15（server_version_num=160015）
- MinIO：`/usr/local/bin/minio`，135,938,217 bytes，
  sha256=`229154e06dfe3cdd3be9bd5aef3d4504cecdafe2a0c4da9a505089a26724a028`；
  `--version` 实际输出为 `DEVELOPMENT.GOGET`（源码构建未嵌入 RELEASE 标识）；
  任务声明版本 `RELEASE.2024-08-17T01-24-54Z`、commit `72cff79c8a7c`。
- D01 并发 complete：`[202, 409]`，无 503；会话 COMPLETED；该会话资产 1 个、使用关系 1 条；
  Worker 后唯一资产 VALID。
- MinIO 分片：2 个（5 MiB + 1 KiB），ETag 与分片 MD5 一致；complete 后 ETag 带 `-2`；
  Range GET 返回 206 且前缀为 MCAP magic。
- 下载 URL：`X-Amz-Expires=15`，立即下载 200 且内容一致；过期后 403；
  审计与 api/worker 日志均无 URL、`X-Amz-Signature`、主机明文。
- Worker：`VALIDATING → VALID`；同大小篡改后 `QUARANTINED`，隔离码 `hash_mismatch`。
- pytest：526 tests / 0 failures / 0 errors / 0 skipped（真实 PostgreSQL 测试 URL）。
