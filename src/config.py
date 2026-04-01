"""配置管理模块。

负责读取、校验、保存本地 JSON 配置，确保用户设置持久化。
当前版本仅保留本地 llama-server 相关配置。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse

from app_logger import get_logger


logger = get_logger("config")

DEFAULT_CONFIG: dict[str, Any] = {
    "local_url": "http://127.0.0.1:8080/",
    "local_model": "Qwen3.5-0.8B-IQ4_NL.gguf",
    "temperature": 0.6,
    "max_tokens": 1024,
    "enable_classifier_recommendation": True,
    "auto_classify_execute_threshold": 0.75,
    "auto_classify_recommend_threshold": 0.50,
}


class ConfigManager:
    """应用配置管理器。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        base_dir = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent.parent
        )
        self.config_path = Path(config_path) if config_path else base_dir / "config.json"
        self._lock = threading.RLock()
        self._config: dict[str, Any] = DEFAULT_CONFIG.copy()
        self.load()

    def load(self) -> dict[str, Any]:
        """从本地 JSON 读取配置，不存在时自动创建默认配置。"""
        with self._lock:
            if not self.config_path.exists():
                logger.info("配置文件不存在，创建默认配置：%s", self.config_path)
                self.save()
                return self._config.copy()

            try:
                raw_text = self.config_path.read_text(encoding="utf-8")
                data = json.loads(raw_text) if raw_text.strip() else {}
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                logger.exception("读取配置文件失败，将回退到默认配置：%s", self.config_path)
                data = {}

            migrated = self._migrate_legacy_keys(data)
            merged = DEFAULT_CONFIG.copy()
            merged.update(migrated)
            self._config = merged
            self.save()
            logger.info("配置加载完成：config_path=%s", self.config_path)
            return self._config.copy()

    def save(self) -> None:
        """保存配置到本地 JSON 文件。"""
        with self._lock:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(self._config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("配置保存完成：config_path=%s", self.config_path)

    def get(self, key: str, default: Any = None) -> Any:
        """读取单个配置项。"""
        with self._lock:
            return self._config.get(key, default)

    def all(self) -> dict[str, Any]:
        """返回配置快照副本。"""
        with self._lock:
            return self._config.copy()

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """批量更新并保存配置。"""
        with self._lock:
            migrated = self._migrate_legacy_keys(patch)
            self._config.update(migrated)
            self.save()
            logger.info("配置更新完成：keys=%s", ",".join(sorted(migrated.keys())))
            return self._config.copy()

    @staticmethod
    def _migrate_legacy_keys(data: dict[str, Any]) -> dict[str, Any]:
        """兼容旧版本配置键名，自动迁移到本地模型专用字段。"""
        migrated = dict(data)

        if "local_url" not in migrated and "ollama_url" in migrated:
            migrated["local_url"] = migrated.get("ollama_url", "")
        if "local_model" not in migrated and "ollama_model" in migrated:
            migrated["local_model"] = migrated.get("ollama_model", "")
        if "enable_classifier_recommendation" not in migrated and "enable_torch_recommendation" in migrated:
            migrated["enable_classifier_recommendation"] = bool(migrated.get("enable_torch_recommendation"))

        migrated.pop("provider", None)
        migrated.pop("doubao_api_key", None)
        migrated.pop("doubao_model", None)
        migrated.pop("doubao_endpoint", None)
        migrated.pop("ollama_url", None)
        migrated.pop("ollama_model", None)
        migrated.pop("enable_torch_recommendation", None)
        return migrated


def is_valid_http_url(value: str) -> bool:
    """校验 HTTP/HTTPS URL。"""
    try:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def validate_settings(data: dict[str, Any]) -> tuple[bool, str]:
    """设置界面保存前的统一校验逻辑。"""
    local_url = str(data.get("local_url", "")).strip()
    if not is_valid_http_url(local_url):
        return False, "本地服务地址格式不正确，请填写完整的 HTTP/HTTPS 地址。"

    if not str(data.get("local_model", "")).strip():
        return False, "本地模型名称不能为空。"

    return True, ""
