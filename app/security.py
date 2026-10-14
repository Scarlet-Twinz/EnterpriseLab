"""Authentication and Proxima tenant-context signing.

The public caller authenticates with an Enterprise Lab API key. Tenant identity is
looked up server-side; callers cannot select it through a request header or body.
The resulting tenant ID is then signed for the Proxima Engine.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from fastapi import Header, HTTPException

# Load local .env for developer runs; deployed secrets should be injected by the platform.
load_dotenv()

_TENANT_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_ALLOWED_ROLES = {"admin", "operator", "viewer"}


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    role: str


def _api_keys() -> dict[str, dict[str, str]]:
    raw = os.getenv("LAB_API_KEYS", "{}")
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LAB_API_KEYS must be a JSON object") from exc
    if not isinstance(value, dict):
        raise RuntimeError("LAB_API_KEYS must be a JSON object")

    normalized: dict[str, dict[str, str]] = {}
    for key, identity in value.items():
        if not isinstance(key, str) or not key or not isinstance(identity, dict):
            raise RuntimeError("Each LAB_API_KEYS entry must map a non-empty key to an identity")
        tenant_id = identity.get("tenant_id")
        role = identity.get("role")
        if not isinstance(tenant_id, str) or not _TENANT_RE.fullmatch(tenant_id):
            raise RuntimeError("Each API key must map to a valid tenant_id")
        if role not in _ALLOWED_ROLES:
            raise RuntimeError("Each API key role must be admin, operator, or viewer")
        normalized[key] = {"tenant_id": tenant_id, "role": role}
    return normalized


def authenticate(authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer API key required")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not supplied:
        raise HTTPException(status_code=401, detail="Invalid API key")

    for candidate, identity in _api_keys().items():
        if hmac.compare_digest(supplied, candidate):
            return Principal(tenant_id=identity["tenant_id"], role=identity["role"])
    raise HTTPException(status_code=401, detail="Invalid API key")


def make_proxima_token(tenant_id: str, *, now: int | None = None, ttl_seconds: int | None = None) -> str:
    """Create the v1 tenant token consumed by the Proxima Engine.

    Wire format: v1.<tenant_id>.<expires_epoch>.<hex HMAC-SHA256>
    """
    if not _TENANT_RE.fullmatch(tenant_id):
        raise ValueError("Invalid tenant ID")
    secret = os.getenv("PROXIMA_SIGNING_KEY", "")
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("PROXIMA_SIGNING_KEY must contain at least 32 bytes")
    try:
        configured_ttl = int(os.getenv("PROXIMA_TOKEN_TTL_SECONDS", "300"))
    except ValueError as exc:
        raise RuntimeError("PROXIMA_TOKEN_TTL_SECONDS must be an integer") from exc
    ttl = configured_ttl if ttl_seconds is None else ttl_seconds
    issued_at = int(time.time()) if now is None else now
    expires = issued_at + ttl
    payload = f"v1.{tenant_id}.{expires}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"
