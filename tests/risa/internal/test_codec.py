from __future__ import annotations

import enum

import hikari
import pytest

import risa
from risa.internal import codec
from risa.internal import constants
from risa.internal import wire


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


class Color(enum.Enum):
    RED = 1
    BLUE = 2


class Mode(enum.Enum):
    FAST = "fast"
    SLOW = "slow"


@pytest.mark.parametrize("value", [0, 1, -1, 127, 128, -128, 300, 2**128, -(2**64)])
def test_an_int_survives_the_round_trip(value: int) -> None:
    converter = codec.IntConverter(int)

    assert converter.decode(converter.encode(value)) == value


def test_a_snowflake_comes_back_as_a_snowflake() -> None:
    converter = codec.IntConverter(hikari.Snowflake)

    decoded = converter.decode(converter.encode(1364800923047857855))

    assert isinstance(decoded, hikari.Snowflake)
    assert decoded == 1364800923047857855


def test_a_bool_is_not_an_int() -> None:
    sneaky: bool = True
    with pytest.raises(TypeError):
        codec.IntConverter(int).encode(sneaky)


def test_an_int_converter_rejects_other_types() -> None:
    with pytest.raises(TypeError):
        codec.IntConverter(int).encode("5")


def test_garbled_int_text_is_unreadable() -> None:
    assert codec.IntConverter(int).decode('"') is None


@pytest.mark.parametrize("value", ["", "Red", "hÿ", "🎈🎈", "a" * 60])
def test_a_str_survives_the_round_trip(value: str) -> None:
    converter = codec.StrConverter()

    assert converter.decode(converter.encode(value)) == value


def test_bytes_that_are_not_utf8_are_unreadable() -> None:
    assert codec.StrConverter().decode(wire.pack_bytes(b"\xff\xfe")) is None


def test_a_str_converter_rejects_other_types() -> None:
    with pytest.raises(TypeError):
        codec.StrConverter().encode(5)


@pytest.mark.parametrize("value", [True, False])
def test_a_bool_survives_the_round_trip_in_one_char(*, value: bool) -> None:
    converter = codec.BoolConverter()

    encoded = converter.encode(value)

    assert len(encoded) == 1
    assert converter.decode(encoded) is value


def test_a_bool_converter_rejects_ints() -> None:
    with pytest.raises(TypeError):
        codec.BoolConverter().encode(1)


def test_a_forged_bool_is_unreadable() -> None:
    assert codec.BoolConverter().decode(wire.pack_uint(5, 1)) is None
    assert codec.BoolConverter().decode("00") is None


def test_an_int_valued_enum_survives_the_round_trip() -> None:
    converter = codec.EnumConverter(Color, codec.IntConverter(int), "ei")

    assert converter.decode(converter.encode(Color.BLUE)) is Color.BLUE
    assert converter.type_id == "ei"


def test_a_str_valued_enum_survives_the_round_trip() -> None:
    converter = codec.EnumConverter(Mode, codec.StrConverter(), "es")

    assert converter.decode(converter.encode(Mode.FAST)) is Mode.FAST
    assert converter.type_id == "es"


def test_a_value_the_enum_no_longer_has_is_unreadable() -> None:
    converter = codec.EnumConverter(Color, codec.IntConverter(int), "ei")

    stale = codec.IntConverter(int).encode(99)

    assert converter.decode(stale) is None


def test_an_enum_converter_rejects_foreign_members() -> None:
    converter = codec.EnumConverter(Color, codec.IntConverter(int), "ei")

    with pytest.raises(TypeError):
        converter.encode(Mode.FAST)


def test_no_frames_is_an_empty_tail() -> None:
    assert not codec.pack_frames([])
    assert codec.unpack_frames("") == []


def test_frames_survive_the_round_trip() -> None:
    parts = ["Qe|W", "", "1p"]

    assert codec.unpack_frames(codec.pack_frames(parts)) == parts


def test_a_frame_at_the_cap_fits_and_one_past_it_raises() -> None:
    assert codec.unpack_frames(codec.pack_frames(["x" * 91])) == ["x" * 91]
    with pytest.raises(ValueError, match="does not fit"):
        codec.pack_frames(["x" * 92])


def test_a_truncated_frame_is_unreadable() -> None:
    whole = codec.pack_frames(["Qe|W"])

    assert codec.unpack_frames(whole[:-1]) is None


def test_a_bad_length_digit_is_unreadable() -> None:
    assert codec.unpack_frames('"abc') is None


def test_everything_encoded_stays_inside_the_wire_alphabet() -> None:
    truthy: bool = True
    emitted = "".join(
        (
            codec.IntConverter(int).encode(-(2**64)),
            codec.StrConverter().encode('🎈 weird " text \\ here'),
            codec.BoolConverter().encode(truthy),
            codec.EnumConverter(Mode, codec.StrConverter(), "es").encode(Mode.SLOW),
            codec.pack_frames(["abc", ""]),
        ),
    )

    assert all(char in wire.ALPHABET for char in emitted)
