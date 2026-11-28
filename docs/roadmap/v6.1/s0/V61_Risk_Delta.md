# V61_Risk_Delta — 风险增量（相对 V5.2 收官基线）

> 基线：阶段 6 收官时的健康态（CI success / 单 head / 契约零漂移 / 0 业务 PR）。
> 本文件只列 **V6.1 新引入** 的风险，不重复已闭环项。

## 1. 工程风险

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | **CI 反馈环 ~46 分钟** | 多代理并行 PR 在慢闸门排队，迭代周期被拉长 | 各代理本地先跑**域子集测试**；按域拆小 PR；CI 只在收口时全量跑（阶段 6 已验证此模式有效） |
| R2 | **15 个 Dependabot 积压（含 4 个 major）** | eslint 10 / prettier 3.10 / alembic 1.20 / antd-icons 6 一旦合入可能波及 1,250 条 web 用例 | V6.1 开发期 **冻结 major 合入**；patch 级按需合并；由总控在里程碑间隙统一处置 |
| R3 | **并行修改热点文件** | `canonical/v1.py`、`frame_writer.py`、processing 枚举、routes.tsx 是多任务共同触碰点 | 迁移号预分配 + 写域拆分（video 链一个代理、训练链一个代理、前端一个代理）；`check.sh` 全局锁继续串行 |

## 2. 技术风险

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R4 | **AV1 解码不可用** | G1 样本无法进入链路，M2 阻塞 | T1-01 第一步实测；备选：转码为 H.264 副本（保留原 AV1 为 raw）或换样本 |
| R5 | **video_frames 体量失控** | 逐帧落库百万行级，PG 压力 | 复用阶段 6 表审计阈值（500 万行评估线）；frame_object_ref 走 MinIO，库内只存索引+Hash |
| R6 | **MPS 训练数值差异** | MPS 与 CUDA 结果不可比，可能产生"训练通过但数值可疑" | GATE-B 只宣称「step 可执行/Loss 有效/checkpoint 可回载」，**不宣称训练性能与收敛性**（蓝图红线 8/9）；GPU 到位后重跑 |
| R7 | **Canonical 扩展破坏既有消费方** | 1.40.0 已冻结字段若被改动，全链测试/契约红 | 只增不改：新字段全部 optional；json_schema 生成物同步；parity strict 全程开启 |

## 3. 交付风险

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R8 | **Mock 冒充真实**（最高红线） | 视频链/训练链用合成 fixture 冲 VERIFIED | 证据分级强制：pusht=B 级公开样本（集成验证），RoboMimic ph=既有 B 级，硬件=A 级（BLOCKED 保持）；每份验收写明样本与等级 |
| R9 | **Loader PASS 混充 Training PASS** | 蓝图红线 9 | GATE-A / GATE-B 两个独立证据目录、独立状态位，前端也分开显示 |
| R10 | **范围蔓延** | 12 类禁重建对象被"顺手"重建 | 复用映射表为评审门禁：每个 FR PR 必须引用 `V61_V51_V52_Reuse_Map.md` 的复用结论 |

## 4. 风险处置的既定优势

- 阶段 6 已建立：写路径压测基线（26.46 TPS @err 0%）、连接池红线（26%）、告警真接收端、OTel 零开销开关、Webhook 生产演练——M3 规模化压测**有现成工具链**；
- 记忆库 41 卡 + `check_memory.py` 强制 check_ref——新坑必入库的机制已在 CI 里。

## 5. 需要用户决策的 3 个点

1. **TrainingRun 证据**：方案 A（新表 `training_run_evidence`，可查询可关联）vs 方案 B（仅 evidence 文档归档）——**推荐 A**，理由：GATE-B 是 V6.1 核心验收，无表则 Lineage 断链。
2. **Dependabot major 冻结**：是否同意 V6.1 期间冻结 4 个 major 合入——**推荐冻结**。
3. **硬件 Pilot 资源**：SO-101 单臂是否可调、时间窗口——不阻塞开工，M5 前答复即可。
