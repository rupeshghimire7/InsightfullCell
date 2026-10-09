from __future__ import annotations

from uuid import uuid4

import pytest

from insightfulcell.db.models import Upload
from insightfulcell.domain.errors import InvalidParts
from insightfulcell.services.uploads import _part_length, _request_hash, _validate_parts
from insightfulcell.storage.port import CompletedPart

MIB = 1024 * 1024


def _upload(declared: int, part_size: int = 8 * MIB) -> Upload:
    count = max(1, -(-declared // part_size))
    return Upload(declared_bytes=declared, part_size_bytes=part_size, part_count=count)


class TestPartLength:
    def test_single_small_part(self) -> None:
        assert _part_length(_upload(1234), 1) == 1234

    def test_full_parts_then_remainder(self) -> None:
        upload = _upload(20 * MIB)  # 8 + 8 + 4
        assert [_part_length(upload, n) for n in (1, 2, 3)] == [8 * MIB, 8 * MIB, 4 * MIB]

    def test_exact_multiple_has_a_full_last_part(self) -> None:
        upload = _upload(16 * MIB)
        assert [_part_length(upload, n) for n in (1, 2)] == [8 * MIB, 8 * MIB]

    @pytest.mark.parametrize("size", [1, 5 * MIB, 8 * MIB, 8 * MIB + 1, 250 * MIB])
    def test_part_lengths_sum_to_declared_size(self, size: int) -> None:
        upload = _upload(size)
        total = sum(_part_length(upload, n) for n in range(1, upload.part_count + 1))
        assert total == size


class TestValidateParts:
    def test_accepts_complete_ordered_list(self) -> None:
        _validate_parts([CompletedPart(1, "a"), CompletedPart(2, '"b"')], 2)

    @pytest.mark.parametrize(
        "numbers",
        [[], [2, 1], [1, 1], [1, 3], [1], [1, 2, 3]],
    )
    def test_rejects_wrong_part_lists(self, numbers: list[int]) -> None:
        with pytest.raises(InvalidParts):
            _validate_parts([CompletedPart(n, "etag") for n in numbers], 2)

    def test_rejects_blank_etag(self) -> None:
        with pytest.raises(InvalidParts):
            _validate_parts([CompletedPart(1, '""')], 1)


def test_request_hash_is_stable_and_sensitive_to_every_field() -> None:
    base = _request_hash("a.csv", 10, None)
    assert base == _request_hash("a.csv", 10, None)
    assert base != _request_hash("b.csv", 10, None)
    assert base != _request_hash("a.csv", 11, None)
    assert base != _request_hash("a.csv", 10, uuid4())
