# Copyright (c) 2025-present Rayakame
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
"""The interaction a handler is invoked for."""

from __future__ import annotations

import abc
import typing

import hikari

if typing.TYPE_CHECKING:
    import collections.abc

    from risa.client import Client

__all__ = ("ComponentContext", "Context", "InteractionT", "ModalContext")

type InteractionT = hikari.ComponentInteraction | hikari.ModalInteraction
"""An interaction risa dispatches a handler for."""


class Context[T: InteractionT](abc.ABC):
    """What a handler is given about the interaction it is answering.

    Holds only what every interaction shares. Subclassed per kind, because what
    may be done with one differs: a component may respond with a modal while a
    modal may not, and a component always has a message behind it while a modal
    need not. The generic parameter is what gives each subclass a precisely typed
    :attr:`interaction` without restating it.

    Parameters
    ----------
    client
        The client that dispatched the interaction.
    interaction
        The interaction being answered.
    """

    __slots__ = ("_client", "_interaction")

    def __init__(self, client: Client, interaction: T) -> None:
        self._client = client
        self._interaction = interaction

    @property
    def client(self) -> Client:
        """The client that dispatched this interaction."""
        return self._client

    @property
    def interaction(self) -> T:
        """The interaction being answered."""
        return self._interaction

    @property
    def custom_id(self) -> str:
        """The identifier the interaction routed under."""
        return self._interaction.custom_id

    @property
    def user(self) -> hikari.User:
        """The user who triggered the interaction."""
        return self._interaction.user

    @property
    def member(self) -> hikari.InteractionMember | None:
        """The guild member who triggered the interaction, outside a DM."""
        return self._interaction.member

    @property
    def channel(self) -> hikari.InteractionChannel:
        """The channel the interaction was invoked in."""
        return self._interaction.channel

    @property
    def channel_id(self) -> hikari.Snowflake:
        """The channel the interaction was invoked in."""
        return self._interaction.channel_id

    @property
    def guild_id(self) -> hikari.Snowflake | None:
        """The guild the interaction was invoked in, or ``None`` in a DM."""
        return self._interaction.guild_id

    @property
    def app_permissions(self) -> hikari.Permissions | None:
        """What the bot is permitted to do in the channel, or ``None`` in a DM."""
        return self._interaction.app_permissions

    @property
    def locale(self) -> str | hikari.Locale:
        """The locale of the user who triggered the interaction."""
        return self._interaction.locale

    @property
    def guild_locale(self) -> str | hikari.Locale | None:
        """The guild's preferred locale, or ``None`` in a DM."""
        return self._interaction.guild_locale

    @property
    @abc.abstractmethod
    def message(self) -> hikari.Message | None:
        """The message this interaction came from, if there is one.

        Declared here so every context carries it, and narrowed by subclasses
        that always have one.
        """


class ComponentContext(Context[hikari.ComponentInteraction]):
    """The interaction a component handler is answering.

    A component always belongs to a message, so :attr:`message` is always there,
    and a component is the one interaction Discord permits to open a modal.
    """

    __slots__ = ()

    @property
    @typing.override
    def message(self) -> hikari.Message:
        """The message the component is attached to."""
        return self._interaction.message

    @property
    def values(self) -> collections.abc.Sequence[str]:
        """What was selected, for a select menu.

        Empty for a button. A menu of users, roles, mentionables or channels
        yields snowflakes as strings; the objects behind them are in
        :attr:`resolved`.
        """
        return self._interaction.values

    @property
    def resolved(self) -> hikari.ResolvedOptionData | None:
        """The objects behind the selected snowflakes, if any were resolved."""
        return self._interaction.resolved


class ModalContext(Context[hikari.ModalInteraction]):
    """The interaction a modal handler is answering.

    A modal opened from a component belongs to that component's message, but one
    opened from a command does not, which is why :attr:`message` is optional.
    """

    __slots__ = ()

    @property
    @typing.override
    def message(self) -> hikari.Message | None:
        """The message whose component opened the modal, if one did."""
        return self._interaction.message

    @property
    def components(self) -> collections.abc.Sequence[hikari.ModalActionRowComponent]:
        """The submitted rows, as Discord returned them."""
        return self._interaction.components

    @property
    def values(self) -> collections.abc.Mapping[str, str]:
        """What was submitted, keyed by the custom id of each text input."""
        return {component.custom_id: component.value for row in self._interaction.components for component in row}
