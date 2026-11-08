import io
import pickle
import zipfile
from pathlib import Path

from voice_clone_mic.rvc_checkpoint import LOAD_SITES, scan_checkpoint_file


class Marker:
    pass


def test_custom_pickle_is_blocked_without_an_unsafe_fallback(tmp_path: Path):
    path = tmp_path / "evil.pt"
    path.write_bytes(pickle.dumps(Marker()))
    report = scan_checkpoint_file(path)
    assert report.verdict == "blocked"
    assert report.allows_unsafe_fallback is False
    assert "weights_only=False" in report.detail
    assert "Do not load" in report.detail


def test_plain_dict_and_zip_header_are_candidates(tmp_path: Path):
    plain = tmp_path / "plain.pt"
    plain.write_bytes(pickle.dumps({"model": "tensor-placeholder"}))
    plain_report = scan_checkpoint_file(plain)
    assert plain_report.verdict == "candidate"
    assert plain_report.allows_unsafe_fallback is False

    archive_path = tmp_path / "zip.pt"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("archive/data.pkl", pickle.dumps({"weight": [1, 2, 3]}))
        archive.writestr("archive/data/0", b"\x00\x01")
    archive_path.write_bytes(buffer.getvalue())
    zipped = scan_checkpoint_file(archive_path)
    assert zipped.kind == "pytorch-zip"
    assert zipped.verdict == "candidate"
    assert zipped.allows_unsafe_fallback is False


def test_load_sites_cover_the_training_assets_and_forbid_fallback():
    artifacts = {site.artifact for site in LOAD_SITES}
    assert artifacts == {
        "assets/hubert_base/pytorch_model.bin",
        "assets/rmvpe/rmvpe.pt",
        "assets/pretrained_v2/f0G40k.pth",
        "assets/pretrained_v2/f0D40k.pth",
    }
    for site in LOAD_SITES:
        lowered = site.weights_only_true.lower()
        assert "please use weights_only=false" not in lowered
        assert site.line > 0
