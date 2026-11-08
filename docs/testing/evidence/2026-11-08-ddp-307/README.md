# DDP-307 阶段 2 前端真实接口联调证据

- 日期：2026-11-08
- 任务：DDP-307 采集任务 / 上传队列 / RawAsset 前端服务层联调
- 工作区：`/home/user/agent-team/ddp-team/worktrees/ddp-fe`（`feature/ddp-fe`，与 `origin/main` 同提交 `94e7d32`）
- 范围：仅改 `apps/web/`；本目录仅放证据。
- 真实环境：本机临时 SQLite + 本机后端 `127.0.0.1:8010` + 临时 MinIO `127.0.0.1:9100`（完整链路因镜像不可得阻塞，见下）。
- 未读取或使用任何 staging `.env` / 凭据；未访问 `xxx.xxx.xxx.xxx`。

## 1. 执行结果总览

| 检查 | 命令 | 结果 |
|---|---|---|
| 类型检查 | `npm --prefix apps/web run typecheck` | 通过 |
| Lint | `npm --prefix apps/web run lint` | 通过（`--max-warnings=0`） |
| 单元测试 | `npm --prefix apps/web run test` | 30 个文件通过、2 个集成文件按设计跳过；207 通过、10 跳过 |
| 构建 | `npm --prefix apps/web run build` | 通过（仅有既有 chunk 体积提示） |
| 环境初始化 | `npm --prefix apps/web run test:integration:stage2:setup` | 通过：Alembic 0001→0008、SQLite、种子项目/成员、后端 `/health` |
| 真实后端冒烟 | `npm --prefix apps/web run test:integration:stage2:smoke` | 通过：创建采集任务 + 任务列表 + 415 负向，2/2 |
| 完整上传链路 | `npm --prefix apps/web run test:integration:stage2` | **BLOCKED**：本机无 MinIO 镜像且 registry 拉取失败（原始输出见 `run-full.txt`） |

原始输出文件：

- `run-setup.txt`：迁移/种子/后端启动的真实输出。
- `run-smoke.txt`、`smoke.txt`、`backend.txt`：真实后端冒烟输出与后端日志。
- `run-full.txt`：完整链路的环境阻塞原始输出（MinIO 镜像 403/401/超时）。

## 2. 完整链路阻塞说明（BLOCKED）

`run-full.txt` 摘录：

```text
Error response from daemon: unknown: failed to resolve reference "docker.io/minio/minio:latest":
unexpected status from HEAD request to https://docker.m.daocloud.io/v2/minio/minio/manifests/latest?ns=docker.io: 403 Forbidden
...
registry-1.docker.io: dial tcp [2a03:2880:f12c:83:face:b00c:0:25de]:443: i/o timeout
quay.io: 401 UNAUTHORIZED
本地相关镜像：dataplatform-api:latest、dataplatform-web:latest、redis:7-alpine、postgres:16-alpine
```

处理：

- 已尝试本地缓存、`docker.1ms.run`、`registry-1.docker.io`、`quay.io`。
- 已探测 `dl.min.io` 官方二进制源（410/403），不可用。
- 按任务约束**没有用 moto / 自研 S3 Mock 冒充 MinIO**，因此上传会话、分片、complete、断点、dedup、422、abort 的完整链路标记 **BLOCKED / NOT RUN**。
- 一旦环境可拉取 `minio/minio` 镜像，执行 `npm --prefix apps/web run test:integration:stage2` 即可重复完整联调；无需改代码。

## 3. 集成测试覆盖（已实现，待 MinIO 可用后执行）

文件：`apps/web/src/integration/stage2Upload.integration.test.ts`（默认无环境变量时整组跳过）。

| 验收项 | 用例 |
|---|---|
| 创建采集任务 | “创建采集任务→两分片（末片小分片）→ack→complete→RawAsset 列表/详情” |
| 创建上传会话 | 同上；`part_size_bytes` 从真实会话读取 |
| 2 个分片（最后小分片） | 主链路断言 `part 1 = part_size`、`part 2 = 1024` |
| ack | 每个分片真实 `POST /parts/{n}/ack` |
| complete | 真实 `If-Match` + `Idempotency-Key` |
| RawAsset 列表/详情 | `rawAssetsApi.listAssets/getAsset`，真实分页与项目归属校验 |
| 断点恢复 / ListParts / 缺失分片 | “断点恢复”用例：只传第 1 片 → `missing_part_numbers=[2]`；先 complete 得 `409 upload_incomplete`；补第 2 片后成功 |
| 小文件单分片 | “小文件单分片可直接 complete” |
| 同内容 dedup | “同内容重复上传命中 dedup”；首资产按 VALIDATE Worker 迁移置 `VALID` 后，第二次 complete 断言 `deduplicated=true` 且复用 `asset_id` |
| 声明类型不符 415 | “声明内容类型不在白名单返回 415 file_type_unsupported” |
| 分片大小不符 422 | ack 提交 `size_bytes+1`，断言 `422 size_mismatch` 与 `details.expected/actual` |
| 总大小不符 422 | complete 提交 `expected_size_bytes+1`，断言 `422 size_mismatch` |
| 上传后取消/中止 | abort 后断言 `ABORTED`、`aborted_at`，再次取分片 URL 返回 `409 upload_session_not_uploading` |

辅助脚本：

- `apps/web/scripts/integration/run-stage2-upload.sh`：临时 MinIO + Alembic + 种子 + uvicorn + Vitest，退出时删除容器。
- `apps/web/scripts/integration/seed_stage2.py`：固定 UUID 项目/用户/owner 成员 + 创建 `ddp-raw` 桶。
- `apps/web/scripts/integration/mark_asset_valid.py`：仅用于 dedup 用例，模拟 VALIDATE Worker 把首资产从 `VALIDATING` 迁移到 `VALID`；只改本任务临时 SQLite，不伪造上传字节/Hash。

安全约束落实：

- 所有 `DDP_*` 环境变量显式注入；后端/Alembic 在 `mktemp` 临时目录启动，不读取仓库 `.env`。
- MinIO 仅绑定 `127.0.0.1:9100`；SQLite 固定 `/tmp/ddp-it-307.db`，脚本每次重建。
- 脚本显式拒绝包含 `staging` 或 `xxx.xxx.xxx.xxx` 的数据库/对象存储地址。

## 4. 契约偏差清单（只记录，未改后端）

1. **预签名分片 URL 未绑定 `x-amz-checksum-sha256`**
   - 代码：`backend/app/application/ingestion/upload_sessions.py` 调用 `presign_upload_part(..., checksum_sha256_base64=None)`；`backend/app/infrastructure/storage/s3.py` 仅在非空时才加入该头。
   - 契约：`docs/architecture/阶段2-上传协议与对象存储-v1.md` §5.3 要求响应 headers 包含该校验头，且 URL 签名绑定它。
   - 影响：前端不能安全地自行追加未签名头，否则 S3/MinIO 可能以签名不匹配拒绝；服务端也失去对象存储侧的分片校验和交叉核对。
   - 前端适配：`useUploadQueue` 改为只发送服务端返回的 `partUrl.headers`；分片 SHA256 仍通过 ack 提交。待后端修复后无需再改前端。
2. **complete 响应 `upload_session.uploaded_parts` 恒为空**
   - 代码：`backend/app/api/v1/upload_schemas.py` 的 `UploadSessionOut.from_session` 传入 `parts=[]`，同时 `uploaded_part_count` 已更新。
   - 影响：前端 `useUploadQueue.finishUpload` 若用 complete 响应的 `uploaded_parts` 覆盖本地分片列表，UI 会丢失分片明细（计数仍在）。
   - 集成测试会在检测到该情况时打印 `[契约偏差]` 警告，不将其当作失败，避免掩盖真实上传结果。
3. **`UploadSession.created_by` 契约未 required，后端类型可空**
   - 前端原先强制 UUID 解析；已放宽为 `string | null`，避免服务端返回 null 时整条会话被判 `invalid_response`。
4. **前端 RawAsset 类型缺少 `device_time_raw`**
   - 契约 `raw-asset.v1.schema.json` 包含该字段，后端 `RawAssetOut` 也返回；前端类型/解析器已补齐。
5. **`part_size_bytes` 请求字段被接受但服务端忽略**
   - 契约允许服务端覆盖客户端建议值，因此不算缺陷；集成测试始终以服务端返回的 `part_size_bytes` 规划分片。
6. **异步校验未运行导致 dedup 前置状态缺失**
   - 后端 dedup 只匹配 `VALID/QUARANTINED/ARCHIVED`；本项目当前没有 VALIDATE Worker，complete 后资产停在 `VALIDATING`。集成测试用 `mark_asset_valid.py` 模拟 Worker 权威迁移，否则 dedup 分支无法被真实触发。

## 5. 未验证范围

- 完整上传链路（会话/分片/complete/RawAsset/断点/dedup/422/abort）因 MinIO 不可用 **NOT RUN**。
- 预签名 URL 在真实 MinIO 下对 `Content-Length`、未签名 `x-amz-checksum-sha256` 的实际行为 **NOT VERIFIED**。
- 浏览器端 `fetch` 对 `Content-Length` 请求头的处理与 Node undici 可能不同 **NOT VERIFIED**。
- 异步 VALIDATE Worker、RawAsset 状态从 `VALIDATING` 到 `VALID/QUARANTINED/REJECTED` 的完整链路不在本任务范围。
- 跨项目越权、权限矩阵变更、迁移回滚未在本任务重复验证（沿用既有测试）。

## 6. 风险与回滚点

风险：

- 前端停止发送未签名 checksum 头后，若后端仍不返回该头，对象存储侧无分片校验和；当前由 ack 的 sha256 与 complete 时 ListParts 的 ETag/大小交叉校验兜底，但强度低于契约要求。
- complete 响应分片列表为空，前端队列 UI 依赖 `uploaded_part_count`/本地 parts，刷新后需重新 `getSession` 恢复。
- `mark_asset_valid.py` 只用于测试夹具；生产环境严禁用它跳过异步校验。

回滚：

- 仅前端与证据目录变更，无数据库迁移、无 backend 契约修改。
- 回滚方式：丢弃 `apps/web/` 下本次变更即可；`/tmp/ddp-it-307.db` 与临时 MinIO 无持久化影响。
- 临时 MinIO 容器由脚本 `trap` 删除；SQLite 为一次性临时文件。

## 7. 复现命令

```bash
# 默认单测（无环境时集成组自动跳过）
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint
npm --prefix apps/web run test
npm --prefix apps/web run build

# 真实后端环境（需要可用的 minio/minio 镜像）
npm --prefix apps/web run test:integration:stage2

# 仅验证迁移/种子/后端启动
npm --prefix apps/web run test:integration:stage2:setup

# 不依赖对象存储的真实后端契约冒烟
npm --prefix apps/web run test:integration:stage2:smoke
```
