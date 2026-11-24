# 16｜PostgreSQL / MinIO / Spool 恢复演练

## 1. 目标
FR-043 当前 PARTIAL。把“有备份”升级为“能恢复、能验证、能证明没有静默丢数据”。

## 2. 目标指标
RPO / RTO 必须由业务负责人确认。V5.1 不擅自编造承诺值。先测当前实际能力，再确认目标。

## 3. PostgreSQL 演练
记录备份时间、版本、加密和存储位置；在隔离环境恢复；执行 Migration 状态检查；核对关键表计数和关联；检查 Outbox、Submission、DatasetVersion 和 Lineage；执行只读健康检查；记录恢复耗时、缺失范围和问题。

## 4. MinIO 演练
选择含 RawAsset、Processing Artifact、Dataset Manifest 的样本；恢复对象；校验 object key、size、SHA256；对照数据库引用；检查孤儿对象和悬空引用；验证预签名下载权限；记录恢复耗时和对象完整率。

## 5. Agent Spool 演练
复制受控 Spool 数据；模拟 Agent 崩溃/机器重启；恢复 SQLite WAL；检查 tmp/pending/uploading/uploaded/failed 状态；验证已 ACK 分片不重复上传；验证未完成文件可继续；确认本地唯一原始副本未被自动删除。

## 6. 联合恢复
按 PG → MinIO → Worker/Outbox → Agent/Spool 顺序或经 ADR 确定的顺序执行；避免数据库引用先恢复但对象缺失。恢复后运行 Lineage、Manifest、Hash 和队列一致性检查。

## 7. Runbook
每项演练包含触发条件、责任人、权限、步骤、命令、预期结果、回滚/中止条件、通知、证据路径和复盘。

## 8. 验收
至少完成一次隔离环境 PG、MinIO、Spool 独立恢复和一次联合一致性检查；RPO/RTO 实测记录；失败项有整改负责人。
