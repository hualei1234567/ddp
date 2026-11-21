# DDP V3.1 N0-01 Current System Baseline（基线清单）

- 采集日期：2026-11-22
- 采集方式：只读命令 + 代码检视 + 四条独立审计线交叉核对
- 事实基线：`origin/main` @ `46864a9`
- 上游文档：`docs/roadmap/v3.1/blueprint/`（V3.1 蓝图包，20 文件 SHA256 校验 0 差异）
- 本文件地位：**V3.1 的唯一事实入口**

## 1. 版本控制

| 项 | 值 | 证据 |
|---|---|---|
| 仓库 | `github.com/hualei1234567/ddp` | — |
| 默认分支 | `main` | — |
| 基线提交 | `46864a9ccdb8cbbe6554c948d4e1e70872aae953` | `git rev-parse origin/main` |
| 本次审计分支 | `docs/v31-n0-audit`（基于 `origin/main`） | `git checkout -b docs/v31-n0-audit origin/main` |
| 工作区 | 干净；本轮只新增 `docs/roadmap/v3.1/` | `git status --short` |
| 提交规范 | Conventional Commits | `CLAUDE.md` |

## 2. 契约与数据库

| 项 | 值 | 证据 |
|---|---|---|
| OpenAPI 版本 | `1.26.0` | `packages/contracts/openapi.yaml:4` |
| 契约路径总数 | 239 | `grep -cE '^  /api/v1/' packages/contracts/openapi.yaml` |
| Alembic head | `0068` | `cd backend && .venv/bin/alembic heads` |
| 最近迁移 | `0066_capture_sessions.py`、`0067_training_dry_run_status.py`、`0068_capture_session_artifacts.py` | `backend/migrations/versions/` |
| 迁移规范 | 新增可回滚 Alembic，不改历史迁移 | `CLAUDE.md` §四 |

V3.1 蓝图包中提到的 `packages/contracts` / `backend/migrations` 均**未**需要在此轮变更。

## 3. 质量门禁真实结果

```bash
DDP_ENV=local make check
```

执行结果（2026-11-22，基线 `46864a9`）：

```text
[1/8] 后端 ruff check + format   All checks passed! / 1025 files already formatted
[2/8] 后端 pytest                全部通过（含跳过项，见下）
[3/8] 前端 typecheck / ESLint / Vitest
[4/8] 前端 build
[5/8] Secret 扫描
[6/8] Agent Bash 守卫
[7/8] Outbox 事件归类
[8/8] 契约一致性              基线内已接受 62 项，无新增差异
全部检查通过
EXIT_CODE=0
```

注意边界：

- 后端 pytest 中存在 `s`（skip）项，包括需要外部真实文件的 HDF5 / ROS Bag 公开样例测试
  （`backend/tests/unit/test_hdf5_real_public_sample.py`、`test_rosbag_real_public_sample.py`）。
  **跳过不等于通过真实数据验收。**
- 契约一致性检查报告有 1 项基线差异已消失，可从 `parity-baseline.json` 移除（不影响通过）。

## 4. 规模与结构（静态计数）

| 层 | 位置 |
|---|---|
| API 适配 | `backend/app/api/v1/*.py`（含 `capture_session_routes.py`、`annotation*.py`、`processing_routes.py`） |
| 应用用例 | `backend/app/application/{devices,collection,ingestion,processing,datasources,episodes,annotation,datasets,modelops}` |
| 领域 | `backend/app/domain/*`（不依赖 Web / ORM / 队列 / 云 SDK） |
| 基础设施 | `backend/app/infrastructure/{db,repositories,storage,queue}` |
| Worker | `backend/app/workers/{processing,exports,...}.py` |
| 数据平台 | `data-platform/ddp_data/{adapters,canonical,hdf5,rosbag,mcap,lerobot}` |
| 前端 | `apps/web/src/{pages,components,services,routes.tsx}` |

文件规模约束：500 行触发评审，800 行原则拆分；函数 50 行有效逻辑。

## 5. 前端路由事实（节选，非“占位页”）

`apps/web/src/routes.tsx`：

- `collection/tasks`、`collection/capture-sessions`、`collection/capture-sessions/:sessionId`（`:154-171`）
- `episodes`、`episodes/:episodeId`、`assets`、`raw-assets/:assetId`（`:183-186`）
- `annotation-tasks`、`annotation-tasks/new`、`annotation-tasks/:taskId`（`:187-196`）
- `annotation/workbench/:assignmentId`（`:197-200`）
- `annotation/submissions/:submissionId`、`annotation/reviews`、`annotation/arbitrations`（`:201-209`）
- `annotation/schemas`、`annotation/sample-sets`（`:210-222`）
- `quality`、`quality/gold-sets`、`quality/iaa-reports`、`quality/quality-samples`、`quality/quality-checks`（`:227-246`）
- `datasets`、`datasets/:datasetId/versions/:versionId`、`exports`、`training-requests`、`training-deliveries`（`:293-310`）

旧 `/annotation/gold-sets` 等地址保留 301 重定向（`:247-277`）。

## 6. 已知阻塞与不可声明项

| 项 | 状态 | 说明 |
|---|---|---|
| staging / 生产部署 | `BLOCKED` | 需用户单独批准；本轮 `NOT RUN` |
| 真实机器人硬件在环 | `BLOCKED` | 无硬件；公开数据不能替代序列号 / 固件 / 标定 / 物理时钟验证 |
| 端侧采集 Agent | `MISSING` | `services/` 只有 `local-adapter`（训练/评测/推理适配器） |
| 向机器人下发控制命令 | 禁止 | 未经明确授权不得执行 |
| 真实 MinIO 全链验收 | `UNVERIFIED` | 相关 API 测试使用 `FakeMultipartStorage` |

## 7. 复现命令

```bash
git rev-parse origin/main
cd backend && .venv/bin/alembic heads && cd ..
grep -cE '^  /api/v1/' packages/contracts/openapi.yaml
grep -n 'version:' packages/contracts/openapi.yaml | head -2
python3 -I - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('docs/roadmap/v3.1/blueprint')
m = json.loads((root / 'PACKAGE_MANIFEST.json').read_text())
bad = [f['path'] for f in m['files']
       if hashlib.sha256((root / f['path']).read_bytes()).hexdigest() != f['sha256']]
print(f"checked={len(m['files'])} mismatched={bad}")
PY
DDP_ENV=local make check
```
