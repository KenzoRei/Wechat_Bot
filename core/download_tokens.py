"""
In-memory, single-process store for short-lived unguessable download links —
same "random token as the entire access control" pattern already used by
api/labels.py (serial_number acts as the token there), generalized for
anything that needs a plain clickable URL instead of an admin-key-gated
endpoint. Assumes a single app process, same assumption api/webhook.py's
_seen_msg_ids dedup dict already makes.
"""
import secrets
import time

_DEFAULT_TTL_SECONDS = 3600

_store: dict[str, dict] = {}


def create_token(data: bytes, filename: str, content_type: str, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
    _prune_expired()
    token = secrets.token_urlsafe(32)
    _store[token] = {
        "data": data,
        "filename": filename,
        "content_type": content_type,
        "expires_at": time.time() + ttl_seconds,
    }
    return token


def get_token(token: str) -> dict | None:
    entry = _store.get(token)
    if entry is None:
        return None
    if time.time() > entry["expires_at"]:
        del _store[token]
        return None
    return entry


def _prune_expired() -> None:
    now = time.time()
    expired = [t for t, e in _store.items() if now > e["expires_at"]]
    for t in expired:
        del _store[t]
