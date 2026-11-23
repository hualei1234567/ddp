# LeRobot 官方 Loader 隔离环境实测报告（FR-034/035，2026-11-23）

任务：V4.1 FR-034/035 —— LeRobot 官方 Loader 隔离环境实测（V31-042 切片 5）。
执行分支：`feat/v41-loader-verify`（基于 origin/main @ b4f965b）。

## 1. 结论（判定）

**BLOCKED 维持。原版交付产物无法被官方 LeRobotDataset 构造加载，不置 `VERIFIED_BY_LOADER`。**

- `verification_status` 保持 `UNVERIFIED` 未动（本次**未改任何代码**，符合任务红线「若步骤 3 失败，不改任何代码，只出证据报告」）。
- 原版产物在官方 Loader 下的**首个阻断点**：`meta/episodes.jsonl` 缺官方必需键 `episode_index`（`KeyError`，见 §4）。
- 通过 /tmp 诊断性探针（仅改 /tmp 副本，**未改交付产物**）逐层补齐 **9 类结构缺口**后构造成功——缺口清单完整已知，修复路径明确（见 §5/§6）。
- `loadable_scope=METADATA_ONLY` 口径下「逐帧读取」无论原版还是探针均不可用（无逐帧数据），本次实测为**结构级**验证且结构级也**未通过**。

## 2. 环境

| 项 | 值 |
| --- | --- |
| 日期 | 2026-11-23（ Asia/Shanghai） |
| 隔离 venv | `/tmp/lerobot-verify-venv`（最新版）与 `/tmp/lerobot-verify-venv-v21`（v2.1 兼容线），均独立于 `backend/.venv`，未污染 |
| Python | 3.11（venv 基于系统 python3；backend/.venv 为 3.11.17） |
| pip 网络 | 直连可达，**未用代理**，`pip install lerobot` 全量依赖（含 torch）一次成功 |
| lerobot 版本 | `0.4.4`（最新，`CODEBASE_VERSION=v3.0`）与 `0.3.3`（`CODEBASE_VERSION=v2.1`）双版本实测 |
| 关键依赖 | pyarrow 26.0.1 / pandas 3.1.1 / datasets 3.6.0 / huggingface-hub（隔离 venv 内） |
| 仓库基线 | origin/main @ b4f965b（`Merge pull request #84`） |

安装命令（实际执行）：

```bash
cd /tmp && python3 -m venv lerobot-verify-venv
lerobot-verify-venv/bin/pip install lerobot          # → 0.4.4，成功
python3 -m venv lerobot-verify-venv-v21
lerobot-verify-venv-v21/bin/pip install "lerobot==0.3.3"  # → 0.3.3，成功
```

**版本锁定事实**：lerobot 0.4.4 已把 `CODEBASE_VERSION` 升到 `v3.0` 并**显式拒绝 v2.1 数据集**（`BackwardCompatibilityError`，提示需先跑 `convert_dataset_v21_to_v30`）。官方最后支持 v2.1 的发布线是 0.3.x（实测 0.3.3 的 `CODEBASE_VERSION == "v2.1"`）。后续实测/锁定应使用 `lerobot==0.3.3`。

## 3. 产物生成（步骤 2，真实导出链路）

用仓库既有测试工厂（`backend/tests/unit/test_export_domain.py` 的 `make_job/make_version/make_item`）经 `build_export_artifacts`（LE_ROBOT）产出 3 个 Episode 到 `/tmp/lerobot-dataset/`，并落盘 aggregate 与 manifest 备查：

- 脚本：`/tmp/gen_lerobot_export.py`（PYTHONPATH=backend:data-platform，backend/.venv 执行，worktree 代码）
- 产物：11 个文件，结构与蓝图一致（`meta/info.json`、`meta/tasks.jsonl`、`meta/stats.json`、`data/chunk-000/episode_00000{0,1,2}.parquet` + DDP 自有文件）
- `aggregate_sha256 = sha256:c92b30f4a2cbe17ebd3d05397adfc797a3e419e1e8ce7d3401814393fcd93ff3`（manifest 全文见 `/tmp/lerobot-manifest.json`）
- 原版 `info.json` 关键值：`codebase_version=v2.1`、`fps=0`、`chunks_size=1`、`total_episodes=3`、`total_frames=0`、`data_path=data/chunk-{chunk:03d}/episode_{episode:06d}.parquet`、features 只含 episode 级 4 列

## 4. 加载实测（步骤 3，原版产物 = 交付事实）

加载脚本：`/tmp/load_lerobot_verify.py`（隔离 venv 执行，`root` 用环境变量指向产物目录）。

### 4.1 原版产物 × lerobot 0.3.3（官方 v2.1 兼容线）

| 构造方式 | 结果 |
| --- | --- |
| 默认（`episodes=None`） | **失败**：`KeyError: 'episode_index'`（`load_episodes` 要求 `meta/episodes.jsonl` 每行有该键） |
| 显式 `episodes=[0,1,2]` | **失败**：同一 `KeyError: 'episode_index'`（metadata 阶段即失败，与 episodes 参数无关） |

错误栈（关键帧，完整日志 `/tmp/evidence-original-default.log`）：

```text
File ".../lerobot/datasets/lerobot_dataset.py", line 107, in load_metadata
    self.episodes = load_episodes(self.root)
File ".../lerobot/datasets/utils.py", line 221, in load_episodes
    return {item["episode_index"]: item for item in sorted(episodes, key=lambda x: x["episode_index"])}
KeyError: 'episode_index'
RESULT: CONSTRUCT_FAILED
```

原因：DDP 自有训练表 `meta/episodes.jsonl`（键：episode_id/episode_version/episode_lineage_id/split/sample_role/weight/manifest_sha256/…）与 LeRobot v2.1 官方要求的同名文件（键：episode_index/tasks/length 等）**路径冲突且 schema 不兼容**。

### 4.2 原版产物 × lerobot 0.4.4（最新版）

**失败**：`BackwardCompatibilityError` —— "The dataset you requested (local/ddp-export) is in 2.1 format. We introduced a new format since v3.0 which is not backward compatible with v2.1."（0.4.4 `CODEBASE_VERSION=v3.0`，见 §2）

## 5. 缺口清单（探针逐层确认，按加载器触达顺序）

探针副本 `/tmp/lerobot-dataset-probe/`（仅 /tmp，不改交付产物）逐层修补，每补一层即暴露下一层，直至构造成功。**全部 9 类缺口均为原版产物的真实结构事实**：

1. **`meta/episodes.jsonl` 缺 `episode_index`**（首个阻断，§4.1）：官方按该键索引 episode；DDP 行用自有键，无该键。
2. **缺 `meta/episodes_stats.jsonl`**：v2.1（`codebase_version>=v2.1` 分支）要求逐 episode stats 文件，DDP 只有聚合 `meta/stats.json`。
3. **`meta/stats.json` 含非法嵌套 `per_split`**：官方要求 stats 值全为 numpy 数组（`ValueError: ... key 'TEST' of feature 'per_split' is of type '<class dict>'`）；DDP 的 per_split 是嵌套 dict。另缺 `std`（v2.1 聚合需要 count/mean/std/min/max，`KeyError: 'std'`）。
4. **路径占位符名不符**：DDP 用 `{chunk:03d}/{episode:06d}`，官方模板是 `{episode_chunk:03d}/{episode_index:06d}`（`KeyError: 'chunk'`）。
5. **`chunks_size=1` 语义错误**：官方语义是「每 chunk 的 episode 容量」（`DEFAULT_CHUNK_SIZE=1000`，`get_episode_chunk = ep_index // chunks_size`）；写 1 导致 episode 1/2 被算到 chunk-001/002 → 数据文件存在性断言失败。writer 的 `EPISODES_PER_CHUNK=1000` 与 info 的 `chunks_size=1` 不一致（真实缺陷）。
6. **`episodes.jsonl` 行缺 `length`**：`get_episode_data_index` 按行内 `length` 计算帧区间（`KeyError: 'length'`）。
7. **`data/` 目录混入非 parquet 文件**：DDP 自有 `data/episodes.jsonl` 与 LeRobot 数据文件同目录，默认构造（`episodes=None`）全目录扫描时报 `Parquet magic bytes not found`。显式传 `episodes=[0,1,2]` 可绕过（官方支持按 episode 逐文件加载）。
8. **parquet 缺 `timestamp` 列**：0.3.3 构造函数**无条件**读取该列并做 `check_timestamps_sync`（`KeyError: "Column timestamp not in the dataset"`）。
9. **`fps=0` 与 `length=0` 无效**：`fps=0` → `ZeroDivisionError`（`1.0/fps`）；`length=0` → episode 区间为空，同步检查抛 `ValueError: timestamps unexpectedly violate the tolerance`（官方语义每 Episode ≥1 帧、fps>0）。DDP 现口径「无真实帧率事实写 fps=0、length 按缺失写 0」与官方加载器数学上不兼容。

## 6. 探针成功证据（补齐后可加载 → 解除条件可验证）

补齐上述 9 类缺口后的探针副本（episode_index=0..2、length=1、episodes_stats.jsonl、stats 仅数组键含 std、官方占位符、chunks_size=1000、timestamp 列、fps=25）在 lerobot 0.3.3 下：

```text
构造成功 (type): LeRobotDataset
ds.meta.info: {…完整 info…}                    # 含 codebase_version=v2.1、total_episodes=3 等
ds.meta.total_episodes: 3
ds.meta.total_tasks: 1
ds.num_frames: 3
data parquet episode_index 列: [0, 1, 2]        # episode 索引可读
逐帧读取探测: ds[0] 失败（'NoneType' object has no attribute 'item'）—— 符合 METADATA_ONLY 边界预期
RESULT: LOADER_VERIFY_OK
```

即：**缺口清单完备且修复后官方 Loader 可构造、可读元数据与 episode 索引**。逐帧读取在探针条件下亦不可用（无逐帧数据），与 `loadable_scope=METADATA_ONLY` 声明一致——如实记录「逐帧读取未测试（无逐帧数据）」。

## 7. 红线遵守声明

- `backend/app/domain/datasets/exporting.py` **未做任何修改**；`verification_status` 仍为 `UNVERIFIED`，未新增 `loader_probe` 字段（该动作按任务约定仅限步骤 3 成功分支）。
- `backend/tests/unit/test_lerobot_export_writer.py` 未修改；没有任何断言被放宽。
- task-dag `docs/roadmap/v3.1/n0/task-dag.md` 的 V31-042 行未修改（任务约定失败分支不改代码、只出证据报告）。建议后续把该行「切片 5（Loader 实测）未做」更新为「切片 5 已实测（2026-11-23，见本文档）：原版产物构造失败（episodes.jsonl 缺 episode_index 等 9 类缺口，探针补齐后可加载），BLOCKED 维持」。
- 探针修改仅存在于 `/tmp/lerobot-dataset-probe/`，交付产物 `/tmp/lerobot-dataset/` 与仓库代码零改动；未在任何不可达条件下伪造「通过」。

## 8. BLOCKED 解除条件（可执行清单）

在 `exporting.py`/`export_writer.py` 补齐以下项并重跑本实测（lerobot==0.3.3），构造+元数据读取成功即可置结构级 `VERIFIED_BY_LOADER`（并加 `ddp.loader_probe` 字段记录实测版本与范围）：

1. LeRobot 官方键写入 `meta/episodes.jsonl`（每行 `episode_index` + `length`；DDP 自有键挪入独立文件如 `meta/ddp_episodes.jsonl`，避免路径冲突）；
2. 新增 `meta/episodes_stats.jsonl`（逐 episode、全数组键 stats，含 std；v2.1 必需）；
3. `meta/stats.json` 重构为数组键 stats（移除嵌套 `per_split` 或挪入 sidecar/divisions）；
4. `info.json` 占位符改为官方 `{episode_chunk:03d}/{episode_index:06d}`；`chunks_size` 与 writer `EPISODES_PER_CHUNK` 对齐（=1000）；
5. `data/` 目录只放 parquet（DDP 自有 `data/episodes.jsonl` 改名/挪位），保证默认构造（`episodes=None`）也可加载；
6. parquet 增加官方必需的 `timestamp` 列、`info.features` 同步声明；
7. 帧率事实落地前，`fps` 与每 Episode 帧数不能写 0（官方要求 fps>0、每 episode ≥1 帧）——需要引入真实帧率/帧数事实或调整声明口径；
8. 版本锁定：`lerobot==0.3.3`（0.4.x 为 v3.0 格式，拒绝 v2.1）。

## 9. 附录：产物文件清单与 Hash

```text
aggregate_sha256: sha256:c92b30f4a2cbe17ebd3d05397adfc797a3e419e1e8ce7d3401814393fcd93ff3
artifact_count: 11
data/chunk-000/episode_000000.parquet  (845 B)
data/chunk-000/episode_000001.parquet  (870 B)
data/chunk-000/episode_000002.parquet  (840 B)
data/episodes.jsonl                    (739 B)
ddp/sidecar.json                       (1685 B)
meta/ddp_divisions.json                (271 B)
meta/episodes.jsonl                    (1627 B)
meta/info.json                         (1184 B)
meta/media.jsonl                       (0 B)
meta/stats.json                        (232 B)
meta/tasks.jsonl                       (52 B)
```

完整命令输出、错误栈与探针日志：`/tmp/evidence-original-default.log`、`/tmp/lerobot-manifest.json`、`/tmp/gen_lerobot_export.py`、`/tmp/load_lerobot_verify.py`（会话临时目录，随本报告归档要点已全部摘录）。
