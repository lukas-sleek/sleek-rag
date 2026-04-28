from openai import OpenAI

from app.config import settings

_client = None
_raw_client = None


def _build_raw() -> OpenAI:
    return OpenAI(
        api_key=settings.gemini_api_key,
        base_url=settings.gemini_base_url,
    )


def gemini_client():
    """OpenAI-shaped client pointed at Gemini's v1beta/openai endpoint.

    Provider is swappable via env vars (GEMINI_BASE_URL + GEMINI_API_KEY +
    GEMINI_CHAT_MODEL) — any OpenAI-compatible endpoint works (Ollama, vLLM,
    OpenRouter, etc.).
    """
    global _client
    if _client is None:
        raw = _build_raw()
        if settings.langsmith_api_key:
            from langsmith.wrappers import wrap_openai

            _client = wrap_openai(raw)
        else:
            _client = raw
    return _client


def gemini_client_untraced():
    """Same provider as `gemini_client()` but never wrapped in LangSmith
    tracing. Use for low-value background calls (e.g. chat auto-title) where
    trace noise outweighs the signal."""
    global _raw_client
    if _raw_client is None:
        _raw_client = _build_raw()
    return _raw_client
