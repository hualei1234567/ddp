# outbox 事件语义界定 v1.0

> 关联：审计 R-13（P2）。现象：**83 种事件类型，只有 2 种有消费者**
> （`ingestion.raw_asset.validation_requested`、`dataset.export.requested`），
> 其余事件永久停在 `PENDING`，表单调增长，且无法区分"漏了消费者"与"本来就不该有消费"。

## 1. 根因

`outbox_events` 同时承担了两个职责：

1. **变更留痕**（谁在什么时候改了什么）—— 用于审计追溯与事件回放；
2. **集成投递**（通知某个 worker 去做事）—— 需要消费者、重试、死信。

两种职责的**生命周期完全不同**：留痕记录应当永久保留或按保留策略归档；
投递消息应当在投递成功后标记 `PUBLISHED` 并定期清理。混在一张表里，
必然出现"要么该清的不敢清，要么该投的没人投"。

## 2. 界定规则（本次决策）

每个事件类型必须**显式**归入下列两类之一。归类写在本文件的表格里，
新增事件时必须在 PR 中同步登记，否则评审打回。

### 2.1 投递类（DISPATCH）

**定义**：有明确的执行方需要据此做事；没有消费者就是缺陷。

**判据**：去掉这个事件后，某个后台动作永远不会发生（而不是"少了一条记录"）。

**要求**：
- 必须有消费者（worker 或 adapter），且在 CI 里有该消费者的测试；
- 投递成功 → `PUBLISHED`；失败 → `mark_retry` 带退避；超限 → `mark_dead_letter`；
- 必须有清理/归档策略（`PUBLISHED` 超过保留期可删）。

### 2.2 留痕类（AUDIT）

**定义**：只记录"发生了什么"，不需要任何执行方响应。

**判据**：去掉这个事件后，系统行为完全不变，只是少了一条可查询的记录。

**要求**：
- **不要求消费者**，也不应因为它没有消费者就被判定为缺陷；
- 写入即视为完成：`status` 直接为 `PUBLISHED`（或专用 `RECORDED`），
  不进入重试队列，避免被 worker 误捞；
- 受保留策略约束，不与投递类混用清理逻辑。

## 3. 当前归类

### 3.1 投递类（3 个，均有消费者与测试）

| 事件 | 消费者 | 状态 |
|---|---|---|
| `ingestion.raw_asset.validation_requested` | `workers/outbox.py` | ✅ 已实现 |
| `dataset.export.requested` | `workers/exports.py` | ✅ 已实现 |
| `datasource.import_requested` | `workers/datasources.py` | ✅ 已实现 |
| `ingestion.job.created` | `workers/ingestion_jobs.py` | ✅ 已实现（2026-11-16 由留痕类改为投递类） |

投递类清单的唯一权威是 `scripts/check_outbox_events.py` 的 `DISPATCH_EVENTS`：
它同时被 CI 与 `make check` 校验，声明与实现不一致会直接失败。

### 3.2 留痕类

前缀分布以 `scripts/check_outbox_events.py` 的实测输出为准（它会同时识别
字面量与模块级常量；数量随各域新增事件变化，不作硬编码断言）。

设备域此前只有 `device.created` / `sensor.created` / `robot.created` 与
`calibration_profile.created` 四个字面量事件可被提取，动作事件写成
`f"device.{action}"` 这类 f-string，AST 抓不到（R-13）。现已改为模块级
字面量映射表，设备域新增事件如下：

| 事件 | 产生点 | 归类 |
|---|---|---|
| `device.created` | `application/devices/devices.py` | 留痕类 |
| `device.activate` | `DEVICE_ACTION_EVENTS` | 留痕类 |
| `device.enter-maintenance` | `DEVICE_ACTION_EVENTS` | 留痕类 |
| `device.report-fault` | `DEVICE_ACTION_EVENTS` | 留痕类 |
| `device.suspend` | `DEVICE_ACTION_EVENTS` | 留痕类 |
| `device.retire` | `DEVICE_ACTION_EVENTS` | 留痕类 |
| `sensor.created` | `application/devices/sensors.py` | 留痕类 |
| `sensor.activate` | `SENSOR_ACTION_EVENTS` | 留痕类 |
| `sensor.enter-maintenance` | `SENSOR_ACTION_EVENTS` | 留痕类 |
| `sensor.report-fault` | `SENSOR_ACTION_EVENTS` | 留痕类 |
| `sensor.suspend` | `SENSOR_ACTION_EVENTS` | 留痕类 |
| `sensor.retire` | `SENSOR_ACTION_EVENTS` | 留痕类 |
| `robot.created` | `application/devices/robots.py` | 留痕类 |
| `robot.activate` | `ROBOT_ACTION_EVENTS` | 留痕类 |
| `robot.enter-maintenance` | `ROBOT_ACTION_EVENTS` | 留痕类 |
| `robot.report-fault` | `ROBOT_ACTION_EVENTS` | 留痕类 |
| `robot.suspend` | `ROBOT_ACTION_EVENTS` | 留痕类 |
| `robot.retire` | `ROBOT_ACTION_EVENTS` | 留痕类 |
| `calibration_profile.created` | `application/devices/profiles.py` | 留痕类 |
| `calibration.created` | `application/devices/calibrations.py` | 留痕类 |
| `calibration.updated` | `application/devices/calibrations.py` | 留痕类 |
| `calibration.submitted` | `_CALIBRATION_ACTION_EVENTS` | 留痕类 |
| `calibration.validated` | `_CALIBRATION_ACTION_EVENTS` | 留痕类 |
| `calibration.rejected` | `_CALIBRATION_ACTION_EVENTS` | 留痕类 |
| `calibration.activated` | `_CALIBRATION_ACTION_EVENTS` | 留痕类 |
| `calibration.revoked` | `_CALIBRATION_ACTION_EVENTS` | 留痕类 |
| `calibration.expired` | `_CALIBRATION_ACTION_EVENTS` | 留痕类 |

为什么全部是留痕类：去掉其中任何一条，系统行为不变，只是少了一条可查询的
记录。没有任何 worker 需要据此做事——标定状态机是同步推进的，
下游读取的是 `calibration_records` 表本身而不是事件。

回归保护：`backend/tests/unit/test_device_outbox_events_registry.py` 断言
设备域源码里没有 `event_type=f"..."`，并逐条核对这些事件名出现在本文件里。

`ingestion.*` 的条目里有 1 条是投递类（上述 validation_requested），
其余（上传会话/分片的进度与状态留痕）为留痕类。

### 3.3 R-13 收尾：其余域的动作事件静态化

设备域之外还有 20 处 `event_type=f"..."`（数据集 / 数据源 / 摄入 / modelops /
供应商），同样拼出 AST 看不到的事件名。现已全部改为模块级映射表 +
下标取值，事件名字符串与改动前**逐字一致**（可观测契约不变）：
映射的键集合等于各域状态机的动作白名单，新增动作时两处必须同步。

| 事件 | 来源映射表 | 归类 |
|---|---|---|
| `dataset.grant.approve` | `DATASET_GRANT_ACTION_EVENTS` | 留痕类 |
| `dataset.grant.reject` | `DATASET_GRANT_ACTION_EVENTS` | 留痕类 |
| `dataset.grant.revoke` | `DATASET_GRANT_ACTION_EVENTS` | 留痕类 |
| `dataset.export.cancel` | `EXPORT_ACTION_EVENTS` | 留痕类 |
| `dataset.export.retry` | `EXPORT_ACTION_EVENTS` | 留痕类 |
| `training.delivery.ready` | `TRAINING_DELIVERY_ACTION_EVENTS` | 留痕类 |
| `training.delivery.deliver` | `TRAINING_DELIVERY_ACTION_EVENTS` | 留痕类 |
| `training.delivery.accept` | `TRAINING_DELIVERY_ACTION_EVENTS` | 留痕类 |
| `training.delivery.reject` | `TRAINING_DELIVERY_ACTION_EVENTS` | 留痕类 |
| `training.delivery.rework` | `TRAINING_DELIVERY_ACTION_EVENTS` | 留痕类 |
| `training.delivery.resume-ready` | `TRAINING_DELIVERY_ACTION_EVENTS` | 留痕类 |
| `training.delivery.cancel` | `TRAINING_DELIVERY_ACTION_EVENTS` | 留痕类 |
| `training.external_reference.sync` | `EXTERNAL_REFERENCE_ACTION_EVENTS` | 留痕类 |
| `training.external_reference.cancel` | `EXTERNAL_REFERENCE_ACTION_EVENTS` | 留痕类 |
| `training.request.revision_submit` | `TRAINING_REVISION_ACTION_EVENTS` | 留痕类 |
| `training.request.revision_approve` | `TRAINING_REVISION_ACTION_EVENTS` | 留痕类 |
| `training.request.revision_reject` | `TRAINING_REVISION_ACTION_EVENTS` | 留痕类 |
| `training.request.block` | `TRAINING_REQUEST_ACTION_EVENTS` | 留痕类 |
| `training.request.unblock` | `TRAINING_REQUEST_ACTION_EVENTS` | 留痕类 |
| `training.request.cancel` | `TRAINING_REQUEST_ACTION_EVENTS` | 留痕类 |
| `training.request.close` | `TRAINING_REQUEST_ACTION_EVENTS` | 留痕类 |
| `datasource.import.start` | `DATASOURCE_IMPORT_ACTION_EVENTS` | 留痕类 |
| `datasource.import.succeed` | `DATASOURCE_IMPORT_ACTION_EVENTS` | 留痕类 |
| `datasource.import.fail` | `DATASOURCE_IMPORT_ACTION_EVENTS` | 留痕类 |
| `datasource.import.cancel` | `DATASOURCE_IMPORT_ACTION_EVENTS` | 留痕类 |
| `ingestion.job.worker-start` | `INGESTION_JOB_ACTION_EVENTS` | 留痕类 |
| `ingestion.job.worker-succeed` | `INGESTION_JOB_ACTION_EVENTS` | 留痕类 |
| `ingestion.job.worker-fail` | `INGESTION_JOB_ACTION_EVENTS` | 留痕类 |
| `ingestion.job.job-timeout` | `INGESTION_JOB_ACTION_EVENTS` | 留痕类 |
| `ingestion.job.retry` | `INGESTION_JOB_ACTION_EVENTS` | 留痕类 |
| `ingestion.job.cancel` | `INGESTION_JOB_ACTION_EVENTS` | 留痕类 |
| `ingestion.job.worker-cancelled` | `INGESTION_JOB_ACTION_EVENTS` | 留痕类 |
| `ingestion.episode.system-start` | `INGESTION_EPISODE_ACTION_EVENTS` | 留痕类 |
| `ingestion.episode.system-ready` | `INGESTION_EPISODE_ACTION_EVENTS` | 留痕类 |
| `ingestion.episode.ready` | `INGESTION_EPISODE_ACTION_EVENTS` | 留痕类 |
| `ingestion.episode.system-fail` | `INGESTION_EPISODE_ACTION_EVENTS` | 留痕类 |
| `ingestion.episode.retry` | `INGESTION_EPISODE_ACTION_EVENTS` | 留痕类 |
| `ingestion.episode.quality-check` | `INGESTION_EPISODE_ACTION_EVENTS` | 留痕类 |
| `ingestion.episode.record-quality` | `INGESTION_EPISODE_ACTION_EVENTS` | 留痕类 |
| `ingestion.episode.accept` | `INGESTION_EPISODE_ACTION_EVENTS` | 留痕类 |
| `ingestion.episode.reject` | `INGESTION_EPISODE_ACTION_EVENTS` | 留痕类 |
| `ingestion.episode.complete` | `INGESTION_EPISODE_ACTION_EVENTS` | 留痕类 |
| `ingestion.episode.archive` | `INGESTION_EPISODE_ACTION_EVENTS` | 留痕类 |
| `ingestion.episode.invalidate` | `INGESTION_EPISODE_ACTION_EVENTS` | 留痕类 |
| `modelops.data_requirement.submit` | `DATA_REQUIREMENT_ACTION_EVENTS` | 留痕类 |
| `modelops.data_requirement.approve` | `DATA_REQUIREMENT_ACTION_EVENTS` | 留痕类 |
| `modelops.data_requirement.reject` | `DATA_REQUIREMENT_ACTION_EVENTS` | 留痕类 |
| `modelops.data_requirement.cancel` | `DATA_REQUIREMENT_ACTION_EVENTS` | 留痕类 |
| `modelops.data_requirement.plan` | `DATA_REQUIREMENT_ACTION_EVENTS` | 留痕类 |
| `modelops.data_requirement.satisfy` | `DATA_REQUIREMENT_ACTION_EVENTS` | 留痕类 |
| `modelops.data_requirement.start` | `DATA_REQUIREMENT_ACTION_EVENTS` | 留痕类 |
| `modelops.data_requirement.close` | `DATA_REQUIREMENT_ACTION_EVENTS` | 留痕类 |
| `modelops.data_requirement.link-retest` | `DATA_REQUIREMENT_ACTION_EVENTS` | 留痕类 |
| `modelops.inference_service.deploy` | `INFERENCE_SERVICE_ACTION_EVENTS` | 留痕类 |
| `modelops.inference_service.activate` | `INFERENCE_SERVICE_ACTION_EVENTS` | 留痕类 |
| `modelops.inference_service.system-deploy-fail` | `INFERENCE_SERVICE_ACTION_EVENTS` | 留痕类 |
| `modelops.inference_service.reset` | `INFERENCE_SERVICE_ACTION_EVENTS` | 留痕类 |
| `modelops.inference_service.stop` | `INFERENCE_SERVICE_ACTION_EVENTS` | 留痕类 |
| `modelops.inference_service.retire` | `INFERENCE_SERVICE_ACTION_EVENTS` | 留痕类 |
| `modelops.feedback.triage` | `FEEDBACK_ACTION_EVENTS` | 留痕类 |
| `modelops.feedback.dismiss` | `FEEDBACK_ACTION_EVENTS` | 留痕类 |
| `modelops.feedback.create-requirement` | `FEEDBACK_ACTION_EVENTS` | 留痕类 |
| `modelops.feedback.resolve` | `FEEDBACK_ACTION_EVENTS` | 留痕类 |
| `modelops.evaluation.cancel` | `EVALUATION_ACTION_EVENTS` | 留痕类 |
| `modelops.evaluation.retry` | `EVALUATION_ACTION_EVENTS` | 留痕类 |
| `modelops.model.activate` | `MODEL_ACTION_EVENTS` | 留痕类 |
| `modelops.model.deprecate` | `MODEL_ACTION_EVENTS` | 留痕类 |
| `modelops.model.archive` | `MODEL_ACTION_EVENTS` | 留痕类 |
| `modelops.model.restore` | `MODEL_ACTION_EVENTS` | 留痕类 |
| `modelops.model_version.validate` | `MODEL_VERSION_ACTION_EVENTS` | 留痕类 |
| `modelops.model_version.system-validate-pass` | `MODEL_VERSION_ACTION_EVENTS` | 留痕类 |
| `modelops.model_version.system-validate-fail` | `MODEL_VERSION_ACTION_EVENTS` | 留痕类 |
| `modelops.model_version.return-draft` | `MODEL_VERSION_ACTION_EVENTS` | 留痕类 |
| `modelops.model_version.publish` | `MODEL_VERSION_ACTION_EVENTS` | 留痕类 |
| `modelops.model_version.deprecate` | `MODEL_VERSION_ACTION_EVENTS` | 留痕类 |
| `modelops.model_version.archive` | `MODEL_VERSION_ACTION_EVENTS` | 留痕类 |
| `supplier.activate` | `SUPPLIER_ACTION_EVENTS` | 留痕类 |
| `supplier.suspend` | `SUPPLIER_ACTION_EVENTS` | 留痕类 |
| `supplier.terminate` | `SUPPLIER_ACTION_EVENTS` | 留痕类 |
| `supplier.resume` | `SUPPLIER_ACTION_EVENTS` | 留痕类 |
| `supplier.batch.dispatch` | `DELIVERY_BATCH_ACTION_EVENTS` | 留痕类 |
| `supplier.batch.arrive` | `DELIVERY_BATCH_ACTION_EVENTS` | 留痕类 |
| `supplier.batch.start-screening` | `DELIVERY_BATCH_ACTION_EVENTS` | 留痕类 |
| `supplier.batch.finalize` | `DELIVERY_BATCH_ACTION_EVENTS` | 留痕类 |
| `supplier.batch.rework` | `DELIVERY_BATCH_ACTION_EVENTS` | 留痕类 |
| `supplier.batch.close` | `DELIVERY_BATCH_ACTION_EVENTS` | 留痕类 |
| `supplier.segment.queue-review` | `INBOUND_SEGMENT_ACTION_EVENTS` | 留痕类 |
| `supplier.segment.system-accept` | `INBOUND_SEGMENT_ACTION_EVENTS` | 留痕类 |
| `supplier.segment.system-reject` | `INBOUND_SEGMENT_ACTION_EVENTS` | 留痕类 |
| `supplier.segment.system-need-more` | `INBOUND_SEGMENT_ACTION_EVENTS` | 留痕类 |
| `supplier.segment.resubmit` | `INBOUND_SEGMENT_ACTION_EVENTS` | 留痕类 |

全部为留痕类：去掉其中任何一条，系统行为不变，只是少一条可查询记录——
这些域的状态推进是同步的事务写入，下游读表而不是读事件。
投递类仍只有 §3.1 的 3 个。回归保护见
`backend/tests/unit/test_outbox_event_registry_all_domains.py`。

## 4. 待办与完成情况

| 项 | 内容 | 优先级 | 状态 |
|---|---|---|---|
| **T-1** | `record_change` 增加 `dispatch: bool` 参数 | P2 | ✅ 已实现 |
| **T-2** | `claim_next` 只捞 `PENDING` 且 `dispatch=true` 的行 | P2 | ✅ 已实现 |
| **T-3** | 投递类事件的保留/归档任务 | P2 | ✅ 已实现（ADR-0027 维护 worker；只删终态事件，死信永不自动删） |
| **T-4** | CI 增加"投递类事件必须有消费者测试"检查 | P2 | ✅ 已实现 |
| **T-5** | 事件清单提取脚本 | P3 | ✅ 已实现（原脚本并不存在，本次新建） |

### 4.1 T-1 / T-2 的实现

`record_change(..., dispatch: bool = False)`，默认 `False`——默认值刻意选
"不需要消费者"那一侧，避免新增事件时无意间留下一条永远不会被消费的待投递消息。
投递类必须显式传 `True`，并在本文件的归类表与
`scripts/check_outbox_events.py` 的白名单里同时登记。

写入语义：

| dispatch | status | 说明 |
|---|---|---|
| `False`（留痕类） | `RECORDED` | 写入即终态，不进重试队列 |
| `True`（投递类） | `PENDING` | 由 worker 抢占、重试、必要时进死信 |

数据库约束 `dispatch_status_consistent` 把这条对应关系写进表：

```sql
(dispatch = false AND status = 'RECORDED')
OR (dispatch = true AND status <> 'RECORDED')
```

没有这条约束时，"投递消息被误标成已记录"只会表现为"消费者一直收不到"，
很难排查。现在它是一插入就失败的硬错误。

`claim_next` 同时按 `dispatch = true` 与 `status = 'PENDING'` 过滤。
两个条件看起来冗余（约束已保证一致性），但保留 `dispatch` 判断是刻意防御：
万一将来有人放宽约束，抢占端仍然不会捞错。

存量数据在迁移 `0030` 里全部回填为 `dispatch = true`——它们此前都是 `PENDING`
等消费者；若改成留痕类，等于把可能待投递的消息静默丢掉。

### 4.2 T-4 / T-5 的实现

`scripts/check_outbox_events.py` 做三件事，`make check` 第 7 步与 CI 都以阻断方式执行：

1. **提取**：用 AST 扫描所有事件类型字符串，**同时**识别字面量与模块级常量。
   T-5 记的"漏掉常量形式"就是因为旧做法只按 `event_type="..."` 字面匹配。
2. **校验**：每个投递类事件必须有消费者文件，且该文件里确实出现该事件类型；
   并且在测试里被引用——没有测试的消费者等于没验证过。
3. **防回归**：源码里 `dispatch=True` 的写入点必须与白名单完全一致，
   双向都查（白名单有但源码没有 → 声明过期；源码有但白名单没有 → 漏登记）。

实测提取到 93 种事件类型（旧口径的 83 条是漏了常量形式的结果），
其中投递类 3 种。

## 4.4 「静默过滤」的 19 个事件（2026-11-15 补登记）

`check_outbox_events.py` 只收首段在 `_EVENT_DOMAINS` 白名单里的事件。OC-13 收尾
时发现 `robot` / `calibration` / `calibration_profile` / 四个 `*_policy` 首段
**不在白名单**，导致下列 19 个事件长期被静默丢弃：既不计入总数，也不会因
"未归类"失败——等于绕过了整道校验。

事件名是可观测契约（已落库的 outbox 记录与历史消费者按名字匹配），
因此选择补白名单而非改名。

| 事件 | 产生点 | 归类 |
|---|---|---|
| `robot.created` | `application/devices/robots.py` | 留痕类 |
| `robot.activate` | `application/devices/robots.py` | 留痕类 |
| `robot.enter-maintenance` | `application/devices/robots.py` | 留痕类 |
| `robot.report-fault` | `application/devices/robots.py` | 留痕类 |
| `robot.suspend` | `application/devices/robots.py` | 留痕类 |
| `robot.retire` | `application/devices/robots.py` | 留痕类 |
| `calibration.created` | `application/devices/calibrations.py` | 留痕类 |
| `calibration.updated` | `application/devices/calibrations.py` | 留痕类 |
| `calibration.submitted` | `application/devices/calibrations.py` | 留痕类 |
| `calibration.validated` | `application/devices/calibrations.py` | 留痕类 |
| `calibration.rejected` | `application/devices/calibrations.py` | 留痕类 |
| `calibration.activated` | `application/devices/calibrations.py` | 留痕类 |
| `calibration.revoked` | `application/devices/calibrations.py` | 留痕类 |
| `calibration.expired` | `application/devices/calibrations.py` | 留痕类 |
| `calibration_profile.created` | `application/devices/profiles.py` | 留痕类 |
| `dedup_policy.created` | `application/datasets/policies.py` | 留痕类 |
| `split_policy.created` | `application/datasets/policies.py` | 留痕类 |
| `leakage_policy.created` | `application/datasets/policies.py` | 留痕类 |
| `quality_policy.created` | `application/datasets/policies.py` | 留痕类 |

全部为留痕类：没有执行方需要据此做事，因此不要求 worker 消费者。
关闭后脚本扫描到 **211 种**事件类型（此前 192，再此前 104）。

**防回归**：脚本新增「静默过滤守卫」——凡在 `event_type=` 位置或 `*_EVENTS`
映射表里出现、形态像事件名、但首段不在白名单的字符串，一律报错而非丢弃。
判据只看事件类型出现的位置，不看全量字符串，避免 `vote.decision` 这类
普通字段路径造成假阳性。

## 5. 对阶段 7 数据平面的约束

`datasource.import_requested` 归为**投递类**（下一批实现 worker 消费它）。
在 worker 落地前，该事件会停在 `PENDING` —— 这是**已知且有意**的状态，
不是缺陷，但必须在阶段 7 第二批交付时一并补齐消费者，否则应降级为留痕类。

## 附录：完整事件清单

见 `docs/architecture/outbox事件清单-v1.0.txt`（由脚本从源码提取，便于比对漂移）。
