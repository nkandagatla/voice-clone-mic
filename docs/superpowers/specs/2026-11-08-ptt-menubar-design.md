# Menu bar for converted-voice mute

Date: 2026-11-08
Status: approved scope is mute of this app's converted virtual-mic output only.

The control does not change the Mac input volume, the system mute switch, other applications, or the mute button inside Zoom or Teams. Those apps hear audio only if the user selects this app's virtual cable as their microphone. The menu says that in the status text.

The app starts muted. Unmute changes the label to Ready and does not start a recording and does not send a queued utterance. Recording still starts and stops only from the menu. Mute during recording or conversion cancels that work, clears playback, and ignores a late transcription or TTS result. Quit does the same and leaves the cable silent.

Phases shown to the user: Needs setup, Muted, Ready, Recording, Converting, Cable lost. Cable loss stays silent and does not route to the speakers.

The state machine is plain Python and is what tests call. The menu bar is a separate adapter. `rumps` 0.4.0 is pure Python. `pyobjc-framework-Cocoa` 13.0 publishes `universal2` wheels (`macosx_10_9` for CPython 3.11 and `macosx_10_13` for 3.12), so the tags include Intel and do not require macOS 14. This change did not launch that UI.

The `.app` writer builds an unsigned, unnotarized bundle that runs `python -m voice_clone_mic ptt menu`. It does not download a model, sign the bundle, or install it.
