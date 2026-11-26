"""依赖锁与 SBOM 的一致性守卫（审计 R5）。

背景与血泪坑见 `docs/ops/dependency-reproducibility.md`：`requirements*.txt` 是
人读的意图声明，`requirements*.lock` 是带 sha256 的解析结果，CI 按 lock 安装。

这里的断言全部是**静态**的（不装依赖、不联网），因此可以在 CI 的 security job
与本地 `scripts/check.sh` 第 6 步里低成本拦人：

1. `.txt` 里的每个直接依赖，在对应 `.lock` 中必须是**同一个版本**（防止只改意图
   不改 lock、或反向）；
2. lock 里每个包都必须有 sha256（hash-checking 模式是「全有或全无」，漏一个
   哈希 CI 就会报错）；
3. lock 必须是在 **Linux 目标平台**解析的：`SQLAlchemy 2.0` 的 `greenlet` 依赖
   带 `platform_machine` 标记，macOS arm64 解析会漏掉 greenlet，而 CI（Linux）
   需要它，hash 模式下直接失败——首版就是这么红的；
4. `sbom/*.cdx.json` 必须与 lock 的包集合/版本一致（防止改了 lock 忘了重生成 SBOM）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# (意图声明, 解析结果)
DECLARED_LOCK_PAIRS = [
    ("backend/requirements.txt", "backend/requirements.lock"),
    ("backend/requirements-dev.txt", "backend/requirements-dev.lock"),
    ("data-platform/requirements.txt", "data-platform/requirements.lock"),
]

# (解析结果, 对应 SBOM)；开发面 lock 不单独出 SBOM，运行面 SBOM 覆盖生产依赖。
LOCK_SBOM_PAIRS = [
    ("backend/requirements.lock", "sbom/backend.cdx.json"),
    ("data-platform/requirements.lock", "sbom/data-platform.cdx.json"),
]

# SQLAlchemy 2.0 的 greenlet 是平台标记依赖，Linux 解析必有；见测试 3 的说明。
_SQLALCHEMY_WITH_GREENLET_MAJOR = 2

_REQUIREMENT_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<extras>\[[^\]]*\])?=="
    r"(?P<version>[^\s\\]+)\s*\\?$"
)
_HASH_LINE = re.compile(r"--hash=sha256:([0-9a-f]{64})")
_INCLUDE_LINE = re.compile(r"^-r\s+(?P<path>\S+)$")


def _normalize(name: str) -> str:
    """PEP 503 归一化：大小写、`.`/`_` 与 `-` 等价。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_declared(path: Path, seen: set[Path] | None = None) -> dict[str, str]:
    """读取意图声明（展开 `-r` 引用），返回 {归一化包名: 版本}。"""
    seen = seen if seen is not None else set()
    if path in seen:
        return {}
    seen.add(path)
    declared: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        include = _INCLUDE_LINE.match(line)
        if include:
            declared.update(_read_declared(path.parent / include.group("path"), seen))
            continue
        match = _REQUIREMENT_LINE.match(line)
        if match:
            declared[_normalize(match.group("name"))] = match.group("version")
    return declared


def _read_lock(path: Path) -> tuple[dict[str, str], dict[str, int]]:
    """读取 lock，返回 ({包名: 版本}, {包名: 哈希条数})。"""
    versions: dict[str, str] = {}
    hashes: dict[str, int] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _REQUIREMENT_LINE.match(line)
        if match:
            current = _normalize(match.group("name"))
            versions[current] = match.group("version")
            hashes[current] = 0
            continue
        if current is not None and current in hashes and _HASH_LINE.search(line):
            hashes[current] += 1
    return versions, hashes


def _sbom_components(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload.get("bomFormat") == "CycloneDX", (
        f"{path} 不是 CycloneDX：{payload.get('bomFormat')}"
    )
    return {
        _normalize(component["name"]): component["version"]
        for component in payload["components"]
    }


def test_declared_dependencies_match_lock() -> None:
    """意图声明里的每个直接依赖，必须在 lock 里同版本存在。"""
    problems: list[str] = []
    for declared_path, lock_path in DECLARED_LOCK_PAIRS:
        declared = _read_declared(REPO_ROOT / declared_path)
        locked, _ = _read_lock(REPO_ROOT / lock_path)
        for name, version in sorted(declared.items()):
            if name not in locked:
                problems.append(f"{lock_path} 缺少 {declared_path} 声明的 {name}=={version}")
            elif locked[name] != version:
                problems.append(
                    f"{lock_path} 中 {name}={locked[name]} 与 {declared_path} 的 {version} 不一致"
                )
    assert not problems, "意图声明与 lock 漂移：\n" + "\n".join(problems)


def test_every_locked_package_has_hash() -> None:
    """lock 里每个包都必须带 sha256，否则 `--require-hashes` 安装会直接失败。"""
    problems: list[str] = []
    for _, lock_path in DECLARED_LOCK_PAIRS:
        _, hashes = _read_lock(REPO_ROOT / lock_path)
        for name, count in sorted(hashes.items()):
            if count == 0:
                problems.append(f"{lock_path}: {name} 没有任何 --hash=sha256")
    assert not problems, "lock 哈希缺失：\n" + "\n".join(problems)


def test_lock_resolved_on_linux_platform() -> None:
    """lock 必须在 Linux 目标平台解析：SQLAlchemy 2.0 的 greenlet 标记是试金石。

    `SQLAlchemy 2.0.x` 的依赖为 `greenlet!=0.4.17; platform_machine in
    (aarch64, x86_64, ...)`（见其 METADATA）。macOS arm64 的 platform_machine 是
    `arm64`，不命中，本机生成的 lock 会漏掉 greenlet；CI/Linux 命中，hash 模式下
    立刻报 "all requirements must have their versions pinned with =="。
    SQLAlchemy 升级到不再依赖 greenlet 的版本后，本用例可随之删除或改写。
    """
    for _, lock_path in (
        ("backend/requirements.txt", "backend/requirements.lock"),
        ("backend/requirements-dev.txt", "backend/requirements-dev.lock"),
    ):
        locked, _ = _read_lock(REPO_ROOT / lock_path)
        sqlalchemy_version = locked.get("sqlalchemy")
        assert sqlalchemy_version is not None, f"{lock_path} 缺少 sqlalchemy"
        major = int(sqlalchemy_version.split(".")[0])
        if major == _SQLALCHEMY_WITH_GREENLET_MAJOR:
            assert "greenlet" in locked, (
                f"{lock_path} 缺少 greenlet：这份 lock 可能是在 macOS arm64 上生成的。"
                "请在 Linux 目标平台重新生成，"
                "见 docs/ops/dependency-reproducibility.md §3.1"
            )


def test_sbom_matches_lock() -> None:
    """SBOM 必须与 lock 的包集合和版本一致（改了 lock 必须重生成 SBOM）。"""
    problems: list[str] = []
    for lock_path, sbom_path in LOCK_SBOM_PAIRS:
        locked, _ = _read_lock(REPO_ROOT / lock_path)
        components = _sbom_components(REPO_ROOT / sbom_path)
        missing = sorted(set(locked) - set(components))
        extra = sorted(set(components) - set(locked))
        if missing:
            problems.append(f"{sbom_path} 缺少 lock 中的组件：{missing}")
        if extra:
            problems.append(f"{sbom_path} 多出 lock 中没有的组件：{extra}")
        for name in sorted(set(locked) & set(components)):
            if locked[name] != components[name]:
                problems.append(
                    f"{sbom_path} 中 {name}={components[name]} 与 {lock_path} 的"
                    f" {locked[name]} 不一致"
                )
    assert not problems, "SBOM 与 lock 脱节：\n" + "\n".join(problems)
