"""Any OpenAI-compatible chat-completions endpoint: OpenAI, Gemini, Mistral, Ollama, vLLM."""
from __future__ import annotations

import httpx

from mynaksh.llm.base import LLMClient, LLMError, normalize_messages


class OpenAICompatLLM(LLMClient):
    def __init__(self, api_key: str, model: str, base_url: str, timeout: float = 30.0) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        self.model = model
        self.name = f"openai-compat:{model}"

    def generate(self, system, messages, *, max_tokens=600):
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, *normalize_messages(messages)],
        }
        try:
            r = self._http.post("/chat/completions", json=body)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"] or ""
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"{self.name}: {exc}") from exc
        if not text.strip():
            raise LLMError(f"{self.name} returned an empty response")
        return text.strip()
