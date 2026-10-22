from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from insightfulcell.config import Settings
from insightfulcell.storage.port import PartSpec
from insightfulcell.storage.s3 import S3ObjectStore

BASE = {
    "database_url": "postgresql+psycopg://u@h/db",
    "auth_jwks_url": "https://web.example/api/auth/jwks",
    "auth_issuer": "https://web.example",
    "s3_bucket": "ic-uploads",
    "s3_access_key_id": "test",
    "s3_secret_access_key": "test",
}


def _part_url_host(**overrides: object) -> str:
    settings = Settings(**{**BASE, **overrides})  # type: ignore[arg-type]
    urls = S3ObjectStore(settings).presign_parts(
        "uploads/k", "upload-id", [PartSpec(part_number=1, content_length=10)], expires_in=60
    )
    return urlsplit(urls[1]).netloc


@pytest.mark.parametrize(
    ("region", "host"),
    [
        # The regional host, not the global one: that redirects for new buckets, and browsers
        # cannot follow a redirect on a CORS upload.
        ("ap-southeast-2", "ic-uploads.s3.ap-southeast-2.amazonaws.com"),
        ("eu-west-1", "ic-uploads.s3.eu-west-1.amazonaws.com"),
        ("us-east-1", "ic-uploads.s3.amazonaws.com"),
    ],
)
def test_aws_part_urls_use_the_buckets_regional_host(region: str, host: str) -> None:
    assert _part_url_host(s3_region=region, s3_endpoint_url=None, s3_force_path_style=False) == host


def test_self_hosted_storage_keeps_path_style_urls() -> None:
    host = _part_url_host(
        s3_endpoint_url="http://localhost:9000",
        s3_public_endpoint_url=None,
        s3_force_path_style=True,
    )
    assert host == "localhost:9000"
