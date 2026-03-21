"""UI 组件模块。

包含系统托盘、光标跟随指令面板、设置窗口。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QAction, QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
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
        self._drag_active = False
        self._drag_offset = QPoint()
        self.setWindowTitle("LLM 输入增强")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setMinimumWidth(360)
        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        self.title_label = QLabel("LLM 输入增强")
        self.title_label.setObjectName("title_label")

        self.pin_tip_label = QLabel("此处拖动")
        self.pin_tip_label.setObjectName("drag_tip_label")

        close_button = QPushButton("×")
        close_button.setObjectName("close_button")
        close_button.setFixedSize(28, 28)
        close_button.clicked.connect(self.hide)

        title_row.addWidget(self.title_label)
        title_row.addStretch(1)
        title_row.addWidget(self.pin_tip_label)
        title_row.addWidget(close_button)
        layout.addLayout(title_row)

        tip = QLabel("Alt+数字键快速选择：")
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
            QLabel#drag_tip_label {
                border: none;
                color: #6b7c93;
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
            QPushButton#close_button {
                background: #e7edf7;
                color: #42566f;
                border: 1px solid #c7d3e3;
                border-radius: 6px;
                font-size: 16px;
                font-weight: 600;
                padding: 0;
            }
            QPushButton#close_button:hover {
                background: #dbe6f4;
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
        self.grabKeyboard()
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

    def hideEvent(self, event):  # type: ignore[override]
        """面板关闭时释放键盘占用。"""
        self.releaseKeyboard()
        self._drag_active = False
        super().hideEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """按住面板空白区域或标题区域时允许拖动窗口。"""
        if event.button() == Qt.LeftButton:
            target_widget = self.childAt(event.position().toPoint())
            if target_widget in {self, self.title_label, self.pin_tip_label}:
                self._drag_active = True
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """拖动时更新窗口位置。"""
        if self._drag_active and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        """结束拖动状态。"""
        if event.button() == Qt.LeftButton:
            self._drag_active = False
        super().mouseReleaseEvent(event)


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

        self.local_url_input = QLineEdit()
        self.local_model_input = QLineEdit()

        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setDecimals(2)

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(32, 8192)

        form.addRow("本地服务地址", self.local_url_input)
        form.addRow("本地模型名称", self.local_model_input)
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
        self.local_url_input.setText(str(config.get("local_url", "")))
        self.local_model_input.setText(str(config.get("local_model", "")))
        self.temperature_spin.setValue(float(config.get("temperature", 0.2)))
        self.max_tokens_spin.setValue(int(config.get("max_tokens", 1024)))

    def collect_settings(self) -> dict[str, Any]:
        """读取界面输入并组装配置字典。"""
        return {
            "local_url": self.local_url_input.text().strip(),
            "local_model": self.local_model_input.text().strip(),
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
