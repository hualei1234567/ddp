### MEM-INC-025 | macOS 上生成的 pip lock 在 Linux CI 因 greenlet 平台标记直接红

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-26`
- **适用范围**：项目 `DDP` | 环节 `依赖锁定 / CI` | 批次/资产 `backend、data-platform 的 requirements*.lock` | 密级 `INTERNAL`
- **核心结论**：`pip-compile` 只按**当前平台**求环境标记，不跨平台求全集。在 macOS arm64 上生成的带哈希 lock 会漏掉 `SQLAlchemy 2.0` 的 `greenlet`（其标记只命中 `aarch64`/`x86_64` 等 `platform_machine`，macOS 报 `arm64`）；CI 是 Linux，需要 greenlet，`--require-hashes` 模式下报 `all requirements must have their versions pinned with ==` 直接失败。**lock 必须在 CI/生产目标平台（Linux）生成，并在双平台各验证一次安装。**
- **关键依据与细节**：
  - 首版 lock 在 macOS arm64 由 `pip-compile --generate-hashes` 生成，包数 46/52；
    CI `backend`/`e2e`/`contract` 三个 job 均在「安装依赖」步骤 16–28s 失败：
    `ERROR: In --require-hashes mode, all requirements must have their versions pinned with ==.
    These do not: greenlet!=0.4.17 ... (from sqlalchemy==2.0.36->-r requirements-dev.lock)`
  - 在 `python:3.11-slim` 容器（Linux）重新生成后包数 47/53（恰好多出 greenlet），
    hash 数 1183/1326；macOS 本机与 Linux 容器各装一遍均 exit=0、逐包版本一致。
  - 顺带确认 hash-checking 是真的生效：把某包全部哈希改成 0 → 安装 exit=1，
    `THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE`。
    （只改多个哈希中的一个不报错，因为 sdist 哈希仍是合法候选，属正常行为。）
  - 第二条教训：`pip install -r lock` 在**已有包**的 venv 里不会下载/校验哈希，
    验证哈希必须用全新 venv，否则看到的 exit=0 是假绿。
- **影响与后续动作**：
  - 生成/升级 lock 一律走 Linux 容器（`docs/ops/dependency-reproducibility.md` §3.1），
    改完 lock 要重生成 SBOM（`sbom/*.cdx.json`）；
  - 已加静态守卫 `scripts/tests/test_requirements_lock.py`：`.txt` 与 lock 版本一致、
    每包有 hash、Linux 解析（greenlet 存在）、SBOM 与 lock 同步；随
    `pytest scripts/tests`（本地 check.sh 第 6 步 / CI security job）自动执行；
  - 责任人：QA & DevOps；后续接 Dockerfile 时同样按 lock 构建。

---

#### 工程扩展字段

- **check_ref**：`scripts/tests/test_requirements_lock.py::test_lock_resolved_on_linux_platform + .github/workflows/ci.yml`（「安装依赖」按 lock 安装，错平台生成会在此步失败）
- **recurrence**：`1`
- **tags**：`ci, dependency-lock, greenlet, hash-checking, pip-compile, platform-marker, sbom`
