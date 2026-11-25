### MEM-SOP-002 | 并行智能体开发 SOP：worktree 隔离 + 资源预分配 + check.sh 串行

- **类型与状态**：`SOP` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `多 Agent 并行开发` | 批次/资产 `所有 worktree 与并行 PR` | 密级 `INTERNAL`
- **核心结论**：≥2 个智能体同时开发时必须执行三件套：①**worktree 隔离**——每个 Agent 在独立 worktree + 独立分支上工作，禁止跨目录改文件；②**共享编号资源预分配**——迁移号、契约版本号、分支名、记忆卡序号在开工前由 Owner 一次性分配到人，禁止各自「顺手取号」（版本单点冲突见 MEM-INC-015）；③**check.sh 全局串行**——同一时刻只允许一个 worktree 跑完整 `check.sh`（10 步含后端 pytest、前端构建与真实 PG/MinIO 容器，并发互相污染见 MEM-INC-009，已复发 3 次）。
- **关键依据与细节**：

  **开工前资源预分配表（Owner 填写，Agent 不得自取）**
  ```text
  | Agent | worktree 目录 | 分支名 | 迁移号区间 | 契约版本号 | 记忆卡序号 |
  |-------|--------------|--------|-----------|-----------|-----------|
  |（示例）docs-dev | .claude/worktrees/phase2-docs | docs/v51-report-update | 无 | 无 | INC-015、SOP-002 |
  ```
  - 迁移号：Alembic `down_revision` 是链式单点，同号必撞。
  - 契约版本：`openapi.yaml` 的 `info.version` 是全仓共享单点（MEM-INC-015）。
  - 记忆卡序号：`MEM-<TYPE>-<NNN>` 由索引脚本聚合，重号会让 CI 校验失败（ID 重复）。

  **执行流程**
  ```bash
  # 1. 只在自己的 worktree 内编码（本例为 .claude/worktrees/phase2-docs）
  pwd   # 确认目录后再动手

  # 2. 跑 check.sh 前确认没有其他 worktree 正在跑（当前靠人为串行约定，无代码级锁）
  #    顺序执行，不并行；失败立即上报，不边修边继续

  # 3. 批次收尾（check.sh 第 10 步会强制执行）
  python3 scripts/build_memory_index.py
  python3 scripts/check_memory.py
  python3 scripts/sync_memory_nebula.py
  ```
  - 实测背景：本轮 6 个 PR（#126–#131）按本 SOP 并行开发并全部合入 main；期间 `check.sh` 10 步全过、无并发污染事故。
  - 已知缺口：全局锁目前是**流程性约束**（靠约定串行），尚未脚本化；再次因并发撞库时（MEM-INC-009 第 4 次）必须升级为代码级锁（flock 或端口占用探测）。

- **影响与后续动作**：
  - 每次并行批次开工前由 Owner（General Owner）输出预分配表；Agent 发现表中未覆盖的共享资源（新表、新事件类型、新枚举）必须先申请再使用。
  - 责任人：General Owner（分配）+ 各 Agent（遵守）；**每次并行开发执行**。

---

#### 工程扩展字段

- **check_ref**：`scripts/check_memory.py + scripts/sync_memory_nebula.py`
- **recurrence**：`1`
- **tags**：`check-lock,parallel,migration-number,resource-preallocation,sop,version-bump,worktree`
