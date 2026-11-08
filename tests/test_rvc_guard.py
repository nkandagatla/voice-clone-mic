import subprocess
from pathlib import Path

import pytest

from voice_clone_mic.cli import main
from voice_clone_mic.rvc_guard import (
    PATCHED_WEBUI_SHA256,
    PINNED_COMMIT,
    RvcGuardError,
    UPSTREAM_SHA256,
    assert_loopback_host,
    assess_tree,
    patch_webui_text,
    planned_launch_argv,
    prepare_loopback_patch,
    sha256_file,
)

UPSTREAM_SNIPPET = """
def find_available_port(start_port, host="0.0.0.0"):
    return start_port

def launch_webui_with_port_fallback(app, config):
    next_port = config.listen_port
    while True:
        config.listen_port = find_available_port(next_port)
        try:
            queued_app.launch(
                server_name="0.0.0.0",
                inbrowser=not config.noautoopen,
                server_port=config.listen_port,
                quiet=True,
            )
            return
        except OSError:
            raise

    if config.iscolab:
        app.queue(concurrency_count=511, max_size=1022).launch(share=True)
    else:
        launch_webui_with_port_fallback(app, config)
"""


def _tree(
    tmp_path: Path,
    webui: str = UPSTREAM_SNIPPET,
    companion: bytes = b"companion",
) -> tuple[Path, dict[str, str], str]:
    root = tmp_path / "rvc"
    (root / "configs").mkdir(parents=True)
    (root / "webui.py").write_text(webui, encoding="utf-8")
    (root / "configs" / "config.py").write_bytes(companion)
    upstream = {
        "webui.py": sha256_file(root / "webui.py"),
        "configs/config.py": sha256_file(root / "configs" / "config.py"),
    }
    patched = sha256_file_bytes(patch_webui_text(webui).encode("utf-8"))
    return root, upstream, patched


def sha256_file_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def test_patch_removes_public_bind_and_share():
    patched = patch_webui_text(UPSTREAM_SNIPPET)
    assert 'server_name="127.0.0.1"' in patched
    assert "share=False" in patched
    assert "inbrowser=False" in patched
    assert 'server_name="0.0.0.0"' not in patched
    assert "share=True" not in patched
    assert 'host="0.0.0.0"' not in patched
    with pytest.raises(RvcGuardError):
        patch_webui_text(UPSTREAM_SNIPPET + "\n" + UPSTREAM_SNIPPET)


def test_unpatched_tree_cannot_launch_and_prepare_is_idempotent(tmp_path):
    root, upstream, patched = _tree(tmp_path)
    assessment = assess_tree(root, upstream=upstream, patched_webui=patched, commit="ignored")
    assert assessment.state == "unpatched"
    with pytest.raises(RvcGuardError):
        planned_launch_argv(root, "python", 7865, upstream=upstream, patched_webui=patched, commit="ignored")
    prepare_loopback_patch(root, upstream=upstream, patched_webui=patched, commit="ignored")
    again = prepare_loopback_patch(root, upstream=upstream, patched_webui=patched, commit="ignored")
    assert again.state == "patched"
    body = (root / "webui.py").read_text(encoding="utf-8")
    assert "share=False" in body and "share=True" not in body
    assert sha256_file(root / "webui.py.vcm-upstream.bak") == upstream["webui.py"]
    argv = planned_launch_argv(root, "python", 7865, upstream=upstream, patched_webui=patched, commit="ignored")
    assert argv == ["python", "webui.py", "--noautoopen", "--port", "7865"]
    assert all("0.0.0.0" not in item and "share" not in item and "--colab" not in item for item in argv)


def test_changed_upstream_file_and_bad_backup_are_rejected(tmp_path):
    root, upstream, patched = _tree(tmp_path)
    (root / "configs" / "config.py").write_bytes(b"tampered")
    assessment = assess_tree(root, upstream=upstream, patched_webui=patched, commit="ignored")
    assert assessment.state == "rejected"
    with pytest.raises(RvcGuardError):
        prepare_loopback_patch(root, upstream=upstream, patched_webui=patched, commit="ignored")
    (root / "configs" / "config.py").write_bytes(b"companion")
    (root / "webui.py").write_text(UPSTREAM_SNIPPET.replace("quiet=True", "quiet=False"), encoding="utf-8")
    with pytest.raises(RvcGuardError):
        prepare_loopback_patch(root, upstream=upstream, patched_webui=patched, commit="ignored")
    assert "quiet=False" in (root / "webui.py").read_text(encoding="utf-8")
    root, upstream, patched = _tree(tmp_path / "second")
    (root / "webui.py.vcm-upstream.bak").write_bytes(b"not-the-upstream-file")
    with pytest.raises(RvcGuardError):
        prepare_loopback_patch(root, upstream=upstream, patched_webui=patched, commit="ignored")


def test_wrong_git_head_and_non_loopback_hosts_are_rejected(tmp_path):
    root, upstream, patched = _tree(tmp_path)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "webui.py", "configs/config.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-m", "pin"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    assessment = assess_tree(root, upstream=upstream, patched_webui=patched, commit=PINNED_COMMIT)
    assert assessment.state == "rejected"
    assert assessment.git_head != PINNED_COMMIT
    for host in ("0.0.0.0", "::", "::1", "localhost", ""):
        with pytest.raises(RvcGuardError):
            assert_loopback_host(host)
    assert_loopback_host("127.0.0.1")


def test_cli_check_and_audit_do_not_launch(tmp_path, capsys):
    root, _upstream, _patched = _tree(tmp_path)
    assert main(["rvc", "check", "--root", str(root)]) == 2
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "state=rejected" in text
    assert "Pinned file" in text or "neither" in text
    assert main(["rvc", "audit"]) == 0
    report = capsys.readouterr().out
    assert "weights_only=True" in report
    assert "unsafe fallback: disabled" in report
    assert "0.0.0.0" not in report


def test_recorded_upstream_webui_hash_is_the_reviewed_constant():
    assert UPSTREAM_SHA256["webui.py"] == "3c6ebf133db4a4b1cebb671f1077bf3f0fa4b570d478c1247a3209b4e3a7167f"
    assert PATCHED_WEBUI_SHA256 == "4cd410f526dbce044dba98848e306bf8073fb3e14dd2f2c30bb6416ee87b7cea"


def test_real_pinned_tree_prepare_matches_recorded_hash(tmp_path):
    source = Path("/tmp/rvc-audit")
    if not (source / "webui.py").is_file():
        pytest.skip("pinned RVC checkout is not present")
    root = tmp_path / "real"
    for relative in UPSTREAM_SHA256:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / relative).read_bytes())
    assert assess_tree(root).state == "unpatched"
    assert prepare_loopback_patch(root).state == "patched"
    assert sha256_file(root / "webui.py") == PATCHED_WEBUI_SHA256
    body = (root / "webui.py").read_text(encoding="utf-8")
    assert 'server_name="0.0.0.0"' not in body
    assert "share=True" not in body
