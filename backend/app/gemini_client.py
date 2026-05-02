from openai import OpenAI

from app.config import settings

_client = None
_raw_client = None


def _build_raw() -> OpenAI:
    # Bumped from the SDK default of 2 retries to 5 because Google's
    # AI Studio compat shim (`generativelanguage.googleapis.com`) returns
    # 503 UNAVAILABLE under capacity spikes far more often than e.g.
    # native Vertex. The SDK retries on >=500 / 429 / 408 / 409 and on
    # connection errors with exponential backoff + jitter; 5 attempts
    # absorbs the multi-second 503 bursts we see during incidents.
    # The 18.3 migration replaces this client entirely with native
    # Vertex; until then this knob is the cheapest reliability win.
    return OpenAI(
        api_key=settings.gemini_api_key,
        base_url=settings.gemini_base_url,
        max_retries=5,
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
