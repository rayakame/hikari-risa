from __future__ import annotations

import pytest

import risa
from risa.internal import codec
from risa.internal import constants


def custom_id(
    *,
    fragment: str = "",
    tail: str = "",
    fragment_index: int = 0,
) -> codec.CustomID:
    return codec.CustomID(
        cookie=codec.make_cookie("poll", 1),
        handler=codec.make_handler_token("vote", 1),
        fragment_index=fragment_index,
        fragment=fragment,
        tail=tail,
    )


def test_a_cookie_is_six_deterministic_characters() -> None:
    assert len(codec.make_cookie("poll", 1)) == codec.COOKIE_LENGTH
    assert codec.make_cookie("poll", 1) == codec.make_cookie("poll", 1)


def test_a_handler_token_is_two_deterministic_characters() -> None:
    assert len(codec.make_handler_token("vote", 1)) == codec.HANDLER_LENGTH
    assert codec.make_handler_token("vote", 1) == codec.make_handler_token("vote", 1)


def test_the_version_participates_in_both_identities() -> None:
    assert codec.make_cookie("poll", 1) != codec.make_cookie("poll", 2)
    assert codec.make_handler_token("vote", 1) != codec.make_handler_token("vote", 2)


def test_the_name_participates_in_both_identities() -> None:
    assert codec.make_cookie("poll", 1) != codec.make_cookie("todo", 1)
    assert codec.make_handler_token("vote", 1) != codec.make_handler_token("close", 1)


def test_the_header_arithmetic_adds_up() -> None:
    assert codec.HEADER_LENGTH == 11
    assert codec.MAX_FRAGMENT_LENGTH == constants.MAX_CUSTOM_ID_LENGTH - codec.HEADER_LENGTH


@pytest.mark.parametrize("fragment", ["", "f", "y" * 40])
@pytest.mark.parametrize("tail", ["", "t", "args-go-here"])
@pytest.mark.parametrize("fragment_index", [0, 1, 91])
def test_every_encodable_id_round_trips(fragment: str, tail: str, fragment_index: int) -> None:
    original = custom_id(fragment=fragment, tail=tail, fragment_index=fragment_index)
    assert codec.parse_custom_id(original.encode()) == original


def test_a_full_width_fragment_round_trips_at_exactly_the_limit() -> None:
    original = custom_id(fragment="z" * codec.MAX_FRAGMENT_LENGTH)
    encoded = original.encode()

    assert len(encoded) == constants.MAX_CUSTOM_ID_LENGTH
    assert codec.parse_custom_id(encoded) == original


def test_a_tail_can_fill_the_id_to_exactly_the_limit() -> None:
    original = custom_id(fragment="z" * 40, tail="t" * (codec.MAX_FRAGMENT_LENGTH - 40))
    encoded = original.encode()

    assert len(encoded) == constants.MAX_CUSTOM_ID_LENGTH
    assert codec.parse_custom_id(encoded) == original


def test_one_character_too_many_raises_the_overflow_error() -> None:
    with pytest.raises(risa.CustomIdOverflowError) as exc_info:
        custom_id(fragment="z" * codec.MAX_FRAGMENT_LENGTH, tail="t").encode()
    assert exc_info.value.length == constants.MAX_CUSTOM_ID_LENGTH + 1


def test_a_fragment_wider_than_its_length_digit_is_refused() -> None:
    with pytest.raises(ValueError, match="does not fit"):
        custom_id(fragment="z" * 92).encode()


def test_a_fragment_index_wider_than_its_digit_is_refused() -> None:
    with pytest.raises(ValueError, match="does not fit"):
        custom_id(fragment_index=92).encode()


def test_a_malformed_cookie_is_a_caller_bug() -> None:
    broken = codec.CustomID(cookie="short", handler="ab", fragment_index=0, fragment="", tail="")
    with pytest.raises(ValueError, match="cookie"):
        broken.encode()


def test_a_malformed_handler_token_is_a_caller_bug() -> None:
    broken = codec.CustomID(cookie="abcdef", handler="a", fragment_index=0, fragment="", tail="")
    with pytest.raises(ValueError, match="handler token"):
        broken.encode()


@pytest.mark.parametrize(
    "foreign",
    [
        "",
        "x",
        "1abc",
        "miru:settings:3",
        "flare persistent button",
        "a" * constants.MAX_CUSTOM_ID_LENGTH,
        "1" + "a" * constants.MAX_CUSTOM_ID_LENGTH,
    ],
)
def test_foreign_ids_fail_soft(foreign: str) -> None:
    assert codec.parse_custom_id(foreign) is None


def test_a_wrong_version_character_fails_soft() -> None:
    encoded = custom_id(fragment="abc").encode()
    assert codec.parse_custom_id("2" + encoded[1:]) is None


def test_a_corrupted_index_character_fails_soft() -> None:
    encoded = custom_id(fragment="abc").encode()
    assert codec.parse_custom_id(encoded[:9] + '"' + encoded[10:]) is None


def test_a_corrupted_length_character_fails_soft() -> None:
    encoded = custom_id(fragment="abc").encode()
    assert codec.parse_custom_id(encoded[:10] + '"' + encoded[11:]) is None


def test_an_id_truncated_mid_fragment_fails_soft() -> None:
    encoded = custom_id(fragment="z" * 30).encode()
    assert codec.parse_custom_id(encoded[:-10]) is None


def test_a_declared_fragment_longer_than_the_id_fails_soft() -> None:
    assert codec.parse_custom_id(custom_id().encode() + "") is not None
    assert codec.parse_custom_id(custom_id(fragment="abc").encode()[:-3]) is None


def test_parse_never_inspects_the_tail() -> None:
    parsed = codec.parse_custom_id(custom_id(tail='weird "tail" \\ content').encode())
    assert parsed is not None
    assert parsed.tail == 'weird "tail" \\ content'
