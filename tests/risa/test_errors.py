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

import pytest

import risa


@pytest.mark.parametrize(
    "exc_type",
    [
        risa.ArgBindError,
        risa.ClickRejectedError,
        risa.CustomIdOverflowError,
        risa.EphemeralOriginError,
        risa.HandlerSignatureError,
        risa.LayoutError,
        risa.LockTimeoutError,
        risa.NotAHandlerError,
        risa.NoViewError,
        risa.SerializationError,
        risa.StateConflictError,
        risa.StateError,
        risa.StateNotFoundError,
        risa.StoreConformanceError,
    ],
)
def test_every_error_inherits_from_risa_error(exc_type: type[Exception]) -> None:
    assert issubclass(exc_type, risa.RisaError)


def test_layout_error_exposes_structured_fields() -> None:
    error = risa.LayoutError("Container[0] > Section[2]", "expected 1-3 TextDisplay children, got 4")

    assert error.path == "Container[0] > Section[2]"
    assert error.reason == "expected 1-3 TextDisplay children, got 4"
    assert str(error) == "Container[0] > Section[2]: expected 1-3 TextDisplay children, got 4"


def test_arg_bind_error_names_the_parameter_when_it_has_one() -> None:
    error = risa.ArgBindError("PollView.vote", "count", "is required but was not supplied")

    assert error.callback_name == "PollView.vote"
    assert error.parameter == "count"
    assert error.reason == "is required but was not supplied"
    assert str(error) == "handler 'PollView.vote': parameter 'count' is required but was not supplied"


def test_arg_bind_error_reads_cleanly_without_a_parameter() -> None:
    error = risa.ArgBindError("PollView.vote", None, "received 4 wire arguments, but the handler declares 2")

    assert error.parameter is None
    assert str(error) == "handler 'PollView.vote': received 4 wire arguments, but the handler declares 2"


def test_custom_id_overflow_reports_the_offending_length() -> None:
    error = risa.CustomIdOverflowError("poll", 137)

    assert error.view_name == "poll"
    assert error.length == 137
    assert "137" in str(error)


def test_state_overflow_lists_each_field_largest_first_then_the_framing() -> None:
    error = risa.StateOverflowError(
        "poll",
        needed=200,
        available=198,
        slots=3,
        codec_name="base85",
        fields={"question": 12, "counts": 4, "voters": 174},
    )

    lines = str(error).splitlines()
    assert lines[0] == (
        "view 'poll' needs 200 bytes of durable state, but its 3 interactive components"
        " carry only 198 with codec 'base85':"
    )
    assert lines[1:5] == [
        "    voters    174 bytes",
        "    question   12 bytes",
        "    counts      4 bytes",
        "    framing    10 bytes",
    ]
    assert lines[5].startswith("Make room by marking per-dispatch fields risa.Prop[...]")
    assert "risa.InStore" in lines[5]
    assert len(lines) == 6
    assert list(error.fields) == ["question", "counts", "voters"]


def test_state_overflow_breakdown_says_byte_for_a_size_of_one() -> None:
    error = risa.StateOverflowError(
        "poll", needed=3, available=2, slots=1, codec_name="base85", fields={"yes": 1, "no": 0}
    )

    assert "    yes      1 byte\n" in str(error)
    assert "    no       0 bytes\n" in str(error)


def test_state_overflow_on_one_component_reads_in_the_singular() -> None:
    error = risa.StateOverflowError("poll", needed=90, available=70, slots=1, codec_name="base85", fields={"a": 80})

    assert "its 1 interactive component carries only 70" in str(error)


def test_state_overflow_without_a_breakdown_is_one_sentence_and_its_remedy() -> None:
    error = risa.StateOverflowError("poll", needed=200, available=198, slots=3, codec_name="base85")

    lines = str(error).splitlines()
    assert error.fields == {}
    assert lines[0] == (
        "view 'poll' needs 200 bytes of durable state, but its 3 interactive components"
        " carry only 198 with codec 'base85'."
    )
    assert lines[1].startswith("Make room by")
    assert len(lines) == 2


def test_not_a_handler_names_the_offending_type() -> None:
    error = risa.NotAHandlerError("str")

    assert error.type_name == "str"
    assert "str" in str(error)
    assert "risa.bind" in str(error)


def test_store_conformance_lists_every_failed_check() -> None:
    error = risa.StoreConformanceError(
        [
            "a_stale_cas_is_refused: accepted expected=1 after the version moved to 2",
            "touch_ignores_a_missing_key: touch created an entry",
        ]
    )

    assert error.failures == [
        "a_stale_cas_is_refused: accepted expected=1 after the version moved to 2",
        "touch_ignores_a_missing_key: touch created an entry",
    ]
    assert "2 conformance checks" in str(error)
    assert "a_stale_cas_is_refused" in str(error)
    assert "touch_ignores_a_missing_key" in str(error)


@pytest.mark.parametrize(
    "exc_type",
    [
        risa.BotMissingPermissionsError,
        risa.ComponentExpiredError,
        risa.MaxConcurrencyReachedError,
        risa.MissingPermissionsError,
        risa.MissingRolesError,
        risa.NotInvokerError,
        risa.NotOwnerError,
        risa.OnCooldownError,
    ],
)
def test_every_rejection_of_a_ready_made_hook_is_a_click_rejected_error(exc_type: type[Exception]) -> None:
    assert issubclass(exc_type, risa.ClickRejectedError)
