# V61_Execution_DAG — V6.1 推荐建设 DAG 与首批任务

## 1. 里程碑依赖图（蓝图 25 号 + S0 实测修正）

```
M0 基线审计 ──→ M1 V5.2 稳定化（已被 S0 大幅收窄，见 §2）
   │
   ├────────→ M2 视频多模态 Golden Path（P0，本机可全量推进）
   │             │
   │             ├────→ M3 规模化 Pipeline + 质量治理（P0/P1）
   │             │        │
   │             │        └──→ M4 训练消费 + 反馈闭环（P0/P1）★ 与 M2 部分并行
   │             │
   └──（硬件到位后）──→ M5 硬件 Pilot + 生产部署（P1，BLOCKED 待资源）
                          │
                          └──→ M6 扩展（P2：多格式/GPU/外部平台）
```

**S0 修正点（相对蓝图原 DAG）**：
- M4 不再整体等 M3：GATE-B 只依赖「LeRobot v2.1 数据集 + 训练 venv」，与 M2 的 video feature **可并行**——ph 数据集路径先行，pusht 视频路径后到；
- M1 工作量大幅下降：CI 已绿、单 head、契约零漂移、0 业务 PR（阶段 6 收官红利）。

## 2. M1 残余清单（半天量级）

| 项 | 动作 |
|---|---|
| Dependabot 15 个 | patch 合并 / major 冻结（决策点 2） |
| 契约 parity 基线复核 | 68 项逐条确认仍有效（例行） |
| E2E 白名单 2 条 skipped | 补真实栈种子后移出白名单（V5.1 遗留） |

## 3. Sprint 1（M2 视频链 + M4 训练链并行）

### 视频链（T1 系列，依赖迁移 0087）

| 任务 | 内容 | 产出证据 |
|---|---|---|
| T1-01 Video Fixture | pusht G1+G2 准入登记（manifest/SHA256/许可/expected）；**AV1 解码实测** | `docs/testing/evidence/2026-11-xx-video-fixture/` |
| T1-02 Video Decode | `ddp_data/media/video_decode.py`：ffmpeg 帧抽取 + pts_time 实测（复用 mp4.py 安全模式） | 解码报告：帧数/PTS 范围/错误 |
| T1-03 Frame Index | 迁移 0087：`video_frames` 表 + STREAM_EXTRACTION 枚举扩展 + `sync_frame_index` 落库接线 | 表往返验证 + 抽样帧与原视频一致 |
| T1-04 Image Observation | Canonical 扩展（只增 optional 字段）+ json_schema 三件套 | 帧引用可追溯 |
| T1-05 Time Sync | 视频 PTS ↔ 状态流时间戳误差报告（ClockSyncEvidence） | 误差分布 + sync_status 升级路径 |
| T1-06 LeRobot Video | frame_writer `video_provided=True` + GATE-A 官方 Loader 解码 E1 | Loader video PASS |

### 训练链（T3 前半，依赖训练 venv）

| 任务 | 内容 | 产出证据 |
|---|---|---|
| T3-01 环境 | 独立 venv：torch(MPS)+lerobot==0.3.3（依赖评估注释按 §三 模板） | venv 清单 + 安装记录 |
| T3-02 GATE-B | ph→v2.1 数据集→`lerobot-train --steps=2 --device=mps`：Batch/Loss/step/checkpoint 回载 | `training-smoke/` 证据（stdout/metrics/ckpt hash） |
| T3-03 Evidence 落库 | 方案 A：`training_run_evidence` 表（迁移 0088）+ 写入用例 + Lineage TRAINED_ON 边 | 表 + API + 测试 |
| T3-04 闭环 E2E | 本机训练 → evaluations 写入 → feedback → data_requirement → link-retest → close（复用现有全通链路，跑真实数据一次） | E2E 报告 |

### 前端（低垂果实，与上并行）

| 任务 | 内容 |
|---|---|
| F1 Badcase 页 | feedback 列表+详情（API 已有，对齐 evaluations 页模式） |
| F2 Pipeline 监控页 | processing_runs 有数据无界面——列表+阶段步骤+耗时 |
| F3 视频帧查看器 | Episode 详情嵌入帧索引/时间轴（依赖 T1-03） |

## 4. Sprint 2+（后续）

- T2-01/02：全量 Pipeline 基准 + Worker 并发/故障注入（复用阶段 6 压测工具链）
- T2-03/04：TRANSFORM / QUARANTINE（迁移规则版本表）
- T3-05：Badcase 聚类/归因增强 + 训练效果对比（retest 链已有，补对比视图）
- T4 系列：Judge 校准集 / 标注运营看板 / Token 预算告警
- T5 系列：硬件 Pilot（BLOCKED，资源到位即启动）

## 5. 执行治理（沿用阶段 6 验证过的模式）

- **1+6 组织**：总控（合并/迁移号/契约 bump 单点）+ 每代理单域单 worktree
- 任务卡 ≤2KB、单交付、收尾命令写死（记忆星云 procedure）
- 迁移号预分配：0087=video_frames（T1-03），0088=training_run_evidence（T3-03）
- 契约 version：M2 收口 PR bump 一次、M4 收口 PR bump 一次
- 每代理收工必跑：`ruff check/format` + 域子集 pytest + `check_secrets`；总控合并前跑全量

## 6. 第一批可立即启动的 5 张任务卡（等你确认后发放）

1. **T1-01**（视频 fixture + AV1 实测）——无前置
2. **T3-01+02**（训练 venv + GATE-B smoke）——无前置，独立 venv 不碰主仓
3. **F1+F2**（Badcase 页 + Pipeline 监控页）——API 已有，纯前端
4. **T1-02**（video_decode 模块，先不落库，纯 data-platform 层 + 测试）——无前置
5. **M1 收尾**（Dependabot patch + E2E 白名单）——总控亲自
