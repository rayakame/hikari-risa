"""Probe: do dense (non-ASCII) custom_ids survive both read-back paths?

Posts one V2 message whose buttons carry a risa-shaped custom_id: an 11-char ASCII
header followed by 89 characters produced by a radixly codec. Then compares what
Discord returns on (A) ``rest.fetch_message`` and (B) ``interaction.message`` when
one of the buttons is clicked, byte for byte, and decodes the payload back.

Run from the repo root (reads TOKEN from testbot/.env or DISCORD_TOKEN):

    uv run --with radixly --group pytest python test_bots/dense_roundtrip.py

Then click any button in the posted message within the wait window.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import secrets
import sys
import time
import typing

import hikari

CHANNEL_ID = int(os.environ.get("TEST_CHANNEL_ID", "1266869428490993765"))
HEADER_LEN = 11
PAYLOAD_CHARS = 100 - HEADER_LEN
CLICK_WAIT = float(os.environ.get("CLICK_WAIT", "900"))
CODEC_NAMES = ("base32768", "uro14", "braille")


def _read_env_token() -> str | None:
    tok = os.environ.get("DISCORD_TOKEN")
    if tok:
        return tok
    try:
        with open("testbot/.env", encoding="utf-8") as fh:
            for line in fh:
                key, sep, value = line.strip().partition("=")
                if sep and key == "TOKEN":
                    return value.strip().strip('"').strip("'")
    except FileNotFoundError:
        return None
    return None


class Probe(typing.NamedTuple):
    name: str
    header: str
    payload: bytes
    custom_id: str


def _ascii_header() -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return "".join(secrets.choice(alphabet) for _ in range(HEADER_LEN))


class _B85:
    name = "b85-ascii"

    @staticmethod
    def max_bytes(chars: int) -> int:
        return chars // 5 * 4

    @staticmethod
    def encode(data: bytes) -> str:
        import base64
        return base64.b85encode(data).decode()

    @staticmethod
    def decode(text: str) -> bytes:
        import base64
        return base64.b85decode(text)


def _codec(name: str):
    return _B85 if name == "b85-ascii" else importlib.import_module(f"radixly.{name}")


def build_probes() -> list[Probe]:
    probes: list[Probe] = []
    for payload in (secrets.token_bytes(_B85.max_bytes(PAYLOAD_CHARS)),):
        text = _B85.encode(payload)
        header = _ascii_header()
        probes.append(Probe("b85-ascii", header, payload, header + text))
        print(f"  {'b85-ascii':<10} {len(payload):>3} bytes -> {len(text):>3} cp (control)")
    for name in CODEC_NAMES:
        try:
            codec = importlib.import_module(f"radixly.{name}")
        except ImportError:
            print(f"  {name:<10} not in this radixly release, skipped")
            continue
        payload = secrets.token_bytes(codec.max_bytes(PAYLOAD_CHARS))
        text = codec.encode(payload)
        assert len(text) <= PAYLOAD_CHARS, (name, len(text))
        assert codec.decode(text) == payload
        header = _ascii_header()
        probes.append(Probe(name, header, payload, header + text))
        print(
            f"  {name:<10} {len(payload):>3} bytes -> {len(text):>3} cp, "
            f"{len(text.encode()):>3} utf-8 bytes, {len(text.encode('utf-16-le')) // 2:>3} utf-16 units"
        )
    return probes


def collect_ids(components: typing.Sequence[hikari.PartialComponent]) -> dict[str, str]:
    """Map the ASCII header of every button found to the full custom_id Discord returned."""
    found: dict[str, str] = {}

    def walk(items: typing.Iterable[object]) -> None:
        for comp in items:
            cid = getattr(comp, "custom_id", None)
            if isinstance(cid, str) and len(cid) >= HEADER_LEN:
                found[cid[:HEADER_LEN]] = cid
            sub = getattr(comp, "components", None)
            if sub:
                walk(sub)
            acc = getattr(comp, "accessory", None)
            if acc is not None:
                walk([acc])

    walk(components)
    return found


def compare(label: str, probes: list[Probe], returned: dict[str, str]) -> bool:
    ok_all = True
    print(f"\n=== {label} ===")
    for p in probes:
        got = returned.get(p.header)
        codec = _codec(p.name)
        if got is None:
            print(f"  [MISSING] {p.name}")
            ok_all = False
            continue
        same = got == p.custom_id
        try:
            decoded = codec.decode(got[HEADER_LEN:]) == p.payload
        except ValueError as exc:
            decoded = False
            print(f"           decode error: {exc}")
        status = "OK " if same and decoded else "BAD"
        print(f"  [{status}] {p.name:<10} identical={same} decodes={decoded} len={len(got)}")
        if not same:
            diff = next((i for i, (a, b) in enumerate(zip(got, p.custom_id, strict=False)) if a != b), min(len(got), len(p.custom_id)))
            print(f"           first difference at index {diff}: sent U+{ord(p.custom_id[diff]):04X} got U+{ord(got[diff]):04X}" if diff < min(len(got), len(p.custom_id)) else f"           length differs: sent {len(p.custom_id)} got {len(got)}")
        ok_all = ok_all and same and decoded
    return ok_all


async def main() -> None:
    token = _read_env_token()
    if not token:
        sys.exit("no token: set DISCORD_TOKEN or put TOKEN=... into testbot/.env")

    print("codecs:")
    probes = build_probes()
    if not probes:
        sys.exit("radixly has none of the probed codecs")

    bot = hikari.GatewayBot(token, logs="WARNING", intents=hikari.Intents.NONE)
    clicked: asyncio.Future[hikari.ComponentInteraction] = asyncio.get_running_loop().create_future()
    finished = asyncio.Event()
    message_id: hikari.Snowflake | None = None

    @bot.listen()
    async def on_interaction(event: hikari.InteractionCreateEvent) -> None:
        inter = event.interaction
        if not isinstance(inter, hikari.ComponentInteraction) or inter.message.id != message_id:
            return
        if not clicked.done():
            clicked.set_result(inter)

    @bot.listen()
    async def on_started(_: hikari.StartedEvent) -> None:
        nonlocal message_id
        rest = bot.rest

        container = hikari.impl.ContainerComponentBuilder()
        container.add_text_display("### risa dense custom_id probe\nKlick irgendeinen Button.")
        row = hikari.impl.MessageActionRowBuilder()
        for p in probes:
            row.add_interactive_button(hikari.ButtonStyle.SECONDARY, p.custom_id, label=p.name)
        container.add_component(row)

        t0 = time.perf_counter()
        msg = await rest.create_message(CHANNEL_ID, components=[container])
        message_id = msg.id
        print(f"\nposted message {msg.id} in {(time.perf_counter() - t0) * 1000:.0f} ms")

        a_ok = compare("A: create_message response", probes, collect_ids(msg.components))
        fetched = await rest.fetch_message(CHANNEL_ID, msg.id)
        b_ok = compare("B: rest.fetch_message", probes, collect_ids(fetched.components))

        print(f"\nwaiting up to {CLICK_WAIT:.0f}s for a click ...", flush=True)
        try:
            inter = await asyncio.wait_for(asyncio.shield(clicked), CLICK_WAIT)
        except TimeoutError:
            print("no click received; interaction path NOT measured")
            c_ok = None
        else:
            c_ok = compare("C: interaction.message.components", probes, collect_ids(inter.message.components))
            sent = next(p for p in probes if p.header == inter.custom_id[:HEADER_LEN])
            d_ok = inter.custom_id == sent.custom_id
            print(f"\n=== D: interaction.custom_id of the clicked button ({sent.name}) ===\n  identical={d_ok}")
            c_ok = c_ok and d_ok
            await rest.create_interaction_response(
                inter.id,
                inter.token,
                hikari.ResponseType.MESSAGE_CREATE,
                f"A={a_ok} B={b_ok} C={c_ok}",
                flags=hikari.MessageFlag.EPHEMERAL,
            )

        await rest.delete_message(CHANNEL_ID, msg.id)
        print(f"\nSUMMARY  create={a_ok}  fetch={b_ok}  interaction={c_ok}")
        finished.set()

    await bot.start()
    await finished.wait()
    await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
