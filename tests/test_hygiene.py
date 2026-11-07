from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_env_example_has_empty_secrets_only():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ELEVENLABS_API_KEY=" in text
    assert "sk_" not in text
    for line in text.splitlines():
        if line.startswith("ELEVENLABS_API_KEY="):
            assert line.strip() == "ELEVENLABS_API_KEY="


def test_gitignore_covers_secrets_and_audio():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for token in (".env", ".vcm/", "*.wav", "*.pcm", "*.mp3"):
        assert token in text


def test_sources_do_not_persist_audio_or_send_deprecated_param():
    root = ROOT / "src" / "voice_clone_mic"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    assert "wave.open" not in text
    assert "optimize_streaming_latency" not in text
    assert ".wav" not in text
    assert "sk_" not in text
