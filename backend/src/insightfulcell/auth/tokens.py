"""Verify the short-lived JWT the web app (Better Auth) issues for each API call (IC-005).

Trust model: the web app signs EdDSA (Ed25519) JWTs; the API verifies them offline against the
JWKS the web app publishes. The API never sees passwords, cookies or provider tokens, and
never reads ``org_id`` from a request body: the only source of tenant identity is a verified
token. Token contents are never logged.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

import jwt
from jwt import PyJWKClient

from insightfulcell.domain.errors import IdentityProviderUnavailable, Unauthenticated
from insightfulcell.domain.security import Principal, Role

ALGORITHMS = ["EdDSA"]  # pinned: never accept "none" or an HMAC key confusion
REQUIRED_CLAIMS = ["exp", "iat", "sub", "iss", "aud"]
_ORG_NAME_MAX = 200
#: An event ticket names exactly one run and lives at most this long, whatever it claims.
MAX_TICKET_SECONDS = 300 + 30


@dataclass(frozen=True, slots=True)
class EventTicket:
    """A run-scoped ticket for one event stream (audience ``events``)."""

    principal: Principal
    run_id: UUID
    expires_at: datetime


class SigningKeyResolver(Protocol):
    """Finds the public key that signed a token (by its ``kid`` header)."""

    def get_signing_key(self, token: str) -> Any: ...


class JwksKeyResolver:
    """Fetches and caches the JWKS; refetches once when a token names an unknown ``kid``
    (key rotation)."""

    def __init__(
        self, jwks_url: str, *, cache_seconds: int = 300, timeout_seconds: int = 5
    ) -> None:
        self._client = PyJWKClient(
            jwks_url, cache_keys=True, lifespan=cache_seconds, timeout=timeout_seconds
        )

    def get_signing_key(self, token: str) -> Any:
        try:
            return self._client.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientConnectionError as exc:
            raise IdentityProviderUnavailable(
                "Could not reach the sign-in service to verify your session.",
                hint="Try again in a moment.",
            ) from exc
        except jwt.PyJWTError as exc:
            raise Unauthenticated("The access token is not signed by a trusted key.") from exc


class TokenVerifier:
    def __init__(
        self,
        resolver: SigningKeyResolver,
        *,
        issuer: str,
        audience: str,
        leeway_seconds: int = 30,
    ) -> None:
        self._resolver = resolver
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway_seconds

    async def verify(self, token: str) -> Principal:
        # PyJWKClient does blocking I/O on a cache miss, so keep it off the event loop.
        return await asyncio.to_thread(self.verify_sync, token)

    def verify_sync(self, token: str) -> Principal:
        return _principal_from_claims(self._decode(token, REQUIRED_CLAIMS))

    async def verify_ticket(self, token: str) -> EventTicket:
        return await asyncio.to_thread(self.verify_ticket_sync, token)

    def verify_ticket_sync(self, token: str) -> EventTicket:
        """Verify a run-scoped event ticket. Use a verifier built with the *events* audience:
        an API token then fails the audience check, and a ticket fails it on the API."""
        claims = self._decode(token, [*REQUIRED_CLAIMS, "run_id"])
        if int(claims["exp"]) - int(claims["iat"]) > MAX_TICKET_SECONDS:
            raise Unauthenticated("The event ticket is invalid.")
        try:
            run_id = UUID(str(claims["run_id"]))
        except ValueError as exc:
            raise Unauthenticated("The event ticket is invalid.") from exc
        return EventTicket(
            principal=_principal_from_claims(claims),
            run_id=run_id,
            expires_at=datetime.fromtimestamp(int(claims["exp"]), UTC),
        )

    def _decode(self, token: str, required: list[str]) -> dict[str, Any]:
        try:
            key = self._resolver.get_signing_key(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": required},
            )
        except jwt.ExpiredSignatureError as exc:
            raise Unauthenticated("Your session has expired.", hint="Sign in again.") from exc
        except jwt.PyJWTError as exc:
            # Deliberately generic: do not tell a caller *which* check failed.
            raise Unauthenticated("The access token is invalid.") from exc
        return claims


def _principal_from_claims(claims: dict[str, Any]) -> Principal:
    try:
        user_id = UUID(str(claims["sub"]))
        org_id = UUID(str(claims["org_id"]))
        role = Role(str(claims["role"]))
    except (KeyError, ValueError) as exc:
        raise Unauthenticated(
            "The access token has no active workspace or role.",
            hint="Sign out and sign in again.",
        ) from exc
    raw_name = claims.get("org_name")
    org_name = raw_name.strip()[:_ORG_NAME_MAX] if isinstance(raw_name, str) else None
    return Principal(user_id=user_id, org_id=org_id, role=role, org_name=org_name or None)
