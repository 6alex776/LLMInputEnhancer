"""全局热键监听模块。

基于专用 Win32 消息窗口接收 WM_HOTKEY，不使用键盘钩子。
"""

from __future__ import annotations

from dataclasses import dataclass

import win32api
import win32con
import win32gui
from PySide6.QtCore import QObject, Signal

from app_logger import get_logger


VK_OEM_3 = getattr(win32con, "VK_OEM_3", 0xC0)
logger = get_logger("hotkey")


@dataclass(frozen=True)
class HotkeyDefinition:
    """热键定义。"""

    hotkey_id: int
    name: str
    modifiers: int
    vk: int
    display: str


DEFAULT_HOTKEYS: tuple[HotkeyDefinition, ...] = (
    HotkeyDefinition(1, "show_panel", win32con.MOD_ALT, VK_OEM_3, "Alt+`"),
    HotkeyDefinition(2, "quick_polish", win32con.MOD_ALT, ord("1"), "Alt+1"),
    HotkeyDefinition(3, "quick_translate", win32con.MOD_ALT, ord("2"), "Alt+2"),
    HotkeyDefinition(4, "quick_expand", win32con.MOD_ALT, ord("3"), "Alt+3"),
    HotkeyDefinition(5, "quick_summarize", win32con.MOD_ALT, ord("4"), "Alt+4"),
    HotkeyDefinition(6, "auto_classify", win32con.MOD_ALT, ord("A"), "Alt+A"),
)


class HotkeyListener(QObject):
    """注册并监听全局热键。"""

    hotkey_triggered = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._registered: dict[int, HotkeyDefinition] = {}
        self._class_name = f"LLMInputEnhancerHotkeyWindow_{id(self)}"
        self._class_atom = 0
        self._hwnd = 0
        self._wnd_proc = self._build_wnd_proc()
        self._create_message_window()

    @property
    def default_hotkeys(self) -> list[HotkeyDefinition]:
        """默认全局热键。"""
        return list(DEFAULT_HOTKEYS)

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

    def get_active_hotkey_map(self) -> dict[str, str]:
        """返回当前实际生效的热键映射。"""
        return {item.name: item.display for item in self._registered.values()}

    def unregister_all(self) -> None:
        """注销所有热键并释放 Win32 资源。"""
        for hotkey_id in list(self._registered.keys()):
            try:
                win32gui.UnregisterHotKey(self._hwnd, hotkey_id)
                logger.info("热键注销成功：id=%s", hotkey_id)
            except Exception:
                logger.exception("热键注销失败：id=%s", hotkey_id)
            finally:
                self._registered.pop(hotkey_id, None)

        if self._hwnd:
            try:
                win32gui.DestroyWindow(self._hwnd)
                logger.info("热键消息窗口已销毁：hwnd=%s", self._hwnd)
            except Exception:
                logger.exception("销毁热键消息窗口失败：hwnd=%s", self._hwnd)
            self._hwnd = 0

    def _build_wnd_proc(self):
        """构建窗口过程函数，专门接收 WM_HOTKEY。"""

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == win32con.WM_HOTKEY:
                hotkey_id = int(wparam)
                hotkey = self._registered.get(hotkey_id)
                logger.info(
                    "收到热键消息：hwnd=%s id=%s hotkey=%s",
                    hwnd,
                    hotkey_id,
                    hotkey.display if hotkey else "unknown",
                )
                if hotkey:
                    self.hotkey_triggered.emit(hotkey.name)
                return 0

            if msg == win32con.WM_DESTROY:
                return 0

            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        return _wnd_proc

    def _create_message_window(self) -> None:
        """创建隐藏消息窗口，用于接收热键消息。"""
        hinstance = win32api.GetModuleHandle(None)
        wnd_class = win32gui.WNDCLASS()
        wnd_class.hInstance = hinstance
        wnd_class.lpszClassName = self._class_name
        wnd_class.lpfnWndProc = self._wnd_proc

        try:
            self._class_atom = win32gui.RegisterClass(wnd_class)
        except Exception:
            self._class_atom = 0
            logger.info("热键窗口类已存在，复用现有类名：%s", self._class_name)

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
        logger.info("热键消息窗口创建成功：hwnd=%s", self._hwnd)

    def _register_one(self, hotkey: HotkeyDefinition) -> bool:
        """注册单个热键。"""
        try:
            result = win32gui.RegisterHotKey(
                self._hwnd,
                hotkey.hotkey_id,
                hotkey.modifiers,
                hotkey.vk,
            )
        except Exception:
            logger.exception("热键注册失败：%s", hotkey.display)
            return False

        # pywin32 在部分环境中成功时会返回 None，只要未抛异常就视为成功。
        if result == 0:
            logger.warning("热键注册返回 0：%s", hotkey.display)
            return False

        self._registered[hotkey.hotkey_id] = hotkey
        logger.info("热键注册成功：%s hwnd=%s result=%r", hotkey.display, self._hwnd, result)
        return True
