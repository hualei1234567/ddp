# V51 Execution DAG（执行依赖图与里程碑）

- 依据：蓝图 `22_实施顺序依赖DAG与里程碑.md` + S0 实测状态校正
- **关键校正：M1 治理缺口 6 项中 3 项已完成**（PR #103/104/107），剩 3 项

---

## 一、里程碑依赖图

```
M0 审计基线（✅ 本次完成）
 │
 ├─→ M1 治理缺口（剩 3 项，可与 M2 并行）
 │    ├─ V51-013 RBAC 浏览器 E2E        （无外部依赖）
 │    ├─ V51-017 Frozen Dataset 引用保护（无外部依赖）
 │    └─ V51-010 QC 证据区间可视化      （无外部依赖）
 │
 └─→ M2 Golden Path 数据物化（🔴 阻塞于真实样本）
      ├─ [GATE-0] 真实样本到位 ← ★ 需用户决策
      ├─ V51-005 Adapter 六级分级      （前置：ADR）
      ├─ V51-001 SensorStream 物化
      ├─ V51-002 Observation 逐帧
      ├─ V51-003 Action（有源才做）
      ├─ V51-004 多流同步
      ├─ V51-007 步骤级幂等
      └─ V51-006 DATA_CLEANING
           ↓
      [GATE-1] 至少一种真实格式 RawAsset→Episode 逐帧物化通过
           ↓
      M3 训练就绪
      ├─ V51-011 LeRobot Frame-level Loader
      ├─ V51-012 Lineage 正反向 E2E（补 Session 段）
      └─ V51-024 Dataset 来源许可校验
           ↓
      [GATE-2] 官方 Loader 读取真实 Parquet+视频+Observation
           ↓
      M4 Staging / Hardware Pilot（外部资源）
      M5 条件性 AI / 训练平台（外部资源）
```

---

## 二、GATE 定义（硬门禁）

| Gate | 条件 | 当前状态 |
|---|---|---|
| **GATE-0** | 真实样本可读（SHA256 校验通过），Golden Path 格式确定 | 🔴 **未满足** —— 需用户决策 |
| GATE-1 | 至少一种真实格式从 RawAsset 到 Episode 的逐帧物化通过；`processing_artifacts > 0` | ⬜ 未开始 |
| GATE-2 | 官方 Loader 读取真实 Parquet + 视频 + Observation；有 Action 源时读取 Action | ⬜ 未开始 |
| GATE-3 | Staging 部署 + 真实硬件 Pilot（外部资源） | ⛔ BLOCKED |

**GATE-0 是唯一需要用户输入的阻塞点。其余 Gate 依赖它。**

---

## 三、详细任务 DAG（含前置与并行关系）

### Sprint 1（可立即开工，无外部依赖）

| 任务 | V51 ID | 前置 | 并行 | 交付 |
|---|---|---|---|---|
| T1-01 RBAC 浏览器 E2E | V51-013 | 无 | ✅ 可与 T1-02/03 并行 | 2 项目 × 多角色越权拒绝，CI 可重复 |
| T1-02 Frozen Dataset 引用保护 | V51-017 | 无 | ✅ | 事务级阻断 + 删除预览 + 并发/冻结竞态测试 |
| T1-03 QC 证据区间可视化 | V51-010 | 无 | ✅ | 点击规则 → Episode 时间轴定位 + 区间高亮 |

**这三项完全不依赖样本，是 V5.1 可以立刻产出的价值。**

### Sprint 2（**阻塞于 GATE-0**）

| 任务 | V51 ID | 前置 | 说明 |
|---|---|---|---|
| T2-00 **获取 Golden Sample** | — | 用户决策 | **RoboMimic HDF5 36.7MB，MIT** |
| T2-01 Adapter 六级分级 + ADR | V51-005 | ADR 批准 | 扩展枚举或建独立矩阵 |
| T2-02 Canonical 1.1 字段扩展 + ADR | （R-V51-003） | ADR 批准 | 10 类字段，全可选，向后兼容 |
| T2-03 HDF5 升级到 PAYLOAD_PARSE | V51-005 | T2-00 | h5py 读实际数值 |
| T2-04 SensorStream 生产者 | V51-001 | T2-03 | 从真实 HDF5 obs/* 产出 |
| T2-05 Observation 生产者 | V51-002 | T2-04 | 逐帧 payload + 列式存储 |
| T2-06 Action 生产者 | V51-003 | T2-03 | RoboMimic `actions` 真实源 |
| T2-07 多流同步实测 | V51-004 | T2-04/05 | 复用 `sync_analysis.py` |
| T2-08 Episode 分段 | （既有） | T2-04 | 复用 `segmentation_stage.py` |
| T2-09 步骤级幂等键 | V51-007 | 无 | 复合键 input_hash+parser_ver+pipeline_ver+config_hash |
| T2-10 DATA_CLEANING FLAG 规则 | V51-006 | T2-05 | 先只上 FLAG，不 TRANSFORM |

**执行顺序**：T2-01/T2-02（ADR）→ T2-03 → T2-04/06 并行 → T2-05 → T2-07/08 → T2-10

### Sprint 3（依赖 GATE-1）

| 任务 | V51 ID | 前置 |
|---|---|---|
| T3-01 LeRobot 逐帧导出 | V51-011 | T2-05（需真实 observation） |
| T3-02 Frame-level Loader 验证 | V51-011 | T3-01 |
| T3-03 Lineage E2E 补 Session 段 | V51-012 | 需真实采集链路 或 产品裁定 |
| T3-04 Dataset 来源许可校验 | V51-024 | 无 |
| T3-05 数据可用性漏斗指标 | V51-023 | T2 有产物后可算 |

### Sprint 4+（外部资源）

| 任务 | V51 ID | 解除条件 |
|---|---|---|
| Staging | V51-018 | 主机/网络/Secrets |
| 硬件 Pilot | V51-019 | 真实机器人 |
| AI 推理 | V51-020 | GPU + 权重 |
| 训练平台连接器 | V51-021 | 外部 API |

---

## 四、关键路径（Critical Path）

```
用户确认 Golden Path (GATE-0)
  → 下载 RoboMimic HDF5 + SHA256 校验
  → ADR：Adapter 六级 + Canonical 1.1
  → HDF5 PAYLOAD_PARSE 升级
  → SensorStream + Action 生产者
  → Observation 逐帧生产者
  → 多流同步 + Episode 分段
  → [GATE-1] processing_artifacts > 0，真实逐帧物化通过
  → LeRobot 逐帧导出
  → Frame-level Loader 验证
  → [GATE-2] 训练就绪
```

**关键路径上的每一步都不能省，且都依赖 GATE-0。**

---

## 五、可并行度分析

| 阶段 | 可并行任务数 | 说明 |
|---|---|---|
| Sprint 1 | **3** | 治理三项互不冲突（不同文件域） |
| Sprint 2 前半 | 2 | 两个 ADR 可并行；T2-03 单独 |
| Sprint 2 后半 | **3~4** | SensorStream / Action / 同步 / 幂等可并行 |
| Sprint 3 | 2~3 | Loader 验证 / Lineage / 许可校验 |

**建议用多智能体并行**：Sprint 1 三项 + Sprint 2 的 Producers（写入域互不重叠）。

---

## 六、与蓝图原计划的差异

| 蓝图 | S0 校正 |
|---|---|
| M1 含 6 项（V51-008/009/010/013/014~016/017） | **剩 3 项**（008/009/014~016 已完成） |
| M2 假设样本可获取 | **GATE-0 未满足，M2 全部阻塞** |
| M3 含 V51-012 Lineage | 部分完成（PR #108，7/8 段），仅剩 Session 段 |
| M2 含 V51-006 Cleaning | 建议拆为「FLAG 先行」，TRANSFORM/QUARANTINE 后置 |

---

## 七、下一步（等用户确认）

| 决策 | 选项 | 推荐 |
|---|---|---|
| **Golden Path 格式** | HDF5(RoboMimic) / rosbag / LeRobot / MP4 / 等硬件 | **HDF5** |
| **样本获取方式** | 自动下载 / 用户提供 / 等硬件 | **自动下载 RoboMimic** |
| **Adapter 分级方案** | 扩展枚举 / 独立矩阵 / 仅 Golden Path | 见 FR 矩阵 V51-005 |
| **Canonical 变更** | 1.1 增量 / 扩 1.0 / 新建 V2 | **1.1 增量** |
| **是否先做 Sprint 1** | 是（无外部依赖，可立即产出） | **是** |

**S0 已完成。按用户要求暂停，等待上述确认后再进入 M2 实施。**
