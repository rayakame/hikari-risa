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

import hikari
import pytest

import risa
from risa.internal import codec


class Fruit(enum.IntEnum):
    APPLE = 1
    BANANA = 2


class Mode(enum.StrEnum):
    FAST = "fast"
    SLOW = "slow"


def spec(name: str, annotation: type, *, required: bool = True) -> codec.ArgSpec:
    converter = codec.resolve_converter(annotation)
    assert converter is not None
    return codec.ArgSpec(name=name, converter=converter, required=required)


def signature(*specs: codec.ArgSpec) -> codec.HandlerSignature:
    fingerprint = codec.make_signature_fingerprint([s.converter.type_id for s in specs]) if specs else ""
    return codec.HandlerSignature(args=specs, fingerprint=fingerprint)


def roundtrip(sig: codec.HandlerSignature, *values: object) -> tuple[object, ...]:
    payload = codec.encode_args(values, sig, handler_id="h")
    return codec.decode_args(payload, sig, view_name="v", handler_id="h", version=1)


@pytest.mark.parametrize("value", [0, 1, -1, 127, 128, 255, -256, 2**62, -(2**62), 1234567890123456789])
def test_int_roundtrip(value: int) -> None:
    assert roundtrip(signature(spec("n", int)), value) == (value,)


def test_snowflake_roundtrip_reconstructs_the_annotation_type() -> None:
    (decoded,) = roundtrip(signature(spec("id", hikari.Snowflake)), hikari.Snowflake(123456789012345678))
    assert isinstance(decoded, hikari.Snowflake)
    assert decoded == hikari.Snowflake(123456789012345678)


def test_a_snowflake_encodes_far_below_its_decimal_width() -> None:
    payload = codec.encode_args([2**63 - 1], signature(spec("id", int)), handler_id="h")
    # Fingerprint, length prefix, and eight value characters -- not nineteen digits.
    assert len(payload) == codec.FINGERPRINT_LENGTH + 1 + 8


@pytest.mark.parametrize("value", ["", "a", "héllo wörld", "x" * 80])
def test_str_roundtrip(value: str) -> None:
    assert roundtrip(signature(spec("s", str)), value) == (value,)


@pytest.mark.parametrize("value", [True, False])
def test_bool_roundtrip(value: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
    assert roundtrip(signature(spec("b", bool)), value) == (value,)


@pytest.mark.parametrize("value", [Fruit.APPLE, Fruit.BANANA])
def test_int_enum_roundtrip(value: Fruit) -> None:
    assert roundtrip(signature(spec("f", Fruit)), value) == (value,)


@pytest.mark.parametrize("value", [Mode.FAST, Mode.SLOW])
def test_str_enum_roundtrip(value: Mode) -> None:
    assert roundtrip(signature(spec("m", Mode)), value) == (value,)


def test_multiple_args_roundtrip_in_order() -> None:
    sig = signature(spec("name", str), spec("count", int), spec("loud", bool))
    loud = True
    assert roundtrip(sig, "abc", 42, loud) == ("abc", 42, loud)


def test_a_stale_enum_value_fails_to_decode() -> None:
    sig = signature(spec("f", Fruit))
    payload = sig.fingerprint + chr(1) + "\x63"  # 99 is no Fruit.
    with pytest.raises(risa.SignatureMismatchError):
        codec.decode_args(payload, sig, view_name="v", handler_id="h", version=1)


def test_int_and_snowflake_share_a_type_id() -> None:
    int_spec = spec("a", int)
    snowflake_spec = spec("b", hikari.Snowflake)
    assert int_spec.converter.type_id == snowflake_spec.converter.type_id


def test_the_fingerprint_ignores_parameter_names() -> None:
    assert signature(spec("old_name", int)).fingerprint == signature(spec("new_name", int)).fingerprint


def test_the_fingerprint_reflects_types_and_order() -> None:
    assert signature(spec("a", int)).fingerprint != signature(spec("a", str)).fingerprint
    assert (
        signature(spec("a", int), spec("b", str)).fingerprint != signature(spec("a", str), spec("b", int)).fingerprint
    )


def test_unsupported_annotations_have_no_converter() -> None:
    assert codec.resolve_converter(float) is None
    assert codec.resolve_converter(int | None) is None
    assert codec.resolve_converter(None) is None


def test_a_defaulted_tail_may_be_omitted() -> None:
    sig = signature(spec("name", str), spec("count", int, required=False))
    assert roundtrip(sig, "abc") == ("abc",)


def test_omitting_a_required_arg_fails_at_encode() -> None:
    sig = signature(spec("name", str), spec("count", int))
    with pytest.raises(risa.ArgBindError):
        codec.encode_args(["abc"], sig, handler_id="h")


def test_encoding_more_values_than_parameters_fails() -> None:
    sig = signature(spec("name", str))
    with pytest.raises(risa.ArgBindError):
        codec.encode_args(["abc", "extra"], sig, handler_id="h")


def test_encoding_a_wrongly_typed_value_fails() -> None:
    sig = signature(spec("count", int))
    with pytest.raises(risa.ArgBindError):
        codec.encode_args(["not an int"], sig, handler_id="h")


def test_a_handler_without_wire_parameters_carries_no_payload() -> None:
    sig = signature()
    assert not codec.encode_args([], sig, handler_id="h")
    assert codec.decode_args("", sig, view_name="v", handler_id="h", version=1) == ()


def test_a_payload_on_an_argless_handler_is_a_mismatch() -> None:
    with pytest.raises(risa.SignatureMismatchError):
        codec.decode_args("xx", signature(), view_name="v", handler_id="h", version=1)


def test_an_empty_payload_on_an_argful_handler_is_a_mismatch() -> None:
    sig = signature(spec("count", int, required=False))
    with pytest.raises(risa.SignatureMismatchError):
        codec.decode_args("", sig, view_name="v", handler_id="h", version=1)


def test_a_changed_signature_is_caught_by_the_fingerprint() -> None:
    old = signature(spec("role_id", int))
    new = signature(spec("role_name", str))
    payload = codec.encode_args([123], old, handler_id="h")
    with pytest.raises(risa.SignatureMismatchError):
        codec.decode_args(payload, new, view_name="v", handler_id="h", version=1)


def test_a_truncated_frame_is_a_mismatch() -> None:
    sig = signature(spec("name", str))
    payload = sig.fingerprint + chr(5) + "ab"
    with pytest.raises(risa.SignatureMismatchError):
        codec.decode_args(payload, sig, view_name="v", handler_id="h", version=1)


def test_more_frames_than_parameters_is_a_mismatch() -> None:
    sig = signature(spec("name", str))
    payload = sig.fingerprint + chr(1) + "a" + chr(1) + "b"
    with pytest.raises(risa.SignatureMismatchError):
        codec.decode_args(payload, sig, view_name="v", handler_id="h", version=1)


def test_an_omitted_required_arg_on_the_wire_is_a_mismatch() -> None:
    lenient = signature(spec("name", str), spec("count", int, required=False))
    strict = signature(spec("name", str), spec("count", int))
    payload = codec.encode_args(["abc"], lenient, handler_id="h")
    with pytest.raises(risa.SignatureMismatchError):
        codec.decode_args(payload, strict, view_name="v", handler_id="h", version=1)
