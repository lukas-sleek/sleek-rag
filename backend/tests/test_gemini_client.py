def test_gemini_client_chat_smoke():
    from app.gemini_client import gemini_client
    from app.config import settings

    # Gemini 2.5 Flash spends "thinking tokens" before producing output.
    # Disable thinking via the OpenAI-compat extra_body so the response is deterministic
    # under a small max_tokens cap.
    resp = gemini_client().chat.completions.create(
        model=settings.gemini_chat_model,
        messages=[{"role": "user", "content": "Say 'pong' and nothing else."}],
        max_tokens=10,
        extra_body={"reasoning_effort": "none"},
    )
    assert "pong" in resp.choices[0].message.content.lower()


def test_gemini_client_embeddings_smoke():
    from app.gemini_client import gemini_client
    from app.config import settings

    # gemini-embedding-001 defaults to 3072 dims; pin via the OpenAI-compat
    # `dimensions` param to match settings.gemini_embedding_dim (768).
    resp = gemini_client().embeddings.create(
        model=settings.gemini_embedding_model,
        input="hello world",
        dimensions=settings.gemini_embedding_dim,
    )
    assert len(resp.data[0].embedding) == settings.gemini_embedding_dim
