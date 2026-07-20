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
        risa.CustomIdOverflowError,
        risa.LayoutError,
        risa.LockTimeoutError,
        risa.SchemaMismatchError,
        risa.SerializationError,
        risa.StateConflictError,
        risa.StateError,
        risa.StateNotFoundError,
    ],
)
def test_every_error_inherits_from_risa_error(exc_type: type[Exception]) -> None:
    assert issubclass(exc_type, risa.RisaError)


def test_layout_error_exposes_structured_fields() -> None:
    error = risa.LayoutError("Container[0] > Section[2]", "expected 1-3 TextDisplay children, got 4")

    assert error.path == "Container[0] > Section[2]"
    assert error.reason == "expected 1-3 TextDisplay children, got 4"
    assert str(error) == "Container[0] > Section[2]: expected 1-3 TextDisplay children, got 4"


def test_custom_id_overflow_reports_the_offending_length() -> None:
    error = risa.CustomIdOverflowError("poll", 137)

    assert error.view_name == "poll"
    assert error.length == 137
    assert "137" in str(error)


def test_schema_mismatch_reports_both_versions() -> None:
    error = risa.SchemaMismatchError("poll", found_version=1, expected_version=2)

    assert error.found_version == 1
    assert error.expected_version == 2
