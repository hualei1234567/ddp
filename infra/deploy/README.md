# DDP 线上部署指南（Cloudflare Pages + 公网 VPS + 自建 MinIO）

本目录提供开源学习项目将 DDP 上线至 `https://www.ddp.online` 的最小无侵入生产配置套件（不改动本地开发默认 `docker-compose.yml`）。

## 1. 架构与子域规划

| 域名 | 承载位置 | Cloudflare DNS 状态 | 用途说明 |
| :--- | :--- | :--- | :--- |
| `www.ddp.online` | Cloudflare Pages (`ddp-web`) | CNAME 橙云代理 (Proxied) | 托管 `apps/web/dist` 前端静态产物，免费全球 CDN |
| `api.ddp.online` | 公网 VPS Nginx → `127.0.0.1:8000` | A 记录 橙云代理 (Proxied) | FastAPI REST 接口与 `/health` |
| `s3.ddp.online` | 公网 VPS Nginx → `127.0.0.1:9000` | A 记录 **灰云直连 (DNS Only)** | 自建 MinIO 分片预签名直传（>10GB 零云存储费，避开 CF 100MB 限制） |

## 2. 公网 VPS 后端一键部署步骤

```bash
# 1. 准备生产环境变量（替换其中 4 个 <REPLACE_WITH_...> 随机密钥）
cp infra/deploy/env.production.example .env

# 2. 启动 PostgreSQL 16 + Redis 7.4 + MinIO + Alembic 迁移 + API + 10 个后台 Worker
docker compose -f docker-compose.yml -f infra/deploy/docker-compose.prod.yml --profile app up -d --build

# 3. 核验 API 健康检查
curl -fsS http://127.0.0.1:8000/health

# 4. 配置宿主机 Nginx 反向代理与证书（参考 nginx-origin.conf.example）
sudo cp infra/deploy/nginx-origin.conf.example /etc/nginx/conf.d/ddp-online.conf
sudo nginx -t && sudo systemctl reload nginx
```

## 3. 签发轻量登录令牌与创建首个项目

```bash
# 在容器内登记管理员用户并签发 72 小时有效的 HS256 Bearer JWT：
docker compose -f docker-compose.yml -f infra/deploy/docker-compose.prod.yml \
  exec -T api python - <<'PY'
import datetime, os, uuid, jwt, psycopg

dsn = os.environ["DDP_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
with psycopg.connect(dsn) as conn:
    row = conn.execute("SELECT id FROM users WHERE display_name = '开源管理员' LIMIT 1").fetchone()
    uid = str(row[0]) if row else str(uuid.uuid4())
    if not row:
        conn.execute("INSERT INTO users (id, display_name) VALUES (%s, '开源管理员')", (uid,))
        conn.commit()

now = int(datetime.datetime.now(datetime.UTC).timestamp())
token = jwt.encode(
    {"sub": uid, "platform_role": "admin", "iss": os.environ["DDP_JWT_ISSUER"], "aud": os.environ["DDP_JWT_AUDIENCE"], "iat": now, "exp": now + 72 * 3600},
    os.environ["DDP_JWT_SECRET"],
    algorithm="HS256",
)
print("USER_ID:", uid)
print("BEARER_JWT:", token)
PY
```

打开 `https://www.ddp.online`，点击右上角 **「轻量登录」** 按钮，粘贴输出的 `BEARER_JWT` 点击 **「验证并登录」**，即可通过 `GET /api/v1/me` 实时核验身份并在抽屉内一键切换项目上下文。

> 宿主机也可直接运行 `backend/.venv/bin/python scripts/ops/issue_jwt.py --display-name "开源管理员" --role admin` 完成幂等建用户与签发。

## 4. 上线前自检与公网冒烟验证（`online_deploy_verify.py`）

上线前与发布后均可通过 `scripts/ops/online_deploy_verify.py` 执行自动化核验（退出码 `0` 表示全部通过，`1` 表示存在失败项；支持 `--json` 输出）：

```bash
# 1. 上线前离线自检：校验 .env 生产安全红线 + docker-compose.prod.yml + nginx-origin.conf.example
python3 scripts/ops/online_deploy_verify.py --env-file .env

# 仅核验仓库内的 compose 与 nginx 配置（不依赖本机 .env，适合 CI/评审阶段）
python3 scripts/ops/online_deploy_verify.py --skip-env

# 2. 发布后公网冒烟探测：校验 API /health、CORS 预检、前端 SPA /assets 回退与 S3 OPTIONS 预检
backend/.venv/bin/python scripts/ops/online_deploy_verify.py \
  --skip-offline \
  --api-url https://api.ddp.online \
  --web-url https://www.ddp.online \
  --s3-url https://s3.ddp.online

# 3. 离线 + 公网联合核验（输出 JSON 报告便于归档）
backend/.venv/bin/python scripts/ops/online_deploy_verify.py \
  --env-file .env \
  --api-url https://api.ddp.online \
  --web-url https://www.ddp.online \
  --s3-url https://s3.ddp.online \
  --json
```
