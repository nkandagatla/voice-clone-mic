"""Optional commands for a separate RVC training checkout. They do not start training."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from voice_clone_mic.rvc_checkpoint import render_load_sites, scan_checkpoint_file
from voice_clone_mic.rvc_guard import (
    PINNED_COMMIT,
    RvcGuardError,
    assess_tree,
    planned_launch_argv,
    prepare_loopback_patch,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice-clone-mic rvc",
        description=(
            "Check or loopback-patch an external RVC checkout at "
            f"{PINNED_COMMIT}. This does not train a model or open a microphone."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("check", help="Verify the pinned tree and whether the loopback patch is present")
    check.add_argument("--root", type=Path, required=True)
    prepare = sub.add_parser("prepare", help="Write the reviewed loopback patch and a backup")
    prepare.add_argument("--root", type=Path, required=True)
    launch = sub.add_parser("launch", help="Start the patched WebUI only when the pin still matches")
    launch.add_argument("--root", type=Path, required=True)
    launch.add_argument("--python", default=sys.executable)
    launch.add_argument("--port", type=int, default=7865)
    audit = sub.add_parser("audit", help="Print load-site notes or disassemble a checkpoint header")
    audit.add_argument("--file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "check":
            return _print_assessment(assess_tree(args.root))
        if args.action == "prepare":
            assessment = prepare_loopback_patch(args.root)
            print("Loopback patch verified. The WebUI was not started.")
            return _print_assessment(assessment)
        if args.action == "launch":
            argv = planned_launch_argv(args.root, args.python, args.port)
            print("Launching the patched WebUI. Listening is fixed to 127.0.0.1 by the patch.")
            print(" ".join(argv))
            completed = subprocess.run(argv, cwd=args.root, check=False)
            return int(completed.returncode)
        if args.action == "audit":
            print(render_load_sites())
            if args.file is not None:
                report = scan_checkpoint_file(args.file)
                print(
                    f"scan kind={report.kind} verdict={report.verdict} "
                    f"unsafe_fallback={str(report.allows_unsafe_fallback).lower()}"
                )
                print(report.detail)
                return 0 if report.verdict == "candidate" else 2
            return 0
    except RvcGuardError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


def _print_assessment(assessment) -> int:
    print(f"state={assessment.state}")
    print(f"git_head={assessment.git_head or 'absent'}")
    print(f"webui_sha256={assessment.webui_sha256}")
    for reason in assessment.reasons:
        print(reason)
    return 0 if assessment.state in {"patched", "unpatched"} else 2


if __name__ == "__main__":
    sys.exit(main())
