"""程序入口。

初始化应用、注册热键、启动托盘与指令面板，串联完整文本处理闭环。
"""

from __future__ import annotations

import sys
import time
import traceback

from PySide6.QtCore import QSharedMemory, QThread, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from app_logger import get_logger, setup_logging
from clipboard_manager import ClipboardManager, StreamEditSession
from config import ConfigManager
from hotkey_listener import HotkeyListener
from llm_client import LLMClient, LLMClientError
from ui_components import AppTray, CommandPanel, SettingsDialog


TASK_NAME_MAP = {
    "polish": "文本润色",
    "translate": "中英互译",
    "expand": "文本扩写",
    "summarize": "文本缩写",
    "custom": "自定义指令",
}

logger = get_logger("main")


class TextProcessThread(QThread):
    """后台处理线程：获取文本、调用 LLM、输出结果。"""

    STREAM_FLUSH_INTERVAL = 0.05 #返回时间阈值
    STREAM_FLUSH_CHARS = 6 #返回字符阈值
    STREAM_FLUSH_ENDINGS = (" ", "\n", "\t", ",", ".", "!", "?", ";", ":", "，", "。", "！", "？", "；", "：")

    success = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        clipboard_manager: ClipboardManager,
        llm_client: LLMClient,
        task_type: str,
        custom_instruction: str = "",
    ) -> None:
        super().__init__()
        self.clipboard_manager = clipboard_manager
        self.llm_client = llm_client
        self.task_type = task_type
        self.custom_instruction = custom_instruction

    def run(self) -> None:  # type: ignore[override]
        """执行完整处理链路。"""
        session: StreamEditSession | None = None
        try:
            logger.info("后台任务开始：task=%s", self.task_type)
            text, context, source = self.clipboard_manager.get_selected_text()
            if not text:
                raise ValueError("未检测到选中文本，请先选中内容后再触发快捷键。")

            session = self.clipboard_manager.create_stream_session(text)
            result_parts: list[str] = []
            pending_parts: list[str] = []
            last_flush = time.monotonic()

            for chunk in self.llm_client.stream_generate(
                task_type=self.task_type,
                text=text,
                custom_instruction=self.custom_instruction,
                context=context,
            ):
                if not chunk:
                    continue

                result_parts.append(chunk)
                pending_parts.append(chunk)
                pending_text = "".join(pending_parts)
                now = time.monotonic()
                if self._should_flush_pending(pending_text, now - last_flush):
                    self.clipboard_manager.append_stream_text(session, pending_text)
                    pending_parts.clear()
                    last_flush = now

            if pending_parts:
                pending_text = "".join(pending_parts)
                self.clipboard_manager.append_stream_text(session, pending_text)

            result = "".join(result_parts).strip()
            if not result:
                raise LLMClientError("模型返回为空，未执行替换。")

            if session.started:
                self.clipboard_manager.finish_stream_session(session)
            else:
                self.clipboard_manager.paste_text(result)

            logger.info(
                "后台任务完成：task=%s source=%s input_length=%s output_length=%s",
                self.task_type,
                source,
                len(text),
                len(result),
            )
            self.success.emit(f"{TASK_NAME_MAP.get(self.task_type, '文本处理')}完成（来源：{source}）。")
        except Exception as exc:
            restored = False
            if session is not None:
                restored = self.clipboard_manager.abort_stream_session(session)
            logger.exception("后台任务失败：task=%s", self.task_type)
            if restored:
                self.failed.emit(f"{exc}\n已自动恢复原文本。")
            else:
                self.failed.emit(str(exc))

    @classmethod
    def _should_flush_pending(cls, pending_text: str, elapsed_seconds: float) -> bool:
        """根据长度、标点和时间片决定是否立即写回。"""
        if not pending_text:
            return False
        if len(pending_text) >= cls.STREAM_FLUSH_CHARS:
            return True
        if pending_text.endswith(cls.STREAM_FLUSH_ENDINGS):
            return True
        return elapsed_seconds >= cls.STREAM_FLUSH_INTERVAL


class ServiceCheckThread(QThread):
    """后台检查本地模型服务状态。"""

    success = Signal(str)
    failed = Signal(str)

    def __init__(self, llm_client: LLMClient) -> None:
        super().__init__()
        self.llm_client = llm_client

    def run(self) -> None:  # type: ignore[override]
        try:
            ok, message = self.llm_client.check_service()
            if ok:
                self.success.emit(message)
                return
            self.failed.emit(message)
        except Exception as exc:
            logger.exception("本地模型服务检查失败。")
            self.failed.emit(str(exc))


class AppController:
    """应用总控制器。"""

    def __init__(self, app: QApplication, shared_memory: QSharedMemory) -> None:
        self.app = app
        self.shared_memory = shared_memory
        self._is_shutting_down = False

        self.config_manager = ConfigManager()
        self.clipboard_manager = ClipboardManager()
        self.llm_client = LLMClient(self.config_manager)

        self.command_panel = CommandPanel()
        self.tray = AppTray()
        self.hotkey_listener = HotkeyListener()
        self.worker: TextProcessThread | None = None
        self.service_checker: ServiceCheckThread | None = None

        self._bind_signals()
        self._register_hotkeys()
        self.tray.show()
        logger.info("应用控制器初始化完成。")

    def _bind_signals(self) -> None:
        """集中绑定所有 UI 与事件信号。"""
        self.hotkey_listener.hotkey_triggered.connect(self._on_hotkey)

        self.command_panel.task_requested.connect(self.start_task)

        self.tray.show_panel_requested.connect(self.show_command_panel)
        self.tray.show_settings_requested.connect(self.show_settings)
        self.tray.check_service_requested.connect(self.check_local_service)
        self.tray.quit_requested.connect(self.shutdown)

        self.app.aboutToQuit.connect(self._cleanup_resources)

    def _register_hotkeys(self) -> None:
        """注册默认热键并提示冲突。"""
        failed = self.hotkey_listener.register_default_hotkeys()
        if failed:
            logger.warning("部分热键注册失败：%s", "、".join(failed))
            conflict_text = "、".join(failed)
            self.tray.show_error(
                "热键冲突",
                f"以下热键注册失败：{conflict_text}。请关闭占用软件后重启。",
            )

        active_map = self.hotkey_listener.get_active_hotkey_map()
        logger.info("热键注册结果：%s", active_map)
        self.tray.show_info(
            "LLM 输入增强",
            (
                "程序已启动。"
                f"面板:{active_map.get('show_panel', '未注册')} "
                f"润色:{active_map.get('quick_polish', '未注册')} "
                f"翻译:{active_map.get('quick_translate', '未注册')} "
                f"扩写:{active_map.get('quick_expand', '未注册')} "
                f"缩写:{active_map.get('quick_summarize', '未注册')}"
            ),
        )

    def _on_hotkey(self, hotkey_name: str) -> None:
        """处理全局热键回调。"""
        logger.info("收到热键动作：%s", hotkey_name)
        if hotkey_name == "show_panel":
            self.show_command_panel()
            return
        if hotkey_name == "quick_polish":
            self.start_task("polish", "")
            return
        if hotkey_name == "quick_translate":
            self.start_task("translate", "")
            return
        if hotkey_name == "quick_expand":
            self.start_task("expand", "")
            return
        if hotkey_name == "quick_summarize":
            self.start_task("summarize", "")
            return

    def show_command_panel(self) -> None:
        """显示指令面板。"""
        logger.info("显示指令面板。")
        self.command_panel.show_near_cursor()

    def show_settings(self) -> None:
        """打开设置窗口。"""
        logger.info("打开设置窗口。")
        dialog = SettingsDialog(self.config_manager.all())
        dialog.settings_saved.connect(self._save_settings)
        dialog.exec()

    def _save_settings(self, patch: dict) -> None:
        """保存用户设置。"""
        self.config_manager.update(patch)
        logger.info("设置保存成功。")
        self.tray.show_info("设置已保存", "新的 LLM 配置将在下一次调用时生效。")

    def start_task(self, task_type: str, custom_instruction: str = "") -> None:
        """启动后台线程执行文本处理。"""
        if self.worker and self.worker.isRunning():
            logger.info("任务启动被忽略：已有任务在执行。")
            self.tray.show_info("处理中", "上一个任务仍在执行，请稍候。")
            return

        logger.info("准备启动任务：task=%s custom_instruction_length=%s", task_type, len(custom_instruction))
        self.worker = TextProcessThread(
            clipboard_manager=self.clipboard_manager,
            llm_client=self.llm_client,
            task_type=task_type,
            custom_instruction=custom_instruction,
        )
        self.worker.success.connect(self._on_task_success)
        self.worker.failed.connect(self._on_task_failed)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_task_success(self, message: str) -> None:
        """任务成功提示。"""
        logger.info("任务成功：%s", message)
        self.tray.show_info("处理完成", message)

    def _on_task_failed(self, error_message: str) -> None:
        """任务失败提示。"""
        final_message = self._build_recovery_message(error_message)
        logger.warning("任务失败：%s", final_message)
        self.tray.show_error("处理失败", final_message)

    def _on_worker_finished(self) -> None:
        """线程结束后清理引用。"""
        logger.info("后台线程已结束。")
        self.worker = None

    def check_local_service(self) -> None:
        """手动检查本地模型服务状态。"""
        if self.service_checker and self.service_checker.isRunning():
            self.tray.show_info("检查中", "本地模型服务检查已在进行，请稍候。")
            return

        logger.info("开始检查本地模型服务。")
        self.service_checker = ServiceCheckThread(self.llm_client)
        self.service_checker.success.connect(self._on_service_check_success)
        self.service_checker.failed.connect(self._on_service_check_failed)
        self.service_checker.finished.connect(self._on_service_check_finished)
        self.service_checker.start()

    def _on_service_check_success(self, message: str) -> None:
        """本地模型服务检查成功。"""
        logger.info("本地模型服务检查成功：%s", message)
        self.tray.show_info("服务检查完成", message)

    def _on_service_check_failed(self, message: str) -> None:
        """本地模型服务检查失败。"""
        logger.warning("本地模型服务检查失败：%s", message)
        self.tray.show_error("服务检查失败", message)

    def _on_service_check_finished(self) -> None:
        """清理服务检查线程引用。"""
        logger.info("本地模型服务检查线程已结束。")
        self.service_checker = None

    @staticmethod
    def _build_recovery_message(error_message: str) -> str:
        """为常见异常补充可执行的恢复建议。"""
        if "无法连接本地 llama-server" in error_message:
            return (
                f"{error_message}\n"
                "恢复建议：确认 llama-server 已启动、检查设置中的本地服务地址，"
                "或从托盘菜单点击“检查本地模型服务”。"
            )

        if "请求超时" in error_message:
            return (
                f"{error_message}\n"
                "恢复建议：等待模型空闲后重试，或换用更小上下文、更快模型。"
            )

        if "未检测到选中文本" in error_message:
            return (
                f"{error_message}\n"
                "恢复建议：请在可编辑输入框中重新选中文本后再触发快捷键。"
            )

        if "目标输入窗口已变化" in error_message or "目标输入框焦点已变化" in error_message:
            return (
                f"{error_message}\n"
                "恢复建议：在生成过程中保持原输入框聚焦，不要切换窗口或点击其他位置，然后重新触发。"
            )

        if "发送输入事件失败" in error_message:
            return (
                f"{error_message}\n"
                "恢复建议：目标程序可能不接受模拟输入，可尝试以相同权限运行本工具和目标程序后重试。"
            )

        return error_message

    def shutdown(self) -> None:
        """主动退出入口。"""
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        logger.info("收到程序退出请求。")
        self.app.quit()

    def _cleanup_resources(self) -> None:
        """退出时释放热键、托盘和单实例锁。"""
        logger.info("开始清理应用资源。")
        if self.worker and self.worker.isRunning():
            self.worker.wait(2000)

        if self.service_checker and self.service_checker.isRunning():
            self.service_checker.wait(2000)

        self.hotkey_listener.unregister_all()

        self.tray.hide()

        if self.shared_memory.isAttached():
            self.shared_memory.detach()
        logger.info("应用资源清理完成。")


def ensure_single_instance() -> QSharedMemory | None:
    """使用 QSharedMemory 防止程序重复启动。"""
    shared_memory = QSharedMemory("LLM_INPUT_ENHANCER_SINGLE_INSTANCE")
    if not shared_memory.create(1):
        logger.warning("检测到重复实例，阻止再次启动。")
        return None
    return shared_memory


def handle_uncaught_exception(exc_type, exc_value, exc_traceback) -> None:
    """兜底异常处理，防止程序静默崩溃。"""
    error_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logger.error("未捕获异常：\n%s", error_text)
    print(error_text, file=sys.stderr)


def main() -> int:
    """应用启动入口。"""
    log_file = setup_logging()
    sys.excepthook = handle_uncaught_exception
    logger.info("程序启动。日志文件：%s", log_file)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    shared_memory = ensure_single_instance()
    if shared_memory is None:
        QMessageBox.warning(None, "提示", "程序已在运行，请勿重复启动。")
        return 0

    controller = AppController(app, shared_memory)
    _ = controller
    logger.info("Qt 事件循环启动。")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
