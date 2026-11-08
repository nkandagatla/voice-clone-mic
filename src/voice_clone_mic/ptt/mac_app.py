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
  <string>local.voice-clone-mic.menu</string>
  <key>CFBundleName</key>
  <string>Voice Clone Mic Menu</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Records only after Start recording. Mute in this menu silences the converted virtual-cable voice. It does not mute the Mac microphone or the meeting mute button.</string>
</dict>
</plist>
"""


def write_unsigned_menu_app(dest: Path, python: str) -> Path:
    if not python or python.startswith("-"):
        raise ValueError("A Python executable path is required.")
    root = dest / "Voice Clone Mic Menu.app"
    macos = root / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    (root / "Contents" / "Info.plist").write_text(INFO_PLIST, encoding="utf-8")
    launch = macos / "launch"
    launch.write_text(
        "#!/bin/bash\n"
        f"exec {shlex.quote(python)} -m voice_clone_mic ptt menu \"$@\"\n",
        encoding="utf-8",
    )
    os.chmod(launch, 0o755)
    return root
