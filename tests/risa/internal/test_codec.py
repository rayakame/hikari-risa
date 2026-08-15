from __future__ import annotations

import enum
import typing

import hikari
import linkd
import pytest

import risa
from risa.internal import codec
from risa.internal import constants
from risa.internal import wire

if typing.TYPE_CHECKING:
    import collections.abc

    class TypeTimeOnly: ...


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


def test_an_empty_int_frame_is_unreadable() -> None:
    assert codec.IntConverter(int).decode("") is None
    assert codec.IntConverter(hikari.Snowflake).decode("") is None


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


class Service: ...


class MixedValues(enum.Enum):
    NUMBER = 1
    WORD = "word"


class TruthyValues(enum.Enum):
    YES = True
    NO = False


class NoValues(enum.Enum): ...


class SnowflakeValues(enum.Enum):
    ALPHA = hikari.Snowflake(123)
    BETA = hikari.Snowflake(456)


class Handlers:
    async def zero(self, ctx: object) -> None: ...

    async def scalars(self, ctx: object, count: int, label: str, flag: bool, role: hikari.Snowflake) -> None: ...  # ruff:ignore[boolean-type-hint-positional-argument]

    async def trailing_default(self, ctx: object, role: hikari.Snowflake, add: bool = True) -> None: ...  # ruff:ignore[boolean-type-hint-positional-argument, boolean-default-value-positional-argument]

    async def enums(self, ctx: object, color: Color, mode: Mode) -> None: ...

    async def di_tail(self, ctx: object, item: int, service: Service) -> None: ...

    async def injected_tail(self, ctx: object, item: int, service: Service = linkd.INJECTED) -> None: ...

    async def union_arg(self, ctx: object, cursor: int | None) -> None: ...

    async def wire_after_di(self, ctx: object, service: Service, count: int) -> None: ...

    async def wire_after_injected(self, ctx: object, item: int = linkd.INJECTED, count: int = 3) -> None: ...

    async def keyword_only_wire(self, ctx: object, *, count: int) -> None: ...

    async def wire_after_var_args(self, ctx: object, *args: object, count: int) -> None: ...

    async def keyword_only_injected(self, ctx: object, *, count: int = linkd.INJECTED) -> None: ...

    async def keyword_only_service(self, ctx: object, *, service: Service) -> None: ...

    async def broken_enum_arg(self, ctx: object, choice: MixedValues) -> None: ...

    async def ghost(self, ctx: object, service: TypeTimeOnly) -> None: ...

    async def noted(self, ctx: object, option: typing.Annotated[str, "display name"]) -> None: ...


class FingerprintHandlers:
    async def named(self, ctx: object, option: str) -> None: ...

    async def renamed(self, ctx: object, choice: str) -> None: ...

    async def int_arg(self, ctx: object, target: int) -> None: ...

    async def snowflake_arg(self, ctx: object, target: hikari.Snowflake) -> None: ...

    async def pair(self, ctx: object, count: int, label: str) -> None: ...

    async def swapped_pair(self, ctx: object, label: str, count: int) -> None: ...


def test_each_scalar_annotation_maps_to_its_converter() -> None:
    assert isinstance(codec.resolve_converter(int), codec.IntConverter)
    assert isinstance(codec.resolve_converter(bool), codec.BoolConverter)
    assert isinstance(codec.resolve_converter(str), codec.StrConverter)
    assert isinstance(codec.resolve_converter(hikari.Snowflake), codec.IntConverter)


def test_a_resolved_int_annotation_round_trips_plain_ints() -> None:
    converter = codec.resolve_converter(int)

    assert converter is not None
    decoded = converter.decode(converter.encode(42))
    assert type(decoded) is int


def test_a_resolved_snowflake_annotation_produces_snowflakes() -> None:
    converter = codec.resolve_converter(hikari.Snowflake)

    assert converter is not None
    decoded = converter.decode(converter.encode(1364800923047857855))
    assert isinstance(decoded, hikari.Snowflake)


def test_enum_annotations_pick_their_inner_kind() -> None:
    int_valued = codec.resolve_converter(Color)
    str_valued = codec.resolve_converter(Mode)

    assert int_valued is not None
    assert int_valued.type_id == "ei"
    assert str_valued is not None
    assert str_valued.type_id == "es"


def test_a_snowflake_valued_enum_counts_as_int_valued() -> None:
    converter = codec.resolve_converter(SnowflakeValues)

    assert converter is not None
    assert converter.type_id == "ei"
    assert converter.decode(converter.encode(SnowflakeValues.ALPHA)) is SnowflakeValues.ALPHA


@pytest.mark.parametrize("annotation", [MixedValues, TruthyValues, NoValues])
def test_unwirable_enums_are_rejected(annotation: type[enum.Enum]) -> None:
    with pytest.raises(ValueError, match="all int or all str"):
        codec.resolve_converter(annotation)


@pytest.mark.parametrize("annotation", [int | None, str | None, list[int], Service, None, "int"])
def test_everything_else_is_not_a_wire_type(annotation: object) -> None:
    assert codec.resolve_converter(annotation) is None


def test_a_ctx_only_handler_has_an_empty_chain() -> None:
    signature = codec.resolve_signature(Handlers.zero)

    assert not signature.converters
    assert signature.required == 0


def test_the_wire_prefix_resolves_in_declaration_order() -> None:
    signature = codec.resolve_signature(Handlers.scalars)

    assert list(signature.converters) == ["count", "label", "flag", "role"]
    assert [converter.type_id for converter in signature.converters.values()] == ["i", "s", "b", "i"]
    assert signature.required == 4


def test_trailing_defaults_are_not_required() -> None:
    signature = codec.resolve_signature(Handlers.trailing_default)

    assert list(signature.converters) == ["role", "add"]
    assert [converter.type_id for converter in signature.converters.values()] == ["i", "b"]
    assert signature.required == 1


def test_enum_parameters_join_the_chain() -> None:
    signature = codec.resolve_signature(Handlers.enums)

    assert [converter.type_id for converter in signature.converters.values()] == ["ei", "es"]


@pytest.mark.parametrize("func", [Handlers.di_tail, Handlers.injected_tail])
def test_a_di_parameter_ends_the_wire_section(func: collections.abc.Callable[..., object]) -> None:
    signature = codec.resolve_signature(func)

    assert list(signature.converters) == ["item"]
    assert [converter.type_id for converter in signature.converters.values()] == ["i"]
    assert signature.required == 1


def test_a_union_is_di_not_wire() -> None:
    signature = codec.resolve_signature(Handlers.union_arg)

    assert not signature.converters


@pytest.mark.parametrize(
    "func",
    [
        Handlers.wire_after_di,
        Handlers.wire_after_injected,
        Handlers.keyword_only_wire,
        Handlers.wire_after_var_args,
    ],
)
def test_a_wire_type_after_the_section_fails_at_resolution(func: collections.abc.Callable[..., object]) -> None:
    with pytest.raises(risa.HandlerSignatureError) as exc_info:
        codec.resolve_signature(func)

    assert exc_info.value.parameter == "count"


@pytest.mark.parametrize("func", [Handlers.keyword_only_injected, Handlers.keyword_only_service])
def test_explicit_di_after_the_section_is_allowed(func: collections.abc.Callable[..., object]) -> None:
    assert not codec.resolve_signature(func).converters


def test_a_broken_enum_error_names_the_parameter() -> None:
    with pytest.raises(risa.HandlerSignatureError) as exc_info:
        codec.resolve_signature(Handlers.broken_enum_arg)

    assert exc_info.value.parameter == "choice"
    assert exc_info.value.callback_name == "Handlers.broken_enum_arg"


def test_an_annotated_wire_parameter_resolves_through_its_underlying_type() -> None:
    signature = codec.resolve_signature(Handlers.noted)

    assert [converter.type_id for converter in signature.converters.values()] == ["s"]


def test_a_type_checking_only_annotation_fails_with_the_import_hint() -> None:
    with pytest.raises(risa.HandlerSignatureError) as exc_info:
        codec.resolve_signature(Handlers.ghost)

    assert exc_info.value.parameter == "service"
    assert "TypeTimeOnly" in str(exc_info.value)
    assert "TYPE_CHECKING" in str(exc_info.value)


def test_the_fingerprint_is_two_wire_characters() -> None:
    fingerprint = codec.resolve_signature(Handlers.scalars).fingerprint

    assert len(fingerprint) == codec.FINGERPRINT_LENGTH
    assert all(char in wire.ALPHABET for char in fingerprint)


def test_renaming_a_parameter_keeps_the_fingerprint() -> None:
    named = codec.resolve_signature(FingerprintHandlers.named)
    renamed = codec.resolve_signature(FingerprintHandlers.renamed)

    assert named.fingerprint == renamed.fingerprint


def test_widening_int_to_snowflake_keeps_the_fingerprint() -> None:
    plain = codec.resolve_signature(FingerprintHandlers.int_arg)
    snowflake = codec.resolve_signature(FingerprintHandlers.snowflake_arg)

    assert plain.fingerprint == snowflake.fingerprint


def test_reordering_parameters_changes_the_fingerprint() -> None:
    pair = codec.resolve_signature(FingerprintHandlers.pair)
    swapped = codec.resolve_signature(FingerprintHandlers.swapped_pair)

    assert pair.fingerprint != swapped.fingerprint


def test_growing_the_chain_changes_the_fingerprint() -> None:
    short = codec.resolve_signature(FingerprintHandlers.int_arg)
    grown = codec.resolve_signature(Handlers.trailing_default)

    assert short.fingerprint != grown.fingerprint
