from __future__ import annotations

from app.ai.base import AIProvider
from app.config import Settings


def get_ai_provider(settings: Settings) -> AIProvider | None:
    """Returns None when AI_PROVIDER=none - all AI-assisted steps degrade to
    their deterministic fallback in that case."""
    if settings.AI_PROVIDER == "openai":
        from app.ai.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.OPENAI_API_KEY, model=settings.OPENAI_MODEL,
            resume_timeout=settings.RESUME_LLM_TIMEOUT_SECONDS,
        )
    if settings.AI_PROVIDER == "ollama":
        from app.ai.ollama_provider import OllamaProvider

        return OllamaProvider(
            base_url=settings.OLLAMA_BASE_URL, model=settings.OLLAMA_MODEL,
            resume_timeout=settings.RESUME_LLM_TIMEOUT_SECONDS,
        )
    return None
