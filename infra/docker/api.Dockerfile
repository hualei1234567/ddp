FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
# data-platform 是纯数据能力（解析 / Canonical / 适配），后端以独立顶层包
# ``ddp_data`` 导入。此前镜像只复制 backend/，导致：
# - 导入 Worker 启动即 ModuleNotFoundError: ddp_data；
# - API 的解析相关端点同样不可用。
# 放在 /app/data-platform 并把该目录加入 PYTHONPATH，与本地开发时的
# sys.path 布局（backend/ 与 data-platform/ 并列为顶层）保持一致。
COPY data-platform/ /app/data-platform/
ENV PYTHONPATH=/app/data-platform

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
