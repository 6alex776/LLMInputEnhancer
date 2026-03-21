"""配置管理模块。

负责读取、校验、保存本地 JSON 配置，确保用户设置持久化。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_CONFIG: dict[str, Any] = {
    "provider": "ollama",  # 可选值：doubao / ollama
    "doubao_api_key": "",
    "doubao_model": "doubao-seed-1-6-250615",
    "doubao_endpoint": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
    "ollama_url": "http://127.0.0.1:8080/",
    "ollama_model": "Qwen3.5-9B-IQ4_NL.gguf",
    "temperature": 0.2,
    "max_tokens": 1024,
}


class ConfigManager:
    """应用配置管理器。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parent
        self.config_path = Path(config_path) if config_path else base_dir / "config.json"
        self._lock = threading.RLock()
        self._config: dict[str, Any] = DEFAULT_CONFIG.copy()
        self.load()

    def load(self) -> dict[str, Any]:
        """从本地 JSON 读取配置，不存在时自动创建默认配置。"""
        with self._lock:
            if not self.config_path.exists():
                self.save()
                return self._config.copy()

            try:
                raw_text = self.config_path.read_text(encoding="utf-8")
                data = json.loads(raw_text) if raw_text.strip() else {}
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}

            merged = DEFAULT_CONFIG.copy()
            merged.update(data)
            self._config = merged
            return self._config.copy()

    def save(self) -> None:
        """保存配置到本地 JSON 文件。"""
        with self._lock:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(self._config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

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
            self._config.update(patch)
            self.save()
            return self._config.copy()


def is_valid_http_url(value: str) -> bool:
    """校验 HTTP/HTTPS URL。"""
    try:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def validate_settings(data: dict[str, Any]) -> tuple[bool, str]:
    """设置界面保存前的统一校验逻辑。"""
    provider = str(data.get("provider", "")).strip().lower()
    if provider not in {"doubao", "ollama"}:
        return False, "LLM 类型仅支持 doubao 或 ollama。"

    if provider == "doubao":
        if not str(data.get("doubao_api_key", "")).strip():
            return False, "云端模式必须填写豆包 API Key。"
        endpoint = str(data.get("doubao_endpoint", "")).strip()
        if not is_valid_http_url(endpoint):
            return False, "豆包接口地址格式不正确，请填写完整的 HTTP/HTTPS 地址。"

    if provider == "ollama":
        ollama_url = str(data.get("ollama_url", "")).strip()
        if not is_valid_http_url(ollama_url):
            return False, "本地服务地址格式不正确，请填写完整的 HTTP/HTTPS 地址。"
        if not str(data.get("ollama_model", "")).strip():
            return False, "本地模式必须填写模型名称。"

    return True, ""
