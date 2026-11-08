# DDP 设备域 UX 审查

> 审查对象：main @ `0018d4e` / `922db85`
> 审查角色：UX Designer（原生 Explore Subagent）
> 日期：2026-11-08
> 结论：目前是“只读展示骨架”，还不是完整的设备运维页面；不能按阶段 1 完成验收。

---

## 1. 做得好的地方

- `PageState` 覆盖 10 种全局状态；403/404 优先于缓存数据，避免权限被拒后继续渲染旧档案。
- 未知状态保留原始码，不冒充已知状态；列表区分“无设备”与“无匹配”；筛选写入 URL。
- 手机端列表改为卡片。

## 2. P0

### P0-1 列表只加载首屏 100 条，无翻页/加载更多

- `apps/web/src/services/devices.ts:16-17,59-67`
- `DeviceListPage.tsx:118-123,141-157,388-402`；`SensorList.tsx:120-133`；`CalibrationList.tsx:136-149`
- 影响：超过 100 条后不可达；搜索可能错误显示“无匹配”；统计只作用于已加载数据。

## 3. P1

| ID | 问题 | 位置 |
|---|---|---|
| P1-2 | 详情页刷新只刷新概览，已打开的传感器/标定 Tab 继续显示旧数据 | `DeviceDetailPage.tsx:131-132,177-203,224-238`；`SensorList.tsx:83-89`；`CalibrationList.tsx:99-105` |
| P1-3 | 阶段 1 的设备操作路径全部缺失（新建/编辑/标定/巡检/停用/退役） | `DeviceListPage.tsx:296-312,428-434`；`DeviceDetailPage.tsx:39-44` 等 |
| P1-4 | 心跳、标定有效期、状态新鲜度未进入 Stale；ACTIVE 一律显示可用 | `types/device.ts:7-30`；`DeviceDetailPage.tsx:39-44,61-63`；`CalibrationList.tsx:23-31` |
| P1-5 | 从详情返回列表丢筛选；Tab 状态未写 URL | `DeviceListPage.tsx:194-197`；`DeviceDetailPage.tsx:177-203,229-231` |
| P1-6 | 窄屏/现场端未达标：筛选未进抽屉、表格横向滚动、触控目标不足 | `DeviceListPage.tsx:69-103,322-376`；`SensorList.tsx:128-134`；`CalibrationList.tsx:144-150`；`tokens.css:346-353` |
| P1-7 | 外键/长 ID 只截断，无 Tooltip/复制/跳转 | `DeviceDetailPage.tsx:84-95`；`DeviceListPage.tsx:236-240`；`CalibrationList.tsx:60-65`；`format.ts:40-54` |
| P1-8 | 传感器类型字典用 `RGB-D`，后端是 `RGB_D` | `devicePresentation.ts:65-75`；后端 `enums.py:39` |

## 4. P2

- P2-9 状态视觉不一致：摘要 Tag 无颜色，使用 AntD 预设色而非主题 token；StatusTag 无图标。
- P2-10 错误/空态使用工程口吻（“DDP-101 契约”“next_cursor”），现场用户无法行动。
- P2-11 详情 Tabs 无数量/异常徽标，Tab 内失败/过期被隐藏。
- P2-12 标定类型/方法/同步方式显示英文原码；证据只有数量，参数只有键名。
- P2-13 Forbidden 无“切换项目/联系管理员”；401 未按规范跳登录。

## 5. 阶段 1 必须补齐的页面状态

| 页面 | 必须补齐 |
|---|---|
| 设备列表 | 分页/加载更多；Partial 逐行“状态未知”；心跳 Stale；新建/标定/停用 Disabled 原因；写操作 Pending/Success/Failed |
| 设备详情 | 心跳/标定过期 Stale；字段级 Partial；编辑/标定/维修/停用三态 |
| 传感器 Tab | Partial/Stale；新增/编辑写操作；移动端卡片化 |
| 标定 Tab | 过期高亮；证据详情；提交/校验/撤销三态 |
| 全局 | 401 跳登录；404 不泄漏；409 冲突；Offline 横幅；429 倒计时；错误去重 |

## 6. 表单与危险操作

本批提交没有表单和写操作，因此字段校验、服务端错误回填、提交中禁用、失败保留输入、
危险操作二次确认均**尚未实现，不能视为通过**。阶段 1 接入写流程时必须补齐：
必填/格式/范围校验；400/422 定位到字段；提交 Pending 防重复；失败保留输入；
停用/退役二次确认展示关联任务数与影响范围，并要求填写原因；未保存离开确认。
