"""LLM 调用封装模块。

当前版本连接本地 llama-server 的 OpenAI 兼容接口，
同时提供一次性输出和流式输出两种调用方式。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from app_logger import get_logger
from config import ConfigManager


logger = get_logger("llm")


class LLMClientError(RuntimeError):
    """统一的 LLM 调用异常。"""


class LLMClient:
    """本地 llama-server 客户端。"""

    def __init__(self, config_manager: ConfigManager) -> None:
        self.config_manager = config_manager

    @staticmethod
    def _system_prompt(task_type: str, custom_instruction: str = "") -> str:
        """生成严格输出约束的系统提示词。"""
        base_rule = (
            "你是一个 Windows 输入增强工具的文本处理引擎。"
            "你必须且只能输出最终处理后的文本内容。"
            "禁止输出任何解释、前言、后记、标题、引号、代码块标记。"
            "如果输入为空，返回空字符串。"
        )

        task_prompts = {
            "polish": "任务：润色用户文本，修正病句和错别字，提升流畅度，保持原意。",
            "translate": "任务：自动识别源语言，在中文与英文之间双向互译，保持准确、自然。",
            "expand": "任务：在不改变原意前提下扩写文本，补充细节与表达。",
            "summarize": "任务：缩写文本，提炼核心信息，保留关键信息。",
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

    @staticmethod
    def _normalize_stream_context(text: str, context: str) -> str:
        """流式模式下尽量去掉重复或明显冗余的上下文。"""
        raw_text = text or ""
        raw_context = context or ""
        if not raw_context.strip():
            return ""

        stripped_text = raw_text.strip()
        stripped_context = raw_context.strip()
        if not stripped_text:
            return stripped_context

        normalized_text = re.sub(r"\s+", "", stripped_text)
        normalized_context = re.sub(r"\s+", "", stripped_context)

        if normalized_context == normalized_text:
            logger.info("流式模式已移除重复 context：与选中文本等价。")
            return ""

        if normalized_context.startswith(normalized_text) or normalized_context.endswith(normalized_text):
            logger.info("流式模式已移除冗余 context：包含选中文本主体。")
            return ""

        if normalized_text in normalized_context and len(normalized_context) <= len(normalized_text) * 2:
            logger.info("流式模式已移除冗余 context：高度重复且增量有限。")
            return ""

        return stripped_context

    def _build_messages(
        self,
        task_type: str,
        text: str,
        custom_instruction: str = "",
        context: str = "",
        stream_mode: bool = False,
    ) -> list[dict[str, str]]:
        """统一构建模型输入消息。"""
        if task_type == "translate":
            return self._build_translate_messages(text)

        effective_context = context
        if stream_mode:
            effective_context = self._normalize_stream_context(text, context)

        return [
            {"role": "system", "content": self._system_prompt(task_type, custom_instruction)},
            {"role": "user", "content": self._user_prompt(text, effective_context)},
        ]

    def generate(
        self,
        task_type: str,
        text: str,
        custom_instruction: str = "",
        context: str = "",
    ) -> str:
        """同步生成接口。"""
        logger.info("开始执行模型任务：task=%s input_length=%s", task_type, len(text))
        messages = self._build_messages(task_type, text, custom_instruction, context)

        result = self._call_local_sync(messages, self.config_manager.all(), task_type)
        if task_type == "translate":
            self._validate_translation_result(text, result)

        logger.info("模型任务完成：task=%s output_length=%s", task_type, len(result))
        return result

    async def generate_async(
        self,
        task_type: str,
        text: str,
        custom_instruction: str = "",
        context: str = "",
    ) -> str:
        """异步生成接口。"""
        logger.info("开始执行异步模型任务：task=%s input_length=%s", task_type, len(text))
        messages = self._build_messages(task_type, text, custom_instruction, context)

        result = await self._call_local_async(messages, self.config_manager.all(), task_type)
        if task_type == "translate":
            self._validate_translation_result(text, result)

        logger.info("异步模型任务完成：task=%s output_length=%s", task_type, len(result))
        return result

    def stream_generate(
        self,
        task_type: str,
        text: str,
        custom_instruction: str = "",
        context: str = "",
    ):
        """流式生成接口，按增量片段持续产出文本。"""
        logger.info("开始执行流式模型任务：task=%s input_length=%s", task_type, len(text))
        messages = self._build_messages(task_type, text, custom_instruction, context, stream_mode=True)

        chunks: list[str] = []
        for chunk in self._call_local_stream(messages, self.config_manager.all(), task_type):
            if not chunk:
                continue
            chunks.append(chunk)
            yield chunk

        result = "".join(chunks).strip()
        if not result:
            raise LLMClientError("模型返回为空，未生成可用内容。")

        if task_type == "translate":
            self._validate_translation_result(text, result)

        logger.info("流式模型任务完成：task=%s output_length=%s", task_type, len(result))

    def check_service(self, timeout_seconds: float = 3.0) -> tuple[bool, str]:
        """检查本地 llama-server 是否可用，并验证当前模型配置。"""
        config = self.config_manager.all()
        base_url = str(config.get("local_url", "http://127.0.0.1:8080/")).rstrip("/")
        target_model = str(config.get("local_model", "")).strip()
        timeout = httpx.Timeout(timeout_seconds)

        logger.info("开始检查本地模型服务：base_url=%s target_model=%s", base_url, target_model)

        try:
            with httpx.Client(timeout=timeout) as client:
                health_response = client.get(f"{base_url}/health")
                if health_response.status_code == 200:
                    logger.info("本地模型服务健康检查通过：/health")

                models_response = client.get(f"{base_url}/v1/models")
                models_response.raise_for_status()
                payload = models_response.json()
        except httpx.ConnectError:
            logger.exception("本地模型服务检查失败：无法连接。")
            return False, "无法连接本地 llama-server，请确认服务已经启动。"
        except httpx.TimeoutException:
            logger.exception("本地模型服务检查失败：请求超时。")
            return False, "本地模型服务检查超时，请确认服务是否卡住或机器负载过高。"
        except httpx.HTTPError as exc:
            logger.exception("本地模型服务检查失败：HTTP 异常。")
            return False, f"本地模型服务检查失败：{exc}"
        except Exception as exc:
            logger.exception("本地模型服务检查失败：未知异常。")
            return False, f"本地模型服务检查失败：{exc}"

        models = payload.get("data", []) if isinstance(payload, dict) else []
        model_ids = [str(item.get("id", "")).strip() for item in models if isinstance(item, dict)]

        if target_model and model_ids and target_model not in model_ids:
            logger.warning("本地模型服务已连接，但未找到当前配置模型：%s", target_model)
            return False, f"服务已启动，但未找到当前配置的模型：{target_model}"

        if target_model and not model_ids:
            logger.info("本地模型服务已连接，模型列表为空，继续使用当前配置模型。")
            return True, f"本地模型服务可访问，当前模型配置为：{target_model}"

        if target_model:
            logger.info("本地模型服务检查成功，已找到目标模型：%s", target_model)
            return True, f"本地模型服务正常，已检测到模型：{target_model}"

        logger.info("本地模型服务检查成功。")
        return True, "本地模型服务正常。"

    def _call_local_sync(
        self,
        messages: list[dict[str, str]],
        config: dict[str, Any],
        task_type: str,
    ) -> str:
        """同步调用本地 llama-server(OpenAI 兼容接口)。"""
        base_url = str(config.get("local_url", "http://127.0.0.1:8080/")).rstrip("/")
        model = str(config.get("local_model", "")).strip()
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
        started_at = time.perf_counter()

        logger.info("发起本地模型请求：task=%s model=%s endpoint=%s", task_type, model, endpoint)

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(endpoint, json=payload)
                response.raise_for_status()
                data = response.json()

            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info("本地模型响应成功：task=%s elapsed_ms=%s", task_type, elapsed_ms)
            return self._parse_chat_completion(data)
        except httpx.ConnectError as exc:
            logger.exception("连接本地 llama-server 失败：endpoint=%s", endpoint)
            raise LLMClientError("无法连接本地 llama-server，请确认服务已启动且地址可访问。") from exc
        except httpx.TimeoutException as exc:
            logger.exception("本地模型请求超时：task=%s endpoint=%s", task_type, endpoint)
            raise LLMClientError("本地模型请求超时（60秒），请检查模型负载或机器性能。") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300] if exc.response is not None else ""
            logger.exception("本地模型返回 HTTP 错误：task=%s detail=%s", task_type, detail)
            raise LLMClientError(
                f"本地接口返回错误：HTTP {exc.response.status_code} {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.exception("本地模型请求失败：task=%s endpoint=%s", task_type, endpoint)
            raise LLMClientError(f"本地请求失败：{exc}") from exc
        except Exception as exc:
            logger.exception("本地模型响应解析失败：task=%s", task_type)
            raise LLMClientError(f"本地响应解析失败：{exc}") from exc

    def _call_local_stream(
        self,
        messages: list[dict[str, str]],
        config: dict[str, Any],
        task_type: str,
    ):
        """流式调用本地 llama-server(OpenAI 兼容接口)。"""
        base_url = str(config.get("local_url", "http://127.0.0.1:8080/")).rstrip("/")
        model = str(config.get("local_model", "")).strip()
        if not model:
            raise LLMClientError("本地模型名称为空，请在设置中配置。")

        endpoint = f"{base_url}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(config.get("temperature", 0.2)),
            "max_tokens": int(config.get("max_tokens", 1024)),
            "stream": True,
        }
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=60.0)
        started_at = time.perf_counter()

        logger.info("发起本地模型流式请求：task=%s model=%s endpoint=%s", task_type, model, endpoint)

        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream("POST", endpoint, json=payload) as response:
                    response.raise_for_status()
                    headers_elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                    logger.info("本地模型流式响应头到达：task=%s elapsed_ms=%s", task_type, headers_elapsed_ms)
                    content_type = response.headers.get("content-type", "").lower()
                    if "text/event-stream" not in content_type:
                        fallback_payload = response.json()
                        fallback_text = self._parse_chat_completion(fallback_payload)
                        if fallback_text:
                            yield fallback_text
                        return

                    first_chunk_logged = False
                    for data_line in self._iter_sse_data_lines(response):
                        if data_line == "[DONE]":
                            break

                        chunk = self._parse_stream_chunk(data_line)
                        if chunk:
                            if not first_chunk_logged:
                                first_chunk_elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                                logger.info(
                                    "本地模型流式首个 chunk 到达：task=%s elapsed_ms=%s chunk_length=%s",
                                    task_type,
                                    first_chunk_elapsed_ms,
                                    len(chunk),
                                )
                                first_chunk_logged = True
                            yield chunk

            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info("本地模型流式响应成功：task=%s elapsed_ms=%s", task_type, elapsed_ms)
        except httpx.ConnectError as exc:
            logger.exception("连接本地 llama-server 失败：endpoint=%s", endpoint)
            raise LLMClientError("无法连接本地 llama-server，请确认服务已启动且地址可访问。") from exc
        except httpx.TimeoutException as exc:
            logger.exception("本地模型流式请求超时：task=%s endpoint=%s", task_type, endpoint)
            raise LLMClientError("本地模型流式请求超时（60秒），请检查模型负载或机器性能。") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300] if exc.response is not None else ""
            logger.exception("本地模型流式接口返回 HTTP 错误：task=%s detail=%s", task_type, detail)
            raise LLMClientError(
                f"本地接口返回错误：HTTP {exc.response.status_code} {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.exception("本地模型流式请求失败：task=%s endpoint=%s", task_type, endpoint)
            raise LLMClientError(f"本地流式请求失败：{exc}") from exc
        except Exception as exc:
            logger.exception("本地模型流式响应解析失败：task=%s", task_type)
            raise LLMClientError(f"本地流式响应解析失败：{exc}") from exc

    async def _call_local_async(
        self,
        messages: list[dict[str, str]],
        config: dict[str, Any],
        task_type: str,
    ) -> str:
        """异步调用本地 llama-server(OpenAI 兼容接口)。"""
        base_url = str(config.get("local_url", "http://127.0.0.1:8080/")).rstrip("/")
        model = str(config.get("local_model", "")).strip()
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
        started_at = time.perf_counter()

        logger.info("发起异步本地模型请求：task=%s model=%s endpoint=%s", task_type, model, endpoint)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(endpoint, json=payload)
                response.raise_for_status()
                data = response.json()

            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info("异步本地模型响应成功：task=%s elapsed_ms=%s", task_type, elapsed_ms)
            return self._parse_chat_completion(data)
        except httpx.ConnectError as exc:
            logger.exception("连接异步本地 llama-server 失败：endpoint=%s", endpoint)
            raise LLMClientError("无法连接本地 llama-server，请确认服务已启动且地址可访问。") from exc
        except httpx.TimeoutException as exc:
            logger.exception("异步本地模型请求超时：task=%s endpoint=%s", task_type, endpoint)
            raise LLMClientError("本地模型请求超时（60秒），请检查模型负载或机器性能。") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300] if exc.response is not None else ""
            logger.exception("异步本地模型返回 HTTP 错误：task=%s detail=%s", task_type, detail)
            raise LLMClientError(
                f"本地接口返回错误：HTTP {exc.response.status_code} {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            logger.exception("异步本地模型请求失败：task=%s endpoint=%s", task_type, endpoint)
            raise LLMClientError(f"本地请求失败：{exc}") from exc
        except Exception as exc:
            logger.exception("异步本地模型响应解析失败：task=%s", task_type)
            raise LLMClientError(f"本地响应解析失败：{exc}") from exc

    @staticmethod
    def _iter_sse_data_lines(response: httpx.Response):
        """从 SSE 响应中提取 data 行。"""
        for raw_line in response.iter_lines():
            if raw_line is None:
                continue

            if isinstance(raw_line, bytes):
                raw_line = raw_line.decode("utf-8", errors="ignore")

            line = raw_line.strip()
            if not line or not line.startswith("data:"):
                continue

            yield line[5:].strip()

    @staticmethod
    def _parse_chat_completion(payload: dict[str, Any]) -> str:
        """解析 OpenAI 兼容 chat/completions 输出。"""
        choices = payload.get("choices", []) if isinstance(payload, dict) else []
        if not choices:
            raise LLMClientError("模型返回结果为空。")

        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = LLMClient._extract_text_content(message.get("content", ""))
        content = content.strip()
        if not content:
            raise LLMClientError("模型返回内容为空。")
        return content

    @staticmethod
    def _parse_stream_chunk(payload_line: str) -> str:
        """解析 OpenAI 兼容流式分片中的文本内容。"""
        try:
            payload = json.loads(payload_line)
        except json.JSONDecodeError as exc:
            raise LLMClientError(f"无法解析流式响应片段：{payload_line[:120]}") from exc

        choices = payload.get("choices", []) if isinstance(payload, dict) else []
        if not choices or not isinstance(choices[0], dict):
            return ""

        delta = choices[0].get("delta", {})
        if isinstance(delta, dict):
            delta_content = LLMClient._extract_text_content(delta.get("content", ""))
            if delta_content:
                return delta_content

        message = choices[0].get("message", {})
        if isinstance(message, dict):
            message_content = LLMClient._extract_text_content(message.get("content", ""))
            if message_content:
                return message_content

        return LLMClient._extract_text_content(choices[0].get("text", ""))

    @staticmethod
    def _extract_text_content(value: Any) -> str:
        """兼容字符串和结构化内容数组的文本抽取。"""
        if isinstance(value, str):
            return value

        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                    continue

                if not isinstance(item, dict):
                    continue

                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                    continue

                nested_text = item.get("content")
                if isinstance(nested_text, str):
                    parts.append(nested_text)

            return "".join(parts)

        return ""

    def _validate_translation_result(self, source_text: str, result_text: str) -> None:
        """对翻译结果做基础可信度校验。"""
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
            logger.warning("翻译结果校验失败：返回了通用对话内容。")
            raise LLMClientError(
                "当前本地模型没有正确执行翻译，而是返回了通用对话内容。建议改用更大的模型。"
            )

        if result == source:
            logger.warning("翻译结果校验失败：返回内容与原文相同。")
            raise LLMClientError("当前本地模型没有完成翻译，返回了与原文相同的内容。")

        source_has_cjk = self._contains_cjk(source)
        source_has_latin = self._contains_latin(source)
        result_has_cjk = self._contains_cjk(result)
        result_has_latin = self._contains_latin(result)

        if source_has_cjk and not source_has_latin and result_has_cjk and not result_has_latin:
            logger.warning("翻译结果校验失败：中文未译为英文。")
            raise LLMClientError(
                "当前本地模型未将中文正确翻译成英文。建议使用至少 3B 以上模型以获得稳定翻译效果。"
            )

        if source_has_latin and not source_has_cjk and result_has_latin and not result_has_cjk:
            logger.warning("翻译结果校验失败：英文未译为中文。")
            raise LLMClientError(
                "当前本地模型未将英文正确翻译成中文。建议使用至少 3B 以上模型以获得稳定翻译效果。"
            )
