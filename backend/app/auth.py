import logging

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import settings

log = logging.getLogger("sleek_rag.auth")

bearer = HTTPBearer()

_jwks_client = PyJWKClient(settings.supabase_jwks_url, cache_keys=True)


def current_user_id(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(creds.credentials)
        payload = jwt.decode(
            creds.credentials,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as exc:
        log.warning("jwt rejected: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        # PyJWKClient errors (network, parse, kid lookup) aren't PyJWTError —
        # surface them too so we can see "JWKS fetch failed" vs "bad signature".
        log.warning("jwks lookup failed: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"jwks: {type(exc).__name__}: {exc}")
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing sub")
    return sub
