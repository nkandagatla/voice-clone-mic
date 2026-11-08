"""Fail-closed checks for an external RVC checkout.

This module does not vendor Retrieval-based Voice Conversion. It only verifies
a pinned upstream tree and can rewrite the WebUI bind address to loopback.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

PINNED_COMMIT = "81eed5e8f68b6bed1789f682fe78cdd324495afc"
LOOPBACK_HOST = "127.0.0.1"
BACKUP_NAME = "webui.py.vcm-upstream.bak"

# SHA256 of the listed files at PINNED_COMMIT. webui.py is the unpatched file.
UPSTREAM_SHA256 = {
    "webui.py": "3c6ebf133db4a4b1cebb671f1077bf3f0fa4b570d478c1247a3209b4e3a7167f",
    "configs/config.py": "f304463184b55898aa256fadeafd50d640ed0f880dbae6f89ad54db358240043",
    "infer/hubert.py": "bbfba54d1d83eae3fb634b2909f8f6bf41e6ffe436200ec246867c5c93ebba26",
    "infer/rmvpe.py": "074171aec63e4b68f04d8691c62af50a1d99b9bc6dc56c4d6749fad4ba64853a",
    "infer/cli.py": "8e321cd606bfa1f4756685db897e90df2278e77d3e58869bc83d4735ff7b7387",
    "infer/vc/modules.py": "7b2e12f56c2b2462d7944d8eda470796ffd4aec50f784c3f6f5169133eaae89b",
    "train/train.py": "5e7d316962943935422ff225468ab22ac8dee30668131ba53728ed2e4efe78fd",
    "train/utils.py": "43093d5c394ecd9938a565a981450fa388d9ee826a1ae0bd7d38e8f265c4a69d",
    "train/process_ckpt.py": "b19785cf715b0ce1c39726d2b559ed54beb53ae5e41f3f75c41592ead2092d5c",
    "train/dataset/extract_f0.py": "0fc4e35b8dba10b722b145ba0566632f35248c85bfae1341dbb95f8016733147",
    "train/dataset/extract_hubert_feature.py": "20b6aed536d1348f3620eeaefd0b54875b8c722dfbc47d5b41462b4e3bbd02a6",
}

# Filled after the exact patch is applied to that webui.py. Tests also recompute it.
PATCHED_WEBUI_SHA256 = "4cd410f526dbce044dba98848e306bf8073fb3e14dd2f2c30bb6416ee87b7cea"

_FIND_PORT = 'def find_available_port(start_port, host="0.0.0.0"):'
_FIND_PORT_PATCHED = 'def find_available_port(start_port, host="127.0.0.1"):'
_PORT_CALL = "        config.listen_port = find_available_port(next_port)\n"
_PORT_CALL_PATCHED = '        config.listen_port = find_available_port(next_port, host="127.0.0.1")\n'
_LAUNCH = """            queued_app.launch(
                server_name="0.0.0.0",
                inbrowser=not config.noautoopen,
                server_port=config.listen_port,
                quiet=True,
            )
"""
_LAUNCH_PATCHED = """            # voice-clone-mic loopback patch: 127.0.0.1 only, sharing off, browser off.
            queued_app.launch(
                server_name="127.0.0.1",
                share=False,
                inbrowser=False,
                server_port=config.listen_port,
                quiet=True,
            )
"""
_COLAB = """    if config.iscolab:
        app.queue(concurrency_count=511, max_size=1022).launch(share=True)
    else:
        launch_webui_with_port_fallback(app, config)
"""
_COLAB_PATCHED = """    if config.iscolab:
        raise RuntimeError(
            "Colab public sharing is disabled by the voice-clone-mic loopback patch."
        )
    launch_webui_with_port_fallback(app, config)
"""


class RvcGuardError(Exception):
    """The external RVC tree is not safe to launch."""


@dataclass(frozen=True)
class TreeAssessment:
    state: str
    reasons: tuple[str, ...]
    git_head: str | None
    webui_sha256: str

    @property
    def launch_allowed(self) -> bool:
        return self.state == "patched"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def assert_loopback_host(host: str) -> None:
    if host != LOOPBACK_HOST:
        raise RvcGuardError(
            f"Refusing host {host!r}. The training UI may bind only {LOOPBACK_HOST}."
        )


def patch_webui_text(text: str) -> str:
    """Return loopback-only WebUI source. Each unsafe anchor must occur once."""

    replacements = (
        (_FIND_PORT, _FIND_PORT_PATCHED),
        (_PORT_CALL, _PORT_CALL_PATCHED),
        (_LAUNCH, _LAUNCH_PATCHED),
        (_COLAB, _COLAB_PATCHED),
    )
    updated = text
    for old, new in replacements:
        count = updated.count(old)
        if count != 1:
            raise RvcGuardError(
                f"Refusing to patch WebUI source: expected one exact anchor, found {count}."
            )
        updated = updated.replace(old, new, 1)
    if 'server_name="0.0.0.0"' in updated or "share=True" in updated or 'host="0.0.0.0"' in updated:
        raise RvcGuardError("Patch left a public bind or share enabled.")
    if 'server_name="127.0.0.1"' not in updated or "share=False" not in updated or "inbrowser=False" not in updated:
        raise RvcGuardError("Patch did not install the loopback launch.")
    return updated


def read_git_head(root: Path) -> str | None:
    git_dir = root / ".git"
    if not git_dir.exists():
        return None
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RvcGuardError("RVC checkout has .git but HEAD could not be read.")
    return completed.stdout.strip()


def assess_tree(
    root: Path,
    *,
    upstream: dict[str, str] | None = None,
    patched_webui: str | None = None,
    commit: str | None = None,
) -> TreeAssessment:
    root = root.resolve()
    expected_files = upstream or UPSTREAM_SHA256
    patched_hash = patched_webui or PATCHED_WEBUI_SHA256
    required_commit = commit or PINNED_COMMIT
    reasons: list[str] = []
    webui_path = root / "webui.py"
    if not webui_path.is_file():
        return TreeAssessment("rejected", ("webui.py is missing.",), None, "")
    webui_hash = sha256_file(webui_path)
    try:
        git_head = read_git_head(root)
    except RvcGuardError as exc:
        return TreeAssessment("rejected", (str(exc),), None, webui_hash)
    if git_head is None:
        reasons.append("No .git directory. Identity is the pinned file hashes only.")
    elif git_head != required_commit:
        reasons.append(f"HEAD {git_head} is not pinned commit {required_commit}.")
    for relative, expected in expected_files.items():
        if relative == "webui.py":
            continue
        path = root / relative
        if not path.is_file():
            reasons.append(f"Missing pinned file {relative}.")
            continue
        actual = sha256_file(path)
        if actual != expected:
            reasons.append(f"Pinned file changed: {relative}.")
    if webui_hash == expected_files["webui.py"]:
        webui_state = "upstream"
    elif webui_hash == patched_hash:
        webui_state = "patched"
    else:
        webui_state = "unknown"
        reasons.append("webui.py matches neither the pinned upstream bytes nor the loopback patch.")
    if any(not item.startswith("No .git") for item in reasons):
        return TreeAssessment("rejected", tuple(reasons), git_head, webui_hash)
    if webui_state == "patched":
        reasons.append("Loopback patch is present. Gradio has not been started by this check.")
        return TreeAssessment("patched", tuple(reasons), git_head, webui_hash)
    reasons.append("Upstream WebUI bytes match. Loopback patch is not applied, so launch is refused.")
    return TreeAssessment("unpatched", tuple(reasons), git_head, webui_hash)


def prepare_loopback_patch(
    root: Path,
    *,
    upstream: dict[str, str] | None = None,
    patched_webui: str | None = None,
    commit: str | None = None,
) -> TreeAssessment:
    root = root.resolve()
    expected_files = upstream or UPSTREAM_SHA256
    patched_hash = patched_webui or PATCHED_WEBUI_SHA256
    assessment = assess_tree(root, upstream=expected_files, patched_webui=patched_hash, commit=commit)
    if assessment.state == "patched":
        return assessment
    if assessment.state != "unpatched":
        raise RvcGuardError(" ".join(assessment.reasons))
    webui_path = root / "webui.py"
    original = webui_path.read_bytes()
    if sha256_bytes(original) != expected_files["webui.py"]:
        raise RvcGuardError("webui.py changed while preparing the patch.")
    try:
        patched = patch_webui_text(original.decode("utf-8")).encode("utf-8")
    except UnicodeError as exc:
        raise RvcGuardError("webui.py is not UTF-8; refusing to patch.") from exc
    if sha256_bytes(patched) != patched_hash:
        raise RvcGuardError("Patched WebUI hash does not match the reviewed patch. Nothing was written.")
    backup = root / BACKUP_NAME
    if backup.exists() and sha256_file(backup) != expected_files["webui.py"]:
        raise RvcGuardError(f"{BACKUP_NAME} exists and is not the pinned upstream file.")
    if not backup.exists():
        backup.write_bytes(original)
    webui_path.write_bytes(patched)
    confirmed = assess_tree(root, upstream=expected_files, patched_webui=patched_hash, commit=commit)
    if confirmed.state != "patched":
        webui_path.write_bytes(original)
        raise RvcGuardError("Patch did not verify after writing and was reverted.")
    return confirmed


def planned_launch_argv(
    root: Path,
    python: str,
    port: int,
    *,
    upstream: dict[str, str] | None = None,
    patched_webui: str | None = None,
    commit: str | None = None,
) -> list[str]:
    if not python or python.startswith("-"):
        raise RvcGuardError("Python executable is required and cannot be an option.")
    if port < 1 or port > 65535:
        raise RvcGuardError(f"Port {port} is outside 1-65535.")
    assessment = assess_tree(root, upstream=upstream, patched_webui=patched_webui, commit=commit)
    if not assessment.launch_allowed:
        raise RvcGuardError(" ".join(assessment.reasons) or "Launch refused.")
    argv = [python, "webui.py", "--noautoopen", "--port", str(port)]
    banned = ("0.0.0.0", "--colab", "share", "::")
    for item in argv:
        for token in banned:
            if token in item:
                raise RvcGuardError(f"Launch command contains banned token {token}.")
    return argv
