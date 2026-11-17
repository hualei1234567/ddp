# 固定 digest，避免基础镜像漂移（CLAUDE.md §三：禁止 latest）。
# digest 与 infra/docker/api.Dockerfile 使用同一 python:3.11-slim 镜像。
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg/ffprobe 是真实抽帧依赖；不提供时服务仍可启动，但任务会以
# ffmpeg_unavailable 明确失败，不会伪造训练结果。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/local-adapter/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/local-adapter/app ./app

EXPOSE 9100
HEALTHCHECK --interval=10s --timeout=5s --retries=10 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9100/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9100"]
