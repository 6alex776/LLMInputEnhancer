"""Global hotkey listener based on a dedicated Win32 message window."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import win32api
import win32con
import win32gui
from PySide6.QtCore import QObject, Signal

VK_OEM_3 = getattr(win32con, "VK_OEM_3", 0xC0)


@dataclass(frozen=True)
class HotkeyDefinition:
    """Hotkey definition."""

    hotkey_id: int
    name: str
    modifiers: int
    vk: int
    display: str


class HotkeyListener(QObject):
    """Register and receive global hotkeys from a dedicated Win32 window."""

    hotkey_triggered = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._registered: dict[int, HotkeyDefinition] = {}
        self._log_path = Path(__file__).resolve().parent / "hotkey_debug.log"
        self._class_name = f"LLMInputEnhancerHotkeyWindow_{id(self)}"
        self._class_atom = 0
        self._hwnd = 0
        self._wnd_proc = self._build_wnd_proc()
        self._create_message_window()

    @property
    def default_hotkeys(self) -> list[HotkeyDefinition]:
        """Default hotkeys."""
        return [
            HotkeyDefinition(1, "show_panel", win32con.MOD_ALT, VK_OEM_3, "Alt+`"),
            HotkeyDefinition(2, "quick_polish", win32con.MOD_ALT, ord("1"), "Alt+1"),
            HotkeyDefinition(3, "quick_translate", win32con.MOD_ALT, ord("2"), "Alt+2"),
            HotkeyDefinition(4, "quick_expand", win32con.MOD_ALT, ord("3"), "Alt+3"),
            HotkeyDefinition(5, "quick_summarize", win32con.MOD_ALT, ord("4"), "Alt+4"),
        ]

    def register_hotkeys(self, hotkeys: list[HotkeyDefinition]) -> list[str]:
        """Register a group of hotkeys and return failed display names."""
        failed: list[str] = []
        for item in hotkeys:
            if not self._register_one(item):
                failed.append(item.display)
        return failed

    def register_hotkeys_with_details(
        self, hotkeys: list[HotkeyDefinition]
    ) -> tuple[list[HotkeyDefinition], list[HotkeyDefinition]]:
        """Register a group of hotkeys and return success and failure lists."""
        success: list[HotkeyDefinition] = []
        failed: list[HotkeyDefinition] = []
        for item in hotkeys:
            if self._register_one(item):
                success.append(item)
            else:
                failed.append(item)
        return success, failed

    def register_default_hotkeys(self) -> list[str]:
        """Register default hotkeys."""
        return self.register_hotkeys(self.default_hotkeys)

    def get_active_hotkey_map(self) -> dict[str, str]:
        """Return active action to display mapping."""
        return {item.name: item.display for item in self._registered.values()}

    def unregister_all(self) -> None:
        """Unregister all hotkeys and release Win32 resources."""
        for hotkey_id in list(self._registered.keys()):
            try:
                win32gui.UnregisterHotKey(self._hwnd, hotkey_id)
                self._log(f"unregister ok id={hotkey_id}")
            except Exception as exc:
                self._log(f"unregister failed id={hotkey_id} error={exc!r}")
            finally:
                self._registered.pop(hotkey_id, None)

        if self._hwnd:
            try:
                win32gui.DestroyWindow(self._hwnd)
            except Exception as exc:
                self._log(f"destroy window failed error={exc!r}")
            self._hwnd = 0

    def _build_wnd_proc(self):
        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == win32con.WM_HOTKEY:
                hotkey_id = int(wparam)
                hotkey = self._registered.get(hotkey_id)
                self._log(f"wm_hotkey hwnd={hwnd} id={hotkey_id} hotkey={hotkey.display if hotkey else 'unknown'}")
                if hotkey:
                    self.hotkey_triggered.emit(hotkey.name)
                return 0

            if msg == win32con.WM_DESTROY:
                return 0

            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        return _wnd_proc

    def _create_message_window(self) -> None:
        hinstance = win32api.GetModuleHandle(None)
        wnd_class = win32gui.WNDCLASS()
        wnd_class.hInstance = hinstance
        wnd_class.lpszClassName = self._class_name
        wnd_class.lpfnWndProc = self._wnd_proc

        try:
            self._class_atom = win32gui.RegisterClass(wnd_class)
        except Exception:
            # Class may already exist in the interpreter.
            self._class_atom = 0

        self._hwnd = win32gui.CreateWindow(
            self._class_name,
            self._class_name,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            hinstance,
            None,
        )
        self._log(f"message window created hwnd={self._hwnd}")

    def _register_one(self, hotkey: HotkeyDefinition) -> bool:
        try:
            result = win32gui.RegisterHotKey(
                self._hwnd,
                hotkey.hotkey_id,
                hotkey.modifiers,
                hotkey.vk,
            )
        except Exception as exc:
            self._log(f"register failed {hotkey.display} error={exc!r}")
            return False

        # pywin32 在很多 Win32 API 上“成功时返回 None，失败时抛异常”，
        # 所以这里不能再用 bool(result) 判断成功与否。
        if result == 0:
            self._log(f"register returned false {hotkey.display} result={result!r}")
            return False

        self._registered[hotkey.hotkey_id] = hotkey
        self._log(f"register ok {hotkey.display} hwnd={self._hwnd} result={result!r}")
        return True

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{timestamp}] {message}\n")
        except Exception:
            pass
