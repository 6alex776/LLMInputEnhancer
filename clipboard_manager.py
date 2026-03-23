"""剪贴板与文本获取输出模块。

优先尝试 UIAutomation 获取选中文本，失败后自动回退到剪贴板方案。
同时提供面向流式写回的编辑会话能力，尽量兼容标准编辑框和普通输入框。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

import pyperclip
import win32api
import win32clipboard
import win32con
import win32gui
import win32process

from app_logger import get_logger

try:
    import uiautomation as automation
except Exception:
    automation = None


logger = get_logger("clipboard")

GA_ROOT = 2
EM_GETSEL = 0x00B0
EM_SETSEL = 0x00B1
EM_REPLACESEL = 0x00C2
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

ULONG_PTR = wintypes.WPARAM
user32 = ctypes.WinDLL("user32", use_last_error=True)


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


@dataclass
class ClipboardSnapshot:
    """剪贴板快照，保存所有可读取的数据格式。"""

    items: list[tuple[int, Any]]


@dataclass
class FocusSnapshot:
    """当前前台输入目标快照。"""

    top_hwnd: int
    target_hwnd: int
    thread_id: int
    process_id: int
    class_name: str


@dataclass
class StreamEditSession:
    """一次流式写回的目标编辑会话。"""

    original_text: str
    top_hwnd: int
    target_hwnd: int
    thread_id: int
    process_id: int
    class_name: str
    strategy: str
    started: bool = False
    selection_start: int = 0
    injected_text: str = ""
    flush_count: int = 0


class ClipboardManager:
    """负责文本获取、流式写回、粘贴与剪贴板全量恢复。"""

    def __init__(self, copy_delay: float = 0.06, paste_delay: float = 0.06) -> None:
        self.copy_delay = copy_delay
        self.paste_delay = paste_delay

    def _open_clipboard_with_retry(self, retries: int = 10, delay: float = 0.01) -> None:
        """打开剪贴板，避免被其他进程短暂占用导致失败。"""
        last_error: Exception | None = None
        for _ in range(retries):
            try:
                win32clipboard.OpenClipboard()
                return
            except Exception as exc:
                last_error = exc
                time.sleep(delay)

        raise RuntimeError(f"无法访问系统剪贴板：{last_error}")

    def backup_clipboard(self) -> ClipboardSnapshot:
        """备份当前剪贴板中所有可读取的数据格式。"""
        self._open_clipboard_with_retry()
        items: list[tuple[int, Any]] = []
        try:
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if fmt == 0:
                    break

                try:
                    data = win32clipboard.GetClipboardData(fmt)
                    items.append((fmt, data))
                except Exception:
                    continue
        finally:
            win32clipboard.CloseClipboard()

        logger.info("剪贴板备份完成：共 %s 种格式", len(items))
        return ClipboardSnapshot(items=items)

    def restore_clipboard(self, snapshot: ClipboardSnapshot) -> None:
        """恢复剪贴板为操作前状态。"""
        self._open_clipboard_with_retry()
        try:
            win32clipboard.EmptyClipboard()
            for fmt, data in snapshot.items:
                try:
                    win32clipboard.SetClipboardData(fmt, data)
                except Exception:
                    continue
        finally:
            win32clipboard.CloseClipboard()

        logger.info("剪贴板恢复完成：共恢复 %s 种格式", len(snapshot.items))

    @staticmethod
    def _simulate_ctrl_key(vk_code: int) -> None:
        """模拟 Ctrl + 指定按键。"""
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(vk_code, 0, 0, 0)
        win32api.keybd_event(vk_code, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

    @staticmethod
    def _safe_text(value: str | None, strip: bool = True) -> str:
        """清洗文本，避免 None 和 NUL 导致后续流程异常。"""
        if value is None:
            return ""

        normalized = str(value).replace("\x00", "")
        return normalized.strip() if strip else normalized

    def _get_focus_snapshot(self) -> FocusSnapshot:
        """获取当前前台窗口和聚焦输入控件信息。"""
        foreground_hwnd = int(user32.GetForegroundWindow() or 0)
        if not foreground_hwnd:
            return FocusSnapshot(0, 0, 0, 0, "")

        thread_id, _ = win32process.GetWindowThreadProcessId(foreground_hwnd)
        gui_info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))

        focus_hwnd = foreground_hwnd
        if thread_id and user32.GetGUIThreadInfo(thread_id, ctypes.byref(gui_info)):
            focus_hwnd = int(
                gui_info.hwndFocus
                or gui_info.hwndCaret
                or gui_info.hwndActive
                or foreground_hwnd
            )

        top_hwnd = int(user32.GetAncestor(focus_hwnd, GA_ROOT) or foreground_hwnd)
        target_hwnd = int(focus_hwnd or foreground_hwnd)
        target_thread_id, process_id = win32process.GetWindowThreadProcessId(target_hwnd)

        class_name = ""
        try:
            class_name = win32gui.GetClassName(target_hwnd)
        except Exception:
            class_name = ""

        return FocusSnapshot(
            top_hwnd=top_hwnd,
            target_hwnd=target_hwnd,
            thread_id=int(target_thread_id),
            process_id=int(process_id),
            class_name=class_name,
        )

    @staticmethod
    def _choose_stream_strategy(class_name: str) -> str:
        """根据控件类型选择写回策略。"""
        lowered = class_name.strip().lower()
        if lowered == "edit" or lowered.startswith("richedit"):
            return "direct_message"
        return "send_input"

    def create_stream_session(self, original_text: str) -> StreamEditSession:
        """创建流式写回会话，但暂不修改目标输入框。"""
        snapshot = self._get_focus_snapshot()
        if not snapshot.top_hwnd or not snapshot.target_hwnd:
            raise RuntimeError("未检测到有效输入目标，请重新聚焦输入框后重试。")

        session = StreamEditSession(
            original_text=self._safe_text(original_text, strip=False),
            top_hwnd=snapshot.top_hwnd,
            target_hwnd=snapshot.target_hwnd,
            thread_id=snapshot.thread_id,
            process_id=snapshot.process_id,
            class_name=snapshot.class_name,
            strategy=self._choose_stream_strategy(snapshot.class_name),
        )
        logger.info(
            "已创建流式写回会话：strategy=%s class=%s top_hwnd=%s target_hwnd=%s",
            session.strategy,
            session.class_name or "unknown",
            session.top_hwnd,
            session.target_hwnd,
        )
        return session

    def start_stream_session(self, session: StreamEditSession) -> None:
        """开始流式写回，会先删除当前选区。"""
        if session.started:
            return

        self._ensure_target_ready(session)
        if session.strategy == "direct_message":
            raw_selection = int(win32gui.SendMessage(session.target_hwnd, EM_GETSEL, 0, 0))
            session.selection_start = raw_selection & 0xFFFF
            win32gui.SendMessage(session.target_hwnd, EM_REPLACESEL, True, "")
        else:
            self._send_virtual_key(win32con.VK_DELETE)

        session.started = True
        logger.info("流式写回会话已启动：strategy=%s target_hwnd=%s", session.strategy, session.target_hwnd)

    def append_stream_text(self, session: StreamEditSession, text: str) -> None:
        """向目标输入框追加一段流式文本。"""
        chunk = self._safe_text(text, strip=False)
        if not chunk:
            return

        self._ensure_target_ready(session)
        if not session.started:
            self.start_stream_session(session)

        if session.strategy == "direct_message":
            win32gui.SendMessage(session.target_hwnd, EM_REPLACESEL, True, chunk)
        else:
            self._send_unicode_text(chunk)

        session.injected_text += chunk
        session.flush_count += 1
        logger.info(
            "流式文本已写回：strategy=%s chunk_length=%s total_length=%s flush_count=%s",
            session.strategy,
            len(chunk),
            len(session.injected_text),
            session.flush_count,
        )

    def finish_stream_session(self, session: StreamEditSession) -> None:
        """完成流式写回会话。"""
        logger.info(
            "流式写回完成：strategy=%s total_length=%s flush_count=%s",
            session.strategy,
            len(session.injected_text),
            session.flush_count,
        )

    def abort_stream_session(self, session: StreamEditSession) -> bool:
        """中止流式写回，并在可行时恢复原文本。"""
        if not session.started or not session.injected_text:
            return False

        if session.strategy != "direct_message":
            logger.warning("流式写回中断：当前策略不支持自动恢复原文本。")
            return False

        try:
            self._ensure_target_ready(session)
            restore_end = session.selection_start + len(session.injected_text)
            win32gui.SendMessage(session.target_hwnd, EM_SETSEL, session.selection_start, restore_end)
            win32gui.SendMessage(session.target_hwnd, EM_REPLACESEL, True, session.original_text)
            logger.info("流式写回中断后已恢复原文本：target_hwnd=%s", session.target_hwnd)
            return True
        except Exception:
            logger.exception("流式写回恢复原文本失败。")
            return False

    def _ensure_target_ready(self, session: StreamEditSession) -> None:
        """确认写回目标仍然是原来的输入框。"""
        if not session.target_hwnd or not bool(user32.IsWindow(session.target_hwnd)):
            raise RuntimeError("目标输入框已失效，请重新选中文本后重试。")

        current = self._get_focus_snapshot()
        if current.top_hwnd != session.top_hwnd:
            raise RuntimeError("目标输入窗口已变化，请重新选中文本后重试。")

        if current.target_hwnd != session.target_hwnd:
            raise RuntimeError("目标输入框焦点已变化，请重新选中文本后重试。")

    def _send_virtual_key(self, vk_code: int) -> None:
        """发送单个虚拟键。"""
        inputs = self._build_virtual_key_inputs(vk_code)
        self._send_inputs(inputs)

    @staticmethod
    def _build_virtual_key_inputs(vk_code: int) -> tuple[INPUT, INPUT]:
        """构造单个虚拟键的按下和弹起事件。"""
        return (
            INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk_code, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)),
            INPUT(
                type=INPUT_KEYBOARD,
                ki=KEYBDINPUT(wVk=vk_code, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0),
            ),
        )

    def _send_unicode_text(self, text: str) -> None:
        """通过 SendInput 发送 Unicode 文本。"""
        inputs: list[INPUT] = []
        for char in text:
            if char == "\r":
                continue
            if char == "\n":
                inputs.extend(self._build_virtual_key_inputs(win32con.VK_RETURN))
                continue
            if char == "\t":
                inputs.extend(self._build_virtual_key_inputs(win32con.VK_TAB))
                continue

            code_point = ord(char)
            inputs.append(
                INPUT(
                    type=INPUT_KEYBOARD,
                    ki=KEYBDINPUT(wVk=0, wScan=code_point, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0),
                )
            )
            inputs.append(
                INPUT(
                    type=INPUT_KEYBOARD,
                    ki=KEYBDINPUT(
                        wVk=0,
                        wScan=code_point,
                        dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                        time=0,
                        dwExtraInfo=0,
                    ),
                )
            )

        self._send_inputs(inputs)

    def _send_inputs(self, inputs: list[INPUT] | tuple[INPUT, ...]) -> None:
        """调用 Win32 SendInput。"""
        if not inputs:
            return

        array_type = INPUT * len(inputs)
        input_array = array_type(*inputs)
        sent = int(user32.SendInput(len(inputs), input_array, ctypes.sizeof(INPUT)))
        if sent != len(inputs):
            raise RuntimeError("发送输入事件失败，目标程序可能拒绝了模拟输入。")

    def get_selected_text_via_uia(self) -> tuple[str, str]:
        """优先通过 UI Automation 获取选中文本与上下文。"""
        if automation is None:
            logger.info("UI Automation 不可用，直接跳过 UIA 文本获取。")
            return "", ""

        selected_text = ""
        context_text = ""
        initialized = False

        try:
            automation.InitializeUIAutomationInCurrentThread()
            initialized = True
        except Exception:
            initialized = False
            logger.info("UI Automation 初始化失败，将尝试其他文本获取方案。")

        try:
            focused = automation.GetFocusedControl()
            if not focused:
                logger.info("UIA 未获取到焦点控件。")
                return "", ""

            try:
                text_pattern = focused.GetTextPattern()
            except Exception:
                text_pattern = None

            if text_pattern:
                try:
                    selections = text_pattern.GetSelection()
                    if selections:
                        selected_text = selections[0].GetText(-1) or ""
                except Exception:
                    selected_text = ""

                try:
                    doc_range = text_pattern.DocumentRange
                    if doc_range:
                        context_text = doc_range.GetText(-1) or ""
                except Exception:
                    context_text = ""

            if not context_text:
                try:
                    value_pattern = focused.GetValuePattern()
                    context_text = value_pattern.Value or ""
                except Exception:
                    context_text = ""
        except Exception:
            logger.exception("UIA 文本获取失败。")
            return "", ""
        finally:
            if initialized:
                try:
                    automation.UninitializeUIAutomationInCurrentThread()
                except Exception:
                    pass

        selected_text = self._safe_text(selected_text)
        context_text = self._safe_text(context_text)
        logger.info(
            "UIA 文本获取完成：selected_length=%s context_length=%s",
            len(selected_text),
            len(context_text),
        )
        return selected_text, context_text

    def get_selected_text_via_clipboard(self) -> str:
        """剪贴板兜底方案：备份 -> Ctrl+C -> 读取文本 -> 恢复。"""
        snapshot = self.backup_clipboard()
        try:
            pyperclip.copy("")
            time.sleep(0.02)
            self._simulate_ctrl_key(ord("C"))
            time.sleep(self.copy_delay)
            text = pyperclip.paste()
            text = self._safe_text(text)
            logger.info("剪贴板兜底获取文本完成：length=%s", len(text))
            return text
        finally:
            self.restore_clipboard(snapshot)

    def get_selected_text(self) -> tuple[str, str, str]:
        """获取选中文本，返回 (文本, 上下文, 来源)。"""
        selected, context = self.get_selected_text_via_uia()
        if selected:
            logger.info("文本获取成功：来源=uiautomation length=%s", len(selected))
            return selected, context, "uiautomation"

        selected = self.get_selected_text_via_clipboard()
        logger.info("文本获取成功：来源=clipboard length=%s", len(selected))
        return selected, "", "clipboard"

    def paste_text(self, text: str) -> None:
        """输出文本：备份 -> 写入剪贴板 -> Ctrl+V -> 恢复。"""
        final_text = self._safe_text(text)
        if not final_text:
            raise ValueError("处理结果为空，无法粘贴。")

        snapshot = self.backup_clipboard()
        try:
            pyperclip.copy(final_text)
            self._open_clipboard_with_retry()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, final_text)
            finally:
                win32clipboard.CloseClipboard()

            time.sleep(0.02)
            self._simulate_ctrl_key(ord("V"))
            time.sleep(self.paste_delay)
        finally:
            self.restore_clipboard(snapshot)

        logger.info("文本粘贴完成：length=%s", len(final_text))
