from __future__ import annotations

import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from insightfulcell.auth.tokens import JwksKeyResolver, TokenVerifier
from insightfulcell.domain.errors import IdentityProviderUnavailable, Unauthenticated
from insightfulcell.domain.security import Role
from tests.conftest import Identity, MakeToken


def test_valid_token_yields_principal(verifier: TokenVerifier, make_token: MakeToken) -> None:
    identity = Identity(user_id=uuid4(), org_id=uuid4(), role="analyst", org_name="Acme Pty Ltd")
    principal = verifier.verify_sync(make_token(identity))
    assert principal.user_id == identity.user_id
    assert principal.org_id == identity.org_id
    assert principal.role is Role.ANALYST
    assert principal.org_name == "Acme Pty Ltd"


def test_org_name_is_optional_and_trimmed(verifier: TokenVerifier, make_token: MakeToken) -> None:
    assert verifier.verify_sync(make_token(drop=("org_name",))).org_name is None
    assert verifier.verify_sync(make_token(org_name="  Acme  ")).org_name == "Acme"
    assert len(verifier.verify_sync(make_token(org_name="x" * 500)).org_name or "") == 200


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("expired", {"exp": int(time.time()) - 60}),
        ("wrong audience", {"aud": "someone-else"}),
        ("wrong issuer", {"iss": "https://evil.example"}),
        ("not yet valid", {"iat": int(time.time()) + 3600, "nbf": int(time.time()) + 3600}),
        ("no expiry", {"drop": ("exp",)}),
        ("no subject", {"drop": ("sub",)}),
        ("no audience", {"drop": ("aud",)}),
    ],
)
def test_bad_tokens_are_rejected(
    verifier: TokenVerifier, make_token: MakeToken, label: str, kwargs: dict[str, object]
) -> None:
    with pytest.raises(Unauthenticated):
        verifier.verify_sync(make_token(**kwargs))


def test_expired_token_tells_the_user_to_sign_in_again(
    verifier: TokenVerifier, make_token: MakeToken
) -> None:
    with pytest.raises(Unauthenticated) as info:
        verifier.verify_sync(make_token(exp=int(time.time()) - 60))
    assert "expired" in info.value.detail


def test_token_signed_by_another_key_is_rejected(
    verifier: TokenVerifier, make_token: MakeToken
) -> None:
    forged = make_token(key=Ed25519PrivateKey.generate())
    with pytest.raises(Unauthenticated):
        verifier.verify_sync(forged)


def test_algorithm_none_is_rejected(verifier: TokenVerifier) -> None:
    now = int(time.time())
    unsigned = jwt.encode(
        {
            "sub": str(uuid4()),
            "org_id": str(uuid4()),
            "role": "owner",
            "iss": "http://localhost:3000",
            "aud": "insightfulcell-api",
            "iat": now,
            "exp": now + 60,
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(Unauthenticated):
        verifier.verify_sync(unsigned)


def test_hmac_signed_with_the_public_key_is_rejected(verifier: TokenVerifier) -> None:
    """Algorithm-confusion attack: HS256 using the (public) verification key as the secret."""
    now = int(time.time())
    forged = jwt.encode(
        {
            "sub": str(uuid4()),
            "org_id": str(uuid4()),
            "role": "owner",
            "iss": "http://localhost:3000",
            "aud": "insightfulcell-api",
            "iat": now,
            "exp": now + 60,
        },
        key="public-key-bytes-as-secret-0123456789",
        algorithm="HS256",
    )
    with pytest.raises(Unauthenticated):
        verifier.verify_sync(forged)


@pytest.mark.parametrize(
    "overrides",
    [
        {"drop": ("org_id",)},
        {"drop": ("role",)},
        {"org_id": "not-a-uuid"},
        {"sub": "not-a-uuid"},
        {"role": "superuser"},
    ],
)
def test_missing_or_invalid_tenant_claims_are_rejected(
    verifier: TokenVerifier, make_token: MakeToken, overrides: dict[str, object]
) -> None:
    with pytest.raises(Unauthenticated):
        verifier.verify_sync(make_token(**overrides))


def test_garbage_is_rejected(verifier: TokenVerifier) -> None:
    for token in ("", "abc", "a.b.c", "x" * 2000):
        with pytest.raises(Unauthenticated):
            verifier.verify_sync(token)


def test_error_messages_never_contain_the_token(
    verifier: TokenVerifier, make_token: MakeToken
) -> None:
    token = make_token(aud="nope")
    with pytest.raises(Unauthenticated) as info:
        verifier.verify_sync(token)
    assert token not in info.value.detail
    assert token not in str(info.value)


async def test_async_verify_wraps_sync_path(verifier: TokenVerifier, make_token: MakeToken) -> None:
    principal = await verifier.verify(make_token())
    assert principal.role is Role.OWNER


def test_jwks_outage_is_reported_as_unavailable_not_unauthenticated(make_token: MakeToken) -> None:
    resolver = JwksKeyResolver("http://127.0.0.1:1/jwks", timeout_seconds=1)  # nothing listens
    verifier = TokenVerifier(
        resolver, issuer="http://localhost:3000", audience="insightfulcell-api"
    )
    with pytest.raises(IdentityProviderUnavailable):
        verifier.verify_sync(make_token())
