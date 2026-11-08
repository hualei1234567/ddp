# ADR-0010 阶段 2 采集、上传、RawAsset 与 Episode 契约冻结决策

- 状态：**Accepted（已接受并冻结）**
- 日期：2026-11-08（提出）；2026-11-09（接受并冻结）
- 决策者：System Architect；批准人：用户（DDP-312 任务批准）
- 任务：DDP-301（提出）、DDP-312（冻结合同）
- 实现基线：DDP-311
- 契约版本：`ddp.stage2/1.0.0`（FROZEN）
- OpenAPI 入口：`packages/contracts/openapi.yaml`（3.1，`1.0.0`）
- 冻结说明：`docs/architecture/阶段2-契约冻结-v1.0.md`
- 索引：`docs/architecture/阶段2-契约索引.md`
- 关联：ADR-0002、ADR-0003、ADR-0004、ADR-0005、ADR-0009；`docs/architecture/阶段2-采集上传RawAsset-Episode契约-v1.md`；`docs/architecture/阶段2-上传协议与对象存储-v1.md`；`docs/architecture/阶段2-Episode血缘与API摘要-v1.md`；`docs/architecture/阶段2-权限迁移与错误码-v1.md`
- 边界：本 ADR 只做设计与契约决策；不修改 `backend/**`、`apps/web/**`、`backend/migrations/**`，不执行迁移或部署。

## 1. 背景

阶段 1 已冻结设备域（`ddp.device/1.0.0`）的项目隔离、乐观锁、状态机、审计与 DB 兜底原则。阶段 2 需要打通：

```text
采集任务/批次 → 现场采集 → 分片上传/断点续传 → 校验/隔离 → RawAsset
→ 基础 Episode 切片 → 血缘
```

若不在阶段 2 入口冻结上传语义、资产不可变性、Episode 版本与对象 Key，后续会出现：

- 同一文件重复上传产生多个资产，无法去重与审计；
- 分片完成依赖客户端声明，Hash/大小/签名可被伪造；
- 隔离区资产被解析或进入训练集；
- 原始文件被处理结果覆盖，Episode 无法复现；
- 对象 Key 含用户文件名，产生路径穿越与跨项目泄漏；
- Episode 修订原地覆盖，血缘断裂；
- 状态直写绕过合法迁移与审计。

## 2. 决策

### 2.1 上传传输态与资产态分离

- `UploadSession` 负责 `CREATED → UPLOADING → COMPLETING → COMPLETED/ABORTED/EXPIRED/FAILED`；不承载业务资产内容。
- `RawAsset` 只在上传完成时登记，初始 `VALIDATING`；不再使用 `INITIATED/UPLOADING/UPLOADED` 传输态。
- 理由：传输失败/重试/过期不应污染资产状态；资产状态只表达内容校验与可用性。

### 2.2 分片上传、断点与幂等

- 使用对象存储 multipart + 服务端短期预签名分片 URL；客户端可乱序、重试、断点续传。
- 每个分片必须带 `x-amz-checksum-sha256`；客户端通过 `ack` 回传 ETag、大小、分片 SHA256；服务端记录 `(session, part_number)`，同内容幂等、不同内容冲突。
- `parts/{n}/url` 的 `sha256` 为**可选绑定**：提供时在预签名 URL 上绑定校验和，ack 与绑定值不一致返回 409 `upload_part_conflict`；省略时保持旧客户端行为。
- `complete` 以对象存储 `ListParts` 为权威，交叉核对本地确认记录；缺失返回 `upload_incomplete`，大小不符 `size_mismatch`，签名不符 `file_signature_mismatch`。
- `Idempotency-Key` 作用域 `(project_id, user_id, key)`，至少 24h；Collection/UploadSession 写路径可选，Episode/IngestionJob 创建与动作**必填**。
- 整文件 SHA256 由 Worker 流式计算；`complete` 返回 `202`，异步校验；Hash 不符资产 `REJECTED`，不产生可用资产。
- 理由：HTTP 线程不处理大文件；客户端声明不可作为权威；断点续传必须可测试且不重复产生资产。

### 2.3 RawAsset 内容不可变与内容寻址

- RawAsset 的内容字段（`object_key`、`sha256`、`size_bytes`、`content_type`、`format`、`upload_session_id`、`project_id`、`asset_id`）创建后不可变。
- 对象 Key 由服务端生成，按 `project_id/日期/asset_id` 分区，不包含原始文件名。
- 同一项目内相同 `sha256 + size_bytes` 只允许一个 `VALID/QUARANTINED/ARCHIVED` 资产；重复上传通过 `raw_asset_links` 关联任务/批次/会话。
- 内容唯一索引为部分唯一索引，`REJECTED/TOMBSTONED` 不占用；竞争失败方标记 `duplicate_of_asset_id`，不产生第二个可用资产。
- 理由：支持去重、审计与血缘；同时保留不同任务的使用关系。

### 2.4 校验、隔离区与墓碑

- 上传必须校验类型白名单、大小、文件名、内容签名、Hash、权限、存储路径；不只依赖扩展名。
- `QUARANTINED` 为逻辑状态；扫描报告存独立对象 Key；隔离资产禁止解析、切片、下载。
- 隔离放行必须 DE 提案 + QI/PO 批准（提案人 ≠ 批准人），提交证据与扫描报告 Hash；放行后重新 `VALIDATING`，不得直接 `VALID`。
- 墓碑必须 PO + QI 双人审批；只改状态、保留元数据和血缘；对象按保留策略处理，不物理删除。
- **本次 HTTP 冻结不包含**隔离放行、墓碑、重新校验端点；这些能力需在后续任务中实现并通过契约测试后，以非破坏性新增端点方式加入。
- 理由：恶意/不可解析文件不能进入训练集；隔离与删除必须可追责。

### 2.5 Episode 基础切片、不可变与版本

- Episode 必须记录 `raw_asset_ids`、`start/end/duration`、`time_base`、`calibration_snapshot`、`canonical_schema_version`、`pipeline/rule` 版本、`manifest_key/manifest_sha256`。
- `READY` 起冻结数据字段；`ACCEPTED` 起额外冻结质量结论；修订创建新 `episode_id`、`episode_version+1`、`supersedes_id`，旧版本不原地修改。
- 标定快照至少含 `calibration_id`、`calibration_version`、`checksum`、`target_type`、`target_id`、`schema_version`；只给 `calibration_id` 不允许。
- 时间基准缺失或 `sync_status=UNVERIFIED/FAILED` 时创建/质检失败关闭。
- 理由：保证从 RawAsset + Pipeline + 规则 + 标定可复现；防止覆盖历史。

### 2.6 血缘只追加

- 新增 `data_lineage_edges` 与 `lineage_node_refs` 投影；边 `RAW_ASSET → INGESTION_JOB → EPISODE` 等只追加，不更新、不删除。
- 失效通过追加 `INVALIDATES` 边表达；Episode/资产历史保留。
- 跨项目节点由投影表 + 复合外键/触发器阻断；血缘查询深度 1–5、分页、项目隔离。
- HTTP 冻结只提供 `GET /episodes/{id}/lineage` 与 `GET /raw-assets/{id}/lineage`；通用节点遍历未进入本次冻结。
- 理由：训练交付需要正向/反向追溯；删除边会破坏审计。

### 2.7 项目隔离、乐观锁与 DB 兜底

- 所有阶段 2 表带 `project_id`；父引用使用 `(project_id, id)` 复合外键；跨项目统一 404。
- `CollectionTask/Batch/UploadSession/IngestionJob/Episode` 使用 `version` + `If-Match`；缺失 428、失配 409。
- 状态迁移由领域动作 + 数据库触发器双重校验；触发器要求 `ddp.actor_id`/`ddp.request_id`，缺失拒绝；审计与 outbox 同事务。
- RawAsset/Episode/血缘边禁止 DELETE；内容字段与冻结字段由触发器保护。
- 无项目路径前缀的血缘/下载端点使用 `project_id` 查询参数；服务端仍按成员资格与资源项目归属双重校验。
- 理由：沿用阶段 1 的“应用层友好错误 + DB 唯一权威”原则，防止后台脚本/其他服务绕过。

### 2.8 错误码与版本

- 新增上传/资产/Episode/血缘错误码，见冻结说明与 OpenAPI `ErrorCode`；客户端按 `code` 分支，未知不得默认成功。
- 契约版本 `ddp.stage2/1.0.0`；HTTP 主版本仍 `/api/v1`。
- 破坏性变更（删除/改名/改类型/改语义/必填化/状态迁移变化）必须新 ADR + 用户批准。

## 3. 冻结落点

- `packages/contracts/openapi.yaml` 版本 `1.0.0`，覆盖阶段 1 与阶段 2 已实现端点。
- 阶段 2 路径与 Schema 拆分在 `packages/contracts/fragments/`，由 `openapi.yaml` 统一解析；Redocly/oasdiff 输入始终是 `openapi.yaml`。
- 冻结端点：collection-tasks/batches、upload-sessions（含 `parts/{n}/url` 可选 `sha256`、`complete` 返回 `uploaded_parts`）、raw-assets 列表/详情/lineage/download-url、episodes 创建/列表/详情/actions/revisions/lineage、ingestion-jobs 创建/列表/详情/actions。
- 冻结约束：字段、错误码、`If-Match`、`Idempotency-Key`、分页 cursor、项目隔离。
- 冻结字段与变更流程详见 `docs/architecture/阶段2-契约冻结-v1.0.md`。

## 4. 后果

### 4.1 正面

- 上传可断点、可幂等、可审计；重复文件不产生重复可用资产。
- 原始资产不可覆盖，隔离区与墓碑有明确边界。
- Episode 可复现、可修订、可追溯；血缘支持后续标注/数据集/训练。
- 对象 Key 不含用户路径，降低路径穿越与跨项目泄漏风险。
- 状态直写、跨项目引用、冻结字段篡改有 DB 兜底。
- 前后端可基于同一份冻结 OpenAPI 生成类型与契约测试。

### 4.2 代价与约束

- 需要对象存储 multipart 与预签名 URL；本地 MinIO/云存储行为必须一致。
- `complete` 返回 202，客户端需轮询 Job/资产状态；前端必须实现 Pending/Failed/Partial 状态。
- 触发器和投影表增加写路径复杂度；必须在 PostgreSQL 验证，SQLite 结果不作为验收证据。
- 内容去重使“同一文件不同任务”共享对象；若业务要求字节隔离，需新 ADR。
- 异步 Hash/扫描需要 Worker、队列、超时、重试与可观测性。
- 契约拆分为 `fragments/` 后，CI 必须复制整个 `packages/contracts` 目录；单独复制 `openapi.yaml` 会解析失败。

### 4.3 迁移与回滚

- 建议迁移 `0006`–`0010`（详见附件 C）：审计/outbox/幂等 → 任务批次 → 上传资产 → Job/Episode/血缘 → 触发器/索引。
- 只新增结构，不做阶段 2 历史回填；有新数据后回滚必须先导出/备份，禁止直接 drop。
- 回滚点：`0005` 与阶段 1 契约版本；对象存储对象不随数据库回滚删除。
- 迁移执行、生产回滚需用户单独批准；本 ADR 不授权执行。
- 本次 DDP-312 只回滚 `packages/contracts`、`docs`、`scripts` 的文件变更，不涉及数据。

## 5. 替代方案

| 方案 | 结论 | 原因 |
|---|---|---|
| 上传与 RawAsset 同一状态机 | 否决 | 传输失败/过期会污染资产状态；重试语义混乱 |
| 仅信任客户端 Hash/ETag | 否决 | 可伪造；无法满足文件安全 |
| `complete` 同步计算整文件 Hash | 否决（默认） | 大文件占用 HTTP 线程；违反分层与异步任务原则 |
| 对象 Key 使用原始文件名 | 否决 | 路径穿越、跨项目泄漏、重名覆盖 |
| 同一文件按任务复制多份 | 否决（默认） | 存储浪费、血缘重复；如业务要求隔离需新 ADR |
| 隔离资产直接删除 | 否决 | 审计与合规要求保留；删除需审批 |
| Episode 原地修订 | 否决 | 覆盖历史，Manifest/标定不可复现 |
| 血缘边可更新/删除 | 否决 | 破坏审计与训练追溯 |
| 仅应用层校验项目/状态 | 否决 | 直接 SQL/后台脚本可绕过；阶段 1 已选择 DB 兜底 |
| 把未实现端点写入冻结快照 | 否决 | 会让前端生成不可调用的类型；本冻结只声明已实现行为 |

## 6. 批准与生效

- 当前状态：**Accepted，已冻结**。批准依据：DDP-312 任务书与用户批准。
- 生效范围：阶段 2 领域模型、状态机、上传协议、RawAsset/Episode 不可变与血缘、对象 Key、错误码、权限矩阵、迁移策略，以及 `packages/contracts` 中的阶段 2 已实现端点快照。
- 不包含：迁移执行、生产部署、发布、真实数据处理、物理删除、容量/性能承诺；未实现 HTTP 端点仍不在快照中。
- 维护规则：
  - `packages/contracts/**` 仍由 System Architect/契约 Owner 单一维护。
  - 非破坏性变更：更新契约与测试，执行 `check-stage2-freeze.sh --check`。
  - 破坏性变更：必须新建 ADR；契约版本、冻结文档 SHA256、批准人、日期与变更记录必须归档。
  - 实施任务必须引用本 ADR 与 `S2-T01`–`S2-T30` 用例；未跑测试不得声称通过。

## 7. 验收与证据

- 设计覆盖：领域实体、6 个状态机、上传幂等/断点/校验/Hash、隔离区、RawAsset/Episode 血缘、对象 Key、错误码、权限、迁移策略。
- 已实现端点证据：`backend/app/api/v1/collection.py`、`uploads.py`、`episodes.py`、`ingestion_jobs.py`、`lineage.py` 及对应 `*_schemas.py`。
- 契约证据：`packages/contracts/openapi.yaml` 与 `packages/contracts/fragments/*`。
- 契约测试证据：`backend/tests/api/test_collection_api.py`、`test_upload_api.py`、`test_upload_checksum_binding.py`、`test_episodes_api.py`、`test_ingestion_jobs_api.py`、`test_lineage_download_api.py`、`backend/tests/unit/test_collection_contract_alignment.py`。
- 本会话无 Bash 工具：`pytest`、`ruff`、`tsc`、Alembic、Redocly、oasdiff、JSON Schema 校验、Markdown lint 全部 **NOT RUN**；不以此声称通过。
- 首次有 Bash/CI 时必须执行：`npx @redocly/cli@1 lint packages/contracts/openapi.yaml`、
  `bash packages/contracts/scripts/check-stage2-freeze.sh --freeze`、
  `bash packages/contracts/scripts/check-stage2-freeze.sh --check`，并附结果。
- 实施验收必须附：PostgreSQL 跨项目负向测试、并发 complete、触发器非法迁移、冻结字段篡改、隔离双人审批、Hash/签名失败、Episode 边界/重叠、血缘正反向与成环、审计 outbox 回滚证据。

## 8. 变更记录

| 日期 | 变更 | 任务 |
|---|---|---|
| 2026-11-08 | 首次提出；状态“已提议（待批准）” | DDP-301 |
| 2026-11-09 | 状态改为 Accepted；按 DDP-311 已实现端点冻结 `ddp.stage2/1.0.0`；新增冻结/兼容性脚本与冻结说明 | DDP-312 |
