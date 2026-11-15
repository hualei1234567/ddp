# NVIDIA PhysicalAI-Human-1M 全链路验收（2026-11-15，含审核与质量报告 HTTP 复核）

## 关于 run.log

完整运行日志 `run.log` 在本机保留但**不入库**（`.gitignore` 的 `*.log` 全局规则）。
下方"结果"表是从该日志逐行摘录的结论；关键数字可在 `docs/testing/evidence/
2026-11-15-human1m-chain/README.md` 的同名流程中对照（那是本批之前的基线）。

## 结论

`VERIFIED`：真实 Hugging Face 下载 → 真实 PostgreSQL → 真实 MinIO →
质量报告 → 人工审核 → **真实 HTTP 路由复核**，整链跑通。

## 环境与方法

| 项 | 值 |
|---|---|
| 数据集 | `nvidia/PhysicalAI-Human-1M-100K` @ `7e312e654818d1e0d3ebec3b78e50ee833b429f8` |
| 数据库 | 本机 PostgreSQL 16（`localhost:55432/ddp`），迁移至 `0029` |
| 对象存储 | 本机 MinIO（`127.0.0.1:9000`，桶 `ddp-raw`） |
| 脚本 | `scripts/e2e/human1m-chain.py` |
| 认证（HTTP 复核段） | 本地 Header 后门（`DDP_LOCAL_HEADER_AUTH=true` + loopback + `X-User-Id`） |

## 结果

| 步骤 | 结果 |
|---|---|
| 下载 / 内容校验 / 存 MinIO / 建媒体资产 | 15 片段 / 39 资产 / 46,335,568 字节 |
| 质量报告（规则 1.0.0，范围 SAMPLE） | 6/6 PASS |
| 人工审核（职责分离） | APPROVED / ACCEPT，审核人 ≠ 提交人 |
| 预签名播放 URL | 含 `X-Amz-Signature` |
| **质量报告 HTTP 端点** | 200；`rule_version=1.0.0`、`passed=true`、`findings=6` |
| **审核记录 HTTP 端点** | 200；`status=APPROVED`、`decision=ACCEPT`、`version=2` |

最后两行是本次新增的验证点：前端 `ImportReviewPage` 走的是 HTTP 路由，
因此必须证明"路由返回的字段"与"用例层结论"一致，而不只是在用例层验证过。

## 复现

```bash
docker compose up -d postgres minio
backend/.venv/bin/alembic upgrade head
backend/.venv/bin/python scripts/e2e/human1m-chain.py
```

## 未覆盖

- 视频播放的 HTTP 206 分片：本脚本只证明 URL 可签发；`Range` 行为在
  ADR-0017 与阶段 7 第一批的证据中单独验证过（httpx 对该 MinIO 构建的
  keep-alive 不稳定，改用 `curl` 验证）。
- 前端页面的人工点击路径：由 `ImportReviewPage.test.tsx` 的组件测试覆盖状态渲染，
  未做浏览器端 E2E（本机未安装 Playwright）。
