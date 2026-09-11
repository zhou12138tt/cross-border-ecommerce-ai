"""DeepSeek LLM 客户端封装。

DeepSeek 接口兼容 OpenAI Chat Completions 格式：
    base_url: https://api.deepseek.com/v1
    model:    deepseek-chat
    auth:     Bearer <DEEPSEEK_API_KEY>

设计要点：
    - chat()：直接对话，返回纯文本回复
    - chat_json()：约束 LLM 输出 JSON，返回 dict（用于意图+回复双字段）
    - 失败时抛 LLMError；上层负责降级到关键词规则
"""
from __future__ import annotations

import json
from typing import Any, Generator, Optional

from openai import OpenAI

from app.core.config import settings
from app.core.logging import logger


class LLMError(RuntimeError):
    """LLM 调用失败。"""


_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    """懒加载单例 client。"""
    global _client
    if _client is None:
        if not settings.llm_ready:
            raise LLMError(f"LLM provider={settings.LLM_PROVIDER} 未配置 key")
        if settings.LLM_PROVIDER == "deepseek":
            _client = OpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
            )
        elif settings.LLM_PROVIDER == "openai":
            _client = OpenAI(api_key=settings.OPENAI_API_KEY)
        else:
            raise LLMError(f"不支持的 provider: {settings.LLM_PROVIDER}")
    return _client


def _model() -> str:
    if settings.LLM_PROVIDER == "deepseek":
        return settings.DEEPSEEK_MODEL
    return settings.DEEPSEEK_MODEL  # 其他 provider 默认也走 deepseek-chat 配置


def chat(
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 512,
) -> str:
    """直接对话，返回纯文本。messages 为 OpenAI 标准格式。"""
    client = _get_client()
    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"LLM chat 调用失败: {e}")
        raise LLMError(str(e)) from e


def chat_json(
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 512,
) -> dict[str, Any]:
    """约束输出 JSON。提示词中已要求 JSON；这里做容错解析。

    DeepSeek 支持 response_format={"type": "json_object"}，
    但为了兼容性，额外做了正则兜底提取。
    """
    client = _get_client()
    try:
        kwargs: dict[str, Any] = dict(
            model=_model(),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # DeepSeek 支持 json_object 模式
        if settings.LLM_PROVIDER == "deepseek":
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or "{}"
        return _parse_json(content)
    except Exception as e:
        logger.warning(f"LLM chat_json 调用失败: {e}")
        raise LLMError(str(e)) from e


def _parse_json(content: str) -> dict[str, Any]:
    """容错 JSON 解析：直接解析失败时尝试从 ```json ...``` 中提取。"""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # 尝试从 markdown 代码块中提取
    if "```json" in content:
        start = content.index("```json") + 7
        end = content.rfind("```")
        if end > start:
            try:
                return json.loads(content[start:end].strip())
            except json.JSONDecodeError:
                pass
    if "```" in content:
        parts = content.split("```")
        for p in parts:
            try:
                return json.loads(p.strip())
            except json.JSONDecodeError:
                continue
    raise LLMError(f"无法解析 LLM 输出为 JSON: {content[:200]}")


def chat_stream(
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 512,
) -> Generator[str, None, None]:
    """流式对话，逐 token yield 文本片段。

    用于 SSE 端点：前端边收边显示。
    """
    client = _get_client()
    try:
        kwargs: dict[str, Any] = dict(
            model=_model(),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        stream = client.chat.completions.create(**kwargs)
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
    except Exception as e:
        logger.warning(f"LLM chat_stream 调用失败: {e}")
        raise LLMError(str(e)) from e
