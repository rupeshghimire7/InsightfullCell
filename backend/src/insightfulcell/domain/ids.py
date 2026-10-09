"""Time-ordered identifiers (UUIDv7, RFC 9562).

Python 3.13 has no ``uuid.uuid7`` (it arrives in 3.14), and storage keys such as
``bronze/{org}/uploads/{id}`` need the id *before* the row is inserted, so ids are generated
here. Time ordering keeps B-tree inserts append-like (07-database-design.md section 1).
"""

from __future__ import annotations

import os
import time
from uuid import UUID

_VERSION_7 = 0x7
_VARIANT_RFC = 0b10


def new_uuid7(*, unix_ms: int | None = None) -> UUID:
    """Return a UUIDv7: 48-bit millisecond timestamp, then 74 random bits."""
    timestamp = int(time.time() * 1000) if unix_ms is None else unix_ms
    if not 0 <= timestamp < 1 << 48:
        raise ValueError("timestamp does not fit in 48 bits")
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits
    rand_a = (rand >> 68) & 0xFFF  # 12 bits
    rand_b = rand & ((1 << 62) - 1)  # 62 bits
    value = (timestamp << 80) | (_VERSION_7 << 76) | (rand_a << 64) | (_VARIANT_RFC << 62) | rand_b
    return UUID(int=value)
