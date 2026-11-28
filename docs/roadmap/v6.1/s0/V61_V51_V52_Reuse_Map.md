# V61_V51_V52_Reuse_Map — 复用映射（S0-A 实测核验）

> 原则：先核验后复用，缺失只补差距。以下每行均经代码路径 + 测试数核验。

## 1. V5.1 数据链（10 域）

| 能力 | 代码路径 | 测试 | 判定 | V6.1 处理 |
|---|---|---|---|---|
| RawAsset 上传/MinIO | `app/application/ingestion/` + `infrastructure/storage/s3.py` | upload 系列 API 测试 | VERIFIED | 复用；补视频大文件分片压测 |
| STREAM_EXTRACTION（9 阶段） | `app/workers/processing.py` + `application/processing/` | 9(unit)+8(integration) | VERIFIED | **扩展 VIDEO_DECODE/FRAME_INDEX 阶段**（同迁移改 CHECK） |
| Observation/Action 物化 | processing 八阶段产物 + NDJSON | 随上 | VERIFIED（非图像） | 图像 Observation 需扩展 |
| Canonical Schema 1.1 | `ddp_data/canonical/v1.py`（26 测试） | 26 | VERIFIED | **需加 image/video 字段声明**（Episode 已有 `video_stream_ids` ID 引用） |
| Adapter 六级矩阵 | `ddp_data/capability.py` + `adapters/` | 能力测试 | VERIFIED | 六级=DETECT→INSPECT→PAYLOAD_PARSE→STREAM_EXTRACT→EPISODE_BUILD→ROUND_TRIP；hdf5/lerobot 已 ROUND_TRIP 但自认"视频帧级未验证" |
| DATA_CLEANING FLAG | 确定性规则+版本化报告 | FLAG 测试 | VERIFIED（仅 FLAG） | 补 TRANSFORM/QUARANTINE |
| LeRobot 导出 | `ddp_data/lerobot/{export_writer,frame_writer,frame_dataset}.py` | 13+12 | VERIFIED（`video_provided: False`） | **改 video feature 支持** |
| LeRobot Frame Loader | `lerobot==0.3.3` 官方读 | E1 证据（2026-11-25） | VERIFIED（非图像） | 补 video 解码验证 |
| MCAP/ROS Bag/Parquet/DROID | `ddp_data/{mcap,rosbag,parquet,droid}/` | 各 adapter | INSPECT 级 | 按格式逐个升 ROUND_TRIP |

## 2. V5.2 标注质量域（10 域测试数实测）

| 能力 | 测试数 | 判定 | V6.1 处理 |
|---|---|---|---|
| Judge 偏差校正/三区路由 | 18 | VERIFIED | 复用，补校准集/漂移监控 |
| 黄金题（熔断+接线+题库） | 9+18 | VERIFIED | 复用，补覆盖率分析 |
| PRM 步级 | 12+30（api 14） | VERIFIED | 复用，补训练端消费 |
| SFT/DPO 导出 | 17 | VERIFIED | **补 GATE-B 真实训练消费** |
| Token 成本台账 | 27+7 | VERIFIED | 复用，补预算/告警 |
| 技能标签路由 | 0080 | VERIFIED | 复用 |
| LLM 标注 UI | PR #138 组件 | VERIFIED | 复用，补运营看板 |
| Badcase 回流 | 8+9 | VERIFIED | **归因链已 VERIFIED（见下）** |
| Webhook 订阅+出站 | 0085+0086+阶段6 全链 | VERIFIED（生产演练） | 复用 |
| 维护/审计/告警/OTel | 阶段 4-6 | VERIFIED | 复用 |

## 3. ModelOps 域 —— 关键发现（比蓝图预期更好）

**蓝图认为 V61-013/014 需新建，实测大部分已存在：**

| 对象 | 状态 | 证据 |
|---|---|---|
| `evaluations` 表 | **已存在** | `models/modelops.py:135`，`metrics` JSON + thresholds + verdict + failure_cases(0061) + 基线快照(0043) |
| `model_feedback_rows` | **已存在** | Badcase 分类/归因载体 |
| `data_requirements` 表 | **已存在**，状态机 9 态（DRAFT→…→SATISFIED/CLOSED） | `modelops.py:292` |
| **归因链**（Badcase→DataRequest） | **VERIFIED** | `feedback.py:326-327`、`requirements.py:129-130`、`gap_candidates.py:111-112` 共 3 处生产写入点填充 `source_feedback_ids`/`source_evaluation_ids` |
| **效果对比**（retest） | **VERIFIED** | `requirements.py:312-336 _link_retest()` + close 前置门禁 + API `requirement_routes.py:95-116` |
| 前端 | evaluations/requirements/feedback/DataGap 页面**已有** | `apps/web/src/pages/models/` |
| 域模型 | `domain/modelops/gap_evidence.py`（attribute_gap_evidence 归因纯函数） | 18 测试 |

**缺口仅 2 个：**
1. `training_runs` 证据主表缺失（seed / checkpoint hash / 逐步 metrics 无落库；ADR-0036 设计如此，引用行即 run 记录，但 GATE-B 需要更强证据结构）
2. Badcase **前端页面**缺失（后端+API 全通，routes.tsx 无对应路由）

## 4. 禁止重复建设清单（蓝图 02 号 12 类）——核验通过

Upload/ObjectStorage、ProcessingRun/Worker/Outbox、Canonical/Adapter、Annotation/Judge/Gold/PRM、Token Ledger、Dataset Builder/Manifest、Lineage/RBAC/Retention、ModelOps/TrainingRequest：**全部单套实现，无重复**。阶段 6 新增的 webhook_outbox 是对外投递新语义（ADR 已论证），不算违规。

## 5. 复用结论

V6.1 工作量集中在：**①视频像素链（新能力）②PTS 实测（新能力）③TrainingRun 证据表（小迁移）④LeRobot video feature（扩展）⑤前端 3 缺失页面簇**。标注/评测/Badcase/数据需求闭环**基本现成**，比蓝图预估少一个数量级的建设量。
