"""Fixtures shared by unit and integration tests: signing keys and a token factory."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from insightfulcell.auth.tokens import TokenVerifier

ISSUER = "http://localhost:3000"
AUDIENCE = "insightfulcell-api"


@dataclass(frozen=True)
class Identity:
    user_id: UUID
    org_id: UUID
    role: str = "owner"
    org_name: str = "Test Workspace"


class StaticKeyResolver:
    """Stands in for the JWKS client: always answers with one public key."""

    def __init__(self, public_key: Ed25519PublicKey) -> None:
        self._key = public_key

    def get_signing_key(self, token: str) -> Ed25519PublicKey:
        return self._key


@pytest.fixture(scope="session")
def signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture(scope="session")
def verifier(signing_key: Ed25519PrivateKey) -> TokenVerifier:
    return TokenVerifier(
        StaticKeyResolver(signing_key.public_key()),
        issuer=ISSUER,
        audience=AUDIENCE,
        leeway_seconds=0,
    )


MakeToken = Callable[..., str]


@pytest.fixture(scope="session")
def make_token(signing_key: Ed25519PrivateKey) -> MakeToken:
    """Mint a token. Pass ``identity`` for the happy path; override any claim with kwargs;
    pass ``key`` or ``algorithm`` to forge."""

    def _make(
        identity: Identity | None = None,
        *,
        key: Any = None,
        algorithm: str = "EdDSA",
        drop: tuple[str, ...] = (),
        **overrides: Any,
    ) -> str:
        identity = identity or Identity(user_id=uuid4(), org_id=uuid4())
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": str(identity.user_id),
            "org_id": str(identity.org_id),
            "role": identity.role,
            "org_name": identity.org_name,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + 900,
        }
        claims.update(overrides)
        for name in drop:
            claims.pop(name, None)
        return jwt.encode(
            claims,
            key if key is not None else signing_key,
            algorithm=algorithm,
            headers={"kid": "k1"},
        )

    return _make
