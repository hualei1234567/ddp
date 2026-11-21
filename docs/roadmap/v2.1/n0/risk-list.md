# DDP V2.1 N0-07 Risk List

- 日期：2026-11-18
- 基线提交：`7a9bb4b`
- 概率：高/中/低；影响：P0（数据损坏/不可恢复）、P1（阶段出口阻断）、P2（可维护性）。
- 「状态」区分已发生（历史证据）、现存、可预见。

## 1. 环境与外部依赖

| ID | 风险 | 证据 | 概率 | 影响 | 缓解 | 责任 |
|---|---|---|---|---|---|---|
| RK-01 | **staging 不可达**，发布链路（CI→镜像→staging→迁移→smoke）未验证 | `docs/operations/部署与验证记录-2026-11-17.md:118`；`ssh your-staging-host` 解析失败 | 高（已发生） | P1 | 用户提供 SSH 别名；在此之前所有 staging 任务标 BLOCKED，不用本地结果冒充 | 用户 / QA |
| RK-02 | **无真实机器人硬件**，N9 设备格式端到端无法验证 | 报告 §5 #1 | 高（已发生） | P1 | 用 Mock/回放 + 格式 Adapter 单测；不宣称 VERIFIED | 用户 / 后端 |
| RK-03 | **外部训练平台未配置**，Training Adapter 走 503 占位 | `infrastructure/training_adapters.py:17` | 高 | P2 | 保持显式 503；增加 `execution_mode` 区分 LOCAL/MOCK/DRY_RUN/REMOTE | 后端 |
| RK-04 | 本地栈依赖 13 个容器，域名/端口/镜像版本漂移导致「看起来 Up 但跑旧代码」 | 报告 §6.1 两次实测踩中 | 中 | P1 | 发布后四件事检查清单（迁移版本/新端点/静态产物/只读巡检） | DevOps |

## 2. 数据与一致性

| ID | 风险 | 证据 | 概率 | 影响 | 缓解 | 责任 |
|---|---|---|---|---|---|---|
| RK-05 | ~~`calibration_records.supersedes_id` 可分叉~~ **已修复**（N1-002：迁移 0053 部分唯一索引 + `validate_supersedes_link` + 存量冲突逐行打印后失败退出） | 领域/仓储/迁移 16 例；全量 2313 passed | 低 | P1 | 剩余：长度 ≥2 的环未检测（Backlog N1-009） | 后端 |
| RK-06 | ~~设备域 PATCH 不写审计~~ **已修复**（N1-001：`patch_audit.py` + devices/sensors/robots 接入；profiles 无 PATCH） | `tests/api/test_*_api.py` 新增 4 例；定向 49 passed | 低 | P1 | 无 | 后端 |
| RK-07 | **`validity_status=UNKNOWN` 无写入点**，来源不可达时仍显示有效 | TD-04 | 中 | P1 | 可达性探测；探测失败写 UNKNOWN 并阻断交付 | 后端 / 产品 |
| RK-08 | ~~导入 `retry` 系统动作未拦截~~ **已修复**（N2-001：`validate_import_action` 在 HTTP 用例入口拒绝系统动作；Worker 走 `ImportRunner.release_for_retry` 不受影响） | API 422 用例 + Worker 重试测试通过 | 低 | P1 | 无 | 后端 |
| RK-09 | ~~Atomic 语义缺口：Episode→Annotation/Quality、DatasetVersion→TrainingRun 血缘无边~~ **已闭环（N5-002/N5-005）** | `application/annotation/lineage.py`、`application/datasets/lineage.py`；API/Worker/回流 E2E 14 passed（2026-11-20） | 低 | P1 | 三类边均与业务事实同事务写入、唯一约束幂等；存量训练引用有默认 dry-run 回填 Worker | 后端 |
| RK-10 | ~~json vs jsonb 列类型不一致，历史曾在真实 PG 导致 500~~ **已闭环** | ORM 统一 `JSON().with_variant(JSONB, "postgresql")`；真实 PG 迁移/不可变守卫 9 passed（2026-11-20） | 低 | P1 | 新增 JSON 字段继续使用统一 variant，并强制真实 PostgreSQL 升级/回滚及比较语义测试 | 后端 |
| RK-11 | **上传配额并发窗口**：按声明字节预扣，多会话可各自通过 | 报告 §5 #9 | 中 | P2 | 原子预扣/事务锁；或明确接受并监控 | 后端 |
| RK-12 | **对象存储用量以 DB 元数据为准**，与 MinIO 实际对象可能漂移 | 报告 §5 #10 | 中 | P2 | 定期对账任务；漂移时以 MinIO 为准并告警 | 运维 |

## 3. 安全

| ID | 风险 | 证据 | 概率 | 影响 | 缓解 | 责任 |
|---|---|---|---|---|---|---|
| RK-13 | **无分支保护**（私有仓 API 403），可直推 main | 审计 §12.1 R-3；`docs/operations/main-分支保护与评审纪律.md` | 中 | P1 | CI 必过 + 人工评审纪律；所有变更走 PR（本会话已执行） | 用户 |
| RK-14 | ~~Adapter SSRF 未防护~~ **已修复**（N8-001 训练侧 + N8-008 模型侧：同一 `adapter_url_guard.py`、禁重定向、默认拒绝私网；本地例外按出站面分别显式开关） | `test_adapter_url_guard.py` 模型/训练双侧用例；`test_modelops_gateway_chain.py` API 回归；单元 1294 passed | 低 | P1 | 残余：DNS 重绑定 TOCTOU（守卫 docstring 已记录，仅接受部署配置来源前提） | 后端 |
| RK-15 | 限流 fail-open：Redis 不可用时退化为单机内存限流 | ADR-0031 | 中 | P2 | 监控降级事件；关键写接口另有配额 | 后端 |
| RK-16 | 未脱敏历史/文档可能含真实邮箱、内网 IP | 用户约束 | 低 | P1 | 提交前扫描；证据只留摘要 | 全体 |
| RK-17 | 依赖告警积压：14 个 Dependabot PR 未处理（fastapi/alembic/pytest 等大版本） | `gh pr list` | 高 | P2 | 按季度集中升级；先补兼容性测试再合并 | 后端 / 用户 |

| RK-29 | 并行 worktree 共用同一个 PostgreSQL 测试库，互相推进 `alembic_version`，导致迁移测试假失败 | 本轮 3 个任务均命中 `Can't locate revision` | 高 | P2 | 每个并行任务使用独立测试库或隔离 schema；CI 用一次性实例 | QA |

## 4. 工程与交付

| ID | 风险 | 证据 | 概率 | 影响 | 缓解 | 责任 |
|---|---|---|---|---|---|---|
| RK-18 | **6 个文件 >800 行**，继续增长将违反门禁 | TD-01 | 高（现存） | P2 | 拆分专项；新代码不得再增 | 后端 |
| RK-19 | **契约 parity 基线已接受 61 项差异**（其中 9 条生效契约超前错误码），基线可能被当成「已一致」 | `check_contract_parity.py --strict` 输出 | 中 | P1 | 每次变更先看差异清单；新差异零容忍 | 架构 / 后端 |
| RK-20 | **迁移编号缺口**（0037/0039/0045–0047），多人并行易再撞号 | `alembic history` | 中 | P2 | 迁移编号单一 Owner；合并前检查 `alembic heads` 单头 | 后端 |
| RK-21 | 多会话/多 worktree 并行时，同一测试文件语义冲突（历史上已发生） | 报告 §4 #7 | 中 | P2 | 任务卡声明文件所有权；合并后跑全量 | 全体 |
| RK-22 | ~~前端无 jsdom 交互测试 / 无 E2E~~ **已闭环（N8-007/N8-013）** | Testing Library + Playwright；CI 真实栈 4 passed / 0 skipped | 低 | P2 | 保持结构化门禁与失败 trace/screenshot；新增关键路径时同步提高期望总数 | 前端 |
| RK-23 | **无性能基线**（100/1K/10K episodes），规模化能力未知 | TD/agent：无压测脚本 | 高 | P1 | N8-03 建脚本与阈值；先测只读列表与视频 Range | QA |
| RK-24 | 备份只做本机 PG+MinIO，无异地/加密/RTO 指标 | `docs/operations/备份与恢复演练记录-2026-11-17.md` | 中 | P1 | 增加异地副本与恢复时长目标；每季度演练 | 运维 |
| RK-30 | 评测列表复用详情响应，每条最多内联 64 KiB `failure_cases`；一页 100 条最坏约 6.4 MB | `fragments/schemas.stage5.yaml` 的 `Evaluation`；契约明确"上限即分页" | 中 | P2 | 列表页保持小 `limit`；后续评估独立 `failure-cases` 子资源端点 | 后端 / 前端 |
| RK-31 | ~~`quality-runs` 没有按对象列举接口，导入审看页无法自动发现历史运行~~ **已闭环（N4-008）** | `fragments/paths.quality.yaml`；`services/qualityRuns.ts`；`ImportQualityRunSection.tsx` | 低 | P2 | 按对象游标倒序列举并自动选择最近运行；URL 保留 run_id 供刷新与分享回看 | 后端 / 前端 |
| RK-32 | 多基地概览按 `location_id` 聚合依赖 `project_id` 前缀索引，未在 PG 做 `EXPLAIN`；单项目超大时可能退化为项目级扫描 | `location_resources.py`；N9-005 报告 | 中 | P2 | 数据量到阈值时补 `(project_id, location_id)` 索引并留 EXPLAIN 证据 | 后端 |
| RK-33 | 盲审对所有非标注人生效，仲裁人/负责人也看不到标签值；若业务需要仲裁豁免，当前无规则 | `review_policy.py`；N6-004 报告 | 中 | P2 | 明确仲裁豁免规则后再开放；当前按最严格口径 | 产品 / 后端 |
| RK-34 | `/metrics` 业务指标为拉取式全库聚合，状态表缺 `status` 前缀索引；PG 运行时路径仅编译级验证 | `observability.py`；TD-11 | 中 | P2 | 2s 超时按组降级；数据量上来后预聚合或补索引；PG 环境补一次抓取回归 | 后端 / 运维 |
| RK-35 | ~~来源探测只 HEAD Canonical manifest~~ **已修复**（N5-007：manifest + `raw_assets.object_key` 双层有界探测，明确缺失写 UNKNOWN；对象上限口径为两类总数） | `validity.py`；N5-007 报告 | 低 | P2 | 大版本会因对象总数超限整轮跳过，需按部署规模调阈值 | 后端 |
| RK-36 | Adapter 的 fetch/verify/write 能力尚未接入导入链路，当前内置 Adapter 只声明 parse/detect | `registry.py`；N3-002 报告 | 低 | P2 | 后续按格式接入时再启用，避免能力声明与实际执行脱节 | 后端 |
| RK-37 | 训练状态没有受控轮询；`config_summary` 只是脱敏摘要，权威配置仍在外部平台 | `training_references.py`；N7-002 报告 | 中 | P2 | 复用 domain_sweeps 做受控轮询前先定部署开关与失败语义 | 后端 |
| RK-38 | MP4 探测已实现但未接入导入链路；后端镜像未安装 ffprobe，接入前需补部署依赖 | `ddp_data/media/mp4.py`；N3-004 报告 | 中 | P2 | 接入 local-adapter 或落盘路径，并在镜像安装 ffmpeg | 后端 / 运维 |
| RK-39 | Asset Catalog 只按标识前缀检索，不支持名称/文件名；PG `EXPLAIN` 未验收 | `asset_catalog.py`；N5-001 报告 | 中 | P2 | 有名称检索需求时单独评审索引方案；真实 PG 补 EXPLAIN | 后端 |
| RK-40 | 处理运行收口只体现为 `closed_at`/空 `allowed_actions`，响应没有显式“已收口”标签 | `processing_response_models.py`；N4-006 报告 | 低 | P2 | 前端需要时再评估契约字段；当前通过审计时间线观测 | 后端 / 前端 |
| RK-41 | Parquet 仅 footer 元数据；PyArrow wheel 体积较大且要求 glibc ≥2.28 | `ddp_data/parquet`；N3-003 报告 | 中 | P2 | 接入数据页解析前评估；CI/基础镜像保持满足要求 | 后端 / 运维 |
| RK-42 | 采集方案目标/阈值只是计划值，没有自动验收或执行保证 | `collection_plans`；N9-003 报告 | 低 | P2 | 前端展示时必须与实绩区分，禁止当作达标证明 | 产品 / 前端 |
| RK-43 | 时间同步校正只是只读建议，若被前端或人工当成「已校正」会导致误判数据可用 | `domain/episodes/sync_analysis.py`（`CorrectionProposal.applied=False`）；N4-003 报告 | 中 | P1 | 证据里显式 `applied` 字段与 `correction_available` 原因；前端展示必须注明「建议未应用」 | 前端 / 后端 |
| RK-44 | 导入链路无逐流时钟声明，同步证据多为 `UNKNOWN`，容易被读成「同步不合格」 | `application/datasources/quality.py`；N4-003 报告 | 中 | P2 | 文档与 UI 明确 UNKNOWN=「不可判定」而非失败；有真实数据源时补声明 | 产品 / 后端 |
| RK-45 | 逾期提醒只落事件与审计，没有站内/邮件通道，用户可能仍看不到提醒 | `application/annotation/overdue_alerts.py`；N6-005 报告 | 中 | P2 | 事件已有确定性去重，接入通道时可直接消费；未接入前不得宣称「已提醒」 | 后端 / 产品 |
| RK-46 | 血缘图查询有节点/边上限与深度上限，超大链路会被截断 | `application/episodes/graph.py`；N5-003 报告 | 低 | P2 | 截断标志与游标显式返回；超大图需专用离线分析而非在线 API | 后端 |
| RK-47 | 性能基线验证时把**本地开发库** `ddp` 从 0061 升级到 0065（非 staging/生产，未删数据） | N8-002 报告 §5；`alembic upgrade head` | 低 | P2 | 该操作只发生在本机开发库；staging/生产迁移仍需单独批准；后续脚本禁止对非白名单库执行 DDL | 后端 / 运维 |
| RK-48 | MCAP 依赖引入 `lz4`/`zstandard` 两个 C 扩展传递依赖，且仅有合成样例、无真实机器人 MCAP 验证 | `data-platform/requirements.txt`；N3-005 报告 | 中 | P2 | 版本精确固定并纳入 `pip-audit`；接入导入链路前须用真实样例回归；CI 镜像需验证 wheel 可用 | 后端 / 运维 |
| RK-49 | CaptureSession 尚未与 Episode/RawAsset 建立归属链路，只能回答「执行发生过」；迁移 0066 回滚会丢失会话记录 | `docs/adr/0037-capture-session-entity.md`；N9-001 报告 | 中 | P2 | 后续任务补会话→素材/Episode 关联；回滚前导出会话；不得把会话当作产出证明 | 后端 / 产品 |
| RK-50 | MOCK/DRY_RUN 只覆盖提交链路，未接外部回调与轮询；存量 MOCK+SUCCEEDED 记录保持可读（未强制标注）；前端已把 `DRY_RUN` 展示为「演练（未训练）」（橙色），不再与成功混淆 | 迁移 0067；N7-007 报告 | 中 | P1 | 下游登记入口已拒绝非真实引用（`model_training_source_invalid`）；前端已区分演练态；存量数据按可读不篡改原则处理；回调/轮询接入前不得宣称「已同步」 | 后端 / 前端 |
| RK-51 | `ExternalStatus` 声明为开放枚举后，客户端必须容忍未知状态值（本次新增 `DRY_RUN`） | `packages/contracts/fragments/schemas.stage4.yaml`；兼容性脚本 | 低 | P2 | 前端按普通字符串展示并有默认样式；新增状态必须同步下游判定，禁止按「未知即成功」处理 | 前端 / 后端 |
| RK-52 | 数据源远端校验仍只支持 Hugging Face；真实联网验收默认跳过，结构化原因依赖外部异常分类 | `infrastructure/huggingface/remote_verifier.py`；N2-003 报告 | 中 | P2 | 外部异常类型不稳定时统一收敛为 `UNAVAILABLE`，不伪造具体原因；接入 URL/S3 时复用同一有界调用与脱敏 | 后端 |
| RK-53 | 逾期分桶阈值（24h/72h）是领域常量，产品口径变化需改代码并同步契约枚举 | `domain/annotation/overdue_summary.py`；N6-002 报告 | 低 | P2 | 响应回传边界值，前端按服务端边界渲染；改口径走契约版本升级 | 产品 / 后端 |
| RK-54 | 新数据回流 e2e 使用 LOCAL 网关替身，不证明真实训练平台计算与真实权重 | `backend/tests/e2e/test_data_reflow_e2e.py`；N7-006 报告 | 中 | P2 | 真实训练平台仍 `BLOCKED`；文档明确「只证明编排闭环」；接入真实平台后补同链路验收 | 后端 / 运维 |
| RK-55 | 处理运行的 METADATA_EXTRACTION 会把输入对象流式下载到 Worker 本地临时目录后再解析；大对象受 `processing_metadata_max_object_bytes` 限制，但临时磁盘仍需预留 | `application/processing/metadata_stage.py`；N4-002 报告 | 中 | P2 | 上限由部署配置注入并在超限时显式失败；临时目录用后即删；不得把上限调到超过 Worker 磁盘 | 后端 / 运维 |
| RK-56 | 采集会话产物归属只在创建时携带，历史 RawAsset/Episode 保持 NULL（不回填） | 迁移 0068；N9-008 报告 | 中 | P2 | 文档明确「NULL = 未记录执行来源」，不得按推测回填；需要追溯时以导入链路元数据为准 | 后端 / 产品 |
| RK-57 | ~~前端 E2E 三条路径默认 CI 不执行，可能把 SKIPPED 误当通过~~ **已闭环（N8-013）** | `.github/workflows/ci.yml`；`apps/web/playwright.config.ts` | 低 | P2 | 独立 CI job 运行真实后端 + MinIO + Chromium；读取 Playwright JSON，严格门禁 4 passed / 0 skipped；`if: always()` 上传 HTML、trace、screenshot 与 JSON 结果 | 前端 / 运维 |
| RK-58 | ~~TIME_ALIGNMENT 缺少可信 producer 与 Pipeline handler~~ **已闭环（N4-011/N4-012）**：LeRobot v3 只读取逐视频显式时间范围；固定 DROID-100 revision 真实验收；handler 只消费当前项目唯一严格 manifest，不声明未知物理时钟 | `v3_time_manifest.py`；`time_alignment_stage.py`；`test_public_droid_time_manifest.py`；`test_processing_time_alignment_stage.py` | 低 | P2 | 通用 MP4/MCAP/Parquet 摘要不得推断分组或时钟；缺失/空组保持 SKIPPED，非法/歧义输入 fail-closed，校正 `applied=false`；后续真实物理时钟仍需硬件验证 | 后端 / 数据 |
| RK-59 | DROID 官方 GitHub 硬件仓库未声明许可证，且公开资料没有现场序列号、固件、标定与物理时钟；把公开型号清单误当真机联调会形成虚假闭环 | `droid/hardware.py`；`fixtures/public/droid-hardware/facts.json`；`test_public_droid_hardware.py` | 中 | P1 | 只记录固定 commit/blob/SHA 的事实与 provenance，不复制或执行远端代码；未知字段保持 NULL/NONE；状态保持 PARTIAL / HARDWARE BLOCKED，硬件到位后补在环验收 | 数据 / 设备 |
| RK-60 | Processing 标定阶段若把“校验器执行成功”误写成“标定有效”，会让无标定的公开数据或无法回查的外部快照被虚假放行 | `application/processing/calibration_stage.py`；`test_processing_calibration_stage.py` | 中 | P1 | N4-013 只验证快照可追溯性并分开统计一致/无效/缺失；不生成标定参数、不要求历史记录当前 ACTIVE；数据集准入继续由既有 CALIBRATION_MISSING / CALIBRATION_INVALID 门禁决定 | 后端 / 数据 |
| RK-61 | Episode 只冻结 `segmentation_rule_version`，未冻结规则参数；把版本可追溯误写成“可完整重放”会伪造分段闭环 | `domain/episodes/models.py`；`application/processing/segmentation_stage.py`；`test_processing_segmentation_stage.py` | 中 | P1 | N4-014 只报告注册版本可追溯与手工/历史标签不可重放，明确未执行重新切片；后续如需完整重放，先以兼容迁移冻结 method + parameters，历史值保持 UNKNOWN，禁止推测回填 | 后端 / 数据 |
| RK-62 | HDF5 探测只读结构、不解码数据集值；把“结构可读”误当成“观测/动作语义已验证”会形成虚假闭环 | `data-platform/ddp_data/hdf5/metadata.py`；`test_hdf5_probe.py`；`test_hdf5_real_public_sample.py` | 中 | P1 | N3-006 摘要只含路径/shape/dtype/compression/chunks/属性名，明确不含数据集值与属性值；真实公开样例仅证明结构可读，不证明标定、物理时钟或硬件在环；ROS Bag 仍 MISSING | 数据 / 后端 |
| RK-63 | 导入链路的格式识别只拿文件前 64 字节做魔数匹配，HDF5 的 user block 会让签名落在 512 整数倍偏移，被误判为 UNKNOWN | `domain/ingestion/validation.py::detect_format`；`import_runner.py:684`；`upload_completion.py:596` | 中 | P2 | N3-006 的结构探测器已按规范支持 user block 偏移；导入链路要覆盖同一情况需改调用方从“前 64 字节”改为“按偏移取样”，涉及上传/导入两处语义，留作独立任务；在此之前带 user block 的 HDF5 仍会被判 UNKNOWN 并走既有隔离路径 | 后端 |
| RK-64 | ROS Bag 结构探测只读索引/metadata、不解码消息体；把 topic/msgtype/计数可读误当成传感器字段语义已验证会形成虚假闭环 | `data-platform/ddp_data/rosbag/metadata.py`；`test_rosbag_probe.py`；`test_rosbag_real_public_sample.py` | 中 | P1 | N3-007 明确不调用 `messages()`，rosbag2 metadata 设 4 MiB 上限并拒绝 file 级压缩无界解压，摘要不含消息定义、QoS 或消息体；真实公开 ROS1 bag 仅证明索引结构可读，观测/动作字段映射、标定与物理时钟仍需独立任务和硬件验证 | 数据 / 后端 |

## 5. 产品与验收口径

| ID | 风险 | 证据 | 概率 | 影响 | 缓解 | 责任 |
|---|---|---|---|---|---|---|
| RK-25 | 把 PARTIAL/MISSING 说成已完成，误导后续排期 | 历史多次出现（报告 §4 与 §5） | 中 | P1 | 状态六态唯一口径；交付必须附命令/证据 | 全体 |
| RK-26 | ~~Mock/Local 训练被误认为真实训练~~ **已修复**（N7-001：`ExecutionMode` + 迁移 0054 + 契约 1.10.0 + 前端显著标注；未配置模式提交 422） | 领域/API/迁移/前端解析器用例通过 | 低 | P1 | 剩余：MOCK/DRY_RUN 是标记而非独立执行器（Backlog N7-007） | 后端 / 前端 |
| RK-27 | 长链（导入→标注→数据集→训练→评测）任一步失败时，用户看不到「下一步做什么」 | UI 蓝图 §3；当前各页独立 | 中 | P2 | 工作台聚合队列；失败项带 retry/logs 入口 | 前端 / 产品 |
| RK-28 | ~~质量报告六类分层缺失，业务无法按维度定位问题~~ **已闭环（N2-006/N4-004/N4-008）** | `QualityRunReport.tsx`；`ImportQualityRunSection.tsx`；2026-11-21 专项 74 tests passed | 低 | P2 | 六类摘要与 findings 均使用服务端权威结论；历史扁平报告只作对照，禁止混作六类结论 | 产品 / 前端 |

## 6. 风险处置节奏

- **P0/P1 风险**：进入 `v2.1-backlog.md` 的 P0/P1 任务，未关闭前对应阶段不得标记 DONE。
- **已发生风险**（RK-01/02/13/18/23）：保留在清单顶部，直到证据更新。
- 每次阶段验收更新本表：新增风险、关闭风险、变更概率/影响都要留痕。

