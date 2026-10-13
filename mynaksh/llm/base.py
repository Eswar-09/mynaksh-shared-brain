"""Provider-independent LLM interface."""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Provider unavailable, timed out, or returned unusable output."""


def normalize_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop leading assistant turns and merge consecutive same-role turns.

    Several providers reject histories that do not start with a user turn or that
    do not alternate roles.
    """
    out: list[dict[str, str]] = []
    for m in messages:
        if not out and m["role"] != "user":
            continue
        if out and out[-1]["role"] == m["role"]:
            out[-1] = {"role": m["role"], "content": out[-1]["content"] + "\n\n" + m["content"]}
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def parse_json_object(text: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("model did not return a JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON from model: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("model JSON is not an object")
    return data


class LLMClient(ABC):
    name: str = "base"

    @abstractmethod
    def generate(
        self, system: str, messages: list[dict[str, str]], *, max_tokens: int = 600
    ) -> str:
        """Return the assistant text. Raise LLMError on any provider failure."""

    def generate_json(self, system: str, user: str, *, max_tokens: int = 800) -> dict[str, Any]:
        text = self.generate(system, [{"role": "user", "content": user}], max_tokens=max_tokens)
        return parse_json_object(text)


class FallbackLLM(LLMClient):
    """Try the primary provider, fall back to the secondary on LLMError."""

    def __init__(self, primary: LLMClient, secondary: LLMClient) -> None:
        self.primary, self.secondary = primary, secondary
        self.name = f"{primary.name}->{secondary.name}"

    def generate(self, system, messages, *, max_tokens=600):
        try:
            return self.primary.generate(system, messages, max_tokens=max_tokens)
        except LLMError as exc:
            log.warning("primary LLM %s failed (%s); using %s", self.primary.name, exc, self.secondary.name)
            return self.secondary.generate(system, messages, max_tokens=max_tokens)
