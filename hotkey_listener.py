"""全局热键监听模块。

基于 win32 RegisterHotKey + Qt 原生消息循环，不使用键盘钩子。
"""

from __future__ import annotations

from ctypes import wintypes
from dataclasses import dataclass

import win32con
import win32gui
from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal


@dataclass(frozen=True)
class HotkeyDefinition:
    """热键定义。"""

    hotkey_id: int
    name: str
    modifiers: int
    vk: int
    display: str


class HotkeyListener(QAbstractNativeEventFilter, QObject):
    """注册并监听全局热键。"""

    hotkey_triggered = Signal(str)

    def __init__(self) -> None:
        QAbstractNativeEventFilter.__init__(self)
        QObject.__init__(self)
        self._registered: dict[int, HotkeyDefinition] = {}

    @property
    def default_hotkeys(self) -> list[HotkeyDefinition]:
        """默认三组核心热键。"""
        return [
            HotkeyDefinition(1, "show_panel", win32con.MOD_ALT, ord("J"), "Alt+J"),
            HotkeyDefinition(2, "quick_polish", win32con.MOD_ALT, ord("R"), "Alt+R"),
            HotkeyDefinition(3, "quick_translate", win32con.MOD_ALT, ord("T"), "Alt+T"),
        ]

    def register_hotkeys(self, hotkeys: list[HotkeyDefinition]) -> list[str]:
        """注册一组热键，返回注册失败的热键文本列表。"""
        failed: list[str] = []
        for item in hotkeys:
            if not self._register_one(item):
                failed.append(item.display)
        return failed

    def register_default_hotkeys(self) -> list[str]:
        """注册默认热键。"""
        return self.register_hotkeys(self.default_hotkeys)

    def _register_one(self, hotkey: HotkeyDefinition) -> bool:
        """注册单个热键。"""
        try:
            ok = bool(win32gui.RegisterHotKey(None, hotkey.hotkey_id, hotkey.modifiers, hotkey.vk))
        except Exception:
            ok = False

        if not ok:
            return False

        self._registered[hotkey.hotkey_id] = hotkey
        return True

    def unregister_all(self) -> None:
        """注销所有已注册热键，避免热键残留。"""
        for hotkey_id in list(self._registered.keys()):
            try:
                win32gui.UnregisterHotKey(None, hotkey_id)
            except Exception:
                pass
            finally:
                self._registered.pop(hotkey_id, None)

    def nativeEventFilter(self, event_type, message):  # type: ignore[override]
        """通过 Qt 的原生事件过滤器接收 WM_HOTKEY。"""
        if event_type in (
            "windows_generic_MSG",
            "windows_dispatcher_MSG",
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        ):
            try:
                msg = wintypes.MSG.from_address(int(message))
            except Exception:
                return False, 0

            if msg.message == win32con.WM_HOTKEY:
                hotkey_id = int(msg.wParam)
                hotkey = self._registered.get(hotkey_id)
                if hotkey:
                    self.hotkey_triggered.emit(hotkey.name)
                    return True, 0
        return False, 0
