# V5.1 S0 只读审计 · 交付索引

- 执行时间：2026-11-24 20:00–20:30（Asia/Shanghai）
- 执行方式：**只读**，未修改任何业务代码、未新增迁移、未改契约
- 基线：`HEAD = f92e83a`（比蓝图基线 `c71c72a` 新 8 个提交）
- 状态：**已完成，暂停等待用户确认 Golden Path**

---

## 一、九份交付文档

| # | 文档 | 核心结论 |
|---|---|---|
| 1 | [V51_Current_Baseline.md](./V51_Current_Baseline.md) | check.sh 9 步全过；**processing_artifacts = 0**；与蓝图基线 5 项状态差异 |
| 2 | [V51_FR_Status_Matrix.md](./V51_FR_Status_Matrix.md) | 24 项：4 VERIFIED / 14 PARTIAL / 4 BLOCKED / 2 待决策；**P0 物化 FR 无一达标** |
| 3 | [V51_Existing_Code_Reuse_Map.md](./V51_Existing_Code_Reuse_Map.md) | **约 55% 可复用**；8 阶段框架 1965 行现成 |
| 4 | [V51_Golden_Sample_Inventory.md](./V51_Golden_Sample_Inventory.md) | **仓库无任何真实样本**；推荐 RoboMimic HDF5 |
| 5 | [V51_Canonical_Gap_Report.md](./V51_Canonical_Gap_Report.md) | SensorStream 字段覆盖 60%；缺 10 类字段；建议 1.1 增量 |
| 6 | [V51_Adapter_Capability_Matrix.md](./V51_Adapter_Capability_Matrix.md) | **所有格式止步 INSPECT**；无 PAYLOAD_PARSE |
| 7 | [V51_Risk_Delta.md](./V51_Risk_Delta.md) | 9 项新风险；R-V51-001 无样本为最高风险 |
| 8 | [V51_Execution_DAG.md](./V51_Execution_DAG.md) | GATE-0（样本）是唯一用户输入阻塞点 |
| 9 | [V51_No_Duplicate_Work_Report.md](./V51_No_Duplicate_Work_Report.md) | 复用 4 / 扩展 12 / 新建 1；**仅 1 项真正新建** |

---

## 二、四条决定性结论

### 1. 工程质量健康，治理层已基本完备
`check.sh` 9 步 exit=0；后端 2953 用例；契约 1.34.0 / 257 路径；迁移 0072 单头；工作区干净。
P1 四项治理任务中**三项已闭环**（冲突恢复 PR#107、负载提示 PR#104、恢复演练 PR#103）。

### 2. 数据物化能力**零落地**（这是 V5.1 的全部价值所在）
- `processing_artifacts` = **0 行**
- 4 次 ProcessingRun：2 FAILED / 1 CANCELLED / 1 SUCCEEDED
- 所有物化阶段（METADATA/FORMAT/TIME_ALIGNMENT/SEGMENTATION）**全部 SKIPPED**
- 206 个 Episode **从未经过真实 payload 解析**

### 3. 三类核心类型**只有定义、没有生产者**
`SensorStream` / `Observation` / `Action` 在 `canonical/v1.py` 有完整字段定义，但**全仓零处实例化**。
底层 SDK 能力是真实的（h5py / rosbags / ffprobe），但全部止步于 INSPECT（只读结构，不读 payload）。

### 4. 真实样本**完全不可用**（唯一硬阻塞）
- 仓库 11 个二进制样本中，`.h5`/`.mcap`/`.parquet` 内容直接写着 `SYNTHETIC_*_CONTAINER_ONLY`
- 4 份真实公开样本只登记了 facts（SHA256/结构），**文件本体不在仓库**
- 无 `DDP_TEST_*` 环境变量，真实样本测试 **100% skip**
- LeRobot 实测：`ds[0]` 返回 6 个元数据键，**无 observation、无 action**

---

## 三、需要用户确认的 5 项决策

| # | 决策 | 选项 | **推荐** |
|---|---|---|---|
| 1 | **Golden Path 格式** | HDF5 / rosbag / LeRobot / MP4 / 等硬件 | **HDF5（RoboMimic）** |
| 2 | **样本获取** | 自动下载 / 用户提供 / 等硬件 | **自动下载** `amandlek/robomimic` rev `74fa018` 的 `v1.5/lift/ph/demo_v15.hdf5`（36.7MB, MIT） |
| 3 | **Adapter 分级** | 扩展枚举 / 独立矩阵 / 仅 Golden Path | **独立能力矩阵**（不动现有枚举，避免破坏） |
| 4 | **Canonical 变更** | 1.1 增量 / 扩 1.0 / 新建 V2 | **1.1 增量**（全可选字段，向后兼容） |
| 5 | **是否先做 Sprint 1** | 是 / 否 | **是**（3 项治理无外部依赖，可立即产出） |

---

## 四、Golden Path 推荐方案（详见 Golden Sample Inventory §5）

```
RoboMimic demo_v15.hdf5 (36.7MB, MIT, SHA256 68f2a8cf…)
  → HDF5 升级 PAYLOAD_PARSE（h5py 已有，加数值读取）
  → SensorStream：obs/agentview_image（视频流）+ obs/robot0_joint_pos（关节流）
  → Observation：逐帧 payload → 列式存储 + DB 索引
  → Action：data/demo_*/actions（7 维真实末端动作，非伪造）
  → 时间同步：单 demo 多流共享时间轴
  → Episode：每个 demo_* 一个 Episode（HDF5 天然边界）
  → Cleaning：FLAG 规则先行
  → LeRobot 逐帧导出 → lerobot 0.3.3 Frame-level Loader 验证
```

**为什么选它**：唯一同时具备「真实 payload + 真实动作源 + 真实时间戳 + 多模态」的可得样本；仓库已有 facts 登记与测试骨架；MIT 非 gated。

---

## 五、S0 完成声明

- ✅ 仓库 HEAD / Migration / OpenAPI / 测试 / CI 全部重新核对（E1）
- ✅ 24 项 V5.1 FR 逐项复核并标注证据路径
- ✅ 复用映射完成，明确 23 项「不新建」清单
- ✅ SensorStream / Observation / Action 真实生产链路排查完成（结论：零生产者）
- ✅ 真实样本、Adapter 能力、LeRobot 逐帧读取全部实测盘点
- ✅ 基线 / 差距 / 复用映射 / Golden Sample / 风险 / DAG 六类交付齐备
- ✅ **未修改任何业务代码**

**按用户要求暂停。等待 Golden Path 与样本来源确认后进入 M2 实施。**
