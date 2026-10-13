from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    brain_backend: str = field(default_factory=lambda: _env("BRAIN_BACKEND", "memory"))
    neo4j_uri: str = field(default_factory=lambda: _env("NEO4J_URI", "bolt://localhost:7687"))
    neo4j_user: str = field(default_factory=lambda: _env("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: _env("NEO4J_PASSWORD", "mynaksh-dev"))
    neo4j_database: str | None = field(default_factory=lambda: _env("NEO4J_DATABASE") or None)

    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "fake"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL"))
    extraction_model: str = field(default_factory=lambda: _env("EXTRACTION_MODEL"))
    fallback_provider: str = field(default_factory=lambda: _env("LLM_FALLBACK_PROVIDER"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.openai.com/v1"))

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls()
