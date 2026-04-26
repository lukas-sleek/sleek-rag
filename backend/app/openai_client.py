from openai import OpenAI

from app.config import settings

_client = None


def openai_client():
    global _client
    if _client is None:
        raw = OpenAI(api_key=settings.openai_api_key)
        if settings.langsmith_api_key:
            from langsmith.wrappers import wrap_openai

            _client = wrap_openai(raw)
        else:
            _client = raw
    return _client
