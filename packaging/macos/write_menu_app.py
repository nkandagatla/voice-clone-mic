"""Create an unsigned menu-bar app bundle. Does not install, sign, or download a model."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from voice_clone_mic.ptt.mac_app import write_unsigned_menu_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write an unsigned Voice Clone Mic menu app.")
    parser.add_argument("--dest", type=Path, default=Path("dist"))
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    app = write_unsigned_menu_app(args.dest, args.python)
    print(app)
    print("Unsigned and not notarized. It was not launched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
