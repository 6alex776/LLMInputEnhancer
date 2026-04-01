"""应用日志模块。

提供统一的日志初始化、日志目录管理和模块级 logger 获取方法。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys


BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"
_IS_CONFIGURED = False


def setup_logging() -> Path:
    """初始化全局日志系统，仅执行一次。"""
    global _IS_CONFIGURED
    if _IS_CONFIGURED:
        return LOG_FILE

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=1_048_576,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    if not any(
        isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", "") == str(LOG_FILE)
        for handler in root_logger.handlers
    ):
        root_logger.addHandler(file_handler)

    _IS_CONFIGURED = True
    root_logger.info("日志系统初始化完成，日志文件：%s", LOG_FILE)
    return LOG_FILE


def get_logger(name: str) -> logging.Logger:
    """获取模块专用 logger。"""
    if not _IS_CONFIGURED:
        setup_logging()
    return logging.getLogger(name)
