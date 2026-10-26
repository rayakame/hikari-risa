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
from risa.internal import codec
from risa.internal import registry
from risa.state import placement
from risa.state import schema as schema_


class Sample(risa.View):
    pass


class Other(risa.View):
    pass


def make_meta(
    *,
    name: str = "registry-sample",
    version: int = 1,
    cls: type[risa.View] = Sample,
) -> registry.ViewMeta:
    return registry.ViewMeta(
        cls=cls,
        name=name,
        version=version,
        schema=schema_.StateSchema.of(cls, view_name=name),
        state=placement.DEFAULT_PLACEMENT,
    )


def test_the_key_is_the_view_cookie() -> None:
    meta = make_meta()
    assert meta.cookie == codec.make_cookie("registry-sample", 1)
    assert len(meta.cookie) == codec.COOKIE_LENGTH


def test_bumping_the_version_changes_the_key() -> None:
    assert make_meta(version=1).cookie != make_meta(version=2).cookie


def test_registering_and_resolving_round_trips() -> None:
    reg = registry.Registry()
    meta = make_meta()
    reg.register(meta)
    assert reg.get(meta.cookie) is meta


def test_an_unknown_cookie_resolves_to_nothing() -> None:
    assert registry.Registry().get(codec.make_cookie("registry-ghost", 1)) is None


def test_two_classes_cannot_share_a_cookie() -> None:
    reg = registry.Registry()
    reg.register(make_meta(cls=Sample))
    with pytest.raises(risa.DuplicateViewError):
        reg.register(make_meta(cls=Other))


def test_the_same_class_twice_is_not_a_collision() -> None:
    reg = registry.Registry()
    meta = make_meta()
    reg.register(meta)
    reg.register(make_meta())
    assert reg.get(meta.cookie) is not None


def test_clear_drops_every_view() -> None:
    reg = registry.Registry()
    meta = make_meta()
    reg.register(meta)
    reg.clear()
    assert reg.get(meta.cookie) is None


def test_an_unregistered_subclass_does_not_inherit_its_parents_meta() -> None:
    @risa.register(name="registry-parent")
    class Parent(risa.View):
        pass

    class Child(Parent):
        pass

    assert registry.meta_of(Parent) is not None
    assert registry.meta_of(Child) is None
    with pytest.raises(risa.NotAViewError):
        registry.require_meta(Child)
