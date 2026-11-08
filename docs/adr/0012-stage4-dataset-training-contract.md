# ADR-0012 阶段 4 数据集与训练支撑契约决策

- 状态：**Proposed（已提议，待审阅；契约草案 `ddp.stage4/1.0.0-draft`）**
- 日期：2026-11-09
- 决策者：System Architect；批准人：用户（待批准）
- 任务：DDP-325
- 关联：ADR-0002、ADR-0003、ADR-0004、ADR-0005、ADR-0010、ADR-0011；`docs/product/阶段4-数据集与训练支撑PRD.md`；`docs/product/阶段4-数据集与训练支撑验收标准.md`；`docs/architecture/阶段3-契约冻结-v1.0.md`；`docs/architecture/contract-draft/dataset/*`
- 契约版本：`ddp.stage4/1.0.0-draft`
- 边界：本 ADR 只做设计与契约决策；不修改 `packages/contracts/**`、`backend/**`、`apps/web/**`、`backend/migrations/**`，不执行迁移、部署或生产数据操作。

## 1. 背景

阶段 2 已形成 RawAsset、IngestionJob、Episode、Manifest 与基础血缘；阶段 3 已形成可版本化的标签 Schema、标注样本集、任务、提交、复核、仲裁、黄金集、IAA 与抽检，并冻结 `ddp.stage3/1.0.0`。阶段 4 需要把经过质量与标注验收的 Episode 变成算法团队可稳定获取、可复现、可追溯、可授权、可交付的数据集版本，并把训练需求、导出、交付与外部训练引用纳入同一条血缘链。

当前 `main@659fe2b` 未发现阶段 4 的 Dataset、DatasetVersion、TrainingRequest、ExportJob 领域模块、API 路由或迁移。本 ADR 与契约草案是**设计基线，不代表功能可用**；所有阶段 4 验收默认 `NOT RUN`，真实训练交付预期 `BLOCKED`。

## 2. 决策摘要

1. 阶段 4 采用**数据集版本中心**模型：`Dataset` 是容器，`DatasetVersion` 是可复现快照；冻结后样本、划分、Manifest、报告与 Hash 不可原地修改。
2. 所有数据集输入必须来自阶段 2 的 Episode 与阶段 3 的**已批准**标注；禁止绕开 Episode 直接导出 RawAsset，禁止把未批准标注写入训练集。
3. 导出异步化：HTTP 只创建/查询 Job；产物存对象存储，逐文件与汇总 SHA256；`PARTIAL` 不是成功。
4. 训练平台只通过 Adapter 集成；DDP 不建设训练集群、GPU 调度、分布式训练或模型权重托管。
5. 所有对象强制项目隔离；冻结、导出、下载、授权、归档、Adapter 调用/回调与跨项目拒绝写审计；业务事务与 outbox 同事务。
6. 阶段 4 契约维持 `/api/v1/projects/{project_id}/...`、`If-Match`、`Idempotency-Key`、统一错误体与 cursor 分页，与阶段 2/3 一致。
7. 未验证项必须显式标注 `[未验证]`，不得用文档、草案或 Mock 声称通过。

## 3. [DECISION] 开放问题默认结论

以下结论用于解除 PRD §12.2 的开放问题阻塞；均为**默认值**，可由 PO/用户批准后按版本化策略覆盖。

### 3.1 Q4-01 LeRobot 格式与 Loader 兼容

**[DECISION]** 先冻结 `ddp.lerobot/1.0.0` 最小字段集与 sidecar，不等待真实 Loader：

- 导出格式 `format=LE_ROBOT`，`format_version=ddp.lerobot/1.0.0`（P0 唯一取值）。
- 最小字段集：Episode 标识与版本、时间基/标定引用、相机观测键、机器人状态、动作、帧索引/时间戳、语言指令（如有）、数据划分、样本权重。
- sidecar 至少包含：DDP `DatasetVersion`/`DatasetItem` 引用、Manifest SHA256、标定快照、时间同步状态、血缘引用、Exporter/映射版本、配置 SHA256。
- 字段映射、Episode 分块、视频/观测/动作映射、帧对齐规则必须在 `mapping_version` 中版本化。
- `ExportJob.verification_status` 在真实 LeRobot Loader 验证前固定 `UNVERIFIED`；只有真实 Loader 证据可置 `VERIFIED_BY_LOADER`。
- **未验证**：与真实 LeRobot Loader 的兼容性、帧对齐正确性、视频编码参数、跨版本迁移；不得宣称“已兼容 LeRobot”。

理由：先固定可测试的交换契约与 sidecar，避免格式漂移导致导出不可复现；同时把“格式正确”与“生态兼容”分开，防止无证据的兼容性声明。

### 3.2 Q4-02 精确/近似去重

**[DECISION]** 精确去重使用 `episode_lineage_id`、`manifest_sha256`、`raw_asset_sha256`；近似重复默认仅告警：

- `DedupPolicy.exact_keys` 默认 `[EPISODE_LINEAGE, MANIFEST_SHA256, RAW_ASSET_SHA256]`。
- 同一 `episode_lineage_id` 默认只保留一个版本；保留规则必须确定（`NEWEST_ACCEPTED/HIGHEST_QUALITY/EARLIEST/MANUAL`），不得随机。
- 近似重复 `near_duplicate_enabled=false` 默认关闭；启用时 `near_duplicate_enforcement=WARN_ONLY` 默认，仅告警并记录相似组，不阻断构建/冻结。
- 需要阻断必须显式设为 `BLOCK`，并经 PO 审批与审计；阈值、特征、最小簇大小配置化并版本化。
- 相似簇一旦用于划分，簇内样本不得跨划分；相似检测不可用时报告必须显示 `UNKNOWN/N/A`，不得默认“无泄漏”。

理由：精确 Hash/血缘规则确定、可复算，适合作为硬门禁；近似算法受特征与阈值影响，误报可能阻断正常交付，默认告警更符合当前证据水平。

### 3.3 Q4-03 默认划分与数据不足

**[DECISION]** Episode 级 80/10/10，可配置；空测试集默认失败：

- `SplitPolicy.strategy=RATIO`，默认 `train_ratio=0.8`、`validation_ratio=0.1`、`test_ratio=0.1`，三者之和必须为 1。
- 最小不可拆分单元默认 `group_by=[EPISODE_LINEAGE]`；启用相似/场景组时并入不可拆分单元。
- `seed` 必填；划分由 `seed + split_policy_version + 规范化排序键` 确定，重复构建必须一致。
- `empty_split_policy` 默认 `FAIL`；训练/验证划分始终不得为空。`ALLOW_EMPTY_TEST` 需 PO 审批并记录理由，且 `empty_test_waiver_required=true`。
- 低于 `min_item_count/min_duration_ns` 返回 `dataset_insufficient_data`，不得静默成功。
- 舍入默认 `LARGEST_REMAINDER`，必须写入质量报告与 Manifest。

理由：Episode 级划分最简单且与血缘硬规则一致；空测试集会使评估失效，默认失败比默认放行更安全；确定性种子与舍入是可复现的前提。

### 3.4 Q4-04 泄漏规则分级与豁免

**[DECISION]** 血缘/内容 Hash/跨划分重复为硬规则；场景组/相似簇/时间重叠默认软规则：

- 硬规则：`EPISODE_LINEAGE_CROSS_SPLIT`、`MANIFEST_SHA256_CROSS_SPLIT`、`RAW_ASSET_SHA256_CROSS_SPLIT`；启用相似检测时 `SIMILARITY_GROUP_CROSS_SPLIT` 也按硬规则。
- 软规则默认：`SCENE_GROUP_CROSS_SPLIT`、`TEMPORAL_OVERLAP_CROSS_SPLIT`；`BATCH_GROUP_CROSS_SPLIT`、`DUPLICATE_ANNOTATION` 可选。
- 硬规则失败 `leakage_status=FAIL`，禁止冻结/导出，不提供普通豁免。
- 软规则失败 `WARN`；只有 `waiver_allowed_rules` 中的软规则可由 PO 批准豁免，必须记录原因、影响、审批人、时间与审计；导出前显式确认。
- 检查无法完成 `UNKNOWN`，按失败关闭阻断，不得默认 PASS。
- `WARN` Episode、`DEGRADED` 时间同步、`usage_policy=UNKNOWN` 例外同样需要 PO 审批并审计，不因“可运行”而自动放行。

理由：血缘与内容重复是可确定证明的泄漏；场景/时间重叠存在业务判断空间，直接硬阻断会造成大量误伤，因此默认告警但保留 PO 审计豁免。

### 3.5 Q4-05 跨项目 DatasetGrant

**[DECISION]** 默认 PO 审批，含到期与撤销：

- 默认禁止跨项目；未授权访问统一 404/403，不泄漏存在性。
- `DatasetGrant` 创建后为 `PENDING_APPROVAL`；只有 PO 可 approve/reject，审批人不得是创建人。
- 必须带 `scope`（`VIEW/DOWNLOAD/EXPORT`）、`grantee_project_id`、`reason`、`expires_at`。
- 到期由系统置 `EXPIRED`；撤销由 PO 执行并立即生效；`EXPIRED/REVOKED` 为终态，需重新申请。
- 授权访问仍写审计；下载/导出使用短期签名 URL，不返回长期凭据。

理由：跨项目共享是高风险动作，默认拒绝 + 显式审批 + 到期/撤销是当前可审计的最小方案；具体审批链（是否需双人）待用户批准后版本化。

### 3.6 Q4-07 Adapter 回调与外部训练边界

**[DECISION]** HMAC-SHA256 回调签名 + 时间窗 + 幂等键 + 重放防护：

- 回调签名：`HMAC-SHA256(secret, timestamp + "." + nonce + "." + raw_body)`，以 `sha256=<hex>` 放在 `X-DDP-Signature`。
- 时间窗默认 ±300 秒（`X-DDP-Timestamp`，Unix 秒）；`X-DDP-Nonce` 在窗口内唯一。
- 非法签名/时间戳/nonce 返回 401 `training_callback_invalid`；重放返回 409 `training_callback_replayed`；重复 `event_id` 幂等，不重复更新。
- Adapter 提交必须带幂等键；同一幂等键返回同一外部引用；不同请求体同键返回 409。
- 不支持能力显式返回 422 `training_adapter_unsupported`；平台不可达返回 503 `training_adapter_unavailable`，状态显示 `UNKNOWN/UNAVAILABLE`，不得显示成功。
- 凭据只存 Secret 管理；不进入 Dataset、ExportJob、Manifest、交付单、日志或前端。
- **未验证**：真实训练平台 API、签名密钥分发/轮换、状态映射、配额与对账频率；P0 仅允许 `[MOCK]` Adapter，真实集成验收 `BLOCKED`。

理由：回调和外部提交是跨信任边界入口，HMAC + 时间窗 + nonce + 幂等键是防止伪造与重放的最低要求；状态未知不得映射为成功。

### 3.7 Q4-11 DatasetVersion 编号

**[DECISION]** 版本号单调递增且不复用；`VALIDATION_FAILED/CANCELLED` 保留尝试记录：

- `dataset_version` 在同一 `dataset_lineage_id` 内单调递增；已分配的号即使版本被取消或校验失败也不得复用。
- 可交付版本必须为 `FROZEN`；`CANCELLED/VALIDATION_FAILED` 不产生可导出产物，但保留审计与 `build_attempt`。
- 新版本通过 `supersedes_version_id` 指向旧版本；旧版本 `SUPERSEDED` 或保持可追溯，不原地修改。

理由：不复用版本号可避免下游缓存、外部引用与审计混淆；保留失败尝试便于复现问题。

### 3.8 其他开放问题

| 问题 | 默认结论 | 状态 |
|---|---|---|
| Q4-06 归档后保留期/物理删除 | 阶段 4 只归档，不物理删除；清理需独立保留策略与用户批准 | 不在本期实现 |
| Q4-08 真实团队/平台/数据 | 未提供；真实训练交付 `BLOCKED` | `BLOCKED` |
| Q4-09 质量/覆盖阈值与优先级默认值 | 策略化并版本化；不硬编码；具体数值待业务评审 | `未验证` |
| Q4-10 业务日/周/月时区 | 未确认；指标周期边界标 `未验证` | `未验证` |
| Q4-12 资产索引实现/频率/Stale 阈值 | 阶段 4 只定义 `FRESH/STALE/PARTIAL/UNKNOWN` 语义与只读接口；索引实现另案 | `未验证` |
| Q4-13 导出并发/配额/超时/成本 | 配置化；本契约默认 `timeout_seconds=3600`、`max_attempts=3`，仅为草案默认，未测量 | `未验证` |
| Q4-14 跨团队协作模型 | 只允许项目内协作 + 显式 DatasetGrant；其他模型未批准 | 默认拒绝 |

## 4. 契约边界

### 4.1 阶段 4 负责

- 资产目录只读检索视图与新鲜度语义（权威数据仍在阶段 2/3 对象）。
- Dataset、不可变 DatasetVersion、DatasetItem、SelectionPreview。
- DedupPolicy、SplitPolicy、LeakagePolicy/LeakageReport、QualityReport、Manifest。
- 冻结、复现、授权（DatasetGrant）、归档（不删除）。
- ExportJob、产物清单、逐文件/汇总 SHA256、LeRobot 导出与 sidecar。
- TrainingRequest/Revision、TrainingDelivery、ExternalTrainingReference。
- 阶段 4 审计、outbox、血缘扩展与跨项目拒绝。

### 4.2 非目标（明确不做）

1. 不建设或托管训练集群、GPU 资源池、训练调度器、分布式训练、超参搜索。
2. 不执行模型训练、推理服务或完整模型评测；阶段 4 只保存引用，完整管理属阶段 5。
3. 不物理删除数据，不覆盖已冻结版本，不删除审计/血缘。
4. 不绕开 Episode 直接从 RawAsset 生成训练集。
5. 不直接读取/改写阶段 3 标注工作台，不自动接受未人工确认的 AI 标签。
6. 不默认跨项目共享，不建设数据市场/数据交易。
7. 不引入未批准的微服务、CQRS、Event Sourcing。
8. 不用 Mock/合成数据冒充真实导出兼容性或真实训练交付。
9. 不承诺未测量的导出吞吐、数据集规模上限、训练时长或模型效果。

## 5. 与阶段 2/3 的血缘衔接

### 5.1 输入门禁

DatasetItem 必须显式引用：

- Episode：`episode_id + episode_version + episode_lineage_id + manifest_sha256`。
- 标注：`annotation_submission_id + annotation_version + annotation_record_digest`；提交必须为 `APPROVED/RESOLVED_APPROVED`。
- Schema：`schema_id + schema_version + schema_sha256`，必须与阶段 3 任务冻结快照一致。
- RawAsset：`raw_asset_ids[]`，不得处于隔离、墓碑、失效或跨项目状态。
- 时间同步不得为 `UNVERIFIED/FAILED`；`DEGRADED` 默认拒绝，例外需 QA/PM 审批。
- `usage_policy` 必须为 `TRAINING_ALLOWED`；`UNKNOWN/TRAINING_DENIED` 默认阻断冻结。

禁止“最新版本”隐式解析；禁止跨项目引用；禁止在 DatasetVersion 创建时修改 Episode/Annotation。

### 5.2 血缘节点与边

阶段 4 新增节点：

`DATASET`、`DATASET_VERSION`、`DATASET_ITEM`、`EXPORT_JOB`、`EXPORT_ARTIFACT`、`TRAINING_REQUEST`、`TRAINING_REQUEST_REVISION`、`TRAINING_DELIVERY`、`TRAINING_RUN`、`MODEL_VERSION_REF`、`EVALUATION_RESULT_REF`、`DATASET_GRANT`。

兼容阶段 2/3 节点：`RAW_ASSET`、`EPISODE`、`QUALITY_RUN`、`ANNOTATION`、`ANNOTATION_SUBMISSION`、`ANNOTATION_SCHEMA`、`IAA_REPORT` 等。

阶段 4 边：

- `INCLUDED_IN`：`EPISODE → DATASET_VERSION`；`ANNOTATION_SUBMISSION → DATASET_VERSION`。
- `SUPERSEDES`：`DATASET_VERSION(new) → DATASET_VERSION(old)`；`TRAINING_REQUEST_REVISION(new) → TRAINING_REQUEST_REVISION(old)`。
- `EXPORTED_AS`：`DATASET_VERSION → EXPORT_JOB`。
- `PRODUCED`：`EXPORT_JOB → EXPORT_ARTIFACT`。
- `DELIVERED_AS`：`EXPORT_JOB → TRAINING_DELIVERY`。
- `SATISFIES`：`TRAINING_DELIVERY → TRAINING_REQUEST_REVISION`。
- `TRAINED_AS`：`TRAINING_DELIVERY → TRAINING_RUN`。
- `EVALUATED_BY`：`TRAINING_RUN → EVALUATION_RESULT_REF`。
- `PRODUCED_MODEL`：`TRAINING_RUN → MODEL_VERSION_REF`。
- `INVALIDATES`：Episode/Annotation/RawAsset 失效 → DatasetVersion（失效传播，不删除历史）。
- `GRANTED_TO`：`DATASET_VERSION/DATASET → DATASET_GRANT → PROJECT`（建议实施时评估节点表示）。

规则：边只追加；跨项目禁止；不得成环（`SUPERSEDES` 线性）；终态不可通过血缘改写；查询支持正向/反向、深度 1–5、分页，默认仅当前项目。

### 5.3 阶段 3 边界

`AnnotationSampleSet` 与 `Dataset/DatasetVersion` 不是同一对象。阶段 3 只做标注生产与质量评估；阶段 4 才做正式清单、去重、划分、质量报告、冻结、Manifest、导出与训练交付。阶段 3 的 `annotation_export_not_supported` 错误码明确指向阶段 4。

## 6. 权限与职责分离

- 所有路由带 `{project_id}`；服务端校验成员资格并强制项目过滤；跨项目统一 404 `resource_not_found`。
- DatasetVersion 构建人不得是唯一冻结审批人；至少一名 PO/PM 审批，QA 独立核验。
- TrainingRequest 提交人不得自批；交付创建人不得是唯一验收人。
- 跨项目 DatasetGrant 只能由 PO 批准。
- 归档、撤销授权、解除阻塞属于高风险动作，必须记录原因并审计。
- 前端隐藏按钮不构成权限；所有动作以服务端 `allowed_actions` 与策略为准。
- 外部训练平台凭据只存 Secret 管理，不进入任何业务对象或前端响应。

## 7. 版本、变更与回滚策略

### 7.1 版本

- 契约草案版本 `ddp.stage4/1.0.0-draft`；HTTP 主版本 `/api/v1`。
- 状态机与错误码同版本、`status=DRAFT`；未冻结前允许修订，但必须保留变更记录。
- 冻结后破坏性变更（删除/改名/改类型/改语义/必填化/状态迁移变化/错误码状态变化）必须新建 ADR 并获用户批准，提升契约版本（如 `ddp.stage4/1.1.0` 或 `/api/v2`）。
- `packages/contracts/**` 由 System Architect 单一 Owner；本次不修改该目录。

### 7.2 变更流程

1. 提交影响分析：受影响对象、端点、状态机、错误码、迁移与前端。
2. 新建/更新 ADR，获用户批准。
3. 更新 OpenAPI、JSON Schema、状态机、错误码与索引。
4. 更新实现与契约测试；未实现项保持 `implemented=false`。
5. 生成新基线并保留旧基线；通知前后端与 QA。

### 7.3 回滚

- 本 ADR 与契约草案只新增 `docs/architecture/contract-draft/dataset/**`、`docs/architecture/阶段4-数据集与训练契约索引.md` 与 `docs/adr/0012-stage4-dataset-training-contract.md`。
- 未修改 `packages/contracts/**`、`backend/**`、`apps/web/**`、迁移或配置；未执行迁移/部署/数据操作。
- 回滚只需还原上述文档；不影响阶段 2/3 已冻结契约与基线。
- 未来实现迁移的回滚：只新增结构，不做历史回填；有数据后回滚必须先导出/备份，禁止直接 drop；迁移执行与生产回滚需用户单独批准。

## 8. 未验证项与风险

| ID | 事项 | 状态 | 影响 |
|---|---|---|---|
| U4-01 | 阶段 4 无实现代码、路由与迁移（`main@659fe2b`） | 未实现 | 所有阶段 4 验收 `NOT RUN` |
| U4-02 | LeRobot `ddp.lerobot/1.0.0` 与真实 Loader 兼容性 | `未验证` | 导出可用性/语义正确性 |
| U4-03 | 字段映射、帧对齐、视频编码、sidecar 完备性 | `未验证` | 训练数据可用性 |
| U4-04 | 真实训练平台 API、HMAC 密钥分发/轮换、状态映射 | `未验证`；P0 `[MOCK]` | Adapter 集成与回调安全 |
| U4-05 | 真实算法团队、真实训练平台、真实数据集 | `BLOCKED` | AC-TR-29 无法验收 |
| U4-06 | 质量/覆盖阈值、导出并发/配额/超时默认值 | `未验证` | 需业务评审与压测 |
| U4-07 | 资产索引实现与 Stale 阈值 | `未验证` | 目录新鲜度语义 |
| U4-08 | 指标周期时区与边界 | `未验证` | 指标口径 |
| U4-09 | 大规模导出、对象存储限流与成本 | `未验证` | 不得承诺容量/SLA |
| U4-10 | 迁移编号与阶段 4 表结构 | `未验证` | 实施排期；不得与并行迁移冲突 |

风险缓解：先冻结选择/去重/划分/泄漏/冻结/导出/交付的语义与错误码；实现时以真实 PostgreSQL、对象存储、真实 Loader 与真实平台逐项验收；Mock/合成数据必须标注且不进入真实指标分母。

## 9. 替代方案

| 方案 | 结论 | 原因 |
|---|---|---|
| 一个可变 Dataset 表承载全部样本 | 否决 | 无法冻结与复现，历史不可追溯 |
| 冻结后允许原地修补样本 | 否决 | 破坏不可变与交付可信度；应创建新版本 |
| 直接导出 RawAsset | 否决 | 绕开 Episode/Manifest/标定/时间同步与标注门禁 |
| LeRobot 作为 DDP 内部唯一模型 | 否决 | 绑定单一交换格式；DDP 需要 Canonical/Manifest 内部模型 |
| 同步导出大文件 | 否决 | 阻塞 HTTP、超时与部分失败不可控 |
| 近似去重默认硬阻断 | 否决（默认） | 误报会阻断正常交付；默认告警 + 可配置 BLOCK |
| 空测试集默认允许 | 否决 | 评估失真；默认 FAIL，例外需 PO |
| 跨项目默认共享 | 否决 | 数据安全与合规风险；必须显式 Grant |
| Mock Adapter 视为真实集成 | 否决 | 虚假验收；必须标注 `[MOCK]` |
| 建设训练集群/GPU 调度 | 否决 | 非 DDP 职责；重复建设 |
| 微服务/CQRS/Event Sourcing | 否决（默认） | 无真实收益与 ADR |

## 10. 验收与证据

- 设计覆盖：资产目录、Dataset/Version/Item、选择/去重/划分/泄漏、报告/Manifest、冻结/复现/授权/归档、ExportJob、TrainingRequest/Revision、TrainingDelivery、ExternalTrainingReference、Adapter 回调、权限/隔离、审计/outbox、血缘、错误码、版本与回滚。
- 机器可读基线：`docs/architecture/contract-draft/dataset/openapi.stage4.v1.yaml`、`*.schema.json`、`state-machines.v1.json`、`error-codes.v1.json`。
- 本次已执行：YAML/JSON 解析自检（见提交说明）；未执行 Redocly lint、oasdiff、pytest、迁移或部署，相关项 `NOT RUN`。
- 实施验收必须附：跨项目负向测试、并发冻结/导出、冻结后篡改、泄漏硬/软规则、去重确定性、划分复现、Manifest/报告 Hash、导出校验和/重试/取消/部分失败、Adapter 回调签名/重放、审计/outbox 回滚、血缘正反向与成环证据。真实训练交付以真实人员/平台/数据为前提，未满足时 `BLOCKED`。

## 11. 批准与生效

- 当前状态：**Proposed**。批准后生效范围为阶段 4 数据集与训练支撑契约草案；不构成实现完成、迁移批准或生产发布批准。
- 批准人：用户；维护：System Architect 单一 Owner。
- 生效后，任何破坏性变更必须新建 ADR；任何未验证项必须在实现验收前关闭或继续显式标注。

## 12. 变更记录

| 日期 | 变更 | 任务 |
|---|---|---|
| 2026-11-09 | 首次提出：阶段 4 边界、非目标、血缘衔接、七项 [DECISION] 默认结论、版本/变更/回滚、未验证项与风险 | DDP-325 |
