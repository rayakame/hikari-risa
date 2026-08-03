from __future__ import annotations

import pytest

import risa
from risa.internal import codec
from risa.internal import registry


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
    return registry.ViewMeta(cls=cls, name=name, version=version)


def test_the_key_is_the_view_cookie() -> None:
    meta = make_meta()
    assert meta.key == codec.make_cookie("registry-sample", 1)
    assert len(meta.key) == codec.COOKIE_LENGTH


def test_bumping_the_version_changes_the_key() -> None:
    assert make_meta(version=1).key != make_meta(version=2).key


def test_registering_and_resolving_round_trips() -> None:
    reg = registry.Registry()
    meta = make_meta()
    reg.register(meta)
    assert reg.get(meta.key) is meta


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
    assert reg.get(meta.key) is not None


def test_clear_drops_every_view() -> None:
    reg = registry.Registry()
    meta = make_meta()
    reg.register(meta)
    reg.clear()
    assert reg.get(meta.key) is None
