from __future__ import annotations

import anthropic

from mynaksh.llm.base import LLMClient, LLMError, normalize_messages


class AnthropicLLM(LLMClient):
    def __init__(self, api_key: str, model: str, timeout: float = 30.0) -> None:
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.model = model
        self.name = f"anthropic:{model}"

    def generate(self, system, messages, *, max_tokens=600):
        try:
            resp = self._client.messages.create(
                model=self.model,
                system=system,
                messages=normalize_messages(messages),
                max_tokens=max_tokens,
            )
        except anthropic.APIError as exc:
            raise LLMError(f"anthropic: {exc}") from exc
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if not text:
            raise LLMError("anthropic returned an empty response")
        return text
