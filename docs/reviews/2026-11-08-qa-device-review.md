# DDP 设备域 QA 代码审查

> 审查对象：main @ `922db85`（BE）、`0018d4e`（FE）
> 审查角色：QA / code-reviewer（原生 Subagent）
> 日期：2026-11-08
> 结论：**REQUEST CHANGES**。设备域不能以 QA Accepted 进入阶段 1。

---

## 1. 实测结果

| 命令 | 结果 |
|---|---|
| `ruff check app tests migrations` | PASS |
| `ruff format --check app tests migrations` | **FAIL**：6 files would be reformatted（已在 `a9c5643` 修复并加入门禁） |
| `pytest` | **107 passed**（提交说明写 130，已更正） |
| `alembic upgrade head` + `alembic check`（SQLite） | No new upgrade operations detected |
| `npm run typecheck` / `lint` / `test` / `build` | PASS；11 文件 60 测试；chunk 1.13 MB 警告 |
| SQLite 外键探针 | `PRAGMA foreign_keys = 0`；跨项目 robot 引用写入成功 |
| 前端契约探针 | `channels` 数组、`uncertainty` 数字均与后端不一致，真实响应会抛 `ContractValidationError` |
| 依赖漏洞扫描 | NOT RUN |

---

## 2. P0

### P0-1 前后端设备契约漂移

- `apps/web/src/services/deviceValidation.ts:135`：`channels` 按 `string[]` 解析；
  后端 `domain/devices/models.py:196`、`db/models/devices.py:318` 定义为 `int | None`。
- `deviceValidation.ts:184`：`uncertainty` 按 `number` 解析；
  后端 `domain/devices/calibration.py:119`、`db/models/devices.py:240` 与
  `docs/data-standards/Canonical草案.md:134` 定义为 JSON 对象
  `{"translation_m": ..., "rotation_rad": ...}`。
- 前端测试 fixture 使用错误形状（`deviceValidation.test.ts:49,82`），造成假绿。

**影响**：任何带 `channels` 的传感器、任何标定记录进入页面都会报错，传感器/标定 Tab 永远失败。

**修复**：冻结契约，统一 `channels: number`、`uncertainty: Record<string, unknown>`；
用后端真实序列化产物作为前端 fixture；补契约测试。

---

## 3. P1

| ID | 问题 | 位置 | 修复 |
|---|---|---|---|
| P1-2 | 标定激活非原子：先替换旧 ACTIVE，再校验当前记录，失败会留下部分写入 | `repositories/devices.py:708,727-737` | savepoint/先条件激活再替换；补失败回滚测试 |
| P1-3 | 并发激活裸抛 `IntegrityError`，未映射领域错误 | `repositories/devices.py:727` | 捕获部分唯一索引冲突，映射 Conflict/VersionConflict |
| P1-4 | 激活不校验有效期，过期标定可 ACTIVE | `domain/devices/calibration.py:237-238` | 校验 `valid_to > now`；补过期测试 |
| P1-5 | SQLite 未开外键，项目隔离测试假绿 | `infrastructure/db/session.py:30-41` | `PRAGMA foreign_keys=ON`；跨项目负向测试；PG CI |
| P1-6 | 写路径项目隔离覆盖不足 | `tests/infrastructure/test_devices_repository.py:318` | 覆盖 save/activate/嵌套引用跨项目 |
| P1-7 | 并发/乐观锁仅测 Robot 顺序陈旧版本 | 同上:333 | 按聚合补 stale save、双 session 并发激活 |
| P1-8 | 后端没有设备 API/应用用例层，前端端点 404；设备域异常未映射 | `api/v1/router.py:12-13`、`application/devices/` 仅 ports、`main.py:45-58` | 补 application service + API + 错误码映射，或明确前端功能开关 |
| P1-9 | 前端页面零组件测试，只测纯函数 | `apps/web/src/pages/devices/**` | 覆盖 403/404/空态/刷新失败保留旧数据/详情跳转 |

---

## 4. P2

- 传感器类型字典用 `RGB-D`，后端枚举是 `RGB_D`；测试也断言错误值。
- `ruff format --check` 失败（已修）。
- `repositories/devices.py` 744 行、`DeviceListPage.tsx` 482 行，接近/超过评审阈值。
- 迁移 `target_type` CHECK 误用 `_SCOPE_VALUES`；枚举值三处来源。
- 迁移测试只做静态元数据检查，未验证行为级约束、带数据降级。
- `aware_datetime` 未统一 UTC；`performed_by` 未校验 UUID。
- `IntegrityError` 按字符串分类脆弱；`session.get` 错误查询未带 project_id。
- 列表分页未接入，只显示前 100 条。
- 提交说明 130 项与实测 107 项不符（已更正记录）。

---

## 5. 测试覆盖缺口

- 状态机：领域层覆盖较好；仓储层非法激活未覆盖。
- 项目隔离：读路径覆盖；写路径仅 Robot add；save/activate/嵌套引用/API 鉴权未覆盖。
- 唯一约束：Robot/Device/Sensor code 覆盖；serial、更新重复、并发冲突未覆盖。
- 迁移：upgrade/downgrade/re-upgrade 覆盖；行为级约束/带数据降级/SQLite FK 未覆盖。
- 标定：happy path + 重复 ACTIVE 覆盖；失败回滚/并发/过期/跨项目 supersede 未覆盖。
- 并发/乐观锁：仅 Robot 陈旧版本；Device/Sensor/Calibration 与真实并发未覆盖。
- 边界：缺空字符串、version=0、limit=0/101、负分辨率、空白 code、嵌套冻结。
- 前端：页面/组件零测试；fixture 使用非后端枚举，掩盖契约问题。

---

## 6. 结论

当前最多是 **Code Complete**。P0-1 与 P1-2/3/5/6/8 修复并复测后，才可进入阶段验收。
前端设备页目前只对 mock 全绿，接真实后端会因契约漂移和设备 API 缺失而失败。
