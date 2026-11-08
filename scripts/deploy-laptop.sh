#!/usr/bin/env bash
# 部署到用户指定的 Ubuntu 笔记本（xxx.xxx.xxx.xxx）。
# 阶段 0 只做语法与连通性检查；真正发布在阶段 4 之后启用。
set -euo pipefail

HOST="${DDP_DEPLOY_HOST:-your-staging-host}"
TARGET="${DDP_DEPLOY_PATH:-/home/user/vscodeworkspace/DataPlatform}"

echo "[deploy] 目标：$HOST:$TARGET"
ssh -o BatchMode=yes "$HOST" "test -d '$TARGET/.git' || { echo '目标仓库尚未 clone'; exit 1; }"

echo "[deploy] 拉取代码"
ssh -o BatchMode=yes "$HOST" "cd '$TARGET' && git pull --ff-only"

echo "[deploy] 构建并升级 Compose"
ssh -o BatchMode=yes "$HOST" "cd '$TARGET' && docker compose --profile app build && docker compose --profile app up -d"

echo "[deploy] 健康检查"
ssh -o BatchMode=yes "$HOST" "curl -fsS -m 5 http://127.0.0.1:8000/health"

echo "[deploy] 完成"
