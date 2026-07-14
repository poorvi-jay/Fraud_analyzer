"""Reviewer auth for the human-override endpoint.

Verifies the caller's Supabase session token by asking Supabase's Auth API
(`auth.get_user`) rather than decoding the JWT locally -- this works whether
the Supabase project signs tokens with the legacy shared secret (HS256) or
the newer asymmetric keys (ES256/JWKS), with no local key material to keep
in sync.
"""
from fastapi import Header, HTTPException
from supabase import create_client

from app.config import settings

_client = None


def _get_client():
    global _client
    if _client is None:
        if not settings.supabase_url or not settings.supabase_anon_key:
            raise HTTPException(
                status_code=503,
                detail="Reviewer auth is not configured (SUPABASE_URL / SUPABASE_ANON_KEY missing).",
            )
        _client = create_client(settings.supabase_url, settings.supabase_anon_key)
    return _client


def require_reviewer(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency: returns the reviewer's Supabase user id, or 401s."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")

    token = authorization.removeprefix("Bearer ").strip()
    # _get_client() is called outside the try block deliberately: its 503
    # ("not configured") is a distinct failure mode from "token rejected by
    # Supabase" and must not be swallowed into a misleading 401 by the
    # except clause below.
    client = _get_client()
    try:
        response = client.auth.get_user(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired reviewer session.")

    if response is None or response.user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired reviewer session.")

    return response.user.id
