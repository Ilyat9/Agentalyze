"""API-key authentication for the HTTP service.

Deliberate minimalism (documented in docs/DEPLOYMENT.md): the threat model is
"untrusted internet traffic against a trusted-team tool", not "mutually
distrustful tenants". Bearer API keys, hashed at rest with scrypt and compared
in constant time, are exactly sufficient for that; a full OAuth/JWT apparatus
would add token rotation/revocation complexity with no additional safety for
the actual usage (a handful of manually distributed long-lived keys).

Plaintext keys are NEVER persisted or logged: :func:`create_api_key` returns
the plaintext once to the operator, only the scrypt hash enters the database,
and responses identify keys by their ``name`` alone.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from agentalyze.api.db import ApiKeyRecord

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_api_key(plaintext: str) -> str:
    """scrypt-hash a key into the self-describing stored format."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        plaintext.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_api_key_hash(plaintext: str, stored: str) -> bool:
    """Constant-time verification against a stored hash."""
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    try:
        digest = hashlib.scrypt(
            plaintext.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


def generate_api_key() -> str:
    """Generate a fresh plaintext key: ``agt-`` + 32 url-safe random chars."""
    return "agt-" + secrets.token_urlsafe(24)


class AuthenticatedKey:
    """Identity attached to a request after successful authentication."""

    __slots__ = ("id", "name")

    def __init__(self, record: ApiKeyRecord) -> None:
        self.name = record.name
        self.id = record.id


async def _load_records(session: AsyncSession) -> list[ApiKeyRecord]:
    from sqlalchemy import select

    result = await session.execute(
        select(ApiKeyRecord).where(ApiKeyRecord.is_active.is_(True))
    )
    return list(result.scalars().all())


async def require_api_key(
    request: Request,
    authorization: Annotated[
        str | None, Header(description="Bearer <api key>")
    ] = None,
) -> AuthenticatedKey | None:
    """FastAPI dependency enforcing ``Authorization: Bearer <key>``.

    Returns None only when auth is disabled via settings (local development).
    On failure raises 401 WITHOUT echoing which part was wrong — the response
    must not help an attacker enumerate valid keys.
    """
    settings = request.app.state.settings
    if not settings.api_auth_required:
        return None
    session_factory = request.app.state.session_factory
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    plaintext = authorization.removeprefix("Bearer ").strip()
    if not plaintext:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    async with session_factory() as session:
        for record in await _load_records(session):
            if verify_api_key_hash(plaintext, record.key_hash):
                request.state.api_key_name = record.name
                return AuthenticatedKey(record)
    # Constant-ish delay to blunt timing side channels on miss.
    hmac.compare_digest(hash_api_key(plaintext), hash_api_key(plaintext))
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


ApiKeyAuth = Annotated[AuthenticatedKey | None, Depends(require_api_key)]
