# S0 审计总览（README）

> 2026-11-28 · General Owner 汇总 · 全部只读审计 · 未修改任何业务代码/迁移/契约

## 文档索引

| # | 文档 | 内容 | 依据 |
|---|---|---|---|
| 1 | [V61_Current_Baseline](V61_Current_Baseline.md) | Git/迁移/契约/测试规模/运行栈基线 | 总控实测 |
| 2 | [V61_V51_V52_Reuse_Map](V61_V51_V52_Reuse_Map.md) | 10+10 域能力核验与复用结论 | S0-A |
| 3 | [V61_FR_Status_Matrix](V61_FR_Status_Matrix.md) | 26 个 FR 实测定级（6 VERIFIED / 11 PARTIAL / 5 NOT_STARTED / 2 BLOCKED / 2 OUT） | 综合 |
| 4 | [V61_Golden_Sample_Inventory](V61_Golden_Sample_Inventory.md) | Golden Sample 盘点（本机已有 pusht 视频+状态样本） | S0-B |
| 5 | [V61_Video_Gap_Report](V61_Video_Gap_Report.md) | 视频五层缺口 + M2 最小 3 增量路径 | S0-B |
| 6 | [V61_Training_Environment_Report](V61_Training_Environment_Report.md) | GATE-B 可行性 GO + TrainingRun 证据缺口两方案 | S0-C/A |
| 7 | [V61_Hardware_Pilot_Readiness](V61_Hardware_Pilot_Readiness.md) | Pilot BLOCKED 三解除条件 | S0-C |
| 8 | [V61_Migration_Contract_Risk](V61_Migration_Contract_Risk.md) | 单 head 证明 + 0087/0088 预判 + 契约影响 | S0-D |
| 9 | [V61_Risk_Delta](V61_Risk_Delta.md) | V6.1 新增风险 10 项 + 3 个决策点 | 综合 |
| 10 | [V61_Execution_DAG](V61_Execution_DAG.md) | DAG 修正 + Sprint 1 任务卡 + 首批 5 张卡 | 综合 |

原始核验记录：`A/B/C/D-capability…md` 四份代理明细。

## 三句话总结

1. **比蓝图预期好**：训练反馈闭环（评测/Badcase 归因/DataRequest/效果对比）后端已基本建成；Badcase→需求→retest 链路代码+测试全通，缺的只是前端页和真实数据 E2E。
2. **真正的缺口集中两处**：视频像素链（解码/PTS/帧索引/Canonical 图像/LeRobot video，5 项 NOT_STARTED）与 TrainingRun 证据表（seed/ckpt/metrics 无落库）。
3. **两个好消息**：Golden Sample 已在本机（lerobot/pusht 真实视频 25,650 帧 + 配套状态动作）；训练环境本机即可 GO（lerobot 自带训练入口 + M5 MPS 原生支持）——M2/M4 可立即开工，无需等待任何外部资源。

## 状态

**S0 完成，按总控令暂停，等待用户确认后启动 Sprint 1。**
