"""记忆库校验脚本的结构性测试（``scripts/check_memory.py``）。

**为什么测这个**：记忆库的价值完全依赖校验器可信。如果 `check_memory.py` 有
漏洞（比如 check_ref 指向不存在的文件却放过），"记忆"就会退化成不可信文档，
比没有更糟。因此要用**注入故障**的方式验证它真的能抓到问题。

覆盖：
- 格式校验（缺字段、类型目录不符）
- check_ref 真实性（指向不存在的文件/测试必须报错）
- ID 唯一性
- 索引一致性（手改索引必须报错）
- 反向验证：故意破坏后脚本必须 exit 1
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MEMORY_DIR = ROOT / "docs" / "memory"
CHECK_SCRIPT = ROOT / "scripts" / "check_memory.py"
BUILD_SCRIPT = ROOT / "scripts" / "build_memory_index.py"

#: 需要一个完整可用的记忆库做基线；没有就跳过（避免在未建库时误报）。
pytestmark = pytest.mark.skipif(
    not MEMORY_DIR.is_dir() or not (MEMORY_DIR / "MEMORY_INDEX.md").is_file(),
    reason="记忆库尚未建立",
)


def _run(script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )


def test_memory_library_passes_validation() -> None:
    """基线：当前记忆库必须通过校验（这是 CI 的常规路径）。"""
    result = _run(CHECK_SCRIPT)
    assert result.returncode == 0, f"记忆库校验失败：\n{result.stdout}\n{result.stderr}"
    assert "校验通过" in result.stdout


def test_index_is_in_sync_with_cards() -> None:
    """索引必须与卡片一致；重新生成不应改变内容（幂等）。"""
    before = (MEMORY_DIR / "MEMORY_INDEX.md").read_text(encoding="utf-8")
    result = _run(BUILD_SCRIPT)
    assert result.returncode == 0, result.stderr
    after = (MEMORY_DIR / "MEMORY_INDEX.md").read_text(encoding="utf-8")
    assert before == after, "索引重新生成后内容变化，说明之前不一致"


def test_check_script_detects_dangling_check_ref(tmp_path: Path) -> None:
    """**核心反向测试**：check_ref 指向不存在的文件时必须报错。

    这是记忆库最容易腐烂的地方——测试被删/改名后，卡片仍在引用它。若校验器
    放过，卡片就成了"看起来有约束、实际没有"的假约束。
    """
    cards = sorted((MEMORY_DIR / "incidents").glob("MEM-*.md"))
    assert cards, "需要至少一张 INC 卡片"
    source = cards[0]
    broken = MEMORY_DIR / "incidents" / "MEM-INC-999-broken-ref.md"

    text = source.read_text(encoding="utf-8")
    # 把 ID 换成不冲突的，并把 check_ref 指向不存在的文件
    text = text.replace("MEM-INC-001", "MEM-INC-999")
    import re

    text = re.sub(
        r"(- \*\*check_ref\*\*[：:]).*",
        r"\1 `backend/tests/unit/does_not_exist_at_all.py`",
        text,
        count=1,
    )
    broken.write_text(text, encoding="utf-8")
    try:
        result = _run(CHECK_SCRIPT)
        assert result.returncode == 1, "check_ref 指向不存在的文件却被放过"
        assert "不存在" in result.stderr
    finally:
        broken.unlink()
        _run(BUILD_SCRIPT)


def test_check_script_detects_index_drift() -> None:
    """手改索引必须被抓住（防止"卡改了索引没改"的两边漂移）。"""
    index = MEMORY_DIR / "MEMORY_INDEX.md"
    original = index.read_text(encoding="utf-8")
    try:
        index.write_text(original + "\n<!-- 手工注入的漂移 -->\n", encoding="utf-8")
        result = _run(CHECK_SCRIPT)
        assert result.returncode == 1, "索引被手改却未被检测到"
        assert "不一致" in result.stderr or "索引" in result.stderr
    finally:
        index.write_text(original, encoding="utf-8")


def test_check_script_detects_missing_required_field(tmp_path: Path) -> None:
    """缺少 tags 等必填字段必须报错。"""
    source = sorted((MEMORY_DIR / "incidents").glob("MEM-*.md"))[0]
    broken = MEMORY_DIR / "incidents" / "MEM-INC-998-missing-tags.md"
    text = source.read_text(encoding="utf-8")
    text = text.replace("MEM-INC-001", "MEM-INC-998")
    import re

    text = re.sub(r"- \*\*tags\*\*[：:].*\n", "", text, count=1)
    broken.write_text(text, encoding="utf-8")
    try:
        result = _run(CHECK_SCRIPT)
        assert result.returncode == 1
        assert "tags" in result.stderr
    finally:
        broken.unlink()
        _run(BUILD_SCRIPT)


def test_all_active_incidents_have_executable_check() -> None:
    """所有 ACTIVE 的 INC 卡片必须有真实可执行的 check_ref。

    这是本记忆系统区别于"又一个文档库"的核心：事故必须绑回归测试。
    """
    import re

    missing: list[str] = []
    for path in sorted((MEMORY_DIR / "incidents").glob("MEM-*.md")):
        text = path.read_text(encoding="utf-8")
        if "状态：`ACTIVE`" not in text:
            continue
        match = re.search(r"- \*\*check_ref\*\*[：:]\s*(.+)", text)
        if not match or not match.group(1).strip("` "):
            missing.append(path.name)
    assert not missing, f"ACTIVE 的 INC 卡片缺 check_ref：{missing}"


def test_multi_type_directories_exist() -> None:
    """六类记忆目录必须齐备（缺目录会让新增卡片无处安放）。"""
    for directory in ("incidents", "decisions", "requirements", "sop", "changes", "ops"):
        assert (MEMORY_DIR / directory).is_dir(), f"缺少目录：{directory}"


def test_template_exists_and_documents_all_fields() -> None:
    """模板必须覆盖全部必填字段（否则新增卡片会缺项）。"""
    template = MEMORY_DIR / "templates" / "memory-card.md"
    assert template.is_file()
    text = template.read_text(encoding="utf-8")
    for field in (
        "类型与状态",
        "版本与血缘",
        "适用范围",
        "核心结论",
        "关键依据与细节",
        "影响与后续动作",
        "check_ref",
        "recurrence",
        "tags",
    ):
        assert field in text, f"模板缺少字段：{field}"
