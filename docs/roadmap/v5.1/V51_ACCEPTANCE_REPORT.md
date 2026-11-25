# DDP V5.1 验收报告（2026-11-25 早间验收）

- 验收时间：2026-11-25 10:20（Asia/Shanghai）
- 基线：`main @ 6a7f266`，契约 **1.36.0**，迁移 **0073**
- 本轮范围：V5.1 全面建设 —— 真实数据物化闭环 + 训练就绪 + 部署推进
- 证据等级：**E1（实跑）/ E2（代码核实）**

---

## 一、验收结论摘要

| 维度 | 结果 |
|---|---|
| **全链路真实测试** | ✅ **PASS**（真实 RoboMimic 36.7MB → MinIO → 物化 → 3 个产物） |
| **GATE-2 训练就绪** | ✅ **PASS**（官方 `lerobot==0.3.3` 逐帧读取 59 帧真实数据） |
| **部署推进** | ✅ 数据库迁移 **0069 → 0073**（4 个迁移成功应用） |
| **质量门禁** | ✅ main 上 `check.sh` **10 步全过** |
| **PR 交付** | ✅ 7 个 PR 全部 CI 五项全绿后合并 |
| **仓库卫生** | ✅ 单分支、零 worktree、零开放 PR |

---

## 二、7 个 PR 交付清单

| PR | 类型 | 内容 | 关键成果 |
|---|---|---|---|
| **#118** | feat | STREAM_EXTRACTION 阶段 | 首次从真实 payload 产出三件套 |
| **#119** | docs | 记忆批次 2 | 3 类真实教训入库 |
| **#120** | feat | 编排器接线 | **物化链路真正打通**（此前阶段未接入） |
| **#121** | feat | Canonical 1.1 | 补齐 8 个必需字段，1.0 仍可读 |
| **#122** | feat | **LeRobot 逐帧导出** | **GATE-2 PASS**：`METADATA_ONLY` → `LOADER_FRAME` |
| **#123** | docs | 全链路证据 + 部署验证 | 迁移 0069→0073 + 6 项检查全过 |
| **#124** | docs | 记忆批次 3 | Pipeline 版本冲突教训 |

---

## 三、全链路真实测试（E1）

### 3.1 样本

| 项 | 值 |
|---|---|
| 来源 | HuggingFace `amandlek/robomimic` rev `74fa018` |
| 文件 | `v1.5/lift/ph/demo_v15.hdf5` |
| 大小 | **36,699,586 B** |
| SHA256 | `68f2a8cfe2502de1224f0708fb82e46da07abce108c63e721d72fc1618272f37` |
| 许可 | MIT，非 gated |
| 真实内容 | 200 demos / 9,666 帧 / actions(59,7) / states(59,32) / 真实 timestamp |

### 3.2 实测链路

```
真实 HDF5 (36.7MB, SHA256 校验通过)
  → MinIO 上传（36,699,586B 确认）
  → STREAM_EXTRACTION 阶段执行
  → 3 个 Artifact 产出：
       0000.ndjson 78,343B  streams=2  action_source=PRESENT
       0001.ndjson 75,238B  streams=2  action_source=PRESENT
       0002.ndjson 73,737B  streams=2  action_source=PRESENT
  → 共 174 条 observation + 174 条 action，可从 MinIO 读回
```

**6 项检查全 PASS**：上传 / 执行成功 / 产出 Artifact / 类型正确 / 有 checksum / 可读回

### 3.3 GATE-2 逐帧训练就绪（E1，最关键）

官方 `lerobot==0.3.3` Loader 逐帧读取真实数据：

| 检查项 | 结果 |
|---|---|
| Loader 构造 | ✅ PASS |
| 总帧数真实（>1） | ✅ **59 帧** |
| fps 真实（>1） | ✅ **20.0 Hz** |
| `observation.state` 维度 | ✅ **32 维** |
| `action` 维度 | ✅ **7 维** |
| 全部帧可逐帧读取 | ✅ **59/59** |
| `loadable_scope` | ✅ `LOADER_FRAME` |

---

## 四、S0 审计缺口的闭合情况

| S0 发现的问题 | 本轮处理 | 状态 |
|---|---|---|
| `processing_artifacts` = **0 行** | 编排接线 + 全链路测试 | ⚠️ **部分**（见 §6 诚实边界） |
| 所有物化阶段 **SKIPPED** | STREAM_EXTRACTION 接入并成功执行 | ✅ 已解决 |
| SensorStream/Observation/Action **零实例化** | 产出 174 obs + 174 action | ✅ 已解决 |
| 无真实样本 | 下载 RoboMimic 并 SHA256 校验 | ✅ 已解决 |
| **LeRobot 仅 METADATA_ONLY** | 逐帧 writer + GATE-2 验证 | ✅ **已升级 LOADER_FRAME** |
| Canonical 字段缺 10 类 | 1.1 补齐 8 个 SensorStream 字段 | ⚠️ 部分（Observation/Action 待补） |
| Adapter 无六级分级 | ADR-0048 已提交 | ⚠️ ADR 待落地 |

---

## 五、部署验证（E1）

| 步骤 | 结果 |
|---|---|
| 迁移 0069 → 0073 | ✅ 4 个迁移成功应用 |
| `quality_findings` 新增 4 列 | ✅ 全部存在 |
| `edge_agents` 四表 | ✅ 全部存在 |
| PG 表数 | 96 |
| 应用装配 | ✅ OK（processing 路由 6 条） |
| MinIO | ✅ healthy |
| main 上 `check.sh` | ✅ **10 步全过** |

---

## 六、诚实边界（不得扩大宣称）

| 项 | 状态 | 说明 |
|---|---|---|
| **`processing_artifacts` 表仍 0 行** | ⚠️ **未闭环** | 全链路测试直接调 handler + Pipeline 版本冲突（MEM-INC-011）未解决；表记录需 Worker `process_once()` 事件驱动才能写入 |
| **视频帧读取** | ❌ 未验证 | RoboMimic `ph` 版本不含图像，`video_provided=false` |
| **真实硬件采集** | ❌ `HARDWARE_CAPTURE_NOT_TESTED` | 无采集硬件 |
| **多 Episode 数据集** | ⚠️ 部分 | 3 个 demo 已验证，完整数据集未测 |
| **真实训练脚本消费** | ❌ 未验证 | `VERIFIED_FOR_TRAINING_PIPELINE` 需目标训练环境 |
| **Observation/Action Canonical 字段** | ⚠️ 未补齐 | 本轮只补 SensorStream 8 个字段 |
| **Adapter 六级分级** | ⚠️ ADR only | 待实施 |
| **数据清洗** | ❌ 仍 SKIPPED | V51-006 未开始 |
| 口径 | `PUBLIC_DATASET_INTEGRATION_TESTED` | **不是**真实硬件 Pilot |

---

## 七、记忆系统（本轮新增基建）

- 上线「记忆星云」系统：**17 张卡片**，全部 ACTIVE，`check_ref` 全部有效
- 接入 `check.sh` **第 10 步**，CI 强制校验
- **3 条反向验证**：故意注入悬空 check_ref / 索引漂移 / 缺字段，校验器均正确 exit 1
- 自动识别 **2 张复发卡片**（INC-005 魔法数字、INC-009 并发争用）

### 本轮新增的 6 条真实教训
| ID | 教训 |
|---|---|
| INC-008 | 纳秒时间戳经 JSON 传输丢精度（2^53 上限） |
| INC-009 | 多 check.sh 并发跑同一 PG 互相污染（**复发 3 次**） |
| INC-010 | 实际读取行数 ≠ 数据集总行数（会造成静默数据伪造） |
| INC-011 | 绕过用例建 Run 导致 Pipeline 版本冲突 |

---

## 八、下一轮待办（按优先级）

| 优先级 | 事项 | 前置 |
|---|---|---|
| **P0** | 走通 Worker `process_once()` 让 `processing_artifacts` 真正落库 | 需修 MEM-INC-011 的调用路径 |
| **P0** | Observation/Action Canonical 字段补齐 | ADR-0049 扩展 |
| **P1** | Adapter 六级能力矩阵落地（ADR-0048） | ADR 批准 |
| **P1** | DATA_CLEANING FLAG 规则（V51-006） | 无 |
| **P1** | 多 Episode 逐帧数据集 + 完整 Pipeline 联动 | 前者 |
| **P2** | 视频链路（需图像样本） | 样本 |
| **P2** | 真实硬件 Pilot | 硬件 |

---

## 九、验收判定

**V5.1 核心命题——「让真实机器人数据能进入 DDP 并被正确解析为逐帧数据」——已获实证。**

- 真实样本 → 真实对象存储 → 真实物化 → 真实逐帧 Parquet → **官方 Loader 逐帧读取成功**
- 这是 V4.1 结束时只有 `METADATA_ONLY` 的状态下**不可能达成的**
- 但**训练数据生产全闭环尚未完成**：`processing_artifacts` 仍为 0 行、无清洗、无多 Episode、无真实训练消费

**建议**：接受本轮为 **「V5.1 第一阶段验收通过（物化能力打通 + 训练就绪核心验证）」**，
而非「V5.1 全部完成」。后者需要 §8 的 P0/P1 全部闭环。
