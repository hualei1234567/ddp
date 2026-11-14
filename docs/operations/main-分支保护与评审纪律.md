# main 分支保护与评审纪律

> 背景：审计 R-3 / OPEN O-2。`hualei1234567/ddp` 是**私有仓库**，GitHub 对私有仓
> 的分支保护与 rulesets 均要求 GitHub Pro；实测两种 API 都返回
> `403 Upgrade to GitHub Pro or make this repository public`（2026-11-14 复核）。
> 因此不能在平台上强制，只能用纪律 + CI 补偿。

## 1. 现状事实

| 项 | 实测结果 | 证据 |
|---|---|---|
| 仓库可见性 | `PRIVATE` | `gh repo view hualei1234567/ddp --json visibility` |
| 分支保护 API | 403（需 Pro 或转公开） | `gh api repos/.../branches/main/protection` |
| rulesets API | 403（同上） | `gh api repos/.../rulesets` |
| 已合并 PR | **0**（13 个 PR 全部是 dependabot 未合并提案） | `gh pr list --state all` |
| main 提交方式 | 全部直推 | `git log` |

## 2. 三个选项与取舍

| 选项 | 成本 | 效果 | 结论 |
|---|---|---|---|
| A 升级 GitHub Pro | 付费 | 真正强制：禁直推、必过 CI、必评审 | **暂不采纳**：个人验证阶段，付费买不到当前最缺的东西 |
| B 仓库转公开 | 免费 | 可启用保护；同时让代码与文档公开 | **暂不采纳**：需用户单独决定，且会把内部 PRD/证据一并公开 |
| C 不启用平台保护，靠 CI + 评审纪律 | 0 | 弱保证：靠流程自律，无法阻止误操作 | **当前采纳** |

**采纳 C 不等于放弃门禁**：把平台能力缺位这件事登记为已知风险，
并用下面的可执行纪律与自动检查补位。一旦仓库规模或协作人数上升，
立即回到 A/B 重新决策（触发表见 §5）。

## 3. 纪律（对人和 Agent 同时生效）

### 3.1 提交

1. **禁止直推 main**。所有变更走任务分支，合并前建 PR；
   例外只允许：`docs/` 纯文档修正且当次已跑 `make check`，需在 PR 描述里注明豁免理由。
2. 不用 `git add -A`；按文件显式暂存。
3. 覆盖别人未提交改动前先 `git status` + `git diff` 并沟通。
4. Conventional Commits（`feat|fix|refactor|docs|test|perf|build|ci|security`）。
5. 不 `git stash`、不强推 main、不重写已推送历史。
   发布/迁移/部署/强推必须单独批准。

### 3.2 合并前必过

```
make check     # ruff + pytest + tsc + build + secret 扫描
```

外加本次 CI 的 4 个 job 必须全绿（后端 / 前端 / 契约 / 安全）。
**CI 红不合并**，不得为了让 CI 变绿而放宽断言、删除用例或跳过检查。

### 3.3 评审

| 变更类型 | 最少评审 |
|---|---|
| 契约（`packages/contracts/**`）、迁移（`backend/migrations/**`）、权限（`deps.py`、`identity/**`）、路由聚合 | 1 次独立评审（Agent 或人工），PR 必须写明影响面 |
| 新增依赖 | 1 次独立评审 + ADR 或 PR 内依赖评估表（用途/替代/版本/体积/许可证/安全公告） |
| 普通功能 | 1 次自审清单（PR 模板） |
| 紧急修复 | 可先合并，24h 内补 PR 说明与测试 |

## 4. 自动补偿（不依赖平台能力）

现有 CI 已覆盖：

- 4 个 job 全绿；契约 Redocly lint + oasdiff 破坏性检查；冻结基线 SHA 重算。
- Secret 扫描（23 用例）。
- 迁移 upgrade → downgrade → upgrade（真实 PG 16）。

本文件额外要求（PR 模板逐项勾选）：

- [ ] `make check` 本地通过，贴真实输出
- [ ] 涉及数据库：说明新增迁移号，确认不改历史迁移
- [ ] 涉及契约：说明版本与是否影响冻结基线
- [ ] 涉及权限：贴越权与跨项目隔离用例名称
- [ ] 涉及依赖：贴评估表或 ADR 链接
- [ ] 写明回滚点

## 5. 重新决策触发条件（任一即回 A/B）

1. 协作者 ≥ 2 人；
2. 单月 main 直推次数 > 10；
3. 出现一次"CI 未过但已合并"或"覆盖他人改动"事故；
4. 仓库需要外部审计或合规留痕。

## 6. 当前状态

- 平台保护：**BLOCKED**（私有仓 + 无 Pro，API 403）。
- 纪律：本文件生效，Agent 与人均适用。
- 未闭环项：无平台强制力，属**已知接受风险**，已登记 R-3。
