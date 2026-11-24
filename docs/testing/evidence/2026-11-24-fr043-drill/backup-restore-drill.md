# 备份恢复演练证据（2026-11-24T05:24:56Z）

| 项 | 值 |
|---|---|
| PostgreSQL 表数 | 96 → 96 |
| alembic | 0072 → 0072 |
| RPO 锚点 | 2026-11-24 00:11:32.267469+00 → 2026-11-24 00:11:32.267469+00 |
| pg_dump | 17314651B sha256=f1e2648a0b011149ee12ca16987802d3d0ada98b6e7d4716b80e8cee8cbec14e |
| MinIO 对象数 | 2094 |
| MinIO 抽样恢复 | 7c304b24b4bbeef8… → 7c304b24b4bbeef8… |
| Spool 演练 | verified |
| 触发器/约束/索引 | 191/1088/387 一致 |
| RTO | 9s (pg_backup=0s minio_backup=2s minio_restore=1s pg_restore=1s spool_backup_restore=0s app_layer_check=1s ) |
