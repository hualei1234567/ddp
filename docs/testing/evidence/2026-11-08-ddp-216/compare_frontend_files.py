"""比较 main 检出与 ddp-fe 工作树的前端契约相关文件是否逐字节一致。

用途：前端依赖只安装在 ddp-fe 工作树；若 ddp-fe 工作区文件与 main 完全一致，
则在该工作树运行 Vitest 的结果可视为 main 的复验证据。不一致时输出差异文件，
避免用旧工作区结果冒充当前提交。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

MAIN_ROOT = Path("/home/user/vscodeworkspace/DataPlatform")
FE_ROOT = Path("/home/user/agent-team/ddp-team/worktrees/ddp-fe")

FILES = (
    "apps/web/package.json",
    "apps/web/src/services/__fixtures__/deviceContract.fixtures.ts",
    "apps/web/src/services/__fixtures__/deviceContract.snapshot.json",
    "apps/web/src/services/deviceContract.test.ts",
    "apps/web/src/services/deviceValidation.test.ts",
    "apps/web/src/services/deviceValidation.ts",
    "apps/web/src/services/validation.ts",
    "apps/web/src/types/device.ts",
    "apps/web/scripts/device_contract_snapshot.py",
    "apps/web/scripts/deviceContract.live.test.mjs",
)


def digest(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    mismatches = 0
    print(f"{'文件':<62} {'main':<12} {'ddp-fe':<12} 结果")
    for relative in FILES:
        left = digest(MAIN_ROOT / relative)
        right = digest(FE_ROOT / relative)
        same = left is not None and left == right
        if not same:
            mismatches += 1
        print(
            f"{relative:<62} {(left or '缺失')[:10]:<12} "
            f"{(right or '缺失')[:10]:<12} {'一致' if same else '不一致'}"
        )
    print(f"\n不一致文件数：{mismatches}")
    return 0 if mismatches == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
