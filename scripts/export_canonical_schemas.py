#!/usr/bin/env python3
"""把 Canonical v1 的 JSON Schema 导出到契约包（V31-017）。

字段声明在 ``data-platform/ddp_data/canonical/v1.py``；本脚本只负责把生成结果
写到 ``packages/contracts/schemas/canonical-v1/``，供跨语言消费者与契约审查使用。

用法::

    python3 scripts/export_canonical_schemas.py           # 写入
    python3 scripts/export_canonical_schemas.py --check   # 只校验是否漂移（CI 用）

``--check`` 模式下任何差异都以退出码 1 结束，并打印需要更新的文件。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PLATFORM_DIR = REPO_ROOT / "data-platform"
OUTPUT_DIR = REPO_ROOT / "packages" / "contracts" / "schemas" / "canonical-v1"

if str(DATA_PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_PLATFORM_DIR))

from ddp_data.canonical.json_schema import (  # noqa: E402
    canonical_json_schemas,
    render_json_schema,
)


def _expected_files() -> dict[Path, str]:
    return {
        OUTPUT_DIR / filename: render_json_schema(schema)
        for filename, schema in canonical_json_schemas().items()
    }


def _display(path: Path) -> str:
    """仓库内路径显示为相对路径；测试注入的临时目录显示绝对路径。"""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验提交产物是否与生成结果一致；有差异时退出码 1",
    )
    args = parser.parse_args(argv)

    expected = _expected_files()
    if args.check:
        drifted = [
            path
            for path, content in expected.items()
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        if drifted:
            print("Canonical JSON Schema 与字段声明不一致，请运行：", file=sys.stderr)
            print("  python3 scripts/export_canonical_schemas.py", file=sys.stderr)
            for path in drifted:
                print(f"  - {_display(path)}", file=sys.stderr)
            return 1
        print(f"Canonical JSON Schema 已同步（{len(expected)} 个文件）")
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in expected.items():
        path.write_text(content, encoding="utf-8")
        print(f"写入 {_display(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
