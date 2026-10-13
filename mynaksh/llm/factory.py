from __future__ import annotations

from mynaksh.config import Settings
from mynaksh.llm.base import FallbackLLM, LLMClient
from mynaksh.llm.fake import FakeLLM

DEFAULT_MODELS = {
    "anthropic": ("claude-sonnet-5-5", "claude-haiku-5-5"),
    "openai": ("gpt-4o-mini", "gpt-4o-mini"),
}


def _make(provider: str, model: str, s: Settings) -> LLMClient:
    if provider == "fake":
        return FakeLLM()
    if provider == "anthropic":
        if not s.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for LLM_PROVIDER=anthropic")
        from mynaksh.llm.anthropic_llm import AnthropicLLM
        return AnthropicLLM(s.anthropic_api_key, model)
    if provider == "openai":
        if not s.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for LLM_PROVIDER=openai")
        from mynaksh.llm.openai_compat import OpenAICompatLLM
        return OpenAICompatLLM(s.openai_api_key, model, s.openai_base_url)
    raise ValueError(f"unknown LLM provider {provider!r}")


def build_llms(s: Settings) -> tuple[LLMClient, LLMClient]:
    """Return (chat_llm, extraction_llm). Extraction can use a cheaper model."""
    chat_default, extract_default = DEFAULT_MODELS.get(s.llm_provider, ("", ""))
    chat = _make(s.llm_provider, s.llm_model or chat_default, s)
    extract = _make(s.llm_provider, s.extraction_model or extract_default, s)
    if s.fallback_provider:
        fb_chat, fb_extract = DEFAULT_MODELS.get(s.fallback_provider, ("", ""))
        chat = FallbackLLM(chat, _make(s.fallback_provider, fb_chat, s))
        extract = FallbackLLM(extract, _make(s.fallback_provider, fb_extract, s))
    return chat, extract
