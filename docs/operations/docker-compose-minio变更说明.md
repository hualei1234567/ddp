# docker-compose MinIO 变更说明（2026-11-14）

> 关联：审计 R-1 / R-1b、OPEN O-9。本文件解释 `docker-compose.yml` 中 MinIO 服务的
> 两处变更及其数据影响，供后续会话与运维人员核对。

## 1. 事实

`minio/minio` 与 `minio/mc` 在 Docker Hub 已不可拉取（API 返回 `object not found`，
2026-11-14 实测）。本地开发因此无法用官方镜像启动 S3 兼容存储。

## 2. 变更

| 项 | 变更前 | 变更后 |
|---|---|---|
| 镜像 | `minio/minio:latest` | `bitnamilegacy/minio:2025.7.23-debian-12-r5@sha256:6dabb4a2…60fb20` |
| 数据卷挂载点 | `/data` | `/bitnami/minio/data` |
| 启动方式 | `command: server /data --console-address ":9001"` | 由镜像内置 entrypoint 起动，端口通过 `MINIO_API_PORT_NUMBER` / `MINIO_CONSOLE_PORT_NUMBER` 指定 |
| 健康检查 | `mc ready local` | `curl -fsS http://127.0.0.1:9000/minio/health/live` |
| 桶初始化 | 独立的 `minio-init`（`minio/mc`）服务 | `MINIO_DEFAULT_BUCKETS` 环境变量 |

镜像使用**固定版本 + digest**，满足 `CLAUDE.md` §三「禁止 `latest`」。
`bitnamilegacy` 是 Bitnami 已于 2025 年归档的命名空间，`2025.7.23` 是其中最后一批
稳定构建；这是一次**受限期替代**，不是长期方案。

## 3. 数据影响（已核验）

原卷 `dataplatform_ddp-miniodata` 的实际内容经核验为**空**：

```
docker run --rm -v ddp-miniodata:/v alpine sh -c 'ls -la /v; find /v -maxdepth 2'
# total 8 / 只有 . 与 ..，无任何桶或对象
```

因此挂载点从 `/data` 改到 `/bitnami/minio/data` **不会造成数据"消失"**：
本来就没有数据。阶段 2–6 的集成测试使用独立的临时桶，不依赖该卷的持久内容。

若将来要在已有数据的卷上做同类变更，必须先执行：
1. `mc mirror` 导出既有桶；
2. 起新卷挂载新路径；
3. `mc mirror` 回灌并校验对象数与 `etag`；
4. 保留旧卷 ≥ 7 天再删。

## 4. 已知限制与后续

| 限制 | 影响 | 处理 |
|---|---|---|
| `bitnamilegacy` 为归档命名空间，不再更新 | 不会收到安全补丁 | 阶段 8 前替换为自托管 MinIO 二进制或改用其他 S3 兼容实现（如 SeaweedFS/Ceph），需立 ADR |
| Bitnami 布局与官方布局不同 | 文档/脚本里出现 `/data` 的地方需同步 | 已确认仓库内无写死 `/data` 的代码路径 |
| 健康检查依赖镜像内的 `curl` | 若镜像变动可能失效 | 已用该版本实测通过 |

## 5. 验证方式

```bash
docker compose up -d minio
docker compose ps minio          # 应为 healthy
curl -fsS http://127.0.0.1:9000/minio/health/live && echo OK
```
