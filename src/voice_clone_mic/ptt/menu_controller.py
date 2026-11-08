"""Headless mute and record state for the converted virtual-mic output."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass


SCOPE_TEXT = (
    "Mute affects only this app's converted voice on the selected virtual cable. "
    "It does not mute the Mac microphone, Zoom, or Teams. "
    "The meeting must select that cable as its microphone."
)


@dataclass(frozen=True)
class MenuView:
    phase: str
    title: str
    detail: str
    muted: bool
    can_record: bool
    output_name: str


class MenuController:
    def __init__(
        self,
        worker: object,
        output_name: str | None,
        setup_problem: str | None = None,
        capture_pcm=None,
        on_record_start=None,
        poll_fault=None,
    ) -> None:
        self.worker = worker
        self.output_name = output_name or ""
        self.setup_problem = setup_problem
        self.capture_pcm = capture_pcm
        self.on_record_start = on_record_start
        self.poll_fault = poll_fault
        self.muted = True
        self.phase = "needs_setup" if setup_problem else "muted"
        self.generation = 0
        self.quit_requested = False
        self._lock = threading.Lock()

    def status(self) -> MenuView:
        with self._lock:
            return self._view()

    def toggle_mute(self) -> MenuView:
        should_cancel = False
        with self._lock:
            if self.quit_requested or self.phase == "device_lost":
                return self._view()
            if self.muted:
                if self.setup_problem:
                    return self._view()
                self.muted = False
                self.phase = "ready"
            else:
                self.muted = True
                self.phase = "muted"
                self.generation += 1
                should_cancel = True
        if should_cancel:
            self.worker.cancel()
        return self.status()

    def start_recording(self) -> MenuView:
        begin = False
        with self._lock:
            if self.quit_requested or self.setup_problem or self.muted or self.phase != "ready":
                return self._view()
            self.phase = "recording"
            begin = True
            view = self._view()
        if begin and self.on_record_start is not None:
            self.on_record_start()
        return view

    def stop_recording(self, pcm: bytes | None = None) -> MenuView:
        generation = None
        should_cancel = False
        with self._lock:
            if self.phase != "recording":
                return self._view()
            if self.muted or self.quit_requested:
                self.phase = "quit" if self.quit_requested else "muted"
                self.generation += 1
                should_cancel = True
            else:
                self.phase = "converting"
                self.generation += 1
                generation = self.generation
        if should_cancel:
            self.worker.cancel()
            return self.status()
        if pcm is None and self.capture_pcm is not None:
            pcm = self.capture_pcm()
        if not pcm:
            with self._lock:
                if self.phase == "converting" and generation == self.generation:
                    self.phase = "ready"
            return self.status()
        assert generation is not None
        self.worker.start(pcm, generation, self.on_worker_done)
        return self.status()

    def poll(self) -> None:
        fault = self.poll_fault
        if fault is None or self.phase in {"device_lost", "quit"}:
            return
        try:
            failed = bool(fault())
        except Exception:
            failed = True
        if failed:
            self.on_device_lost()

    def on_worker_done(self, generation: int, result: object) -> None:
        del result
        with self._lock:
            if (
                generation != self.generation
                or self.muted
                or self.quit_requested
                or self.phase != "converting"
            ):
                return
            self.phase = "ready"

    def on_device_lost(self) -> MenuView:
        with self._lock:
            self.phase = "device_lost"
            self.muted = True
            self.generation += 1
        self.worker.cancel()
        return self.status()

    def quit(self) -> MenuView:
        with self._lock:
            self.quit_requested = True
            self.muted = True
            self.phase = "quit"
            self.generation += 1
        self.worker.cancel()
        return self.status()

    def _view(self) -> MenuView:
        titles = {
            "needs_setup": "Converted voice: Needs setup",
            "muted": "Converted voice: Muted",
            "ready": "Converted voice: Ready",
            "recording": "Converted voice: Recording",
            "converting": "Converted voice: Converting",
            "device_lost": "Converted voice: Cable lost",
            "quit": "Converted voice: Quit",
        }
        problem = self.setup_problem or ""
        cable = self.output_name or "none selected"
        detail = f"{SCOPE_TEXT} Output: {cable}."
        if problem:
            detail = f"{problem} {detail}"
        if self.phase == "device_lost":
            detail = f"Virtual cable unavailable. Output stays silent. {detail}"
        return MenuView(
            phase=self.phase,
            title=titles[self.phase],
            detail=detail,
            muted=self.muted,
            can_record=self.phase == "ready" and not self.muted and not self.setup_problem,
            output_name=self.output_name,
        )


class AsyncSessionWorker:
    """Runs one utterance off the UI thread and can cancel it."""

    def __init__(self, session: object) -> None:
        self.session = session

    def start(self, pcm: bytes, generation: int, on_done) -> None:
        def run() -> None:
            result = asyncio.run(self.session.submit(pcm))
            on_done(generation, result)

        threading.Thread(target=run, name="ptt-utterance", daemon=True).start()

    def cancel(self) -> None:
        self.session.cancel()


class MemoryWorker:
    """Test double. start() records PCM and does not call a network or a model."""

    def __init__(self) -> None:
        self.submits: list[tuple[int, bytes]] = []
        self.cancels = 0
        self._callback = None
        self._generation: int | None = None

    def start(self, pcm: bytes, generation: int, on_done) -> None:
        self.submits.append((generation, pcm))
        self._generation = generation
        self._callback = on_done

    def finish(self) -> None:
        if self._callback is not None and self._generation is not None:
            self._callback(self._generation, None)

    def cancel(self) -> None:
        self.cancels += 1


def scripted_smoke(controller: MenuController, utterance: bytes) -> list[str]:
    """Exercise mute and record without Cocoa, a microphone, or ElevenLabs."""

    titles = [controller.status().title]
    titles.append(controller.toggle_mute().title)
    controller.start_recording()
    titles.append(controller.toggle_mute().title)
    titles.append(controller.toggle_mute().title)
    controller.start_recording()
    titles.append(controller.stop_recording(utterance).title)
    controller.worker.finish()
    titles.append(controller.status().title)
    titles.append(controller.quit().title)
    return titles
