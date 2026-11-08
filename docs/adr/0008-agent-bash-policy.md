# ADR-0008 Agent 执行器的 Bash 策略

- 状态：已提议（待用户批准）
- 日期：2026-11-08
- 关联：规范 §21、§22；平台 `executor_policy.py`

## 背景

平台原先对所有项目注入 `--disallowedTools Bash`，导致调度 Agent 能读写代码但不能跑测试/构建，
无法自证 "Test Passed"。用户已同意为 DDP 开启 Bash 前缀白名单。

## 决策

- 默认仍禁用 Bash（其他项目行为不变）。
- DDP 项目通过 `settings_json.execution_bash_enabled=true` 开启。
- 开启后只放行测试/构建/lint/只读 git/只读文件命令；`git push`、`docker`、`ssh`、`migrate`、
  删除、部署等仍然拒绝。
- 仓库层再加 `PreToolUse` 守卫（`scripts/agent_bash_guard.py`），拒绝命令链、重定向与越界路径。
- 任何放宽都必须同时更新平台测试与仓库守卫测试。

## 后果

- Agent 可以在 worktree 内跑 pytest/ruff/npm build，交付证据更完整。
- 仍不能 push、部署、改数据库或执行迁移。
- `--allowedTools "Bash(<prefix>:*)"` 与仓库 hook 形成双保险。

## 替代方案

- 继续禁用 Bash：交付慢、证据弱，否决。
- 全量放开 Bash：风险过高，否决。
