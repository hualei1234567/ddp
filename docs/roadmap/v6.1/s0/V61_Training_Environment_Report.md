# V61_Training_Environment_Report — 训练环境盘点（S0-C 实测）

## 1. 结论先行

**GATE-B（真实训练 Smoke Test）：可行（GO）** —— 本机即可执行，无需 GPU、无需外部资源。

## 2. 算力与环境

| 项 | 实测值 |
|---|---|
| 芯片 | Apple M5（Metal 4），10 核，16 GB |
| CUDA | 无 |
| **MPS** | ✅ torch 原生支持（`lerobot` 训练工具 `utils/utils.py:50-52` 自动 `mps.is_available()` 选路） |
| torch | 各现有 venv 均未安装（需新建独立训练 venv） |
| lerobot | backend venv 无；PyPI `lerobot==0.3.3` 可装 |

## 3. 目标训练脚本（S0-C wheel 解包实证）

`lerobot==0.3.3` wheel **自带完整训练入口**：

- console script：`lerobot-train = lerobot.scripts.train:main`
- 含：完整训练循环、checkpoint 保存/加载、AMP、logging
- 设备选择：cpu / cuda / **mps** 原生支持
- 数据集：可直接指向本地 LeRobot v2.1 数据集目录

**依赖代价（GATE-B 准备步骤）**：独立 venv 装 `torch>=2.2.1,<2.8.0 + torchvision + lerobot==0.3.3 + datasets<=3.6.0`，darwin-arm64 **免 torchcodec**（无 CUDA blob），预估磁盘 1.5–2.5 GB。

## 4. 推荐首跑路径（最小闭环）

```
数据：RoboMimic ph（59 帧/20Hz，已有 LOADER_FRAME E1）
      经现有 frame_writer 转 LeRobot v2.1 本地数据集
命令：lerobot-train --dataset.repo_id=本地路径 \
      --policy.type=act --steps=2 --device=mps --batch_size=2
验收：真实 Batch 读取 → 前向/Loss 有效 → 2 个 step → checkpoint 保存+重新加载
```

备选（视频版联动 M2）：pusht G1+G2 数据集直接被 lerobot 消费（其原生格式）→ 同时验证 GATE-A video 分支。**建议两条都跑**：先 ph（复用已验收样本，变量最少），后 pusht（视频联动）。

## 5. Training Evidence 结构缺口（S0-A/D 交叉确认）

| 证据项 | 现状 | 差距 |
|---|---|---|
| dataset_version | ✅ `external_training_references` 有列 | — |
| code_version | ⚠️ 可空 `code_version`(:356) | 非强制 |
| config hash | ⚠️ 仅 `revision_sha256`(:134) + 8KiB 脱敏 `config_summary`(:354) | 非训练时实际 config hash |
| **seed** | ❌ 无字段 | 缺 |
| **checkpoint 路径/Hash** | ❌ 无字段 | 缺 |
| **逐步 metrics** | ❌ 无落库（metrics 只在 evaluations） | 缺 |
| runtime/device | ⚠️ execution_mode 声明级 | 非实测值 |

**归因**：ADR-0036 有意设计——迁移 0063 docstring 明示「引用行本身就是 run 记录」，训练执行委托外部平台。

**建议方案（供用户确认）**：
- **方案 A（推荐）**：新增 `training_run_evidence` 扩展表（迁移 0087 或 0088）：run 引用 external_training_reference_id、seed、device、runtime_versions JSON、config_sha256、checkpoint_ref + checkpoint_sha256、steps、metrics_summary JSON、stdout_tail、exit_code。不动 ADR-0036 主语义，只补「本机自跑训练」的证据结构。
- 方案 B（最小）：GATE-B 先以 evidence 文档归档（`docs/testing/evidence/`），不建表——可验收但不可查询/关联 Lineage。

## 6. 与 Badcase 闭环的衔接

闭环链路**后端已全通**（S0-A 实证）：TrainingRequest → ExternalTrainingReference → EvaluationRow(metrics/failure_cases) → ModelFeedbackRow → DataRequirement(source_evaluation_ids) → link-retest → close 门禁。
GATE-B 落地后唯一新增接线点：`training_run_evidence` → evaluations（把本机训练产生的评测写入现有 `evaluations` 表），闭环即可在**本机自跑训练**场景真实走通一次 E2E。
