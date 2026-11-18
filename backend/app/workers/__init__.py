"""异步 Worker 入口。

各 Worker 与 HTTP API 同镜像、独立进程；耗时任务不得占用 HTTP 线程。

- ``python -m app.workers.outbox``：RawAsset 校验事件投递；
- ``python -m app.workers.datasources``：数据源导入；
- ``python -m app.workers.ingestion_jobs``：接入任务；
- ``python -m app.workers.exports``：数据集导出；
- ``python -m app.workers.processing``：处理运行八阶段编排（N4-002）。
"""
