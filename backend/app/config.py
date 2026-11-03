"""Settings, read from the environment once when the app starts."""

import os
from dataclasses import dataclass

API_STYLES = ("responses", "chat_completions")


@dataclass(frozen=True)
class Settings:
    database_url: str
    openai_base_url: str
    openai_api_key: str
    model: str
    api_style: str
    instructions: str
    max_output_tokens: int
    temperature: float | None
    history_messages: int
    upstream_timeout_s: float

    @classmethod
    def from_env(cls) -> "Settings":
        api_style = os.environ.get("API_STYLE", "responses")
        if api_style not in API_STYLES:
            raise ValueError(f"API_STYLE must be one of {API_STYLES}, got {api_style!r}")
        temperature = os.environ.get("TEMPERATURE")
        return cls(
            database_url=os.environ.get("DATABASE_URL", "postgresql://chat:chat@127.0.0.1:5432/chat"),
            # Ollama serves its OpenAI-compatible API under /v1 on port 11434.
            openai_base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1").rstrip("/"),
            # Ollama ignores the key, but OpenAI-compatible servers expect the header.
            openai_api_key=os.environ.get("OPENAI_API_KEY", "ollama"),
            model=os.environ.get("MODEL", "llama3.2:3b"),
            api_style=api_style,
            instructions=os.environ.get("INSTRUCTIONS", "You are a helpful assistant. Keep answers short."),
            max_output_tokens=int(os.environ.get("MAX_OUTPUT_TOKENS", "512")),
            temperature=float(temperature) if temperature else None,
            history_messages=int(os.environ.get("HISTORY_MESSAGES", "20")),
            upstream_timeout_s=float(os.environ.get("UPSTREAM_TIMEOUT_S", "60")),
        )
