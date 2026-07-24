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
"""Constants shared across the library.

Limits imposed by Discord live here alongside the attribute names risa stamps
onto user classes, so that every module reads them from one place rather than
repeating magic values that must agree.
"""

from __future__ import annotations

import typing

__all__ = ("AUTODEFER_DELAY", "MAX_CUSTOM_ID_LENGTH", "REST_ACK_TIMEOUT", "VIEW_META")

# The hard limit on the length of a component's ``custom_id``.
MAX_CUSTOM_ID_LENGTH: typing.Final[int] = 100

# Attribute ``@risa.register`` stamps onto a class to hold its ``ViewMeta``.
VIEW_META: typing.Final[str] = "__risa_view_meta__"

# Seconds after decode before the auto-defer watchdog fires. Discord's deadline
# for an initial response is 3 seconds from the click; firing at 2 leaves a
# second of slack for the deferring call itself to reach Discord.
AUTODEFER_DELAY: typing.Final[float] = 2.0

# Seconds a REST listener waits for dispatch to acknowledge an interaction
# before giving up and answering the webhook empty-handed. Generous compared to
# Discord's own deadline: by this point the interaction is dead either way, and
# the timer only bounds how long the listener lingers.
REST_ACK_TIMEOUT: typing.Final[float] = 5.0
