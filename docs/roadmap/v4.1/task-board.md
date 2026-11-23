# DDP V4.1 任务看板（S 阶段 × FR 追踪）

- 创建日期：2026-11-23
- 基线：`origin/main` @ FR-016/017/018 合并后（S1 收口）
- 依据：`docs/roadmap/v4.1/功能设计/13_功能需求追踪矩阵FR.md`（蓝图只读基线）+ `14_V4.1实施顺序与验收门禁.md`
- 状态口径：`已完成` / `进行中` / `未开工` / `BLOCKED`（附解除条件）

---

## S0 基线与决策冻结 —— ✅ 已完成

| 项 | 状态 | 证据 |
|---|---|---|
| 蓝图包入库（18 文件 SHA256 校验 0 差异） | ✅ | PR #85；`docs/roadmap/v4.1/` |
| 基线复核报告（HEAD/契约/迁移/门禁实跑） | ✅ | `docs/roadmap/v4.1/s0-baseline-and-d2.md` |
| D2 选项列出 + 推荐方案（纯拉取，等用户批准） | ⏸ **待用户裁决** | 同上 §2 |

## S1 关闭低依赖缺口 —— ✅ 三线全部完成（2026-11-23）

| FR | 内容 | 状态 | PR | 证据 |
|---|---|---|---|---|
| FR-016/017 | L2⊆L1、L3⊆L2 服务端提交强校验（跨 record 按 Episode 合并，Submission 视角） | ✅ 已完成 | **#88** | `validate_submission_records` 接线提交门禁；322 行测试（越界指明键/多父任一包含/无父级违规） |
| FR-018 | OUTCOME_SPAN 提交校验（字段白名单、outcome 收窄集、失败/部分成功必填失败类型） | ✅ 已完成 | **#88** | `_require_type` 新分支 + 测试 |
| FR-026/027/028 | QC 批量重跑 UI（选择/预览/确认/执行）+ 差异报告四类分组 + 失败项重试开关 | ✅ 已完成 | **#87** | Episode 列表多选入口 + 详情页内联差异；46 例新增测试（解析器 13/API 7/组件 26）；语义红线「排队回执≠结论」全链落实 |
| FR-034/035 | LeRobot 官方 Loader 隔离实测 | ✅ **实测完成：BLOCKED 维持（有据）** | **#86** | lerobot 0.4.4（v3.0）版本级拒绝 v2.1；0.3.3（最后 v2.1 线）构造失败于 `meta/episodes.jsonl` 缺官方必需键等 **9 类结构缺口**（探针逐层补齐后构造成功，证明解除路径可执行）；`verification_status` 未篡改，证据 + 原始日志落盘 `docs/testing/evidence/2026-11-23-lerobot-loader-verify.md` |

### FR-034 关键发现（影响后续）

官方 Loader 对 DDP 导出的 9 类结构缺口中，最深的两个：
1. **DDP 自有训练表占用了 `data/episodes.jsonl` 路径**，与 LeRobot v2.1 官方 `meta/episodes.jsonl`（要求 episode_index/length 键）同名冲突 —— 解除方案需把 DDP NDJSON 训练表迁出该路径（如 `ddp/episodes.jsonl`），属 V31-042 已有「保留既有资产」约束的补充动作；
2. **占位符命名不一致**（`{chunk}/{episode}` vs 官方 `{episode_chunk}/{episode_index}`）与 `chunks_size` 口径 —— writer 参数级修正。

这 9 项已全部列入 #86 证据报告，作为 V31-042 后续切片的输入。

## S2 Edge Agent 最小可用链 —— ⏸ 待 D2 批准

| FR | 内容 | 状态 |
|---|---|---|
| FR-001~008 | 配对/心跳/Lease/Preflight/Spool/Manifest/续传/回写 | ⏸ 未开工；**D2 选项与推荐（纯拉取）已列于 s0-baseline-and-d2.md §2，等用户批准后写 V31-010 ADR → 开工** |

## S3 真实传感器数据物化 —— 未开工（依赖 S2）

| FR | 内容 | 状态 |
|---|---|---|
| FR-009~015 | SensorStream/Observation/Action 物化、多流同步、容器格式、DATA_CLEANING、步骤级重试 | 未开工 |

## S4 AI 真实推理 —— BLOCKED（GPU/权重）

FR-021/022；FR-023 已 VERIFIED。

## S5 Dataset / Training 收口 —— 部分推进

| FR | 内容 | 状态 |
|---|---|---|
| FR-030 | Split 防泄漏按 Session/Subject/Scene 分组 | 未开工（P1） |
| FR-034/035 | Loader 实测 | **已实测：BLOCKED 维持**（见 S1）；解除路径 = 修复 9 类结构缺口后重测 |
| FR-036/037 | 训练真实连接器/回调 | BLOCKED（外部 API 未提供） |

## S6 Staging / Pilot / Release —— BLOCKED（staging 未提供）

FR-038/039/040/041/042/043/044/045。

---

## 交付记录（V4.1 阶段，2026-11-23）

| PR | 内容 |
|---|---|
| #85 | V4.1 蓝图包入库（18 文件 SHA256 校验 0 差异） |
| #86 | FR-034/035 Loader 实测证据 + 原始日志（9 类缺口 + 解除路径） |
| #88 | FR-016/017/018 三级语义 + OUTCOME_SPAN 服务端提交校验（322 行测试） |
| #87 | FR-026/027/028 QC 批量重跑/差异/重试 UI（46 例新增测试） |

全部 CI 五项全绿后合并；`check.sh` 口径未放宽。
