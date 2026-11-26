# 依赖可复现与 SBOM（R5）

- 版本：v1.0 · 2026-11-26
- 分支：`feat/v52-deps-lock-sbom`
- 定位：本文件是**后端 Python 依赖锁定与供应链清单**的唯一操作口径。
  直接依赖的「意图」写在 `requirements*.txt`，可复现的「解析结果」写在
  `requirements*.lock`，供应链清单写在 `sbom/*.cdx.json`。
- 非目标：不改业务代码、不改契约、不改迁移；不升级既有依赖版本（本轮只锁）。

---

## 1. 为什么必须锁传递依赖

`requirements.txt` 里 15 个直接依赖都已经 `==` 精确固定，但这**不足以复现环境**：

- 直接依赖的依赖（传递依赖）没有约束，`pip install -r requirements.txt`
  每次都会按当天索引上的最新兼容版本解析。例如 `fastapi==0.115.6` 依赖
  `starlette` / `pydantic` / `anyio`，这些包的补丁版本每天都在变。
- 同一份 `requirements.txt`，今天和下周装出的 `pip freeze` 不同。
  后果：CI 红/绿不可解释、bug 无法在本地复现、审计无法回答
  「线上到底跑的是哪个版本」。
- 没有哈希时，镜像站被投毒或 CDN 返回不同内容都无法察觉。

锁文件把「直接依赖 + 全部传递依赖 + 每个发布包的 sha256」写死，
`pip install -r *.lock` 自动进入 **hash-checking 模式**：任何版本或哈希不符即失败。
CI 与本地装出的就是同一组版本。

---

## 2. 文件分工

| 文件 | 角色 | 谁消费 |
|---|---|---|
| `backend/requirements.txt` | **人读的意图声明**：15 个直接依赖 + 每条依赖的评估/许可/安全备注 | 人、`pip-compile` 输入、ADR 引用 |
| `backend/requirements.lock` | **运行期解析结果**：47 个包（15 直接 + 32 传递），1183 条 sha256 | 生产镜像（若按 lock 构建）、`sbom` 生成、`pip-audit` |
| `backend/requirements-dev.txt` | 开发/CI 意图声明：`-r requirements.txt` + 5 个开发工具 | 人、`pip-compile` 输入 |
| `backend/requirements-dev.lock` | **CI/开发解析结果**：53 个包（含 5 个开发工具），1326 条 sha256 | CI 全部后端 job（backend / e2e / contract）、`make venv` |
| `data-platform/requirements.txt` | data-platform 独立意图声明：5 个直接依赖 | 人、`pip-compile` 输入 |
| `data-platform/requirements.lock` | data-platform 解析结果：22 个包（5 直接 + 17 传递），786 条 sha256 | CI（后端测试要 import `ddp_data`） |
| `sbom/backend.cdx.json` | 运行期后端依赖的 CycloneDX 1.6 JSON 清单（47 组件，含 sha256） | 供应链事件影响面定位 |
| `sbom/data-platform.cdx.json` | data-platform 依赖的 CycloneDX 1.6 JSON 清单（22 组件） | 同上 |

> lock 文件头部的 `# via ...` 注释会标出每个传递依赖是被谁引入的，
> 升级排查时先看它。**lock 是生成物，不手改**。

---

## 3. 重新生成 lock

前置：Python **3.11**（CI 与本地一致；跨小版本会导致 wheels/markers 不同）、
`pip-tools==7.7.0`。`--no-emit-index-url` 是为了不把本机镜像站地址写进 lock
（否则 CI 被强制走某个镜像）；哈希来自 PyPI/镜像上同一份文件，二者一致。

```bash
# 一次性工具环境（不污染 backend/.venv）
python3 -m venv /tmp/piptools-venv
/tmp/piptools-venv/bin/pip install "pip-tools==7.7.0"

# 运行期
cd backend
/tmp/piptools-venv/bin/pip-compile --generate-hashes \
  --no-emit-index-url --no-emit-trusted-host \
  --output-file=requirements.lock requirements.txt

# 开发/CI
/tmp/piptools-venv/bin/pip-compile --generate-hashes \
  --no-emit-index-url --no-emit-trusted-host \
  --output-file=requirements-dev.lock requirements-dev.txt

# data-platform
cd ../data-platform
/tmp/piptools-venv/bin/pip-compile --generate-hashes \
  --no-emit-index-url --no-emit-trusted-host \
  --output-file=requirements.lock requirements.txt
```

生成后**必须一起提交**对应的 `.txt`（意图）与 `.lock`（结果），且 lock 与
`requirements*.txt` 的版本必须一致；不一致即视为提交错误。

### 3.1 必须在 Linux 目标平台生成 lock（血泪坑）

`pip-compile` 的解析是**按当前平台求环境标记（marker）**的，不是跨平台求全集。
2026-11-26 首版是在 macOS arm64 上生成的，CI（ubuntu-latest x86_64）安装直接红：

```
ERROR: In --require-hashes mode, all requirements must have their versions pinned with ==.
These do not:
    greenlet!=0.4.17 ... (from sqlalchemy==2.0.36->-r requirements-dev.lock)
```

原因：`SQLAlchemy 2.0.36` 的 `greenlet` 依赖带平台标记
（`platform_machine` 为 `aarch64/x86_64/...` 时才有），macOS arm64 的
`platform_machine` 是 `arm64`，**不命中** → 本机解析时 greenlet 被漏掉；
CI/Linux 命中 → 需要 greenlet，但 lock 里没有且 hash 模式下不允许未固定版本。
hash-checking 模式把这个平台差异从「悄悄少装一个包」升级成了**显式 CI 失败**。

正确做法：**在 CI/生产的目标平台（Linux）里生成**，不要在本机 macOS 生成后直接提交：

```bash
# 在 Linux 容器里生成（与 CI ubuntu-latest 的 marker 求值一致；本机 Docker 为 arm64 亦可，
# 因为 greenlet 的标记同时覆盖 aarch64 与 x86_64，二者解析结果相同）
cd <repo>
docker run --rm -v "$PWD":/src -w /src python:3.11-slim bash -lc '
  set -e
  pip install -q "pip-tools==7.7.0"
  cd backend
  pip-compile --generate-hashes --no-emit-index-url --no-emit-trusted-host \
    --output-file=requirements.lock requirements.txt
  pip-compile --generate-hashes --no-emit-index-url --no-emit-trusted-host \
    --output-file=requirements-dev.lock requirements-dev.txt
  cd ../data-platform
  pip-compile --generate-hashes --no-emit-index-url --no-emit-trusted-host \
    --output-file=requirements.lock requirements.txt
'
```

生成后要**双平台各验证一次安装**（§6）：Linux 容器验证 CI 一定装得上，
macOS 本机验证开发机也装得上（Linux 解析出的 lock 在 macOS 安装时可能多装
`greenlet` 这类仅 Linux 需要的包，无功能影响）。

---

## 4. 怎么升级依赖

原则：**先改意图，再重解析，最后跑验证**。不要直接手改 lock。

1. 改 `requirements.txt`（或 `requirements-dev.txt`）里的版本号，并同步更新该行
   的评估注释（用途/替代方案/许可/安全公告复核，见 `CLAUDE.md` §三）。
2. 只升一个包时，优先用 `--upgrade-package`，避免无关包一起漂移：
   ```bash
   cd backend
   /tmp/piptools-venv/bin/pip-compile --generate-hashes \
     --no-emit-index-url --no-emit-trusted-host \
     --upgrade-package <包名> --output-file=requirements-dev.lock requirements-dev.txt
   ```
3. 安全修复类升级（如 `pip-audit` 报警）按「直接依赖改 txt + 重解析 lock」走；
   如果报警包是传递依赖，先找是谁引入的（读 lock 的 `# via`），
   再通过 `--upgrade-package` 或加显式约束解决，不要盲升。
4. 全量刷新（`--upgrade`，不带包名）会同时升所有包，**只在大版本规划时使用**，
   且必须附 `pytest` 全绿证据。
5. 提交前本地验证（见 §6）；CI 用 lock 安装，lock 与 txt 不一致不会自动报错
   （见 §8 缺口），评审时人工核对。

---

## 5. SBOM 怎么用

- 格式：**CycloneDX 1.6 JSON**。每个组件含 `name` / `version` / `purl`
  （`pkg:pypi/<name>@<version>`）/ 发布包 sha256。
- 生成命令（`cyclonedx-bom==9.0.0`，`--output-reproducible` 去掉时间戳，
  产物可入库、可 diff）：

```bash
pip install "cyclonedx-bom==9.0.0"
cyclonedx-py requirements backend/requirements.lock \
  --mc-type application --output-reproducible --of json \
  -o sbom/backend.cdx.json
cyclonedx-py requirements data-platform/requirements.lock \
  --mc-type library --output-reproducible --of json \
  -o sbom/data-platform.cdx.json
```

- 典型用法（供应链事件，例如某包爆出 CVE 要找影响面）：

```bash
# 1) 该包是否在我们的依赖面里、哪个版本
jq -r '.components[] | select(.name=="pyjwt") | "\(.name) \(.version)"' \
  sbom/backend.cdx.json
# 2) 反查某版本对应哪些发布包哈希（比对 HIDS/镜像层里的文件）
jq -r '.components[] | select(.name=="pyjwt") | .externalReferences[].hashes[]?.content' \
  sbom/backend.cdx.json | head -3
```

- 直接依赖在哪个文件维护、哪个包是传递依赖：读 lock 的 `# via`，不要靠猜。
- SBOM 是**快照**，随 lock 变更需要重新生成并一起提交（否则清单与实际环境脱节）。

---

## 6. 验证（干净 venv 复现）

```bash
# A. Linux 平台（= CI 口径，必须过）
docker run --rm -v "$PWD":/src -w /src python:3.11-slim bash -lc '
  python -m venv /tmp/ci-venv
  /tmp/ci-venv/bin/pip install -r backend/requirements-dev.lock
  /tmp/ci-venv/bin/pip install -r data-platform/requirements.lock
  PYTHONPATH=/src/data-platform /tmp/ci-venv/bin/python -c "import fastapi, sqlalchemy, psycopg, greenlet, pyarrow, ddp_data"
'

# B. 本机 macOS（= 开发机口径，也要能装）
rm -rf /tmp/locktest && python3 -m venv /tmp/locktest
/tmp/locktest/bin/pip install -r backend/requirements-dev.lock   # CI 用这份
/tmp/locktest/bin/pip list --format=freeze | wc -l              # 期望 53 + pip/setuptools
```

- exit=0 且包数 = lock 中 `==` 行数（+ venv 自带的 `pip`/`setuptools`）即通过；
  任何一个包版本或 sha256 不符，pip 会直接失败（hash-checking 模式）。
- 2026-11-26 实测（macOS arm64 本地 + `linux/arm64` 容器各一次，均按 CI 口径）：
  `backend/requirements.lock` → exit 0 / 47 包；`backend/requirements-dev.lock` → exit 0 / 53 包；
  `data-platform/requirements.lock` → exit 0 / 22 包。

---

## 7. CI 现状

| job | 改动前 | 改动后 |
|---|---|---|
| `backend` | `pip install -r requirements-dev.txt` + `data-platform/requirements.txt` | 两份 `.lock`（hash-checking） |
| `e2e` | 同上（装进 `backend/.venv`） | 两份 `.lock` |
| `contract` | `pip install -r backend/requirements-dev.txt` | `backend/requirements-dev.lock` |
| `security` | `pip-audit -r backend/requirements.txt` / `data-platform/requirements.txt`（只覆盖直接依赖） | `pip-audit -r` 两份 `.lock`（覆盖传递依赖）；仍 `continue-on-error`，只告警。同 job 的 `pytest scripts/tests` 会跑锁/SBOM 守卫测试 |

`actions/setup-python` 的 `cache: pip` 的 `cache-dependency-path` 全部改为 lock，
否则 lock 更新不会触发缓存失效、会装回旧 wheel。

> 生产镜像（Dockerfile）本轮未改：生产如何构建不在本任务范围，需单独评审后
> 改为 `pip install --require-hashes -r backend/requirements.lock`。

---

## 8. 已知边界与缺口（诚实声明）

| 项 | 现状 |
|---|---|
| lock 与 `.txt` 漂移、SBOM 与 lock 脱节 | 已由静态守卫 `scripts/tests/test_requirements_lock.py` 拦截（随 `pytest scripts/tests` 在本地 `check.sh` 第 6 步与 CI security job 执行）：直接依赖版本一致、每包有 hash、Linux 平台解析、SBOM 组件与 lock 同步。未改 `scripts/check.sh` 本身 |
| SBOM 无主组件（`metadata.component`） | `backend/pyproject.toml` 只有 `[tool.ruff]`/`[tool.pytest]`，不是 PEP 621，`cyclonedx-py --pyproject` 会报错；因此清单只有 47 个 `library` 组件、无顶层应用标识。补 PEP 621 `[project]` 后应重新生成 |
| Python 小版本与 pip 版本未锁 | lock 由 Python 3.11 解析、CI 固定 3.11；但 Python 补丁版本（3.11.x）与 pip 自身版本未锁，存在理论漂移 |
| 前端依赖 | 已有 `apps/web/package-lock.json`（npm lock），不在本文件范围 |
| 已知漏洞 | 本轮不升版本。`pip-audit` 现可扫描全量传递依赖，历史告警（如 `pyjwt==2.10.1`、`python-multipart==0.0.20`）会显性列出，属后续升级任务 |

## 9. 回滚

纯新增文件 + CI 安装来源切换，回滚方式：把 `.github/workflows/ci.yml` 的
安装来源改回 `pip install -r requirements-dev.txt`、删除 `*.lock` 与 `sbom/` 即可；
不涉及数据库、契约、业务代码，回滚无数据风险。
