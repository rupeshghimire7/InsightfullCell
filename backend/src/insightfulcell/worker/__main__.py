"""``python -m insightfulcell.worker [--queues standard,large,maintenance] [--concurrency N]``.

Stops gracefully on SIGINT/SIGTERM: running jobs get time to finish; anything interrupted is
picked up again by the reconciler or the stalled-job reaper.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from insightfulcell.config import get_settings
from insightfulcell.logging_config import configure_logging
from insightfulcell.worker.runtime import ALL_QUEUES, ModelNotUsable, build_worker, check_model


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the InsightfulCell worker.")
    parser.add_argument(
        "--queues",
        default=",".join(ALL_QUEUES),
        help=f"Comma-separated queues to work on (default: {','.join(ALL_QUEUES)}).",
    )
    parser.add_argument("--concurrency", type=int, default=None, help="Jobs run at once.")
    parser.add_argument("--name", default=None, help="Worker name shown in logs.")
    args = parser.parse_args(argv)
    queues = [q.strip() for q in args.queues.split(",") if q.strip()]
    unknown = sorted(set(queues) - set(ALL_QUEUES))
    if not queues or unknown:
        parser.error(f"unknown queue(s): {', '.join(unknown) or '(none given)'}")
    if args.concurrency is not None and not 1 <= args.concurrency <= 32:
        parser.error("--concurrency must be between 1 and 32")
    args.queues = queues
    return args


async def _main(args: argparse.Namespace) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    async with build_worker(settings) as runtime:
        await check_model(runtime.model, settings)
        await runtime.run(queues=args.queues, concurrency=args.concurrency, name=args.name)


def main(argv: list[str] | None = None) -> int:
    try:
        asyncio.run(_main(parse_args(argv)))
    except ModelNotUsable as exc:
        sys.stderr.write(f"The worker did not start: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
