# ADR-0017 视频审看播放器选型

- 状态：**ACCEPTED（选型确定，待阶段 7 落地）**
- 日期：2026-11-14
- 决策者：System Architect + 前端；批准人：用户（长期授权）
- 关联：ADR-0001、ADR-0014；审计报告 O-7；`docs/ddp/07` §1、`docs/ddp/03` §4
- 约束：**不自研视频播放器内核**（`docs/ddp/03` §4 明列暂不实施）

## 1. 背景

阶段 7 数据平面的核心交互是"视频审看 + 时间轴 + 样本导航"：审核员按 Episode
逐条播放第一人称视频，在时间轴上定位片段、打标、跳转与记录裁决。

现状：前端（`apps/web`）**没有任何媒体组件**，依赖只有 antd 5.22.5 /
react 18.3.1 / react-router-dom 6.28.1。全站无 `<video>` 使用。

审看对象的来源决定技术需求：

- Hugging Face 第一人称数据集（LeRobot / Open X-Embodiment 一类）多以
  **MP4（H.264）分片** 形式提供，需按 Episode 定位文件并支持拖拽 seek；
- 后续 ROS Bag / MCAP 派生出的媒体可能是长时长、多相机，需要多路同步播放；
- 不做直播，不需要 LLM/DRM/广告。

## 2. 候选评估

数据取自 npm registry（2026-11-14）：

| 候选 | 版本 | 许可证 | 传递依赖 | 月份维护 | React 适配 |
|---|---|---|---|---|---|
| `video.js` | 10.4.0 | Apache-2.0 | 无 | 2026-11 | 需自包一层 React 组件 |
| `hls.js` | 1.7.4 | Apache-2.0 | 无 | 2026-11 | 非 UI 库，配原生 `<video>` |
| `vidstack` | 1.15.7 | MIT | `fscreen`、`lit-html`、`unplugin`、`media-captions`、`@floating-ui/dom` | 2026-10 | 提供官方 React 组件 |

## 3. 决策

**选 `hls.js` + 原生 `<video>`（方案 B），并以渐进增强方式使用。**

理由：

1. **复用优先**。原生 `<video>` 已是浏览器内置的成熟播放内核，`docs/ddp/03`
   §4 明确"不自研内核"；引入 `video.js` 或 `vidstack` 等于再叠一层 UI 内核，
   与 ADR-0014「组合现有能力 > 新建模块」的判定顺序相反。
2. **依赖最少**。`hls.js` 与 `video.js` 均无传递依赖；`vidstack` 引入
   `lit-html` + `floating-ui` 等 5 个包，且混入 Lit 组件模型与 React 并存，
   违反 `CLAUDE.md` §三「同一职责只保留一套技术栈」。
3. **HF 数据是 MP4 文件流，不是 HLS**。第一人称数据集的 Episode 视频通常是
   可直接播放的 MP4，浏览器原生即可；`hls.js` 只在数据源确为 HLS/m3u8
   （或将来的转码产物）时按需挂载，不必默认加载。
4. **时间轴与样本导航是业务组件，不是播放器能力**。候选三家都不提供
   "Episode 边界 + 标注片段 + 裁决标记"的时间轴，这部分无论如何都要自己写；
   选依赖最少的方案能让自研代码集中在真正差异化的地方。

放弃 `video.js` 的关键原因：它自带皮肤、插件体系与 `tech` 抽象层，
10.x 主版本迁移成本高（8→10 有过 breaking 变更），而 DDP 只用得到
"播放/暂停/seek/倍速"这一小片能力。

放弃 `vidstack` 的关键原因：5 个传递依赖 + Lit 运行时，且它同样不自带
业务时间轴；为有限收益扩大供应链面，不符合 §三。

## 4. 落地约束（阶段 7 实施时必须遵守）

| 约束 | 要求 |
|---|---|
| 默认路径 | 原生 `<video controls>`；`hls.js` 仅在 `canPlayType('application/vnd.apple.mpegurl')` 为空且源为 m3u8 时动态 `import()` |
| 代码分割 | `hls.js` 必须动态导入，不得进入首屏 bundle |
| 可访问性 | 键盘可操作（空格播放/暂停、方向键 seek）、`<track>` 字幕位、`aria-label` |
| 时间轴 | 自研业务组件，复用 `PageContainer` / `PageState`；不引入第二套图表库 |
| 大文件 | 不整段下载：播放走对象存储预签名 URL + HTTP Range；秒开依赖后端返回 `Accept-Ranges` |
| 失败关闭 | 解码失败/源不可用时显示 `PageState variant="error"`，不回退成静默空白 |
| 权限 | 预签名 URL 由后端按 `project_id` 签发，前端不自行拼接存储路径 |

## 5. 依赖评估（`CLAUDE.md` §三）

| 项 | `hls.js` |
|---|---|
| 用途 | HLS（m3u8）流播放，仅在需要时加载 |
| 替代方案 | 原生 HLS（Safari 支持，Chrome/Firefox 不支持）；video.js（更重）；vidstack（传递依赖多） |
| 版本固定 | `hls.js@1.7.4`（精确版本，不用 `^`） |
| 体积 | 约 200KB min（动态 chunk，不进首屏） |
| 许可证 | Apache-2.0 |
| 安全公告 | 无已知未修复高危；引入前复核 `npm audit` |

注：若阶段 7 首批数据源全部是 MP4，**可以不装 `hls.js`**，先用原生
`<video>` 打通链路，待出现 HLS 源再接。此路径为默认，符合增量迭代。

## 6. 回滚

选型未产生代码依赖；若阶段 7 发现原生 `<video>` 无法满足多相机同步，
重新评估时本 ADR 作废，需新立 ADR 说明原因，不得静默替换。
