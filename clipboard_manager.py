"""剪贴板与文本获取输出模块。

优先尝试 UIAutomation 获取选中文本，失败后自动回退到剪贴板方案。
所有剪贴板操作都执行“全格式备份 -> 操作 -> 恢复”闭环，避免污染用户数据。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pyperclip
import win32api
import win32clipboard
import win32con

from app_logger import get_logger

try:
    import uiautomation as automation
except Exception:
    automation = None


logger = get_logger("clipboard")


@dataclass
class ClipboardSnapshot:
    """剪贴板快照，保存所有可读取格式的数据。"""

    items: list[tuple[int, Any]]


class ClipboardManager:
    """负责文本获取、粘贴与剪贴板全量恢复。"""

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
    def _safe_text(value: str | None) -> str:
        """清洗文本，避免 None 导致后续流程异常。"""
        if value is None:
            return ""
        return str(value).replace("\x00", "").strip()

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
