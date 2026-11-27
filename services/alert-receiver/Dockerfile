# 固定 digest，避免基础镜像漂移（CLAUDE.md §三：禁止 latest）。
# digest 与 services/local-adapter/Dockerfile 使用同一 python:3.11-slim 镜像。
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 告警接收端只用标准库（http.server / json / fcntl），**不装任何第三方包**。
# 这是刻意的：接收端是「最后一公里」，依赖越少越不容易在告警时自己先挂掉。

WORKDIR /app
COPY services/alert-receiver/app ./app

# 落盘目录（compose 里挂命名卷 ddp-alertdata 到此处）
ENV ALERT_RECEIVER_DIR=/var/lib/ddp-alerts \
    ALERT_RECEIVER_HOST=0.0.0.0 \
    ALERT_RECEIVER_PORT=8080
RUN mkdir -p /var/lib/ddp-alerts

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["python", "-m", "app.main"]
