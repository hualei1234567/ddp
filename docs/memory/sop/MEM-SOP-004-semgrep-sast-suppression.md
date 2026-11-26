### MEM-SOP-004 | Semgrep SAST 接入与误报基线的正确姿势

- **类型与状态**：`SOP` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-26`
- **适用范围**：项目 `DDP` | 环节 `CI/安全审计` | 批次/资产 `审计 R4 · SAST 接入（V52-042）` | 密级 `INTERNAL`
- **核心结论**：Semgrep 的阻断值不在"装上"，而在**基线分诊**：先跑全量、逐条判真假、真问题改代码、误报才 `nosemgrep` 且必须写理由；规则本身的误报要**收敛规则**，不是逐条静默。接入时踩到的 4 个反直觉点如下，照着做可以少红两轮 CI。
- **关键依据与细节**：

  **1. `# nosemgrep` 必须写完整 rule id**
  - 官方 registry 规则 id 常带重复末段，如
    `python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected`。
  - 只写前缀（少最后一段）**不会报错**，而是静默不生效 → 本地/CI 继续红，排查方向很容易错。
  - 正确写法：`# nosemgrep: <完整 check_id>`；`check_id` 从 `semgrep --json` 的
    `results[].check_id` 或失败输出里照抄。

  **2. 注释位置：finding 起始行，或紧邻它的上一行**
  - 本仓库 `# nosemgrep: ... —— 理由` 想写多行理由时，**理由行必须放在 nosemgrep 行之上**，
    nosemgrep 行紧贴代码；把理由插在 nosemgrep 与代码之间会让抑制失效（实测两次）。
  - 多行匹配（如 `text(` 跨行）以匹配起始行为准。

  **3. 自定义 f-string 规则的写法与盲区**
  - 用 `text(f"...{$EXPR}...")` + `metavariable-regex` 才能绑定插值表达式；
    `pattern-not` 里放 `metavariable-regex` 会连正常告警一起吞掉（实测）。
  - 排除"纯常量插值"用正则 `^(?![_]?[A-Z][A-Z0-9_]*$).*$`（含前导下划线，否则
    `_ACTIVE_STATUS_VALUES` 会漏）。
  - Semgrep 的 f-string 模式**匹配不到** `{name!r}`、`{name:>10}` 这类转换/格式说明符插值，
    属已知盲区（写进规则注释与 PR），不能宣称"覆盖所有拼接"。

  **4. 通配 metavariable 会被 import 解析放大**
  - `$CLIENT.$METHOD($URL, ...)` 在 `from sqlalchemy import create_engine` 存在时会把
    `create_engine(url)` 解析成 `sqlalchemy.create_engine` 而命中，SSRF 规则首轮 20 条
    误报即源于此。必须枚举 HTTP 方法名（get/post/…/request/stream），不要用 `$METHOD` 通配。

  **5. 过宽官方规则的收敛口径**
  - `avoid-sqlalchemy-text` 对一切 `text()` 报错，而本仓库 `text()` 只用于 SQLAlchemy Core
    无法表达的 DDL/运维语句（CREATE TRIGGER/DATABASE、SET LOCAL、部分索引），值一律 bind。
  - 处理：`scripts/check_sast.sh` 的 `--exclude-rule` 显式收敛，并写明"若 text() 开始承接
    请求输入应恢复"；注入风险由 `ddp-no-raw-sql-fstring`（只盯拼接）覆盖。

  **执行入口（唯一来源）**
  ```bash
  bash scripts/check_sast.sh       # 未安装 semgrep 时退出码 2，不静默跳过
  ```
  - 扫描目标：`backend/app scripts`；tests/migrations 不扫（噪声淹没信号），口径见
    `.semgrepignore` 与脚本注释。
  - CI：`security` job 的「Semgrep SAST（阻断）」步，pip 安装固定版本引擎后调用同一脚本。

- **影响与后续动作**：
  - 新版规则/新告警先分类再处理：真问题当场修，误报 `nosemgrep` 写明"为什么不可控/已有等价校验"；
  - 官方规则集来自 registry 会漂移，CI 变红先看是不是上游规则升级，再判断真假；
  - 责任人：DevOps；**每次 Semgrep 告警变化时复核基线**。

---

#### 工程扩展字段

- **check_ref**：`scripts/check_sast.sh` + `.semgrep.yml`
- **recurrence**：`1`
- **tags**：`sast,semgrep,nosemgrep,ssrf,sql-injection,ci,false-positive,baseline`
