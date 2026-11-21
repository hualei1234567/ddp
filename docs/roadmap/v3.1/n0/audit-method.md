# DDP V3.1 N0-05 审计方法与证据边界

- 制定日期：2026-11-22
- 目的：说明本轮 N0 结论“如何得出”，以及哪些结论仍不可宣称

## 1. 审计路径

```text
1. 文档基线       V1.0 总规范、V2.1 基线、V3.1 蓝图包（20 文件 SHA256 校验）
2. 仓库现状       git 状态、origin/main、分支、迁移头、契约版本、前端路由
3. 四条独立审计线  采集上传 / 处理 Episode / 标注控制台 / Dataset 导出
4. 交叉核对       抽样用命令复核每条审计线的关键结论
5. 质量门禁       DDP_ENV=local make check 实跑
6. 落盘           docs/roadmap/v3.1/n0/*
```

## 2. 输入完整性

V3.1 蓝图包位于 `docs/roadmap/v3.1/blueprint/`，随仓库归档。按 `PACKAGE_MANIFEST.json`
逐文件校验 SHA256 与字节数：

```text
checked=20  errors=0
```

## 3. 已完整读取的基线文档

| 文档 | 状态 |
|---|---|
| `docs/DDP_具身智能数据闭环平台建设总规范_V1.0_FINAL.md` | 已读 |
| `docs/05-现状审计报告-2026-11-14.md`（含 §12 追加至 11-15） | 已读 |
| `docs/roadmap/next-stage-v2/audit/N0-N9-项目最终报告-2026-11-17.md` | 已读 |
| `docs/roadmap/v2.1/README.md` 与 `n0/*` 全部 9 个文件 | 已读 |
| `docs/roadmap/v3.1/blueprint/*` 全部 20 个文件 | 已读 |
| 相关 ADR：0002/0003/0004/0005/0009/0010/0011/0012/0014/0017/0035/0037 | 已读 |

## 4. V1.3 数据接入 PRD 的可得性

`PACKAGE_MANIFEST.json` 将 `DDP_数据接入与样本验收中心_PRD_V1.3_全局复用优先版.md`
列为基线来源，但该文件**未随 V3.1 包分发**，也未在仓库、Git 历史或本机可访问路径中找到。
本机仅存 `~/Trash/DDP_V2.1_下一阶段建设包_FINAL.zip`（V2.1，非 V1.3）。

处理方式：

- 不虚构其内容；
- 数据接入相关结论全部以当前代码、契约、迁移和测试为证据；
- 若后续提供该 PRD，再比对差异并更新本文件。

**状态：`BLOCKED`（输入缺失），不影响其余七项能力的审计结论。**

## 5. 证据分级

| 级别 | 含义 | 本轮处理 |
|---|---|---|
| E1 实跑 | 本轮实际执行命令 / 测试并取得输出 | `make check` |
| E2 代码 | 源码、迁移、契约文件存在且接线可读 | 四条审计线主体 |
| E3 测试声明 | 存在测试文件但本轮未执行 | 标注 `UNVERIFIED` |
| E4 文档 | 仅文档或注释声明 | 一律不采信为能力证据 |

## 6. 本轮实际执行

| 命令 | 结果 |
|---|---|
| `git status` / `git rev-parse origin/main` | 基线 `46864a9` |
| `cd backend && .venv/bin/alembic heads` | `0068 (head)` |
| `grep -cE '^  /api/v1/' packages/contracts/openapi.yaml` | 239 |
| V3.1 包 SHA256 校验 | 20/20 一致 |
| `DDP_ENV=local make check` | `全部检查通过`，`EXIT_CODE=0` |

`NOT RUN`：真实 MinIO 端到端、staging、任何真实硬件测试。

## 7. 交叉核对记录

| 审计线结论 | 复核命令 | 结果 |
|---|---|---|
| Dataset 门禁接受 `COMPLETED` | `grep -n '_EPISODE_STATUSES' backend/app/domain/datasets/selection.py` | `frozenset({"ACCEPTED", "COMPLETED"})` 确认 |
| Canonical 版本分裂 | `grep -rn 'ddp.canonical/1.0.0'` | `1.0.0-draft` 与 `1.0.0` 并存确认 |
| 上传→RawAsset 伪测试 | `grep -n 'def test_.*capture_session.*raw_asset'` | 函数名与断言不符确认 |
| 标注控制台缺媒体能力 | `grep -rInE 'video|player|timeline' apps/web/src/{components,pages}/annotation` | 0 命中确认 |

## 8. 不得宣称的内容

- 不得宣称“全链路已完成”。当前是七段能力分别成立，未贯通为单一可验收链。
- 不得宣称“真实数据验收通过”。HDF5 / ROS Bag 真实样例测试默认跳过。
- 不得宣称“LeRobot 训练格式可用”。代码固定 `verification_status=UNVERIFIED`。
- 不得宣称“端侧采集已具备”。Agent `MISSING`。
- 不得宣称 staging 可用。`BLOCKED`。

## 9. 下一轮（开工前）必须补的验证

1. V31-001 修正后重跑 `make check`；
2. 真实 MinIO 专项跑通并留下日志；
3. 平台侧 `Annotation console` 浏览器 E2E 首次基线截图 / 断言；
4. 用户对 `task-dag.md` §5 的 D1–D5 作出决策。
