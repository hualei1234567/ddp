# V51 Current Baseline（S0 只读基线复核）

- 复核时间：2026-11-24 20:00–20:20（Asia/Shanghai）
- 复核方式：**只读**。未修改任何业务代码；本目录首个提交只含蓝图入库与 S0 文档
- 证据等级：E1 = 本次实跑；E2 = 代码核实；E3 = 测试声明；E4 = 文档

> **重要前提**：V5.1 蓝图以 `c71c72a` 为审计基线。复核时仓库 HEAD 已推进到 **里程碑 P1 收口后**，比蓝图基线新 8 个提交。差异已在 §6 逐项列出。

---

## 1. 仓库与版本事实（E1）

| 项 | 值 | 采集命令 |
|---|---|---|
| HEAD | **`f92e83a`** `Merge pull request #109 from ...docs/p1-closure-board` | `git log -n 1` |
| 分支 | `main`，与 `origin/main` 一致 | `git status -sb` |
| 工作区 | **干净**（无未提交改动） | `git status` |
| 工作树 | 1 个（无残留 worktree） | `git worktree list` |
| 本地分支 | 仅 `main` | `git branch` |
| remote | `github.com/hualei1234567/ddp` | `git remote -v` |
| 开放 PR（非 dependabot） | **0** | `gh pr list` |

## 2. 数据库与迁移（E1）

| 项 | 值 |
|---|---|
| Alembic 文件数 | 66 |
| Alembic heads | **0072 (head)**，单头无分叉 |
| 真实 DB current | **0072 (head)** —— 与代码一致 |
| 真实 PG 表数 | **96** |

**真实数据规模（本地栈）**：

| 表 | 行数 | 说明 |
|---|---|---|
| projects | 48 | |
| users | 126 | |
| robots / devices / sensors | 56 / 56 / 49 | |
| capture_sessions | 7 | 真实采集会话很少 |
| upload_sessions | 33 | |
| raw_assets | **283** | 格式：MP4 269 / JSON 9 / MCAP 5 |
| episodes | **206** | |
| episode_raw_assets | 524 | |
| processing_runs | **4** | FAILED 2 / CANCELLED 1 / SUCCEEDED 1 |
| processing_run_steps | 14 | 见 §4 |
| **processing_artifacts** | **0** | ⚠️ **零物化产物** |
| dataset_versions | 87 | |
| annotation_submissions | 160 | |
| quality_runs | 1 | |
| edge_agents | 2 | |

## 3. 契约与测试（E1）

| 项 | 值 | 对比蓝图基线 |
|---|---|---|
| OpenAPI 版本 | **1.34.0** | 1.33.0 → +1 |
| 契约路径数 | **257** | 256 → +1（FR-020 容量预览） |
| Schema 数 | 581 | — |
| Fragment 文件 | 46 | — |
| 后端测试文件 | **341** | 337 → +4 |
| 后端用例数 | **2953** | 2921 → +32 |
| scripts 测试用例 | 21 | — |
| 前端测试文件 | **155** | 149 → +6 |
| E2E spec | 4（6 用例） | 不变 |
| ADR | 47 | 不变 |
| 风险登记册 | 64 项（P1 24 / P2 40） | 不变 |

## 4. 质量门禁（E1 实跑）

```
$ bash scripts/check.sh
[1/9] 后端 ruff check + format        → All checks passed
[2/9] 后端 pytest                     → 通过（2953 用例）
[3/9] 前端类型/ESLint/单元测试         → 通过
[4/9] 前端构建                        → 成功
[5/9] Secret 扫描                     → 通过
[6/9] Agent Bash 守卫测试             → 通过
[7/9] outbox 事件归类自检             → 通过
[8/9] 契约一致性                      → 无新增差异（基线内已接受 63 项）
[9/9] Canonical JSON Schema 同步      → 6 个文件已同步
exit=0  「全部检查通过」
```

CI 五项（`.github/workflows/ci.yml`）：后端 lint/迁移/测试、前端类型/lint/测试/构建、前端关键路径 E2E、契约 OpenAPI lint/兼容性 diff、Secret/依赖/Agent 守卫。

## 5. 真实处理链路状态（E1，**本报告最关键的事实**）

### 5.1 ProcessingRun 阶段执行真实分布

| 阶段 | 状态 | 次数 |
|---|---|---|
| FILE_VALIDATION | **FAILED** | 6 |
| FILE_VALIDATION | SUCCEEDED | 1 |
| METADATA_EXTRACTION | SKIPPED | 1 |
| FORMAT_PARSING | SKIPPED | 1 |
| TIME_ALIGNMENT | SKIPPED | 1 |
| CALIBRATION_VALIDATION | SKIPPED | 1 |
| EPISODE_SEGMENTATION | SKIPPED | 1 |
| DATA_CLEANING | SKIPPED | 1 |
| QUALITY_PRE_CHECK | SKIPPED | 1 |

### 5.2 失败原因（经核实为**正确行为**而非缺陷）

`FILE_VALIDATION` 6 次失败错误码均为 `input_ref_not_project_scoped`（「输入引用必须属于当前项目」）。

经代码核实（`backend/app/application/processing/stages.py:137-146`）：这是**故意的跨项目隔离**——不校验的话任何成员可用猜到的 key 探测其他项目对象是否存在及大小（`object_missing` 与「合计 N 字节」是两个可观测侧信道）。

结论：失败是**历史数据引用失效**（运行创建时的 input_refs 指向的对象已不在当前项目或已被清理），不是代码缺陷。

### 5.3 结论：**数据物化能力零落地**

- `processing_artifacts` = **0 行** —— 从未产出任何物化产物
- 所有物化阶段（METADATA_EXTRACTION / FORMAT_PARSING / TIME_ALIGNMENT / EPISODE_SEGMENTATION）**全部 SKIPPED**
- 206 个 Episode 与 283 个 RawAsset 之间**没有经过真实解析链路**

这正是 V5.1 蓝图痛点一的实证：「平台有 Episode，但 Episode 还不是训练样本」。

## 6. 与蓝图基线（`c71c72a`）的差异（本复核最重要的一节）

蓝图包冻结在 P1 收口**之前**。以下 5 项状态已变化，V5.1 不得再按旧状态开工：

| V5.1 FR | 蓝图基线 | **当前实际** | 变化来源 |
|---|---|---|---|
| V51-008（FR-019 标注冲突恢复） | PARTIAL | ✅ **已 VERIFIED** | PR #107：409 双版本 + 复制草稿 + 逐项人工合并 + 刷新不丢输入，50 测试 |
| V51-009（FR-020 分配负载提示） | PARTIAL | ✅ **已 VERIFIED** | PR #104：只读预览端点 + 抽屉实时展示，39 测试，契约 1.34.0 |
| V51-014/015/016（FR-043 恢复演练） | PARTIAL | ✅ **已 VERIFIED** | PR #103：PG/MinIO/Spool 三处 + RPO/RTO 量化 + Runbook，11 结构性校验 |
| V51-012（FR-040 Lineage E2E） | PARTIAL | 🟡 **仍 PARTIAL，但性质改变** | PR #108：真实栈 E2E 8 段中 7 段 PASS；Dataset→Session 段因真实库无数据 UNVERIFIABLE；修复 3 个真缺陷 |
| V51-010（FR-025 QC 证据区间可视化） | PARTIAL | 🟡 **仍 PARTIAL** | 未变 |

**影响**：M1（治理缺口）中 3 项已完成、1 项部分完成。V5.1 的 M1 剩余工作仅为 V51-013（RBAC 浏览器 E2E）、V51-017（Frozen Dataset 引用保护）、V51-010（QC 证据定位）。

## 7. 环境与工具链（E1）

| 项 | 值 |
|---|---|
| 本地栈 | docker compose：`ddp-postgres`（55432，healthy）、`ddp-minio`（9000，healthy） |
| 后端 venv | `backend/.venv`（backend 未安装 `lerobot`，架构正确） |
| LeRobot 隔离环境 | `/tmp/lerobot-verify-venv-v21`，`lerobot==0.3.3`（可用） |
| 真实样本环境变量 | **无 `DDP_TEST_*`** —— 真实公开样本当前均不可用 |
| 数据平台依赖 | `data-platform`（含 h5py 3.16.0、rosbags、pyarrow；backend 不直接引 pyarrow） |

## 8. 基线结论

1. **工程质量健康**：`check.sh` 9 步全过，2953 后端用例，契约 1.34.0 单头迁移 0072，工作区干净。
2. **治理层已基本完备**：P1 四项中三项已闭环（冲突恢复、负载提示、恢复演练），Lineage E2E 亦已跑通 7/8 段。
3. **数据物化层零落地**：`processing_artifacts = 0`，全部物化阶段 SKIPPED，这是 V5.1 的全部价值所在。
4. **真实样本不可用**：仓库仅有字节级合成容器头，4 份真实公开样本只登记了事实（SHA256/结构）而无文件本体。**这是 V5.1 唯一硬阻塞**。
