### MEM-SOP-003 | 子代理并行开发协作模式：迁移号独占 + 契约策略 + 禁改共享文件 + 坑点统一入库 + check.sh 全局串行

- **类型与状态**：`SOP` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `多子代理并行开发` | 批次/资产 `所有 worktree 与并行 PR` | 密级 `INTERNAL`
- **核心结论**：V5.2 阶段 2（#134/#135/#136 三线并行）验证出的子代理并行协作五规则：①**迁移号独占**——每个 Agent 开工前领取独占迁移号（含 `down_revision` 基线指针约定，见 MEM-INC-018）；②**契约策略**——批次只允许 1 个 PR bump `info.version`，其余 PR 改 fragments/`$ref` 引用、版本号延迟到合入前统一顺延（MEM-INC-015）；③**禁改共享文件**——`packages/contracts/**`、`backend/migrations/**`、`backend/app/core/security.py`、`apps/web/src/routes.tsx`、权限矩阵、事件契约只允许 Owner 或被指派的单一 Agent 修改；④**坑点上报考官统一入库**——子代理发现 CI 红/反直觉陷阱时不自行建卡，上报 General Owner 统一编号入库（保证 `MEM-<TYPE>-<NNN>` 序号不重号）；⑤**check.sh 全局锁串行**——同一时刻仅一个 worktree 跑完整 `check.sh`（MEM-SOP-002，并发污染见 MEM-INC-009）。
- **关键依据与细节**：
  - 实测背景（2026-11-25，V5.2 阶段 2）：#134（PRM 步级标注，契约 1.40.0）、#135（Token 成本台账，迁移 0081）、#136（技能路由，迁移 0080）三线并行；期间踩到的 6 类坑已按本规则上报并入库为 MEM-INC-016~021（本批次）。
  - 规则②避免了 #135/#136 与 #134 的版本号合并冲突（对比 MEM-INC-015 中 1.35.0 双占的教训）；规则①的「基线指针约定」仍未覆盖 `down_revision`——三个 PR 都写了 `down_revision="0074"` 导致双 head（MEM-INC-018），说明预分配表必须明确「谁的迁移指向谁的基线」，或约定后合方负责重排。
  - 与 MEM-SOP-002 的关系：002 定义 worktree 隔离 + 资源预分配 + check.sh 串行的骨架；本卡补充**子代理视角的执行细则**（契约策略、上报路径、坑点入库分工），二者配合使用。
- **影响与后续动作**：
  - 每个并行批次开工前：Owner 输出预分配表（迁移号区间 + down_revision 基线 + 契约 bump 归属 + 记忆卡序号区间）；子代理按五规则执行，收尾把坑点清单随 PR 上报。
  - 待办：把 `down_revision` 基线指针列加入 MEM-SOP-002 的预分配表模板（下批次修订 002 时落实）。
  - 责任人：General Owner（分配与裁决）+ 各子代理（执行与上报）；每次并行开发执行。

---

#### 工程扩展字段

- **check_ref**：`docs/memory/sop/MEM-SOP-002-parallel-agent-development.md`
- **recurrence**：`1`
- **tags**：contract-strategy,parallel,sop,subagent,resource-preallocation,migration-number,check-lock
