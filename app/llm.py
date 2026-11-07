"""LLM connection. Built lazily, so importing the app never needs an API key."""
from functools import lru_cache

from langchain_groq import ChatGroq

from app.config import get_settings


class LLMNotConfiguredError(RuntimeError):
    """Raised when a node needs the model but GROQ_API_KEY is missing."""


@lru_cache(maxsize=1)
def get_llm() -> ChatGroq:
    settings = get_settings()
    if not settings.groq_api_key:
        raise LLMNotConfiguredError(
            "GROQ_API_KEY is not set. Copy .env.example to .env and add your Groq key."
        )
    return ChatGroq(
        api_key=settings.groq_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
    )


def check_llm() -> str:
    """Round-trip a tiny prompt. Raises on bad key, bad model name or network failure."""
    return get_llm().invoke("Reply with exactly one word: pong").content
