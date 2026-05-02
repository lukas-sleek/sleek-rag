"""Speech-to-text endpoint backed by Google Cloud Speech-to-Text v2.

Frontend records via MediaRecorder (WebM/Opus by default) and POSTs the blob
here. We hand it off to STT v2 with auto_decoding_config so we don't have to
care about the exact codec; the chirp_2 model handles short utterances in many
languages — we pin to de-DE per master spec.

Sync recognize() caps at ~60 seconds of audio. For voice composer input that's
plenty; longer audio would need batchRecognize, which we don't expose.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from google.api_core.client_options import ClientOptions
from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech
from google.oauth2 import service_account

from app.auth import current_user_id
from app.config import settings

router = APIRouter(prefix="/api/transcribe", tags=["transcribe"])

# Sync recognize accepts up to ~10 MB / 60 sec. Cap on our side too so a stuck
# MediaRecorder can't ship a giant blob.
_MAX_AUDIO_BYTES = 10 * 1024 * 1024

_client: SpeechClient | None = None


def _stt_client() -> SpeechClient:
    global _client
    if _client is None:
        creds = None
        if settings.gcp_service_account_json_path:
            creds = service_account.Credentials.from_service_account_file(
                settings.gcp_service_account_json_path
            )
        # STT v2 uses regional endpoints. "global" hits speech.googleapis.com,
        # any other location wants {location}-speech.googleapis.com.
        location = settings.gcp_stt_location
        api_endpoint = (
            "speech.googleapis.com"
            if location == "global"
            else f"{location}-speech.googleapis.com"
        )
        _client = SpeechClient(
            credentials=creds,
            client_options=ClientOptions(api_endpoint=api_endpoint),
        )
    return _client


@router.post("")
async def transcribe(
    audio: UploadFile = File(...),
    _: str = Depends(current_user_id),
):
    data = await audio.read()
    if not data:
        raise HTTPException(400, "empty audio payload")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(413, f"audio too large (>{_MAX_AUDIO_BYTES} bytes)")
    if not settings.gcp_project_id:
        raise HTTPException(500, "gcp_project_id not configured")

    config = cloud_speech.RecognitionConfig(
        auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
        language_codes=[settings.gcp_stt_language],
        model=settings.gcp_stt_model,
    )
    request = cloud_speech.RecognizeRequest(
        recognizer=(
            f"projects/{settings.gcp_project_id}"
            f"/locations/{settings.gcp_stt_location}/recognizers/_"
        ),
        config=config,
        content=data,
    )
    try:
        response = _stt_client().recognize(request=request)
    except Exception as exc:  # google.api_core.exceptions.GoogleAPICallError etc.
        raise HTTPException(502, f"speech-to-text failed: {exc}") from exc

    parts: list[str] = []
    for result in response.results:
        if result.alternatives:
            parts.append(result.alternatives[0].transcript)
    return {"text": " ".join(p.strip() for p in parts if p).strip()}
