# 固定 digest 而不是浮动 tag：`python:3.11-slim` 已从 bookworm 漂到 trixie，
# 同一份 Dockerfile 在不同时间构建会拿到不同的发行版与系统包版本。
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# pg_dump：maintenance worker 的定时备份要用它，`scripts/ops/backup-restore-drill.sh`
# 也依赖它。不装的话 worker 会每 2 秒报一次「未找到 pg_dump」并被 restart 策略
# 反复拉起——容器永远在 Restarting，看不出是缺依赖还是真故障。
#
# 版本约束：客户端主版本必须 >= 服务端（本仓库是 postgres:16-alpine），
# pg_dump 不会备份比自己新的服务端。基础镜像 trixie 自带 postgresql-client-17，
# 满足；不要再从 PGDG 装 bookworm 源的 16——那套包依赖 libldap-2.5-0，
# trixie 只有 libldap-2.6，实测报 `E: Unable to correct problems, you have held
# broken packages`。
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ .
# data-platform 是纯数据能力（解析 / Canonical / 适配），后端以独立顶层包
# ``ddp_data`` 导入。此前镜像只复制 backend/，导致：
# - 导入 Worker 启动即 ModuleNotFoundError: ddp_data；
# - API 的解析相关端点同样不可用。
# 放在 /app/data-platform 并把该目录加入 PYTHONPATH，与本地开发时的
# sys.path 布局（backend/ 与 data-platform/ 并列为顶层）保持一致。
COPY data-platform/ /app/data-platform/
ENV PYTHONPATH=/app/data-platform

# 标定参数契约文件：`app.domain.devices.parameter_schema` 以「仓库根/docs/...」
# 的布局读取它。镜像里代码在 /app，推导出的根是 `/`，所以文件要落在 /docs。
# 不放这块的话，容器内 `POST /calibrations` 必定 422：
#   「标定参数契约文件缺失：/docs/architecture/contract-draft/device/...」
COPY docs/architecture/contract-draft/device/calibration-parameters.v1.schema.json \
     /docs/architecture/contract-draft/device/calibration-parameters.v1.schema.json

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
