"""LLM 调用封装模块。

统一封装云端豆包 API 与本地 llama-server，提供一致的 generate 接口。
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from config import ConfigManager


class LLMClientError(RuntimeError):
    """LLM 调用统一异常类型。"""


class LLMClient:
    """统一 LLM 客户端。"""

    def __init__(self, config_manager: ConfigManager) -> None:
        self.config_manager = config_manager

    @staticmethod
    def _system_prompt(task_type: str, custom_instruction: str = "") -> str:
        """生成严格输出约束的系统提示词。"""
        base_rule = (
            "你是一个Windows输入增强工具的文本处理引擎。"
            "你必须且只能输出最终处理后的文本内容。"
            "禁止输出任何解释、前言、后记、标题、引号、代码块标记。"
            "如果输入为空，返回空字符串。"
        )

        task_prompts = {
            "polish": "任务：润色用户文本，修正病句和错别字，提升流畅度，保持原意。",
            "translate": "任务：自动识别源语言，在中文与英文之间双向互译，保持准确、自然。",
            "expand": "任务：在不改变原意前提下扩写文本，补全细节与表达。",
            "summarize": "任务：缩写文本，提炼核心信息，保留关键结论。",
            "custom": f"任务：严格按照用户自定义指令处理文本。用户指令：{custom_instruction.strip()}",
        }

        return f"{base_rule}\n{task_prompts.get(task_type, task_prompts['polish'])}"

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        """判断文本是否包含中日韩统一表意文字。"""
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    @staticmethod
    def _contains_latin(text: str) -> bool:
        """判断文本是否包含英文字母。"""
        return bool(re.search(r"[A-Za-z]", text))

    def _build_translate_messages(self, text: str) -> list[dict[str, str]]:
        """为翻译任务构建更强约束的消息。"""
        has_cjk = self._contains_cjk(text)
        has_latin = self._contains_latin(text)

        if has_cjk and not has_latin:
            direction_prompt = (
                "你是中英翻译引擎。"
                "把用户给出的中文直接翻译成自然、准确、简洁的英文。"
                "只输出英文译文，不要解释，不要补充。"
            )
        elif has_latin and not has_cjk:
            direction_prompt = (
                "你是中英翻译引擎。"
                "把用户给出的英文直接翻译成自然、准确、简洁的中文。"
                "只输出中文译文，不要解释，不要补充。"
            )
        else:
            direction_prompt = (
                "你是中英翻译引擎。"
                "自动识别用户文本是中文还是英文，并把它翻译成另一种语言。"
                "只输出译文，不要解释，不要补充。"
            )

        return [
            {"role": "system", "content": direction_prompt},
            {"role": "user", "content": text},
        ]

    @staticmethod
    def _user_prompt(text: str, context: str = "") -> str:
        """构建用户输入提示。"""
        context_part = ""
        if context:
            context_part = f"\n\n可选上下文（用于辅助理解，不必原样输出）：\n{context[:2000]}"
        return f"请处理以下文本：\n{text}{context_part}"

    def generate(
        self,
        task_type: str,
        text: str,
        custom_instruction: str = "",
        context: str = "",
    ) -> str:
        """同步生成接口（MVP 主入口）。"""
        config = self.config_manager.all()
        provider = str(config.get("provider", "doubao")).strip().lower()

        if task_type == "translate":
            messages = self._build_translate_messages(text)
        else:
            messages = [
                {"role": "system", "content": self._system_prompt(task_type, custom_instruction)},
                {"role": "user", "content": self._user_prompt(text, context)},
            ]

        if provider == "ollama":
            result = self._call_local_sync(messages, config)
        else:
            result = self._call_doubao_sync(messages, config)

        if task_type == "translate":
            self._validate_translation_result(text, result)
        return result

    async def generate_async(
        self,
        task_type: str,
        text: str,
        custom_instruction: str = "",
        context: str = "",
    ) -> str:
        """异步生成接口，便于后续扩展异步调用。"""
        config = self.config_manager.all()
        provider = str(config.get("provider", "doubao")).strip().lower()

        if task_type == "translate":
            messages = self._build_translate_messages(text)
        else:
            messages = [
                {"role": "system", "content": self._system_prompt(task_type, custom_instruction)},
                {"role": "user", "content": self._user_prompt(text, context)},
            ]

        if provider == "ollama":
            result = await self._call_local_async(messages, config)
        else:
            result = await self._call_doubao_async(messages, config)

        if task_type == "translate":
            self._validate_translation_result(text, result)
        return result

    def stream_generate(
        self,
        task_type: str,
        text: str,
        custom_instruction: str = "",
        context: str = "",
    ):
        """预留流式接口，MVP 暂不实现流式解析。"""
        raise NotImplementedError("已预留流式输出接口，当前 MVP 版本使用全量输出。")

    def _call_doubao_sync(self, messages: list[dict[str, str]], config: dict[str, Any]) -> str:
        """同步调用豆包兼容接口。"""
        api_key = str(config.get("doubao_api_key", "")).strip()
        endpoint = str(config.get("doubao_endpoint", "")).strip()
        model = str(config.get("doubao_model", "")).strip()

        if not api_key:
            raise LLMClientError("豆包 API Key 为空，请先在设置中配置。")
        if not endpoint or not model:
            raise LLMClientError("豆包接口地址或模型名称为空，请检查设置。")

        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(config.get("temperature", 0.2)),
            "max_tokens": int(config.get("max_tokens", 1024)),
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        timeout = httpx.Timeout(30.0)

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(endpoint, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
            return self._parse_chat_completion(data)
        except httpx.TimeoutException as exc:
            raise LLMClientError("豆包请求超时（30秒），请检查网络或稍后重试。") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300] if exc.response is not None else ""
            raise LLMClientError(f"豆包接口返回错误：HTTP {exc.response.status_code} {detail}") from exc
        except httpx.HTTPError as exc:
            raise LLMClientError(f"豆包网络请求失败：{exc}") from exc
        except Exception as exc:
            raise LLMClientError(f"豆包响应解析失败：{exc}") from exc

    async def _call_doubao_async(self, messages: list[dict[str, str]], config: dict[str, Any]) -> str:
        """异步调用豆包接口。"""
        api_key = str(config.get("doubao_api_key", "")).strip()
        endpoint = str(config.get("doubao_endpoint", "")).strip()
        model = str(config.get("doubao_model", "")).strip()

        if not api_key:
            raise LLMClientError("豆包 API Key 为空，请先在设置中配置。")

        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(config.get("temperature", 0.2)),
            "max_tokens": int(config.get("max_tokens", 1024)),
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        timeout = httpx.Timeout(30.0)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
            return self._parse_chat_completion(data)
        except httpx.TimeoutException as exc:
            raise LLMClientError("豆包请求超时（30秒），请检查网络或稍后重试。") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300] if exc.response is not None else ""
            raise LLMClientError(f"豆包接口返回错误：HTTP {exc.response.status_code} {detail}") from exc
        except httpx.HTTPError as exc:
            raise LLMClientError(f"豆包网络请求失败：{exc}") from exc
        except Exception as exc:
            raise LLMClientError(f"豆包响应解析失败：{exc}") from exc

    def _call_local_sync(self, messages: list[dict[str, str]], config: dict[str, Any]) -> str:
        """同步调用本地 llama-server(OpenAI 兼容接口)。"""
        base_url = str(config.get("ollama_url", "http://127.0.0.1:8080/")).rstrip("/")
        model = str(config.get("ollama_model", "")).strip()
        if not model:
            raise LLMClientError("本地模型名称为空，请在设置中配置。")

        endpoint = f"{base_url}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(config.get("temperature", 0.2)),
            "max_tokens": int(config.get("max_tokens", 1024)),
            "stream": False,
        }

        timeout = httpx.Timeout(60.0)

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
            return self._parse_chat_completion(data)
        except httpx.ConnectError as exc:
            raise LLMClientError("无法连接本地 llama-server，请确认服务已启动且地址可访问。") from exc
        except httpx.TimeoutException as exc:
            raise LLMClientError("本地模型请求超时（60秒），请检查模型负载或机器性能。") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300] if exc.response is not None else ""
            raise LLMClientError(f"本地接口返回错误：HTTP {exc.response.status_code} {detail}") from exc
        except httpx.HTTPError as exc:
            raise LLMClientError(f"本地请求失败：{exc}") from exc
        except Exception as exc:
            raise LLMClientError(f"本地响应解析失败：{exc}") from exc

    async def _call_local_async(self, messages: list[dict[str, str]], config: dict[str, Any]) -> str:
        """异步调用本地 llama-server(OpenAI 兼容接口)。"""
        base_url = str(config.get("ollama_url", "http://127.0.0.1:8080/")).rstrip("/")
        model = str(config.get("ollama_model", "")).strip()
        if not model:
            raise LLMClientError("本地模型名称为空，请在设置中配置。")

        endpoint = f"{base_url}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(config.get("temperature", 0.2)),
            "max_tokens": int(config.get("max_tokens", 1024)),
            "stream": False,
        }

        timeout = httpx.Timeout(60.0)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
            return self._parse_chat_completion(data)
        except httpx.ConnectError as exc:
            raise LLMClientError("无法连接本地 llama-server，请确认服务已启动且地址可访问。") from exc
        except httpx.TimeoutException as exc:
            raise LLMClientError("本地模型请求超时（60秒），请检查模型负载或机器性能。") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300] if exc.response is not None else ""
            raise LLMClientError(f"本地接口返回错误：HTTP {exc.response.status_code} {detail}") from exc
        except httpx.HTTPError as exc:
            raise LLMClientError(f"本地请求失败：{exc}") from exc
        except Exception as exc:
            raise LLMClientError(f"本地响应解析失败：{exc}") from exc

    @staticmethod
    def _parse_chat_completion(payload: dict[str, Any]) -> str:
        """解析 OpenAI 兼容 chat/completions 输出。"""
        choices = payload.get("choices", []) if isinstance(payload, dict) else []
        if not choices:
            raise LLMClientError("模型返回结果为空。")

        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content", "")
        content = str(content).strip()
        if not content:
            raise LLMClientError("模型返回内容为空。")
        return content

    def _validate_translation_result(self, source_text: str, result_text: str) -> None:
        """对翻译结果做基础可信度校验，避免错误结果直接覆盖原文。"""
        source = source_text.strip()
        result = result_text.strip()
        result_lower = result.lower()

        generic_bad_patterns = [
            "what would you like",
            "how can i help",
            "i'd love to help",
            "please provide",
            "请告诉我",
            "请提供",
            "我可以帮助你",
            "我无法直接",
        ]
        if any(pattern in result_lower for pattern in generic_bad_patterns):
            raise LLMClientError(
                "当前本地模型没有正确执行翻译，而是返回了通用对话内容。建议改用更大的模型。"
            )

        if result == source:
            raise LLMClientError("当前本地模型没有完成翻译，返回了与原文相同的内容。")

        source_has_cjk = self._contains_cjk(source)
        source_has_latin = self._contains_latin(source)
        result_has_cjk = self._contains_cjk(result)
        result_has_latin = self._contains_latin(result)

        if source_has_cjk and not source_has_latin and result_has_cjk and not result_has_latin:
            raise LLMClientError(
                "当前本地模型未将中文正确翻译成英文。建议使用至少 3B 以上模型以获得稳定翻译效果。"
            )

        if source_has_latin and not source_has_cjk and result_has_latin and not result_has_cjk:
            raise LLMClientError(
                "当前本地模型未将英文正确翻译成中文。建议使用至少 3B 以上模型以获得稳定翻译效果。"
            )
