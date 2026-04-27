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


# --- LangSmith-traced wrappers for OpenAI ops that wrap_openai doesn't patch ---
#
# wrap_openai only instruments chat.completions / completions / responses.
# Vector stores, files, and conversations need explicit tracing so they appear
# as child runs inside the surrounding @traceable request handler.

if settings.langsmith_api_key:
    from langsmith import traceable as _traceable
else:
    def _traceable(*_args, **_kwargs):  # type: ignore[no-redef]
        def deco(fn):
            return fn

        return deco


@_traceable(run_type="tool", name="openai.vector_stores.create")
def vs_create(name: str) -> str:
    vs = openai_client().vector_stores.create(name=name)
    return vs.id


@_traceable(run_type="tool", name="openai.vector_stores.delete")
def vs_delete(vector_store_id: str) -> None:
    openai_client().vector_stores.delete(vector_store_id)


@_traceable(run_type="tool", name="openai.files.create")
def files_create(filename: str, contents: bytes) -> dict:
    f = openai_client().files.create(file=(filename, contents), purpose="user_data")
    return {"id": f.id, "bytes": len(contents), "filename": filename}


@_traceable(run_type="retriever", name="openai.vector_stores.ingest_file")
def vs_ingest_file(vector_store_id: str, file_id: str) -> dict:
    poll = openai_client().vector_stores.files.create_and_poll(
        vector_store_id=vector_store_id,
        file_id=file_id,
    )
    return {
        "vector_store_id": vector_store_id,
        "file_id": file_id,
        "status": poll.status,
        "last_error": getattr(poll, "last_error", None),
    }


@_traceable(run_type="tool", name="openai.vector_stores.delete_file")
def vs_delete_file(vector_store_id: str, file_id: str) -> None:
    openai_client().vector_stores.files.delete(
        vector_store_id=vector_store_id, file_id=file_id
    )


@_traceable(run_type="tool", name="openai.files.delete")
def files_delete(file_id: str) -> None:
    openai_client().files.delete(file_id)


@_traceable(run_type="tool", name="openai.conversations.create")
def conversation_create() -> str:
    return openai_client().conversations.create().id
