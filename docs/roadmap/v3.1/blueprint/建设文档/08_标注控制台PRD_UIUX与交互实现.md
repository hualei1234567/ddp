# 标注控制台 PRD、UI/UX 与交互实现规范（V3.1 核心）

## 1. 建设结论

标注后端能力不等于标注产品完成。V3.1 必须建设真实可用的 Annotation Console，覆盖任务领取/进入、媒体播放与时间轴、标签选择和时间片段编辑、自动保存和版本冲突处理、提交、审核反馈与返工、任务进度和质量提示。不得只做静态 UI。

## 2. 页面组成

建议路由（最终路由以当前 React Router 审计为准）：
- `/annotation`：标注中心总览；
- `/annotation/tasks`：任务管理；
- `/annotation/assignments`：任务分配；
- `/annotation/my-work`：我的标注任务；
- `/annotation/workbench/:taskId`：标注工作台；
- `/annotation/reviews`：审核队列；
- `/annotation/schemas`：标签规范管理。

若仓库已有 `/annotation-tasks` 等页面或路由，优先复用并按现有约定增量扩展，不要创建同功能的第二套路由。

## 3. 三栏标注工作台

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ Project / Annotation Task / Episode / Schema Version / 保存状态 / 提交       │
├──────────────────┬────────────────────────────────────┬─────────────────────┤
│ 左侧任务导航      │ 中央媒体与时间轴区域                │ 右侧标注编辑器       │
│                  │                                    │                     │
│ 当前批次         │ 主视频 / 多视角                     │ Task Instruction    │
│ Episode 列表     │ 播放 / 倍速 / 帧步进 / 全屏          │ Episode Labels      │
│ 状态             │                                    │ Segment Labels     │
│ 完成度           │ 时间轴：                            │ Event Labels       │
│ 筛选             │ 任务阶段 / 动作片段 / 关键事件       │ Value / Enum        │
│ 上一条 / 下一条  │ 传感器状态 / 人工标记                │ Notes / Evidence    │
│                  │                                    │ Validation          │
├──────────────────┴────────────────────────────────────┴─────────────────────┤
│ 底部：快捷键提示 / 保存历史 / 数据来源 / 质量提示 / 版本信息                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

建议比例：左侧队列 20%–24% 可收起；中央媒体/时间轴 48%–55%；右侧标签面板 25%–30% 可调整宽度。小屏幕切换“媒体 / 标签 / 任务”分段视图，不强行压缩三栏。

## 4. 左侧任务队列

展示 Episode ID / Clip、Task / Scene / Action、时长、媒体视角数量、自动预检状态、标注状态、保存状态、任务锁定状态、返工原因摘要。

筛选 Project、CollectionTask、AnnotationBatch、TaskSpec、Schema Version、Status、Priority、Due Date。

任务切换时先保存未提交内容；保存失败阻止切换或要求显式放弃草稿；已提交任务进入只读模式；返工任务展示上次审核意见和差异。

## 5. 中央媒体播放器

优先复用当前已验证的原生 `<video>` + Range 请求能力。先确认当前播放器组件是否可复用；若只有 E2E demo，则抽取最小通用组件，不新建解码内核。

支持播放/暂停、0.25x/0.5x/1x/1.5x/2x、前进/后退帧、跳到时间点、当前时间/总时长、多流选择、多视角同步（只有存在可用同步信息时开放）、缓冲/失效 URL/Range 不支持/解码错误的可恢复状态。

多视角同步以 canonical normalized timestamp 或明确主时钟为基准。未同步流显示 `UNSYNCED`；不得简单按相同 `currentTime` 假定不同视频时钟完全同步。手动 offset 只作为审看辅助，必须标记“人工偏移”，不得写回原始时间语义。

## 6. 时间轴与片段标注

轨道由实际数据动态生成：Episode 全时长、Task Phase、Action Segment、Event Marker、Hand/Pose/Joint/IMU（如存在）、已有 Annotation、自动建议（如启用 AI）。

交互：点击时间轴跳转；拖拽创建片段；拖动边界调整 start/end；片段吸附帧边界（可配置）；缩放和横向滚动；选中片段时右侧自动定位标签表单；冲突/重叠按 Schema 规则显示；删除片段需确认或可撤销；时间精度以源数据能力为准。

## 7. 右侧标注编辑器

根据 LabelSchema 动态渲染，不写死标签字段。支持 Enum / MultiEnum、Boolean、Number / Range、Text、Object / Structured JSON、Temporal Segment、Point Event、Keypoint / Pose（当数据与 Schema 支持）、Action Sequence（按项目定义）。

每个字段展示名称、说明、必填标识、值和验证状态、允许值、来源（人工/AI建议/数据集原始）、置信度（仅模型建议）和错误路径。

AI 预标注必须显式标识为建议；人工确认后产生 Human Annotation，不覆盖 AI 原始输出。

## 8. 保存、版本与并发

- 草稿自动保存，debounce 建议 1–3 秒，实际按交互测试调整；
- 保存状态：`Saving / Saved / Unsaved / Save Failed / Conflict`；
- 关键字段变更可立即保存；
- 使用现有 If-Match / row_version 乐观锁；
- 任务编辑采用短时 Lease 或现有锁机制，避免双人覆盖；
- 冲突时展示服务端版本和本地修改，提供刷新/复制草稿/人工合并；
- 自动保存失败不得显示 Saved；
- Submit 前必须等待所有待保存请求完成；
- 提交后生成不可变 Result Version；
- 后续修改通过新版本/返工任务完成，不覆盖已审核版本。

## 9. 快捷键建议

| 快捷键 | 操作 |
|---|---|
| Space | 播放/暂停（输入框聚焦时不触发） |
| ← / → | 后退/前进短时间 |
| Shift + ← / → | 前后逐帧（按视频 FPS） |
| J / K / L | 后退 / 暂停 / 前进 |
| Ctrl/Cmd + S | 保存草稿 |
| N / P | 下一条 / 上一条（先确认保存） |
| 1–9 | 选择常用枚举（由 Schema 配置） |

快捷键必须可查看、可关闭，不得覆盖输入法、浏览器或辅助技术常用按键。

## 10. 提交与审核

提交前检查必填标签、Enum 值、时间区间边界、片段重叠规则、必要证据、Schema Version、未保存内容和编辑 Lease。提交后创建 AnnotationResult Version、进入现有状态机的 Submitted、触发自动 QC、写 Audit + Outbox。自动下一条仅在服务器确认成功后执行。

审核员看到原始 Episode、标注结果、Schema、自动 QC、历史版本、返工次数和差异。动作包括 Accept、Return for Rework、Arbitrate、Reject（仅业务规则允许时）。

## 11. 组件建议

优先复用 PageContainer / PageState / ApiErrorState、useRequest / useCursorPage、AntD Form / Table / Drawer / Modal / Tabs，以及现有 annotation APIs 和状态机。

新建最小组件建议：`AnnotationWorkspaceShell`、`AnnotationTaskQueue`、`EpisodeMediaStage`、`SynchronizedMediaGrid`、`TimelineTrackPanel`、`TimelineSegmentEditor`、`SchemaDrivenAnnotationForm`、`AnnotationSaveIndicator`、`AnnotationSubmitBar`、`ReviewDecisionPanel`、`AnnotationHistoryDrawer`。已有等价组件时不得重复创建。

## 12. 前端交互测试

必须覆盖播放与时间轴同步、片段定位/创建/编辑/删除、自动保存与失败、If-Match 冲突、切换任务未保存拦截、Schema 校验、Submit 成败、审核通过/退回、无媒体/媒体失效/部分流缺失和角色权限。不能只用 `renderToStaticMarkup` 证明交互正确。
