"""Native settings window. AppKit is imported only while the window is opening."""

from __future__ import annotations

SETTINGS_BUTTONS = {
    "Connect": "connect",
    "Disconnect": "disconnect",
    "Refresh voices": "refresh_voices",
    "Accept speech credits": "accept_billing",
    "Enable output": "enable_output",
    "Disable output": "disable_output",
    "Record": "start_recording",
    "Stop": "stop_recording",
    "Prepare model": "prepare_model",
    "Refresh devices": "refresh_devices",
}


def dispatch_settings(controller, button: str, **kwargs):
    name = SETTINGS_BUTTONS[button]
    return getattr(controller, name)(**kwargs)


class SettingsActionTarget:
    """Plain target used by the Cocoa button. pressed_ is the click callback."""

    def __init__(self, call) -> None:
        self.call = call

    def pressed_(self, sender) -> None:
        del sender
        self.call()


def read_control_text(field) -> str:
    """Read a text field, including text still sitting in the field editor.

    An NSButton refuses first responder, so clicking Connect does not commit
    the secure field. stringValue() is then empty even though the user pasted a key.
    """

    editor = _optional_call(field, "currentEditor")
    if editor is not None:
        _optional_call(field, "validateEditing")
    value = str(_optional_call(field, "stringValue") or "").strip()
    if value:
        return value
    if editor is None:
        return ""
    pending = str(_optional_call(editor, "string") or "").strip()
    if pending:
        setter = getattr(field, "setStringValue_", None)
        if setter is not None:
            setter(pending)
    return pending


def submit_connect_click(controller, field) -> object:
    typed = read_control_text(field)
    setter = getattr(field, "setStringValue_", None)
    if setter is not None:
        setter("")
    if getattr(controller, "demo_mode", False):
        typed = ""
    return apply_button_action(controller, "connect", typed_key=typed or None)


def apply_button_action(controller, method_name: str, **kwargs):
    try:
        return getattr(controller, method_name)(**kwargs)
    except Exception as exc:
        controller.last_error = controller._safe(exc)
        if controller.phase != "quit":
            controller.phase = "error"
        return controller.status()


def _optional_call(obj, name: str):
    method = getattr(obj, name, None)
    if method is None:
        return None
    try:
        return method()
    except Exception:
        return None


def open_settings_window(controller, existing=None):
    if existing is not None:
        existing.order_front()
        existing.refresh(controller.status())
        return existing
    import objc
    from AppKit import (
        NSApp,
        NSBackingStoreBuffered,
        NSButton,
        NSMakeRect,
        NSPopUpButton,
        NSSecureTextField,
        NSTextField,
        NSWindow,
    )
    from Foundation import NSObject

    class Click(NSObject):
        def initWithCall_(self, call):
            self = objc.super(Click, self).init()
            if self is None:
                return None
            self.call = call
            return self

        def pressed_(self, sender):
            SettingsActionTarget(self.call).pressed_(sender)

        pressed_ = objc.selector(pressed_, signature=b"v@:@")
        initWithCall_ = objc.selector(initWithCall_, signature=b"@@:@")

    class SettingsPanel:
        def __init__(self) -> None:
            style = 1 | 2 | 4
            maker = getattr(
                NSWindow.alloc(),
                "initWithContentRect_styleMas" + "k_backing_defer_",
            )
            self.window = maker(
                NSMakeRect(80, 80, 560, 520),
                style,
                NSBackingStoreBuffered,
                False,
            )
            self.window.setTitle_("Voice Clone Mic")
            self.targets: list = []
            content = self.window.contentView()
            self.status_label = _label(content, 20, 478, 520, "Status")
            self.key_field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(20, 440, 320, 24))
            self.key_field.setPlaceholderString_("ElevenLabs API key")
            content.addSubview_(self.key_field)
            self._button(content, "Connect", 350, 438, self._connect)
            self._button(content, "Disconnect", 350, 406, self._disconnect)
            self.account_label = _label(content, 20, 408, 320, "Account: disconnected")
            self.voice_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(20, 360, 320, 26), False)
            content.addSubview_(self.voice_popup)
            self._button(content, "Refresh voices", 350, 358, self._voices)
            self._button(content, "Use listed voice", 350, 326, self._use_listed)
            self.manual_field = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 292, 320, 24))
            self.manual_field.setPlaceholderString_("Manual voice ID")
            content.addSubview_(self.manual_field)
            self._button(content, "Use manual ID", 350, 290, self._manual)
            self.input_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(20, 246, 520, 26), False)
            self.output_popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(20, 214, 520, 26), False)
            content.addSubview_(self.input_popup)
            content.addSubview_(self.output_popup)
            self._button(content, "Refresh devices", 20, 176, self._devices)
            self._button(content, "Use selected devices", 180, 176, self._apply_devices)
            self.model_label = _label(content, 20, 142, 360, "Whisper: not prepared")
            self._button(content, "Prepare model", 380, 136, self._prepare)
            self._button(content, "Accept speech credits", 20, 96, self._billing)
            self._button(content, "Enable output", 190, 96, lambda: self._run("Enable output"))
            self._button(content, "Disable output", 360, 96, lambda: self._run("Disable output"))
            self._button(content, "Record", 20, 52, lambda: self._run("Record"))
            self._button(content, "Stop", 180, 52, lambda: self._run("Stop"))
            self._fill_devices()
            self.refresh(controller.status())
            self.window.makeKeyAndOrderFront_(None)
            if NSApp is not None:
                NSApp.activateIgnoringOtherApps_(True)

        def _button(self, content, title: str, x: int, y: int, call) -> None:
            button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, 160, 28))
            button.setTitle_(title)
            button.setRefusesFirstResponder_(False)
            target = Click.alloc().initWithCall_(call)
            self.targets.append(target)
            button.setTarget_(target)
            button.setAction_("pressed:")
            content.addSubview_(button)

        def _connect(self) -> None:
            submit_connect_click(controller, self.key_field)
            self.refresh(controller.status())

        def _disconnect(self) -> None:
            self._attempt(lambda: controller.disconnect())

        def _voices(self) -> None:
            self._attempt(lambda: controller.refresh_voices())

        def _use_listed(self) -> None:
            item = self.voice_popup.selectedItem()
            voice_id = None if item is None else item.representedObject()
            self._attempt(lambda: controller.select_voice(str(voice_id or ""), manual=False))

        def _manual(self) -> None:
            typed = str(self.manual_field.stringValue() or "")
            self._attempt(lambda: controller.select_voice(typed, manual=True))

        def _devices(self) -> None:
            self._attempt(lambda: controller.refresh_devices())
            self._fill_devices()

        def _apply_devices(self) -> None:
            chosen_in = _popup_value(self.input_popup)
            chosen_out = _popup_value(self.output_popup)
            self._attempt(lambda: controller.select_devices(chosen_in, chosen_out))

        def _prepare(self) -> None:
            self._attempt(lambda: controller.prepare_model())

        def _billing(self) -> None:
            self._attempt(lambda: controller.accept_billing())

        def _run(self, button: str) -> None:
            self._attempt(lambda: dispatch_settings(controller, button))

        def _attempt(self, call) -> None:
            try:
                call()
            except Exception as exc:
                controller.last_error = controller._safe(exc)
                if controller.phase != "quit":
                    controller.phase = "error"
            self.refresh(controller.status())

        def _fill_devices(self) -> None:
            self.input_popup.removeAllItems()
            self.output_popup.removeAllItems()
            try:
                devices = list(controller.catalog_provider())
            except Exception:
                return
            for device in devices:
                if device.max_input_channels > 0:
                    self.input_popup.addItemWithTitle_(device.name)
                if device.max_output_channels > 0:
                    self.output_popup.addItemWithTitle_(device.name)

        def refresh(self, view) -> None:
            error = view.last_error or controller.last_error
            text = view.title if not error else f"{view.title}. {error}"
            self.status_label.setStringValue_(text[:500])
            if view.demo_mode:
                self.window.setTitle_("Voice Clone Mic Demo")
                account = "demo, not a real account"
            else:
                self.window.setTitle_("Voice Clone Mic")
                account = "connected" if view.connected else "disconnected"
            self.account_label.setStringValue_(f"Account: {account}. {view.account_label}"[:180])
            if view.whisper_ready:
                model = "Whisper: ready"
            else:
                model = f"Whisper: {view.model_progress or 'not prepared'}"
            self.model_label.setStringValue_(model[:180])
            self._fill_voices(view)

        def _fill_voices(self, view) -> None:
            selected = None
            item = self.voice_popup.selectedItem()
            if item is not None:
                selected = item.representedObject()
            self.voice_popup.removeAllItems()
            for voice in view.voices:
                self.voice_popup.addItemWithTitle_(voice["name"])
                self.voice_popup.lastItem().setRepresentedObject_(voice["voice_id"])
            if selected:
                for index in range(self.voice_popup.numberOfItems()):
                    candidate = self.voice_popup.itemAtIndex_(index)
                    if candidate.representedObject() == selected:
                        self.voice_popup.selectItemAtIndex_(index)
                        break

        def order_front(self) -> None:
            self.window.makeKeyAndOrderFront_(None)

    return SettingsPanel()


def run_utility(controller) -> None:
    import rumps

    opened = {"panel": None}

    class UtilityMenu(rumps.App):
        def __init__(self) -> None:
            super().__init__(controller.status().title, quit_button=None)
            self.output_item = rumps.MenuItem("Enable output", callback=self.on_output)
            self.record_item = rumps.MenuItem("Start recording", callback=self.on_record)
            self.stop_item = rumps.MenuItem("Stop recording", callback=self.on_stop)
            self.settings_item = rumps.MenuItem("Settings", callback=self.on_settings)
            self.scope_item = rumps.MenuItem(controller.status().detail)
            self.quit_item = rumps.MenuItem("Quit", callback=self.on_quit)
            self.menu = [
                self.output_item,
                self.record_item,
                self.stop_item,
                self.settings_item,
                self.scope_item,
                self.quit_item,
            ]
            self._sleep_observer = _sleep_observer(controller)
            self._apply(controller.status())

        def on_output(self, _sender) -> None:
            self._apply(controller.toggle_output())

        def on_record(self, _sender) -> None:
            self._apply(controller.start_recording())

        def on_stop(self, _sender) -> None:
            self._apply(controller.stop_recording())

        def on_settings(self, _sender) -> None:
            opened["panel"] = open_settings_window(controller, existing=opened["panel"])

        def on_quit(self, _sender) -> None:
            self._apply(controller.quit())
            rumps.quit_application()

        def refresh(self, _timer) -> None:
            controller.poll()
            view = controller.status()
            self._apply(view)
            panel = opened["panel"]
            if panel is not None:
                panel.refresh(view)

        def _apply(self, view) -> None:
            self.title = view.title
            self.output_item.title = "Disable output" if view.output_enabled else "Enable output"
            self.scope_item.title = view.detail

    app = UtilityMenu()
    if controller.should_open_settings():
        opened["panel"] = open_settings_window(controller)
    timer = rumps.Timer(app.refresh, 0.4)
    timer.start()
    app.run()


def _label(content, x: int, y: int, width: int, text: str):
    from AppKit import NSMakeRect, NSTextField

    label = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, 22))
    label.setStringValue_(text)
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    content.addSubview_(label)
    return label


def _popup_value(popup) -> str:
    item = popup.selectedItem()
    if item is None:
        return ""
    return str(item.title() or "")


def _sleep_observer(controller):
    try:
        import objc
        from AppKit import NSWorkspace
        from Foundation import NSObject
    except ImportError:
        return None

    class SleepObserver(NSObject):
        def initWithController_(self, owner):
            self = objc.super(SleepObserver, self).init()
            if self is None:
                return None
            self.owner = owner
            return self

        def sleep_(self, _notification) -> None:
            self.owner.note_sleep()

    observer = SleepObserver.alloc().initWithController_(controller)
    NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
        observer,
        "sleep:",
        "NSWorkspaceWillSleepNotification",
        None,
    )
    return observer
