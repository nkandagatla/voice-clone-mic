"""Menu-bar adapter. Imported only when a real macOS menu is requested."""

from __future__ import annotations

from voice_clone_mic.ptt.menu_controller import MenuController


def run_menu(controller: MenuController) -> None:
    import rumps

    class ConvertedVoiceMenu(rumps.App):
        def __init__(self) -> None:
            super().__init__("Converted voice: Muted", quit_button=None)
            self.controller = controller
            self.mute_item = rumps.MenuItem("Unmute converted voice", callback=self.on_mute)
            self.start_item = rumps.MenuItem("Start recording", callback=self.on_start)
            self.stop_item = rumps.MenuItem("Stop recording", callback=self.on_stop)
            self.quit_item = rumps.MenuItem("Quit", callback=self.on_quit)
            self.scope_item = rumps.MenuItem(controller.status().detail)
            self.menu = [self.mute_item, self.start_item, self.stop_item, self.scope_item, self.quit_item]
            self._apply(controller.status())

        def on_mute(self, _sender) -> None:
            self._apply(self.controller.toggle_mute())

        def on_start(self, _sender) -> None:
            self._apply(self.controller.start_recording())

        def on_stop(self, _sender) -> None:
            self._apply(self.controller.stop_recording())

        def on_quit(self, _sender) -> None:
            self._apply(self.controller.quit())
            rumps.quit_application()

        def refresh(self, _timer) -> None:
            self.controller.poll()
            self._apply(self.controller.status())

        def _apply(self, view) -> None:
            self.title = view.title
            self.mute_item.title = "Mute converted voice" if not view.muted else "Unmute converted voice"
            self.scope_item.title = view.detail

    app = ConvertedVoiceMenu()
    timer = rumps.Timer(app.refresh, 0.4)
    timer.start()
    app.run()
