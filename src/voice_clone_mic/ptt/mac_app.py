"""Write an unsigned menu-bar .app bundle. This does not sign, install, or launch it."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>launch</string>
  <key>CFBundleIdentifier</key>
  <string>local.voice-clone-mic.utility</string>
  <key>CFBundleName</key>
  <string>Voice Clone Mic</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Records only after Start recording. Disabling output silences this app's converted virtual-cable voice. It does not mute the Mac microphone, Zoom, or Teams.</string>
</dict>
</plist>
"""


def write_unsigned_menu_app(dest: Path, python: str) -> Path:
    if not python or python.startswith("-"):
        raise ValueError("A Python executable path is required.")
    root = dest / "Voice Clone Mic.app"
    macos = root / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    (root / "Contents" / "Info.plist").write_text(INFO_PLIST, encoding="utf-8")
    quoted = shlex.quote(python)
    launch = macos / "launch"
    launch.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'SUPPORT="${HOME}/Library/Application Support/VoiceCloneMic"\n'
        'RUNTIME="${HOME}/Applications/VoiceCloneMicRuntime/runtime"\n'
        'mkdir -p "$SUPPORT"\n'
        'if [ -d "$RUNTIME" ]; then\n'
        '  cd "$RUNTIME"\n'
        "fi\n"
        'export VOICE_CLONE_MIC_SUPPORT="$SUPPORT"\n'
        f"exec {quoted} -m voice_clone_mic ptt utility \"$@\"\n",
        encoding="utf-8",
    )
    os.chmod(launch, 0o755)
    return root
