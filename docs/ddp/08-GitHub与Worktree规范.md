# DDP GitHub 与 Git Worktree 规范

## 1. 平台基线

GitHub 是当前代码管理平台。沿用现有仓库权限和分支保护策略，不擅自更换平台。

## 2. 分支

优先沿用仓库现有分支策略。若没有明确规范，可采用：
- `main`：稳定主分支；
- `feature/<task-id>-<short-name>`：功能；
- `fix/<task-id>-<short-name>`：修复；
- `refactor/<task-id>-<short-name>`：重构；
- `docs/<task-id>-<short-name>`：文档。

不强制增加 `develop` 分支。

## 3. Worktree

并行任务可使用独立 Git Worktree：
- 每个开发任务使用独立分支和 Worktree；
- 任务开始前确认基准 Commit；
- 明确文件所有权；
- 不共享未提交文件；
- 不允许两个 Agent 同时编辑同一文件；
- 完成后通过 Pull Request 集成；
- 合并后清理 Worktree 前确认无未提交内容。

## 4. 提交规范

建议使用清晰前缀：
- `feat:` 新功能；
- `fix:` Bug 修复；
- `refactor:` 重构；
- `docs:` 文档；
- `test:` 测试；
- `chore:` 工程维护。

提交说明应描述业务目的，不使用含糊的 `update`、`modify`。

## 5. Pull Request

PR 必须说明：
- 背景和目标；
- 改动范围；
- 复用的已有能力；
- API / Schema / Migration 变化；
- 测试命令和真实结果；
- UI 截图（适用时）；
- 风险和回滚方式；
- 关联 Issue / Task。

未经授权不推送受保护分支、不强制推送、不自行合并。

## 6. 仓库保护

不要提交：
- `.env` 和密钥；
- 本地缓存；
- 大型原始数据；
- 模型权重；
- 临时导出文件；
- 用户隐私或客户敏感数据。

大文件按现有仓库策略使用对象存储或 Git LFS（如已配置并经确认）。
