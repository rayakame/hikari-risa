# Copyright (c) 2025 Rayakame
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from __future__ import annotations

import enum
import typing

import hikari
import msgspec
import pytest

import risa
from risa.internal import anchor
from risa.internal import wire
from risa.state import schema as schema_

if typing.TYPE_CHECKING:
    from decimal import Decimal


class Opaque:
    __slots__ = ()


class HoldsSomethingOpaque(msgspec.Struct):
    thing: Opaque | None = None


class Priced(msgspec.Struct):
    amount: Decimal | None = None


class Node(msgspec.Struct):
    label: str = ""
    child: Node | None = None


class Poll(msgspec.Struct):
    question: str
    tally: dict[str, int] = {}
    rows: schema_.Prop[list[int]] = []


class AllProps(msgspec.Struct):
    rows: schema_.Prop[list[int]] = []


class Pair(msgspec.Struct):
    question: str
    author: str


class PairSwapped(msgspec.Struct):
    author: str
    question: str


class PairRenamed(msgspec.Struct):
    prompt: str
    author: str


class PairRetyped(msgspec.Struct):
    question: int
    author: str


class PairShortened(msgspec.Struct):
    question: str


class PairGrown(msgspec.Struct):
    question: str
    author: str
    page: int = 0


class PairGrownRequired(msgspec.Struct):
    question: str
    author: str
    page: int


class Row(msgspec.Struct):
    id: int
    name: str


class RowUnderAnotherName(msgspec.Struct):
    id: int
    name: str


class RowWithAnExtraField(msgspec.Struct):
    id: int
    name: str
    rank: int = 0


class Colour(enum.Enum):
    RED = 1
    BLUE = 2


class Flavour(enum.StrEnum):
    SWEET = "sweet"


class Wide(msgspec.Struct):
    number: int = 0
    text: str = ""
    flag: bool = False
    items: list[int] = []
    mapping: dict[str, int] = {}
    row: Row = msgspec.field(default_factory=lambda: Row(0, ""))
    colour: Colour = Colour.RED
    flavour: Flavour = Flavour.SWEET
    maybe: int | None = None


class Snowy(msgspec.Struct):
    guild: hikari.Snowflake = hikari.Snowflake(0)


class Inty(msgspec.Struct):
    guild: int = 0


def schema_of(cls: type[msgspec.Struct]) -> schema_.StateSchema:
    return schema_.StateSchema.of(cls, view_name="state-sample")


def names(schema: schema_.StateSchema) -> list[str]:
    return [field.name for field in schema.fields]


# ---- the field partition -------------------------------------------------


def test_plain_fields_are_durable_and_props_are_not() -> None:
    assert names(schema_of(Poll)) == ["question", "tally"]


def test_the_durable_fields_keep_the_order_they_were_declared_in() -> None:
    class Ordered(msgspec.Struct):
        third: int = 0
        first: int = 0
        second: int = 0

    assert names(schema_of(Ordered)) == ["third", "first", "second"]


def test_an_inherited_field_comes_before_the_ones_added_to_it() -> None:
    class Extended(Pair):
        page: int = 0

    assert names(schema_of(Extended)) == ["question", "author", "page"]


def test_whether_a_field_can_be_left_out_is_recorded() -> None:
    assert [field.has_default for field in schema_of(Poll).fields] == [False, True]


def test_a_view_of_nothing_but_props_has_no_state_to_place() -> None:
    assert schema_of(AllProps).durable is False
    assert schema_of(Poll).durable is True


def test_a_prop_marker_survives_postponed_annotations() -> None:
    assert "rows" not in names(schema_of(Poll))


# ---- declaration errors --------------------------------------------------


def test_a_prop_without_a_default_cannot_be_built_from_defaults() -> None:
    class Required(msgspec.Struct):
        rows: schema_.Prop[list[int]]

    with pytest.raises(risa.ViewDeclarationError) as exc_info:
        schema_of(Required)

    assert "'rows'" in str(exc_info.value)
    assert "risa.Prop marker if the field should persist" in str(exc_info.value)


def test_a_marker_buried_in_a_union_is_refused_rather_than_ignored() -> None:
    class Buried(msgspec.Struct):
        maybe: schema_.Prop[int] | None = None

    with pytest.raises(risa.ViewDeclarationError) as exc_info:
        schema_of(Buried)

    assert "risa.Prop[...] around the whole annotation" in str(exc_info.value)


def test_a_marker_buried_in_a_collection_is_refused_too() -> None:
    class Nested(msgspec.Struct):
        rows: list[schema_.Prop[int]] = []

    with pytest.raises(risa.ViewDeclarationError):
        schema_of(Nested)


def test_a_marker_with_no_type_to_carry_is_refused() -> None:
    class Bare(msgspec.Struct):
        rows: schema_.Prop = None  # type: ignore[assignment]

    with pytest.raises(risa.ViewDeclarationError) as exc_info:
        schema_of(Bare)

    assert "risa.Prop[<type>]" in str(exc_info.value)


def test_a_field_msgspec_cannot_write_is_refused_at_declaration() -> None:
    with pytest.raises(risa.ViewDeclarationError) as exc_info:
        schema_of(HoldsSomethingOpaque)

    assert "'thing'" in str(exc_info.value)
    assert "cannot serialize" in str(exc_info.value)


def test_an_annotation_that_only_exists_for_the_type_checker_is_refused() -> None:
    with pytest.raises(risa.ViewDeclarationError) as exc_info:
        schema_of(Priced)

    assert "Decimal" in str(exc_info.value)
    assert "TYPE_CHECKING" in str(exc_info.value)


def test_the_view_is_named_in_every_declaration_error() -> None:
    class Required(msgspec.Struct):
        rows: schema_.Prop[list[int]]

    with pytest.raises(risa.ViewDeclarationError) as exc_info:
        schema_.StateSchema.of(Required, view_name="leaderboard")

    assert exc_info.value.view_name == "leaderboard"


# ---- fingerprints and evolution ------------------------------------------


def test_a_fingerprint_fits_the_characters_the_anchor_reserves() -> None:
    assert len(schema_of(Pair).fingerprint) == anchor.SCHEMA_FP_LENGTH


def test_a_schema_accepts_the_shape_it_currently_has() -> None:
    schema = schema_of(Pair)

    assert schema.accepts(schema.fingerprint) is True


def test_appending_a_defaulted_field_leaves_older_state_readable() -> None:
    assert schema_of(PairGrown).accepts(schema_of(Pair).fingerprint) is True


def test_appending_a_required_field_retires_older_state() -> None:
    assert schema_of(PairGrownRequired).accepts(schema_of(Pair).fingerprint) is False


@pytest.mark.parametrize(
    "changed",
    [PairSwapped, PairRenamed, PairRetyped, PairShortened],
    ids=["reordered", "renamed", "retyped", "removed"],
)
def test_any_other_change_to_the_field_list_fails_closed(changed: type[msgspec.Struct]) -> None:
    assert schema_of(changed).accepts(schema_of(Pair).fingerprint) is False


def test_swapping_two_fields_of_one_type_is_caught_by_their_names() -> None:
    # The packed bytes are identical either way, so only the names can tell them apart.
    assert schema_of(Pair).fingerprint != schema_of(PairSwapped).fingerprint


def test_state_written_before_a_field_was_appended_still_decodes() -> None:
    before = schema_of(Pair)
    after = schema_of(PairGrown)

    assert after.unpack(before.fingerprint, before.pack(["Ship it?", "ray"])) == ("Ship it?", "ray")


def test_a_fingerprint_the_view_has_never_had_decodes_to_nothing() -> None:
    schema = schema_of(Pair)

    assert schema.unpack("zzz", schema.pack(["Ship it?", "ray"])) is None


def test_a_field_renamed_inside_a_nested_struct_is_not_a_class_rename() -> None:
    class Holder(msgspec.Struct):
        rows: list[Row] = []

    class HolderOfTheSameShape(msgspec.Struct):
        rows: list[RowUnderAnotherName] = []

    class HolderOfAGrownRow(msgspec.Struct):
        rows: list[RowWithAnExtraField] = []

    assert schema_of(Holder).fingerprint == schema_of(HolderOfTheSameShape).fingerprint
    assert schema_of(Holder).fingerprint != schema_of(HolderOfAGrownRow).fingerprint


def test_the_view_name_is_not_part_of_the_fingerprint() -> None:
    left = schema_.StateSchema.of(Pair, view_name="one")
    right = schema_.StateSchema.of(Pair, view_name="two")

    assert left.fingerprint == right.fingerprint


# ---- packing and unpacking -----------------------------------------------


def test_every_supported_field_type_survives_the_round_trip() -> None:
    schema = schema_of(Wide)
    values: list[object] = [7, "x", True, [1, 2], {"k": 3}, Row(1, "n"), Colour.BLUE, Flavour.SWEET, None]

    assert list(schema.unpack(schema.fingerprint, schema.pack(values)) or []) == values


def test_a_snowflake_field_comes_back_as_a_snowflake() -> None:
    schema = schema_of(Snowy)
    guild = hikari.Snowflake(1364800923047857855)

    unpacked = schema.unpack(schema.fingerprint, schema.pack([guild]))

    assert unpacked is not None
    assert unpacked[0] == guild
    assert isinstance(unpacked[0], hikari.Snowflake)


def test_widening_an_int_field_to_a_snowflake_costs_nothing() -> None:
    assert schema_of(Inty).fingerprint == schema_of(Snowy).fingerprint


def test_a_view_with_no_durable_fields_writes_nothing_at_all() -> None:
    assert not schema_of(AllProps).pack([])


def test_state_written_before_the_first_durable_field_still_decodes() -> None:
    class Grown(msgspec.Struct):
        page: int = 0

    empty = schema_of(AllProps).fingerprint

    assert schema_of(Grown).accepts(empty) is True
    assert schema_of(Grown).unpack(empty, "") == ()


def test_an_empty_shape_carrying_a_payload_is_refused() -> None:
    empty = schema_of(AllProps).fingerprint
    an_encoded_empty_array = wire.pack_bytes(msgspec.msgpack.encode(()))

    assert schema_of(AllProps).unpack(empty, an_encoded_empty_array) is None


@pytest.mark.parametrize("raw", ["", "!!!!!", "not base85 at all", "z"])
def test_a_forged_payload_decodes_to_nothing(raw: str) -> None:
    schema = schema_of(Pair)

    assert schema.unpack(schema.fingerprint, raw) is None


def test_a_truncated_payload_decodes_to_nothing() -> None:
    schema = schema_of(Pair)

    assert schema.unpack(schema.fingerprint, schema.pack(["Ship it?", "ray"])[:6]) is None


def test_a_payload_with_the_wrong_number_of_values_decodes_to_nothing() -> None:
    grown = schema_of(PairGrown)
    two_values = schema_of(Pair).pack(["Ship it?", "ray"])

    assert grown.unpack(grown.fingerprint, two_values) is None


def test_a_value_msgpack_cannot_carry_names_the_field_it_came_from() -> None:
    with pytest.raises(risa.StateEncodeError) as exc_info:
        schema_of(Inty).pack([2**64])

    assert exc_info.value.field == "guild"
    assert exc_info.value.view_name == "state-sample"


def test_a_value_of_the_wrong_type_names_the_field_it_came_from() -> None:
    with pytest.raises(risa.StateEncodeError) as exc_info:
        schema_of(Pair).pack(["Ship it?", object()])

    assert exc_info.value.field == "author"


# ---- reading and writing a view ------------------------------------------


def test_the_durable_values_are_read_off_the_view_in_field_order() -> None:
    assert list(schema_of(Poll).values(Poll(question="Ship it?", tally={"a": 1}))) == ["Ship it?", {"a": 1}]


def test_adopting_replaces_the_durable_fields_and_leaves_the_props_alone() -> None:
    schema = schema_of(Poll)
    mine = Poll(question="mine", tally={"a": 1}, rows=[1])
    theirs = Poll(question="theirs", tally={"b": 2}, rows=[2])

    schema.adopt(mine, theirs)

    assert mine.question == "theirs"
    assert mine.tally == {"b": 2}
    assert mine.rows == [1]


def test_a_view_re_encodes_to_exactly_what_it_was_built_from() -> None:
    schema = schema_of(Poll)
    poll = Poll(question="Ship it?", tally={"a": 1})

    assert schema.pack(schema.values(poll)) == schema.pack(schema.values(poll))


# ---- structural type ids -------------------------------------------------


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (int, "int"),
        (str, "str"),
        (bool, "bool"),
        (list[int], "list(int)"),
        (dict[str, int], "dict(str,int)"),
        (tuple[int, str], "tuple(int,str)"),
        (Row, "struct(id:int,name:str)"),
        (hikari.Snowflake, "int"),
    ],
)
def test_an_annotation_renders_as_the_shape_it_packs_to(annotation: object, expected: str) -> None:
    assert schema_.structural_type_id(annotation) == expected


def test_a_union_renders_the_same_whichever_order_it_is_written_in() -> None:
    assert schema_.structural_type_id(int | None) == schema_.structural_type_id(None | int)


def test_an_enum_renders_by_its_values_rather_than_its_member_names() -> None:
    class Renamed(enum.Enum):
        CRIMSON = 1
        AZURE = 2

    assert schema_.structural_type_id(Colour) == schema_.structural_type_id(Renamed)


def test_a_struct_that_contains_itself_renders_without_recursing_forever() -> None:
    assert "rec" in schema_.structural_type_id(Node)


def test_an_annotation_with_no_shape_to_render_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot describe"):
        schema_.structural_type_id(typing.Literal["a", 1])
