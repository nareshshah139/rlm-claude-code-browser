"""
Multi-provider LLM client for RLM.

Implements: Spec §5 Model Integration

Supports:
- Anthropic (Claude Opus, Sonnet, Haiku)
- OpenAI (GPT-5.2, GPT-5.2-Codex, GPT-4o)
"""

from __future__ import annotations

import inspect
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import anthropic
import openai
from dotenv import load_dotenv

from .cost_tracker import CostComponent, CostTracker, get_cost_tracker

# Auto-load .env from project root
_project_root = Path(__file__).parent.parent
_env_file = _project_root / ".env"
if _env_file.exists():
    load_dotenv(_env_file)


class Provider(Enum):
    """LLM provider."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


@dataclass
class APIResponse:
    """Response from LLM API."""

    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: Provider
    stop_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamChunk:
    """A chunk from streaming response."""

    text: str
    is_final: bool = False
    input_tokens: int = 0
    output_tokens: int = 0


# Model registry with provider info
MODEL_REGISTRY: dict[str, tuple[Provider, str]] = {
    # Anthropic models
    "opus": (Provider.ANTHROPIC, "claude-opus-4-5-20251101"),
    "sonnet": (Provider.ANTHROPIC, "claude-sonnet-4-20250514"),
    "haiku": (Provider.ANTHROPIC, "claude-haiku-4-5-20251001"),
    "claude-opus-4-5-20251101": (Provider.ANTHROPIC, "claude-opus-4-5-20251101"),
    "claude-sonnet-4-20250514": (Provider.ANTHROPIC, "claude-sonnet-4-20250514"),
    "claude-haiku-4-5-20251001": (Provider.ANTHROPIC, "claude-haiku-4-5-20251001"),
    # OpenAI GPT-5.2 models
    "gpt-5.2": (Provider.OPENAI, "gpt-5.2"),
    "gpt-5.2-pro": (Provider.OPENAI, "gpt-5.2-pro"),
    "gpt-5.2-chat-latest": (Provider.OPENAI, "gpt-5.2-chat-latest"),
    # GPT-5.2-Codex: API access coming soon, use gpt-5.2 as fallback
    "gpt-5.2-codex": (Provider.OPENAI, "gpt-5.2"),  # Fallback until API available
    # OpenAI GPT-4 models
    "gpt-4o": (Provider.OPENAI, "gpt-4o"),
    "gpt-4o-mini": (Provider.OPENAI, "gpt-4o-mini"),
    # OpenAI reasoning models
    "o1": (Provider.OPENAI, "o1"),
    "o1-mini": (Provider.OPENAI, "o1-mini"),
    "o3-mini": (Provider.OPENAI, "o3-mini"),
    # Shortcuts
    "codex": (Provider.OPENAI, "gpt-5.2"),  # Fallback until Codex API available
}


def resolve_model(model: str) -> tuple[Provider, str]:
    """Resolve model shorthand to (provider, full_model_id)."""
    if model in MODEL_REGISTRY:
        return MODEL_REGISTRY[model]
    # Guess provider from model name
    if model.startswith("claude"):
        return (Provider.ANTHROPIC, model)
    if model.startswith(("gpt-", "o1", "o3")):
        return (Provider.OPENAI, model)
    # Default to Anthropic
    return (Provider.ANTHROPIC, model)


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        component: CostComponent = CostComponent.ROOT_PROMPT,
    ) -> APIResponse:
        """Get completion from LLM."""
        pass

    @abstractmethod
    def complete_streaming(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        component: CostComponent = CostComponent.ROOT_PROMPT,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Get streaming completion from LLM."""
        ...


class AnthropicClient(BaseLLMClient):
    """Anthropic Claude API client."""

    def __init__(
        self,
        api_key: str | None = None,
        cost_tracker: CostTracker | None = None,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key required. Set ANTHROPIC_API_KEY environment variable."
            )
        self.client = anthropic.AsyncAnthropic(api_key=self.api_key)
        self.cost_tracker = cost_tracker or get_cost_tracker()

    async def complete(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        component: CostComponent = CostComponent.ROOT_PROMPT,
    ) -> APIResponse:
        model = model or "claude-opus-4-5-20251101"

        request_params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        if system:
            request_params["system"] = system

        if "temperature" in inspect.signature(self.client.messages.create).parameters:
            request_params["temperature"] = temperature
        elif temperature != 0.0:
            raise ValueError("This Anthropic SDK no longer accepts temperature")
        response = await self.client.messages.create(**request_params)

        content = ""
        for block in response.content:
            if block.type == "text":
                content += block.text

        self.cost_tracker.record_usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
            component=component,
        )

        return APIResponse(
            content=content,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
            provider=Provider.ANTHROPIC,
            stop_reason=response.stop_reason,
        )

    async def complete_streaming(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        component: CostComponent = CostComponent.ROOT_PROMPT,
    ) -> AsyncGenerator[StreamChunk, None]:
        model = model or "claude-opus-4-5-20251101"

        request_params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        if system:
            request_params["system"] = system

        input_tokens = 0
        output_tokens = 0

        if "temperature" in inspect.signature(self.client.messages.stream).parameters:
            request_params["temperature"] = temperature
        elif temperature != 0.0:
            raise ValueError("This Anthropic SDK no longer accepts temperature")
        async with self.client.messages.stream(**request_params) as stream:
            async for text in stream.text_stream:
                yield StreamChunk(text=text)

            final_message = await stream.get_final_message()
            input_tokens = final_message.usage.input_tokens
            output_tokens = final_message.usage.output_tokens

        self.cost_tracker.record_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            component=component,
        )

        yield StreamChunk(
            text="",
            is_final=True,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


class OpenAIClient(BaseLLMClient):
    """OpenAI GPT API client."""

    def __init__(
        self,
        api_key: str | None = None,
        cost_tracker: CostTracker | None = None,
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key required. Set OPENAI_API_KEY environment variable.")
        self.client = openai.AsyncOpenAI(api_key=self.api_key)
        self.cost_tracker = cost_tracker or get_cost_tracker()

    async def complete(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        component: CostComponent = CostComponent.ROOT_PROMPT,
    ) -> APIResponse:
        model = model or "gpt-5.2-codex"

        # OpenAI uses system message in messages array
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        # GPT-5.2+ uses max_completion_tokens instead of max_tokens
        if model.startswith("gpt-5") or model.startswith("o1") or model.startswith("o3"):
            response = await self.client.chat.completions.create(
                model=model,
                messages=full_messages,
                max_completion_tokens=max_tokens,
                temperature=temperature,
            )
        else:
            response = await self.client.chat.completions.create(
                model=model,
                messages=full_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        content = response.choices[0].message.content or ""
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

        self.cost_tracker.record_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            component=component,
        )

        return APIResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            provider=Provider.OPENAI,
            stop_reason=response.choices[0].finish_reason,
        )

    async def complete_streaming(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        component: CostComponent = CostComponent.ROOT_PROMPT,
    ) -> AsyncGenerator[StreamChunk, None]:
        model = model or "gpt-5.2-codex"

        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        # GPT-5.2+ uses max_completion_tokens instead of max_tokens
        if model.startswith("gpt-5") or model.startswith("o1") or model.startswith("o3"):
            stream = await self.client.chat.completions.create(
                model=model,
                messages=full_messages,
                max_completion_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                stream_options={"include_usage": True},
            )
        else:
            stream = await self.client.chat.completions.create(
                model=model,
                messages=full_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                stream_options={"include_usage": True},
            )

        input_tokens = 0
        output_tokens = 0

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield StreamChunk(text=chunk.choices[0].delta.content)
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens
                output_tokens = chunk.usage.completion_tokens

        self.cost_tracker.record_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            component=component,
        )

        yield StreamChunk(
            text="",
            is_final=True,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


class MultiProviderClient:
    """
    Unified LLM client that routes to appropriate provider.

    Implements: Spec §5.1 API Integration
    """

    def __init__(
        self,
        default_model: str = "opus",
        anthropic_key: str | None = None,
        openai_key: str | None = None,
        cost_tracker: CostTracker | None = None,
    ):
        """
        Initialize multi-provider client.

        Args:
            default_model: Default model (defaults to Opus)
            anthropic_key: Anthropic API key
            openai_key: OpenAI API key
            cost_tracker: Cost tracker instance
        """
        self.cost_tracker = cost_tracker or get_cost_tracker()
        self.default_model = default_model
        self._clients: dict[Provider, BaseLLMClient] = {}

        # Initialize available clients
        try:
            self._clients[Provider.ANTHROPIC] = AnthropicClient(
                api_key=anthropic_key, cost_tracker=self.cost_tracker
            )
        except ValueError:
            pass  # No Anthropic key available

        try:
            self._clients[Provider.OPENAI] = OpenAIClient(
                api_key=openai_key, cost_tracker=self.cost_tracker
            )
        except ValueError:
            pass  # No OpenAI key available

        if not self._clients:
            raise ValueError(
                "At least one API key required. Set ANTHROPIC_API_KEY or OPENAI_API_KEY."
            )

    def _get_client(self, model: str) -> tuple[BaseLLMClient, str]:
        """Get appropriate client for model."""
        provider, full_model = resolve_model(model)

        if provider not in self._clients:
            available = list(self._clients.keys())
            raise ValueError(
                f"No client for {provider.value}. Available: {[p.value for p in available]}"
            )

        return self._clients[provider], full_model

    async def complete(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        component: CostComponent = CostComponent.ROOT_PROMPT,
    ) -> APIResponse:
        """Get completion, routing to appropriate provider."""
        model = model or self.default_model
        client, full_model = self._get_client(model)

        return await client.complete(
            messages=messages,
            system=system,
            model=full_model,
            max_tokens=max_tokens,
            temperature=temperature,
            component=component,
        )

    async def complete_streaming(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        component: CostComponent = CostComponent.ROOT_PROMPT,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Get streaming completion, routing to appropriate provider."""
        model = model or self.default_model
        client, full_model = self._get_client(model)

        async for chunk in client.complete_streaming(
            messages=messages,
            system=system,
            model=full_model,
            max_tokens=max_tokens,
            temperature=temperature,
            component=component,
        ):
            yield chunk

    async def recursive_query(
        self,
        query: str,
        context: str,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> str:
        """
        Make a recursive sub-query.

        Implements: Spec §3.3 Recursive Call Protocol
        """
        from .prompts import build_recursive_prompt

        # Use faster model for recursive calls
        model = model or "haiku"

        messages = [{"role": "user", "content": build_recursive_prompt(query, context)}]

        response = await self.complete(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            component=CostComponent.RECURSIVE_CALL,
        )

        return response.content

    async def summarize(
        self,
        content: str,
        max_tokens: int = 500,
        model: str | None = None,
    ) -> str:
        """Summarize content."""
        from .prompts import build_summarization_prompt

        model = model or "haiku"

        messages = [{"role": "user", "content": build_summarization_prompt(content, max_tokens)}]

        response = await self.complete(
            messages=messages,
            model=model,
            max_tokens=max_tokens + 100,
            component=CostComponent.SUMMARIZATION,
        )

        return response.content


# Backwards compatibility alias
ClaudeClient = MultiProviderClient

# Global client instance
_client: MultiProviderClient | None = None


def get_client() -> MultiProviderClient:
    """Get global LLM client."""
    global _client
    if _client is None:
        _client = MultiProviderClient()
    return _client


def init_client(
    api_key: str | None = None,
    default_model: str = "opus",
    **kwargs: Any,
) -> MultiProviderClient:
    """Initialize global client with options."""
    global _client
    _client = MultiProviderClient(
        default_model=default_model,
        anthropic_key=api_key,
        **kwargs,
    )
    return _client


__all__ = [
    "APIResponse",
    "AnthropicClient",
    "BaseLLMClient",
    "ClaudeClient",
    "MODEL_REGISTRY",
    "MultiProviderClient",
    "OpenAIClient",
    "Provider",
    "StreamChunk",
    "get_client",
    "init_client",
    "resolve_model",
]
