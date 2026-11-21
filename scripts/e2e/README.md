# 关键路径 E2E：数据准备与启动

前端关键路径 E2E 分两种执行方式。两者都不会在缺少真实数据时"假通过"：
没有 `DDP_E2E_BASE_URL` 时全部用例在报告里是 **skipped**，不是 passed。

## 一、一键本地栈（SQLite + 临时目录）

```bash
cd apps/web
npm run e2e:local
```

`apps/web/scripts/e2e/run-local-stack.sh` 会依次完成：临时 SQLite → Alembic
升级 → 种子（`seed_e2e.py`，含真实 HTTP 状态推进）→ 临时后端（默认 8010）
→ Vite 预览（默认 5174）→ Playwright，退出时清理全部子进程与临时目录。
覆盖上传、动作栏、复核三条路径；**标注控制台用例在此模式下 skipped**，
因为它需要真实 Episode（含 Manifest）与一条 `IN_PROGRESS` 的标注分配。

## 二、真实栈（PostgreSQL + MinIO + 真实后端）

标注控制台的验收必须跑这一种：数据来自真实数据库与对象存储，不允许 Mock。

### 1. 准备本机真实栈

```bash
docker compose up -d postgres minio redis      # 默认 55432 / 9000 / 6379
backend/.venv/bin/alembic upgrade head
```

另开终端启动真实后端（示例，按本机实际配置填写）：

```bash
cd backend
DDP_ENV=local \
DDP_LOCAL_HEADER_AUTH=true \
DDP_DATABASE_URL=postgresql+psycopg://ddp:ddp@127.0.0.1:55432/ddp \
DDP_S3_ENDPOINT=http://127.0.0.1:9000 \
DDP_S3_BUCKET=ddp-raw \
DDP_S3_ACCESS_KEY=minioadmin \
DDP_S3_SECRET_KEY=minioadmin \
DDP_REDIS_URL=redis://127.0.0.1:6379/0 \
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

前端开发服务器（代理到上面的后端）：

```bash
cd apps/web
DDP_WEB_API_PROXY=http://127.0.0.1:8010 npx vite --host 127.0.0.1 --port 5174
```

### 2. 生成一条可标注的分配

```bash
DDP_E2E_PROJECT_ID=<项目 UUID> \
DDP_E2E_OWNER_ID=<项目管理员 UUID> \
DDP_E2E_REVIEWER_ID=<审核人 UUID> \
DDP_E2E_USER_ID=<标注人 UUID> \
backend/.venv/bin/python scripts/e2e/v31-annotation-chain.py
```

脚本只用真实 HTTP API 推进服务端状态机（Schema 发布 → 样本集快照 → 任务
就绪 → 批次派发 → 指定标注人并 claim/start），不直接写库。成功后在标准输出
打印一行 JSON，其中 `assignment_id` 就是下一步要用的分配。

每次运行都会新建 Schema / 任务 / 分配，所以可重复执行；但**同一条分配
只能提交一次**，重复跑 E2E 前请重新执行本脚本换一条新分配。

### 3. 跑标注控制台用例

```bash
cd apps/web
DDP_E2E_BASE_URL=http://127.0.0.1:5174 \
DDP_E2E_USER_ID=<标注人 UUID> \
DDP_E2E_PROJECT_ID=<项目 UUID> \
DDP_E2E_ANNOTATION_ASSIGNMENT_ID=<上一步输出的 assignment_id> \
npx playwright test e2e/annotation-console.spec.ts --reporter=list
```

用例覆盖：我的任务（个人队列）→ 工作台 → 时间轴拖拽框选 → OBJECT_TRACK
行编辑器（新增轨迹带入选区）→ 必填标签（Episode 结果 + 时间片段）→ 提交
→ 服务端冻结提示。通过后可用下面的 SQL 复核记录确实落库：

```sql
SELECT id, status, item_count, data_source, submitted_at
FROM annotation_submissions WHERE assignment_id = '<assignment_id>';

SELECT episode_id, json_array_length(labels::json) AS label_count
FROM annotation_records WHERE submission_id = '<submission_id>';
```

## 环境变量清单

| 变量 | 用途 |
|---|---|
| `DDP_E2E_BASE_URL` | 前端地址；缺失则整组 skipped |
| `DDP_E2E_USER_ID` | 以 `X-User-Id` 注入身份（仅 local + loopback 后端接受） |
| `DDP_E2E_PROJECT_ID` | 项目 UUID |
| `DDP_E2E_TASK_ID` / `DDP_E2E_IMPORT_ID` / `DDP_E2E_REVIEWER_ID` / `DDP_E2E_MCAP_PATH` | 上传与复核路径的种子 |
| `DDP_E2E_ANNOTATION_ASSIGNMENT_ID` | 标注控制台路径的分配 |
| `DDP_E2E_API_BASE` | `v31-annotation-chain.py` 调用的后端地址，默认 `http://127.0.0.1:8010/api/v1` |

## 其他真实栈脚本

| 脚本 | 内容 |
|---|---|
| `v31-minio-upload.py` | 真实 MinIO 分片上传：预签名 PUT、ETag/校验和回读、Complete 合并、下载签名 |
| `human1m-chain.py` / `human1m-full-chain.py` | 公开第一人称数据集接入链路 |
