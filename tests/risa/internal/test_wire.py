from __future__ import annotations

import pytest

from risa.internal import wire


def test_the_alphabet_is_92_unique_printable_symbols() -> None:
    assert len(wire.ALPHABET) == wire.ALPHABET_SIZE == 92
    assert len(set(wire.ALPHABET)) == 92
    assert wire.ALPHABET.isascii()
    assert wire.ALPHABET.isprintable()
    assert '"' not in wire.ALPHABET
    assert "\\" not in wire.ALPHABET
    assert " " not in wire.ALPHABET


@pytest.mark.parametrize("width", [1, 2, 3, 6])
def test_uints_round_trip_at_every_width(width: int) -> None:
    candidates = [0, 1, wire.ALPHABET_SIZE - 1, wire.ALPHABET_SIZE, wire.largest_value(width)]
    for value in [candidate for candidate in candidates if candidate <= wire.largest_value(width)]:
        packed = wire.pack_uint(value, width)
        assert len(packed) == width
        assert wire.unpack_uint(packed) == value


def test_packed_uints_are_most_significant_first() -> None:
    assert wire.pack_uint(1, 2) == wire.ALPHABET[0] + wire.ALPHABET[1]
    assert wire.pack_uint(wire.ALPHABET_SIZE, 2) == wire.ALPHABET[1] + wire.ALPHABET[0]


def test_fixed_width_packing_sorts_like_the_numbers() -> None:
    packed = [wire.pack_uint(value, 2) for value in range(0, wire.largest_value(2), 137)]
    assert packed == sorted(packed)


def test_a_value_that_does_not_fit_is_refused() -> None:
    with pytest.raises(ValueError, match="does not fit"):
        wire.pack_uint(wire.largest_value(1) + 1, 1)


def test_a_negative_value_is_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        wire.pack_uint(-1, 2)


def test_unpacking_a_foreign_character_fails_soft() -> None:
    assert wire.unpack_uint('a"b') is None
    assert wire.unpack_uint("ä") is None
    assert wire.unpack_uint("a b") is None


@pytest.mark.parametrize("data", [b"", b"\x00", b"risa", b"\xff" * 17, bytes(range(256))])
def test_bytes_round_trip(data: bytes) -> None:
    packed = wire.pack_bytes(data)
    assert packed.isascii()
    assert '"' not in packed
    assert "\\" not in packed
    assert wire.unpack_bytes(packed) == data


def test_unpacking_malformed_bytes_fails_soft() -> None:
    assert wire.unpack_bytes('"') is None
    assert wire.unpack_bytes("a b") is None
    assert wire.unpack_bytes("a") is None
    assert wire.unpack_bytes("aaaaaa") is None


def test_a_digest_is_deterministic_and_exactly_as_wide_as_asked() -> None:
    for chars in [1, 2, 6, wire.MAX_DIGEST_CHARS]:
        first = wire.pack_digest("poll:1", chars=chars)
        assert len(first) == chars
        assert first == wire.pack_digest("poll:1", chars=chars)


def test_different_inputs_give_different_digests() -> None:
    assert wire.pack_digest("poll:1", chars=6) != wire.pack_digest("poll:2", chars=6)
    assert wire.pack_digest("poll:1", chars=6) != wire.pack_digest("pole:1", chars=6)


def test_digest_width_participates_in_the_hash() -> None:
    wide = wire.pack_digest("poll:1", chars=6)
    narrow = wire.pack_digest("poll:1", chars=2)
    assert narrow != wide[:2]


@pytest.mark.parametrize("chars", [0, -1, wire.MAX_DIGEST_CHARS + 1])
def test_an_unanswerable_digest_width_is_refused(chars: int) -> None:
    with pytest.raises(ValueError, match="chars"):
        wire.pack_digest("poll:1", chars=chars)


def test_digests_stay_inside_the_printable_no_escape_set() -> None:
    for seed in range(50):
        digest = wire.pack_digest(f"view:{seed}", chars=6)
        assert digest.isascii()
        assert digest.isprintable()
        assert '"' not in digest
        assert "\\" not in digest
