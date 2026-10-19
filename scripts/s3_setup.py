"""Create the upload bucket and its browser CORS rules on any S3-compatible endpoint.

    uv run --project backend python scripts/s3_setup.py --endpoint http://localhost:9000

Idempotent. Browsers PUT file parts straight to the bucket, and the uploader needs each part's
`ETag` response header, which browsers hide from scripts unless CORS exposes it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


def cors_rules(origins: list[str]) -> dict[str, list[dict[str, object]]]:
    return {
        "CORSRules": [
            {
                "AllowedOrigins": origins,
                "AllowedMethods": ["PUT", "GET", "HEAD"],
                "AllowedHeaders": ["*"],
                "ExposeHeaders": ["ETag"],
                "MaxAgeSeconds": 3000,
            }
        ]
    }


def allowed_origins(s3: Any, bucket: str) -> list[str]:
    """Origins the bucket's CORS rules allow today (none if it has no CORS configuration)."""
    try:
        rules = s3.get_bucket_cors(Bucket=bucket).get("CORSRules", [])
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NoSuchCORSConfiguration":
            return []
        raise
    return [origin for rule in rules for origin in rule.get("AllowedOrigins", [])]


def ensure_bucket(s3: Any, bucket: str, origins: list[str], *, add: bool = False) -> list[str]:
    """Create the bucket if needed and set its CORS rule. With ``add``, origins already allowed
    are kept, so a second app (the e2e run on another port) does not lock out the first one.
    Returns the origins now allowed."""
    try:
        s3.create_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        }:
            raise
    if add:
        origins = list(dict.fromkeys([*allowed_origins(s3, bucket), *origins]))
    s3.put_bucket_cors(Bucket=bucket, CORSConfiguration=cors_rules(origins))
    return origins


def web_origins(env_file: Path) -> list[str]:
    """``BETTER_AUTH_URL`` and ``AUTH_TRUSTED_ORIGINS`` from the web app's env file: every origin
    its pages are served at, so a tunnel listed there can upload too."""
    values: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip().strip("'\"")
    entries = [
        values.get("BETTER_AUTH_URL", ""),
        *values.get("AUTH_TRUSTED_ORIGINS", "").split(","),
    ]
    return list(dict.fromkeys(e.strip().rstrip("/") for e in entries if e.strip()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://localhost:9000")
    parser.add_argument("--bucket", default="insightfulcell-dev")
    parser.add_argument("--origin", action="append", help="Allowed browser origin (repeatable)")
    parser.add_argument(
        "--web-env",
        type=Path,
        help="Also allow the origins in this env file (BETTER_AUTH_URL, AUTH_TRUSTED_ORIGINS)",
    )
    parser.add_argument(
        "--add",
        action="store_true",
        help="Keep the origins already allowed instead of replacing them",
    )
    parser.add_argument("--access-key", default=os.environ.get("S3_ACCESS_KEY_ID", "dev"))
    parser.add_argument("--secret-key", default=os.environ.get("S3_SECRET_ACCESS_KEY", "devdevdev"))
    args = parser.parse_args()

    client = boto3.client(
        "s3",
        endpoint_url=args.endpoint,
        region_name="us-east-1",
        aws_access_key_id=args.access_key,
        aws_secret_access_key=args.secret_key,
    )
    origins = list(args.origin or [])
    if args.web_env and args.web_env.is_file():
        origins += web_origins(args.web_env)
    origins = ensure_bucket(
        client, args.bucket, list(dict.fromkeys(origins)) or ["http://localhost:3000"], add=args.add
    )
    print(
        f"Bucket '{args.bucket}' ready on {args.endpoint}; browser uploads allowed from "
        f"{', '.join(origins)} (CORS exposes ETag)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
