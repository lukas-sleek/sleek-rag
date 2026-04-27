from openai import OpenAI

from app.config import settings

_client = None


def gemini_client():
    """OpenAI-shaped client pointed at Gemini's v1beta/openai endpoint.

    Same SDK surface as openai_client(); swap providers later by changing
    base_url + api_key + model in settings.
    """
    global _client
    if _client is None:
        raw = OpenAI(
            api_key=settings.gemini_api_key,
            base_url=settings.gemini_base_url,
        )
        if settings.langsmith_api_key:
            from langsmith.wrappers import wrap_openai

            _client = wrap_openai(raw)
        else:
            _client = raw
    return _client
