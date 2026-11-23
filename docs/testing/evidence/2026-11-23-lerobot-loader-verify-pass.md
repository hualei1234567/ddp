# LeRobot 官方 Loader 隔离环境复测通过报告（V31-042 切片 6 / FR-034/035，2026-11-23）

任务：V31-042 切片 6 —— 修复 DDP LeRobot v2.1 导出 9 类结构缺口，并在隔离环境用官方 `lerobot==0.3.3` 对真实 `build_export_artifacts` 产物完成双路端到端复测。
执行分支：`feat/v31-042-lerobot-fix`（基于 `main @ 5f901fa`）。
前置阻塞证据：[2026-11-23-lerobot-loader-verify.md](./2026-11-23-lerobot-loader-verify.md)（PR #86）。

## 1. 结论（判定）

**PASS（结构级验证通过，`BLOCKED` 解除条件全部满足）。**

- 原版产物经 `build_export_artifacts(format="LE_ROBOT")` 直接生成到 `/tmp/lerobot-dataset-v2/`，**零手工修补**，在隔离 venv `/tmp/lerobot-verify-venv-v21`（`lerobot==0.3.3`，`CODEBASE_VERSION=v2.1`）下：
  - 默认构造 `LeRobotDataset('local/ddp-export', root=Path('/tmp/lerobot-dataset-v2'), episodes=None)`：**PASS**
  - 显式构造 `LeRobotDataset('local/ddp-export', root=Path('/tmp/lerobot-dataset-v2'), episodes=[0, 1, 2])`：**PASS**
  - `ds.meta.total_episodes == 3`、`ds.meta.total_tasks == 1`、`ds.num_frames == 3`、`episode_index == [0, 1, 2]`、`timestamp == [0.0, 0.0, 0.0]`、`episode_data_index == {'from': [0, 1, 2], 'to': [1, 2, 3]}` 全部校验通过。
- 实测通过后，按契约与 §8 解除条件将 `LE_ROBOT` 产物（`meta/info.json`、`ddp/sidecar.json`、`artifact_manifest`）置为 `verification_status="VERIFIED_BY_LOADER"` 并写入 `ddp.loader_probe`（`CANONICAL` 保持 `UNVERIFIED`）。
- **诚实边界不变**：`loadable_scope="METADATA_ONLY"`、`frame_level_mapping=false`、`frame_level_read="NOT_CLAIMED_METADATA_ONLY"`、`fps_source="NOMINAL_METADATA_ONLY"`（`fps=1`、每 Episode `length=1` 对应 parquet 的 1 条元数据行，不伪造逐帧观测/动作或真实视频帧率）。

## 2. 9 类结构缺口修复对照（逐项对齐 PR #86 §5 & §8）

| # | PR #86 缺口 | 修复实现 | 修改文件 |
| --- | --- | --- | --- |
| 1 | `meta/episodes.jsonl` 缺 `episode_index`（与 DDP 自有溯源表冲突） | `write_lerobot_metadata` 产出符合官方 v2.1 schema 的 `meta/episodes.jsonl`（含 `episode_index`、`tasks`、`length`）；DDP 自有 Episode 溯源表迁至 `meta/ddp_episodes.jsonl` | `data-platform/ddp_data/lerobot/export_writer.py`, `backend/app/domain/datasets/exporting.py` |
| 2 | 缺 `meta/episodes_stats.jsonl` | `write_lerobot_metadata` 新增 `_build_episodes_stats` 产出逐 Episode 全数组统计（`min/max/mean/std/count`） | `data-platform/ddp_data/lerobot/export_writer.py` |
| 3 | `meta/stats.json` 含非法嵌套 `per_split` 且缺 `std` | 重构 `_build_stats` 仅输出官方合法数组统计键（含 `std`），移除 `per_split`；`per_split` 信息保留于 `meta/ddp_divisions.json` 与 `ddp/sidecar.json` | `data-platform/ddp_data/lerobot/export_writer.py`, `backend/app/domain/datasets/exporting.py` |
| 4 | `info.json` 路径占位符 `{chunk}/{episode}` 不符官方 | 改为官方 `{episode_chunk:03d}/{episode_index:06d}`（`data_path` 与 `video_path` 同步修正） | `backend/app/domain/datasets/exporting.py` |
| 5 | `chunks_size=1` 与 writer `EPISODES_PER_CHUNK=1000` 不一致 | `info.json` 直接引用 `EPISODES_PER_CHUNK`（`=1000`），消除双处漂移 | `backend/app/domain/datasets/exporting.py` |
| 6 | `meta/episodes.jsonl` 缺 `length` | 每行显式写入 `length=1`（与对应 episode parquet 的实际 1 行元数据记录严格一致） | `data-platform/ddp_data/lerobot/export_writer.py` |
| 7 | `data/` 目录混入非 parquet 文件 `data/episodes.jsonl` | DDP 训练表 `EPISODE_DATA_PATH` 迁至 `ddp/episodes.jsonl`，净化 `data/` 目录仅留 `.parquet`，确保 `episodes=None` 默认加载通过 | `backend/app/domain/datasets/exporting.py` |
| 8 | Parquet 与 `info.features` 缺 `timestamp` 列 | `EPISODE_COLUMNS` 与 `info.features` 补齐 `timestamp`（`float32`，值 `0.0`），同时修正 `task_index` 非空填充 | `data-platform/ddp_data/lerobot/export_writer.py`, `backend/app/domain/datasets/exporting.py` |
| 9 | `fps=0` 与 `length=0` 违反 Loader 数学不变量 | 采用 `NOMINAL_METADATA_FPS=1` 与每 Episode `length=1`（对应 1 条元数据行），并在 `info.json["ddp"]` 与 `ddp/sidecar.json` 显式声明 `fps_source="NOMINAL_METADATA_ONLY"`、`loadable_scope="METADATA_ONLY"` | `data-platform/ddp_data/lerobot/export_writer.py`, `backend/app/domain/datasets/exporting.py` |

## 3. 隔离环境与版本锁定

| 项 | 值 |
| --- | --- |
| 日期 | 2026-11-23（Asia/Shanghai） |
| 后端导出环境 | `/Users/hualei/vscodeworkspace/DataPlatform/backend/.venv/bin/python`（未安装 `lerobot`，未污染） |
| 官方 Loader 隔离 venv | `/tmp/lerobot-verify-venv-v21/bin/python`（`lerobot==0.3.3`，`CODEBASE_VERSION=v2.1`） |
| 产物目录 | `/tmp/lerobot-dataset-v2/`（由 worktree 代码 `build_export_artifacts` 真实生成，13 个文件，零手工修改） |

## 4. 原始复测命令与完整输出

### 4.1 产物生成与官方 `lerobot==0.3.3` 双路加载实测

```text
=== EXPORT GENERATION ===
artifact_count: 13
aggregate_sha256: sha256:cc93af9bf41e02254b627660dda353156aa3cddf5f8929cce77b997cd52879f4
manifest.verification_status: VERIFIED_BY_LOADER
  data/chunk-000/episode_000000.parquet (1133 B) sha256:e22293426264ee3f456e9e94774234e863344ddd594d6dceaa2c7074c3cbe003
  data/chunk-000/episode_000001.parquet (1158 B) sha256:0e99ad2b5f8ec391d1f523c120fb0523ae75291472dc35fda59714a18db9fa60
  data/chunk-000/episode_000002.parquet (1128 B) sha256:2352e9594d9e20711e7fde48b54293cc38307e6812c306b9d910aea2530bd498
  ddp/episodes.jsonl (739 B) sha256:c29709a4fd05b358116232a2e67504675dc70780599806d2361cdd7373bae56e
  ddp/sidecar.json (2095 B) sha256:eaa439cd0a60b079db870c094e34ecb11c51f0bd48c306cc3b5fb5d69e897f49
  meta/ddp_divisions.json (391 B) sha256:bbb1d279ebdf95c076f12ae15d005ea25aef575ba557bfb7ba55201c3f9ded3f
  meta/ddp_episodes.jsonl (1627 B) sha256:fc948c3f80b05efa86c73d84c8bedb702d4b4452e6a64555b1f08e92c0eefb8a
  meta/episodes.jsonl (207 B) sha256:d99f21fc7dcb8bbf4ed369bff23dcf839ca99102fbb622c5e74366e370105f0b
  meta/episodes_stats.jsonl (960 B) sha256:591c319f3c440d45fb019b85c308ad33af7fac33610c3ec37d116a10de5c6f73
  meta/info.json (1712 B) sha256:f0804bd9fcca206b9f1e1d1b1f5019056a3505b18e79c473893b509e419ea728
  meta/media.jsonl (0 B) sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
  meta/stats.json (297 B) sha256:4c268f46a311c3a9e300aa286552fd3ba35f3d68be1c266b12472aeeb4c82e1c
  meta/tasks.jsonl (52 B) sha256:deacff1281a3cdb1f75b393d5e8a7458f409f3f0299d1a9c22d653425f147fa0
=== OFFICIAL LEROBOT LOADER VERIFY ===
lerobot_version: 0.3.3
--- DEFAULT (episodes=None) ---
Generating train split: 3 examples [00:00, 311.44 examples/s]
  type: LeRobotDataset
  codebase_version: v2.1
  fps: 1
  chunks_size: 1000
  total_episodes: 3
  total_tasks: 1
  num_frames: 3
  verification_status: VERIFIED_BY_LOADER
  loadable_scope: METADATA_ONLY
  ddp: {"episode_data_path": "ddp/episodes.jsonl", "episodes_metadata_path": "meta/ddp_episodes.jsonl", "fps_source": "NOMINAL_METADATA_ONLY", "loadable_scope": "METADATA_ONLY", "loader_probe": {"codebase_version": "v2.1", "evidence_ref": "docs/testing/evidence/2026-11-23-lerobot-loader-verify-pass.md", "frame_level_read": "NOT_CLAIMED_METADATA_ONLY", "lerobot_version": "0.3.3", "loadable_scope": "METADATA_ONLY"}, "nominal_fps": 1, "verification_status": "VERIFIED_BY_LOADER"}
  episode_index col: [0, 1, 2]
  timestamp col: [0.0, 0.0, 0.0]
  episode_data_index: {'from': [0, 1, 2], 'to': [1, 2, 3]}
--- EXPLICIT (episodes=[0,1,2]) ---
Generating train split: 3 examples [00:00, 1014.59 examples/s]
  type: LeRobotDataset
  codebase_version: v2.1
  fps: 1
  chunks_size: 1000
  total_episodes: 3
  total_tasks: 1
  num_frames: 3
  verification_status: VERIFIED_BY_LOADER
  loadable_scope: METADATA_ONLY
  ddp: {"episode_data_path": "ddp/episodes.jsonl", "episodes_metadata_path": "meta/ddp_episodes.jsonl", "fps_source": "NOMINAL_METADATA_ONLY", "loadable_scope": "METADATA_ONLY", "loader_probe": {"codebase_version": "v2.1", "evidence_ref": "docs/testing/evidence/2026-11-23-lerobot-loader-verify-pass.md", "frame_level_read": "NOT_CLAIMED_METADATA_ONLY", "lerobot_version": "0.3.3", "loadable_scope": "METADATA_ONLY"}, "nominal_fps": 1, "verification_status": "VERIFIED_BY_LOADER"}
  episode_index col: [0, 1, 2]
  timestamp col: [0.0, 0.0, 0.0]
  episode_data_index: {'from': [0, 1, 2], 'to': [1, 2, 3]}
RESULT: LOADER_VERIFY_PASS
```

### 4.2 自动化测试与静态检查

- `pytest`（覆盖导出领域、writer、Canonical、API、冲突与 Worker 全部 8 个测试文件）：**51 passed**
- `ruff check`（全部 6 个修改的 Python 文件）：**All checks passed!**

## 5. 诚实边界与未验证范围

- **逐帧读取未宣称（`NOT_CLAIMED_METADATA_ONLY`）**：`loadable_scope="METADATA_ONLY"`，产物不含 `observation.*` / `action` 逐帧列与 `videos/` 目录，不可直接用于端到端策略训练（逐帧数据链路属 V31-044）。
- **版本边界**：本复测锁定官方 v2.1 兼容线 `lerobot==0.3.3`；`lerobot>=0.4.x` 已切换为 `v3.0` 格式并显式拒绝 `v2.1` 数据集。
