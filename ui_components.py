"""UI 组件模块。

包含系统托盘、光标跟随指令面板、设置窗口。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from config import validate_settings


TASK_LABELS: list[tuple[str, str]] = [
    ("polish", "1. 文本润色"),
    ("translate", "2. 中英互译"),
    ("expand", "3. 文本扩写"),
    ("summarize", "4. 文本缩写"),
]


class CommandPanel(QWidget):
    """光标跟随的无边框指令面板。"""

    task_requested = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LLM 输入增强")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Popup | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setMinimumWidth(360)
        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("LLM 输入增强")
        title.setObjectName("title_label")
        layout.addWidget(title)

        tip = QLabel("数字键快速选择：1/2/3/4")
        tip.setObjectName("tip_label")
        layout.addWidget(tip)

        for _, label_text in TASK_LABELS:
            row = QLabel(label_text)
            row.setObjectName("task_label")
            layout.addWidget(row)

        layout.addSpacing(6)

        custom_title = QLabel("自定义指令")
        custom_title.setObjectName("section_label")
        layout.addWidget(custom_title)

        row_layout = QHBoxLayout()
        row_layout.setSpacing(6)

        self.custom_input = QLineEdit()
        self.custom_input.setPlaceholderText("输入你的处理指令，例如：改成更正式的商务邮件语气")
        self.custom_input.returnPressed.connect(self._emit_custom_task)

        run_button = QPushButton("执行")
        run_button.clicked.connect(self._emit_custom_task)

        row_layout.addWidget(self.custom_input)
        row_layout.addWidget(run_button)
        layout.addLayout(row_layout)

        close_tip = QLabel("Esc 关闭")
        close_tip.setObjectName("tip_label")
        layout.addWidget(close_tip)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #f6f8fc;
                border: 1px solid #d8deea;
                border-radius: 10px;
                color: #1f2d3d;
                font-family: 'Microsoft YaHei UI';
                font-size: 13px;
            }
            QLabel#title_label {
                font-size: 16px;
                font-weight: 700;
                color: #0f3d80;
                border: none;
            }
            QLabel#section_label {
                font-size: 13px;
                font-weight: 600;
                border: none;
            }
            QLabel#task_label, QLabel#tip_label {
                border: none;
                color: #324d6a;
            }
            QLineEdit {
                border: 1px solid #b8c6db;
                border-radius: 6px;
                padding: 6px;
                background: #ffffff;
            }
            QPushButton {
                background: #1e5db3;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background: #194d93;
            }
            """
        )

    def show_near_cursor(self) -> None:
        """在鼠标光标附近弹出。"""
        cursor_pos = QCursor.pos()
        target = QPoint(cursor_pos.x() + 12, cursor_pos.y() + 12)

        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            panel_size = self.sizeHint()
            if target.x() + panel_size.width() > available.right():
                target.setX(max(available.left(), cursor_pos.x() - panel_size.width() - 12))
            if target.y() + panel_size.height() > available.bottom():
                target.setY(max(available.top(), cursor_pos.y() - panel_size.height() - 12))

        self.move(target)
        self.show()
        self.raise_()
        self.activateWindow()
        self.custom_input.setFocus()

    def _emit_custom_task(self, *_args) -> None:
        instruction = self.custom_input.text().strip()
        if not instruction:
            QMessageBox.warning(self, "提示", "请先输入自定义指令。")
            return

        self.task_requested.emit("custom", instruction)
        self.custom_input.clear()
        self.hide()

    def keyPressEvent(self, event):  # type: ignore[override]
        """支持数字键快速选择任务。"""
        key_to_task = {
            Qt.Key_1: "polish",
            Qt.Key_2: "translate",
            Qt.Key_3: "expand",
            Qt.Key_4: "summarize",
        }
        task = key_to_task.get(event.key())
        if task:
            self.task_requested.emit(task, "")
            self.hide()
            return

        if event.key() == Qt.Key_Escape:
            self.hide()
            return

        super().keyPressEvent(event)

    def focusOutEvent(self, event):  # type: ignore[override]
        """失去焦点自动关闭，减少对输入流程干扰。"""
        self.hide()
        super().focusOutEvent(event)


class SettingsDialog(QDialog):
    """基础设置窗口。"""

    settings_saved = Signal(dict)

    def __init__(self, config: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LLM 输入增强设置")
        self.setModal(True)
        self.setMinimumWidth(540)
        self._build_ui()
        self.load_config(config)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("云端（豆包）", "doubao")
        self.provider_combo.addItem("本地（Ollama / llama-server）", "ollama")
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)

        self.doubao_key_input = QLineEdit()
        self.doubao_key_input.setEchoMode(QLineEdit.Password)
        self.doubao_model_input = QLineEdit()
        self.doubao_endpoint_input = QLineEdit()

        self.ollama_url_input = QLineEdit()
        self.ollama_model_input = QLineEdit()

        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setDecimals(2)

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(32, 8192)

        form.addRow("LLM 类型", self.provider_combo)
        form.addRow("豆包 API Key", self.doubao_key_input)
        form.addRow("豆包模型", self.doubao_model_input)
        form.addRow("豆包接口地址", self.doubao_endpoint_input)
        form.addRow("本地服务地址", self.ollama_url_input)
        form.addRow("本地模型名称", self.ollama_model_input)
        form.addRow("Temperature", self.temperature_spin)
        form.addRow("Max Tokens", self.max_tokens_spin)

        root_layout.addLayout(form)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        save_button = QPushButton("保存")
        cancel_button = QPushButton("取消")
        save_button.clicked.connect(self._on_save_clicked)
        cancel_button.clicked.connect(self.reject)

        button_row.addWidget(save_button)
        button_row.addWidget(cancel_button)
        root_layout.addLayout(button_row)

    def load_config(self, config: dict[str, Any]) -> None:
        """回填当前配置到控件。"""
        provider = str(config.get("provider", "doubao"))
        index = self.provider_combo.findData(provider)
        self.provider_combo.setCurrentIndex(0 if index < 0 else index)

        self.doubao_key_input.setText(str(config.get("doubao_api_key", "")))
        self.doubao_model_input.setText(str(config.get("doubao_model", "")))
        self.doubao_endpoint_input.setText(str(config.get("doubao_endpoint", "")))
        self.ollama_url_input.setText(str(config.get("ollama_url", "")))
        self.ollama_model_input.setText(str(config.get("ollama_model", "")))
        self.temperature_spin.setValue(float(config.get("temperature", 0.2)))
        self.max_tokens_spin.setValue(int(config.get("max_tokens", 1024)))

        self._on_provider_changed()

    def _on_provider_changed(self, *_args) -> None:
        """根据 provider 切换输入框可编辑状态。"""
        provider = self.provider_combo.currentData()
        is_doubao = provider == "doubao"

        self.doubao_key_input.setEnabled(is_doubao)
        self.doubao_model_input.setEnabled(is_doubao)
        self.doubao_endpoint_input.setEnabled(is_doubao)

        self.ollama_url_input.setEnabled(not is_doubao)
        self.ollama_model_input.setEnabled(not is_doubao)

    def collect_settings(self) -> dict[str, Any]:
        """读取界面输入并组装配置字典。"""
        return {
            "provider": self.provider_combo.currentData(),
            "doubao_api_key": self.doubao_key_input.text().strip(),
            "doubao_model": self.doubao_model_input.text().strip(),
            "doubao_endpoint": self.doubao_endpoint_input.text().strip(),
            "ollama_url": self.ollama_url_input.text().strip(),
            "ollama_model": self.ollama_model_input.text().strip(),
            "temperature": float(self.temperature_spin.value()),
            "max_tokens": int(self.max_tokens_spin.value()),
        }

    def _on_save_clicked(self, *_args) -> None:
        """执行设置校验并发出保存信号。"""
        patch = self.collect_settings()
        ok, message = validate_settings(patch)
        if not ok:
            QMessageBox.warning(self, "配置校验失败", message)
            return

        self.settings_saved.emit(patch)
        self.accept()


class AppTray(QObject):
    """系统托盘控制器。"""

    show_panel_requested = Signal()
    show_settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        icon = QApplication.style().standardIcon(QStyle.SP_ComputerIcon)
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip("LLM 输入增强工具")

        self.menu = QMenu()
        self.open_action = QAction("打开指令面板", self.menu)
        self.settings_action = QAction("设置", self.menu)
        self.quit_action = QAction("退出", self.menu)

        self.open_action.triggered.connect(self._on_open_action_triggered)
        self.settings_action.triggered.connect(self._on_settings_action_triggered)
        self.quit_action.triggered.connect(self._on_quit_action_triggered)

        self.menu.addAction(self.open_action)
        self.menu.addAction(self.settings_action)
        self.menu.addSeparator()
        self.menu.addAction(self.quit_action)

        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)

    def show(self) -> None:
        """显示托盘图标。"""
        self.tray.show()

    def hide(self) -> None:
        """隐藏托盘图标。"""
        self.tray.hide()

    def show_info(self, title: str, message: str) -> None:
        """显示普通提示。"""
        self.tray.showMessage(title, message, QSystemTrayIcon.Information, 3000)

    def show_error(self, title: str, message: str) -> None:
        """显示错误提示。"""
        self.tray.showMessage(title, message, QSystemTrayIcon.Critical, 5000)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """单击托盘图标时弹出指令面板。"""
        if reason in {
            QSystemTrayIcon.Trigger,
            QSystemTrayIcon.DoubleClick,
        }:
            self.show_panel_requested.emit()

    def _on_open_action_triggered(self, _checked: bool = False) -> None:
        self.show_panel_requested.emit()

    def _on_settings_action_triggered(self, _checked: bool = False) -> None:
        self.show_settings_requested.emit()

    def _on_quit_action_triggered(self, _checked: bool = False) -> None:
        self.quit_requested.emit()
