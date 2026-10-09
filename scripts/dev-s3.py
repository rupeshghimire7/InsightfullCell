"""Local S3 emulator (moto) for machines without Docker.

    uv run --project backend python scripts/dev-s3.py

Docker users should prefer RustFS from docker-compose.yml, which behaves like the real service.
This emulator does not verify request signatures, so it cannot catch signing mistakes.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

import boto3
from moto.server import ThreadedMotoServer
from s3_setup import (
    ensure_bucket,
)  # sits next to this file; Python puts the script dir on sys.path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--bucket", default="insightfulcell-dev")
    args = parser.parse_args()

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=args.port, verbose=False)
    server.start()
    client = boto3.client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{args.port}",
        region_name="us-east-1",
        aws_access_key_id="dev",
        aws_secret_access_key="dev",
    )
    ensure_bucket(client, args.bucket, ["*"])
    print(
        f"S3 emulator on http://127.0.0.1:{args.port}, bucket '{args.bucket}'",
        flush=True,
    )

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()
    server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
