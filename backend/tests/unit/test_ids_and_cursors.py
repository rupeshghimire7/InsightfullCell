from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from insightfulcell.domain.errors import InvalidCursor
from insightfulcell.domain.ids import new_uuid7
from insightfulcell.services.datasets import decode_cursor, encode_cursor


class TestUuid7:
    def test_version_and_variant_bits(self) -> None:
        value = new_uuid7()
        assert value.version == 7
        assert value.variant == "specified in RFC 4122"

    def test_embeds_the_millisecond_timestamp(self) -> None:
        value = new_uuid7(unix_ms=1_700_000_000_123)
        assert value.int >> 80 == 1_700_000_000_123

    def test_ids_sort_by_creation_time(self) -> None:
        ids = [new_uuid7(unix_ms=1_700_000_000_000 + i) for i in range(50)]
        assert ids == sorted(ids)

    def test_ids_are_unique_within_one_millisecond(self) -> None:
        assert len({new_uuid7(unix_ms=1_700_000_000_000) for _ in range(1000)}) == 1000

    def test_rejects_timestamps_beyond_48_bits(self) -> None:
        with pytest.raises(ValueError, match="48 bits"):
            new_uuid7(unix_ms=1 << 48)


class TestCursor:
    def test_round_trip(self) -> None:
        ts, ident = datetime(2026, 10, 9, 5, 48, 1, 123456, tzinfo=UTC), uuid4()
        assert decode_cursor(encode_cursor(ts, ident)) == (ts, ident)

    def test_cursor_is_url_safe(self) -> None:
        cursor = encode_cursor(datetime.now(UTC), uuid4())
        assert all(c.isalnum() or c in "-_" for c in cursor)

    @pytest.mark.parametrize("bad", ["", "!!!", "e30", "W10", "bm90LWpzb24"])
    def test_garbage_is_an_invalid_cursor(self, bad: str) -> None:
        with pytest.raises(InvalidCursor):
            decode_cursor(bad)

    def test_wrong_shape_is_invalid(self) -> None:
        raw = base64.urlsafe_b64encode(json.dumps(["2026-01-01T00:00:00+00:00", "nope"]).encode())
        with pytest.raises(InvalidCursor):
            decode_cursor(raw.decode().rstrip("="))

    def test_uuid_type_is_preserved(self) -> None:
        _, ident = decode_cursor(encode_cursor(datetime.now(UTC), uuid4()))
        assert isinstance(ident, UUID)
