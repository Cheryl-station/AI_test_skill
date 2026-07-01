#!/usr/bin/env python3
"""Keep the packaged skill script synchronized with the root CLI script."""

from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "api_test_kit.py"
TARGET = ROOT / "skills" / "api-test-kit" / "scripts" / "api_test_kit.py"


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        if SOURCE.read_bytes() != TARGET.read_bytes():
            print(f"脚本不同步：请运行 python {Path(__file__).as_posix()}", file=sys.stderr)
            return 1
        print("脚本已同步")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, TARGET)
    print(f"已同步 {SOURCE.relative_to(ROOT)} -> {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
