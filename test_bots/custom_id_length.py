"""
custom_id_probe.py -- empirisch messen, wie Discord `custom_id` validiert.

Beantwortet drei Fragen, die in den API-Docs nicht dokumentiert sind:

  1. LAENGE   Zaehlt das 100er-Limit UTF-8 Bytes, Unicode Code Points
              oder UTF-16 Code Units?
  2. FIDELITY Kommt der custom_id byte-identisch zurueck, oder normalisiert /
              trimmt / strippt Discord irgendwas? (kritisch fuer einen
              Component-Handler, der Daten in die ID serialisiert)
  3. ZEICHEN  Welche Zeichen sind ueberhaupt erlaubt -> welche Delimiter
              kann man gefahrlos benutzen?

Nutzung:
    export DISCORD_TOKEN="..."
    export TEST_CHANNEL_ID="123456789012345678"
    python custom_id_probe.py            # voller Lauf
    python custom_id_probe.py --dry-run  # nur Testplan + lokale Metriken

Der Bot braucht View Channel + Send Messages + Manage Messages im Testkanal
(Manage Messages nur zum Aufraeumen). Nimm einen leeren Kanal -- das Script
erzeugt und loescht viele Nachrichten.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unicodedata
from dataclasses import dataclass, field

import hikari

# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------

TOKEN = "TOKEN_REDACTED"
CHANNEL_ID = 1266869428490993765

SEARCH_HI = 220          # obere Suchgrenze fuer die Binaersuche
REQUEST_DELAY = 0.35     # zusaetzliche Pause; hikari macht Ratelimits selbst
DRY_RUN = "--dry-run" in sys.argv

DOC_LIMIT = 100          # was die Docs behaupten


# --------------------------------------------------------------------------
# Metriken
# --------------------------------------------------------------------------

def n_codepoints(s: str) -> int:
    return len(s)


def n_utf16(s: str) -> int:
    """UTF-16 Code Units -- das, was JS `.length` liefert."""
    return len(s.encode("utf-16-le")) // 2


def n_utf8(s: str) -> int:
    return len(s.encode("utf-8"))


def n_graphemes_approx(s: str) -> int:
    """Grobe Naeherung ohne externe Lib: Combining Marks + ZWJ-Folgen zaehlen
    nicht als eigene Graphemes. Reicht, um Grapheme-Counting auszuschliessen."""
    count = 0
    skip_next = False
    for i, ch in enumerate(s):
        if skip_next:
            skip_next = False
            continue
        if unicodedata.combining(ch):
            continue
        if ch == "\u200d":  # ZWJ verbindet mit dem naechsten Zeichen
            skip_next = True
            continue
        if "\U0001F3FB" <= ch <= "\U0001F3FF":  # Skin-Tone-Modifier
            continue
        # Regional-Indicator-Paare (Flaggen) bilden ein Grapheme
        if "\U0001F1E6" <= ch <= "\U0001F1FF":
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if "\U0001F1E6" <= nxt <= "\U0001F1FF":
                skip_next = True
        count += 1
    return count


METRICS = {
    "utf8_bytes": n_utf8,
    "codepoints": n_codepoints,
    "utf16_units": n_utf16,
    "graphemes~": n_graphemes_approx,
}


# --------------------------------------------------------------------------
# Testfaelle
# --------------------------------------------------------------------------

@dataclass
class LengthCase:
    """Ein wiederholbares Zeichen-Pattern fuer die Binaersuche."""
    name: str
    unit: str
    note: str = ""

    def build(self, n: int) -> str:
        return self.unit * n


# Die Auswahl ist so gebaut, dass die vier Metriken maximal auseinanderlaufen.
# Nur so kann man sie hinterher unterscheiden.
LENGTH_CASES = [
    LengthCase("ascii",       "a",
               "Baseline: alle Metriken identisch (1/1/1/1)"),
    LengthCase("latin1_nfc",  "\u00e9",
               "e-acute precomposed -- 2 Bytes, 1 CP, 1 UTF-16"),
    LengthCase("latin1_nfd",  "e\u0301",
               "e + Combining Acute -- 3 Bytes, 2 CP, 2 UTF-16, 1 Grapheme"),
    LengthCase("bmp_3byte",   "\u6f22",
               "CJK -- 3 Bytes, 1 CP, 1 UTF-16 (trennt Bytes von CP)"),
    LengthCase("astral",      "\U0001F600",
               "Emoji U+1F600 -- 4 Bytes, 1 CP, 2 UTF-16 (KEY DISCRIMINATOR)"),
    LengthCase("astral_math",  "\U0001D518",
               "Math script U+1D518 -- wie astral, aber kein Emoji"),
    LengthCase("skin_tone",   "\U0001F44B\U0001F3FD",
               "Base + Modifier -- 8 Bytes, 2 CP, 4 UTF-16, 1 Grapheme"),
    LengthCase("flag",        "\U0001F1E9\U0001F1EA",
               "Regional Indicators -- 8 Bytes, 2 CP, 4 UTF-16, 1 Grapheme"),
    LengthCase("zwj_family",  "\U0001F468\u200d\U0001F469\u200d\U0001F467\u200d\U0001F466",
               "ZWJ-Sequenz -- 25 Bytes, 7 CP, 11 UTF-16, 1 Grapheme"),
    LengthCase("zwsp",        "\u200b",
               "Zero Width Space -- wird er gezaehlt? gestrippt?"),
]


# Zeichen-Vertraeglichkeit: wird die ID akzeptiert, und kommt sie heil zurueck?
# Relevant fuer die Delimiter-Wahl im Component-Handler.
CHARSET_CASES: list[tuple[str, str]] = [
    ("empty",            ""),
    ("single_char",      "x"),
    ("space_inner",      "a b"),
    ("space_leading",    " ab"),
    ("space_trailing",   "ab "),
    ("newline",          "a\nb"),
    ("tab",              "a\tb"),
    ("nul",              "a\x00b"),
    ("colon",            "a:b"),
    ("pipe",             "a|b"),
    ("slash",            "a/b"),
    ("backslash",        "a\\b"),
    ("dquote",           'a"b'),
    ("squote",           "a'b"),
    ("backtick",         "a`b"),
    ("unit_sep",         "a\x1fb"),
    ("json_braces",      '{"a":1}'),
    ("percent",          "a%b"),
    ("plus",             "a+b"),
    ("z85_alphabet",     ".-:+=^!/*?&<>()[]{}@%$#"),
    ("nfd_roundtrip",    "cafe\u0301"),
    ("nfc_roundtrip",    "caf\u00e9"),
    ("zwsp_roundtrip",   "a\u200bb"),
    ("emoji_roundtrip",  "a\U0001F600b"),
    ("upper_lower",      "AbCdEf"),
]


# --------------------------------------------------------------------------
# Ergebnis-Container
# --------------------------------------------------------------------------

@dataclass
class LengthResult:
    case: LengthCase
    max_ok: int = 0            # groesste akzeptierte Wiederholungszahl
    first_bad: int | None = None
    error_codes: set[str] = field(default_factory=set)

    @property
    def ok_string(self) -> str:
        return self.case.build(self.max_ok)

    @property
    def bad_string(self) -> str:
        return self.case.build(self.first_bad) if self.first_bad else ""


@dataclass
class CharsetResult:
    name: str
    sent: str
    accepted: bool
    returned: str | None = None
    detail: str = ""

    @property
    def identical(self) -> bool | None:
        if not self.accepted or self.returned is None:
            return None
        return self.returned == self.sent


# --------------------------------------------------------------------------
# Discord-Interaktion
# --------------------------------------------------------------------------

class Probe:
    def __init__(self, rest: hikari.api.RESTClient, channel: int) -> None:
        self.rest = rest
        self.channel = channel
        self.sent = 0

    async def _cleanup(self, message: hikari.Message) -> None:
        try:
            await self.rest.delete_message(self.channel, message.id)
        except hikari.HikariError:
            pass

    async def try_ids(self, custom_ids: list[str]) -> tuple[bool, str, list[str]]:
        """Schickt bis zu 25 Buttons in einer Nachricht.

        Returns (accepted, detail, returned_ids). returned_ids ist das, was
        Discord tatsaechlich gespeichert hat -- per fetch_message zurueckgelesen,
        nicht aus der Create-Response.
        """
        rows: list[hikari.api.MessageActionRowBuilder] = []
        for i, cid in enumerate(custom_ids):
            if i % 5 == 0:
                rows.append(self.rest.build_message_action_row())
            rows[-1].add_interactive_button(
                hikari.ButtonStyle.SECONDARY, cid, label=f"b{i}"
            )

        self.sent += 1
        await asyncio.sleep(REQUEST_DELAY)

        try:
            msg = await self.rest.create_message(
                self.channel, "probe", components=rows
            )
        except hikari.BadRequestError as exc:
            codes = _extract_error_codes(exc)
            return False, codes or f"400: {exc.message}", []
        except hikari.HikariError as exc:
            return False, f"{type(exc).__name__}: {exc}", []

        try:
            fetched = await self.rest.fetch_message(self.channel, msg.id)
            returned = [b.custom_id or "" for b in _iter_buttons(fetched.components)]
        except hikari.HikariError as exc:
            returned = []
            return True, f"created, refetch failed: {exc}", returned
        finally:
            await self._cleanup(msg)

        return True, "ok", returned


def _iter_buttons(components):
    """Component-Baum rekursiv nach Buttons abklappern (V1- und V2-Layouts)."""
    for comp in components:
        if isinstance(comp, hikari.ButtonComponent):
            yield comp
        else:
            sub = getattr(comp, "components", None)
            if sub:
                yield from _iter_buttons(sub)


def _extract_error_codes(exc: hikari.BadRequestError) -> str:
    """Discord liefert im 400 pro Feld einen Code wie BASE_TYPE_MAX_LENGTH.

    Genau der unterscheidet ein Laengen-Reject von jedem anderen Fehler --
    ohne den weiss man nicht, ob man das Limit oder was ganz anderes trifft.
    """
    codes: list[str] = []

    def walk(node, path=""):
        if isinstance(node, dict):
            if "_errors" in node:
                for e in node["_errors"]:
                    codes.append(f"{path}={e.get('code', '?')}")
            for k, v in node.items():
                if k != "_errors":
                    walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(getattr(exc, "errors", None) or {})
    return " ".join(codes)


# --------------------------------------------------------------------------
# Phase 1 -- Binaersuche pro Zeichenklasse
# --------------------------------------------------------------------------

async def phase_length(probe: Probe) -> list[LengthResult]:
    print("\n=== PHASE 1: Laengen-Limit pro Zeichenklasse ===\n")
    results: list[LengthResult] = []

    for case in LENGTH_CASES:
        res = LengthResult(case=case)

        ok, detail, _ = await probe.try_ids([case.build(1)])
        if not ok:
            print(f"  {case.name:<14} n=1 schon abgelehnt ({detail}) -- skip")
            res.error_codes.add(detail)
            results.append(res)
            continue

        lo, hi = 1, SEARCH_HI
        ok_hi, detail_hi, _ = await probe.try_ids([case.build(hi)])
        if ok_hi:
            print(f"  {case.name:<14} auch bei n={hi} akzeptiert -- kein Limit?!")
            res.max_ok = hi
            results.append(res)
            continue

        # Invariante: lo akzeptiert, hi abgelehnt
        while hi - lo > 1:
            mid = (lo + hi) // 2
            accepted, detail, _ = await probe.try_ids([case.build(mid)])
            if accepted:
                lo = mid
            else:
                hi = mid
                res.error_codes.add(detail)

        res.max_ok, res.first_bad = lo, hi
        s_ok = res.ok_string
        print(
            f"  {case.name:<14} max n={lo:<4} -> "
            f"bytes={n_utf8(s_ok):<4} cp={n_codepoints(s_ok):<4} "
            f"utf16={n_utf16(s_ok):<4} graph~={n_graphemes_approx(s_ok):<4} "
            f"| {case.note}"
        )
        results.append(res)

    return results


# --------------------------------------------------------------------------
# Phase 2 -- Zeichen-Vertraeglichkeit + Round-Trip
# --------------------------------------------------------------------------

async def phase_charset(probe: Probe) -> list[CharsetResult]:
    print("\n=== PHASE 2: Erlaubte Zeichen + Round-Trip-Treue ===\n")
    results: list[CharsetResult] = []

    for name, cid in CHARSET_CASES:
        accepted, detail, returned = await probe.try_ids([cid])
        got = returned[0] if returned else None
        r = CharsetResult(name, cid, accepted, got, detail)
        results.append(r)

        status = "OK " if accepted else "REJ"
        if accepted and r.identical is False:
            status = "MOD"  # akzeptiert, aber veraendert zurueckgekommen
        extra = ""
        if r.identical is False:
            extra = f"  sent={cid!r} -> got={got!r}"
        elif not accepted:
            extra = f"  {detail}"
        print(f"  [{status}] {name:<18}{extra}")

    return results


# --------------------------------------------------------------------------
# Phase 3 -- Auswertung
# --------------------------------------------------------------------------

def analyse(results: list[LengthResult]) -> None:
    print("\n=== PHASE 3: Welche Metrik erklaert alle Ergebnisse? ===\n")

    usable = [r for r in results if r.first_bad is not None]
    if not usable:
        print("  Keine verwertbaren Ergebnisse.")
        return

    for metric_name, fn in METRICS.items():
        lower, upper = 0, 10**9
        for r in usable:
            # akzeptiert -> metrik <= L ; abgelehnt -> metrik > L
            lower = max(lower, fn(r.ok_string))
            upper = min(upper, fn(r.bad_string) - 1)

        if lower <= upper:
            span = f"{lower}..{upper}" if lower != upper else str(lower)
            verdict = "KONSISTENT"
            if not (lower <= DOC_LIMIT <= upper):
                verdict += f" (aber schliesst {DOC_LIMIT} aus!)"
        else:
            span = f"leer ({lower} > {upper})"
            verdict = "WIDERSPRUECHLICH -> ausgeschlossen"

        print(f"  {metric_name:<14} L in {span:<14} {verdict}")

    print(
        "\n  Lesart: eine Metrik ist nur dann Discords Zaehlweise, wenn sie"
        "\n  ueber ALLE Zeichenklassen dasselbe Limit ergibt. Der Astral-Fall"
        "\n  (Emoji: 1 CP vs 2 UTF-16) trennt Code Points von UTF-16 Units,"
        "\n  der CJK-Fall trennt Code Points von Bytes."
    )


def budget(results: list[LengthResult]) -> None:
    ascii_res = next((r for r in results if r.case.name == "ascii"), None)
    if not ascii_res or not ascii_res.max_ok:
        return
    n = ascii_res.max_ok
    print("\n=== Praktisches Budget fuer den Component-Handler ===\n")
    print(f"  Reines ASCII: {n} Zeichen == {n} Bytes (alle Metriken fallen zusammen)")
    print(f"    Base64url  -> {n // 4 * 3} Bytes Payload")
    print(f"    Base85/Z85 -> {n // 5 * 4} Bytes Payload")
    print("\n  Empfehlung: ID ASCII-only halten. Dann ist die ganze Encoding-Frage")
    print("  irrelevant und du hast ein hartes, vorhersagbares Byte-Budget.")


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------

def dry_run() -> None:
    print("=== Testplan (dry run, keine API-Calls) ===\n")
    print(f"{'case':<14}{'bytes':>6}{'cp':>5}{'utf16':>7}{'graph~':>8}  note")
    for c in LENGTH_CASES:
        u = c.unit
        print(
            f"{c.name:<14}{n_utf8(u):>6}{n_codepoints(u):>5}"
            f"{n_utf16(u):>7}{n_graphemes_approx(u):>8}  {c.note}"
        )
    print(f"\n{len(CHARSET_CASES)} Zeichen-/Round-Trip-Faelle.")
    est = len(LENGTH_CASES) * 10 + len(CHARSET_CASES)
    print(f"Geschaetzt ~{est} Nachrichten (jeweils erstellt, gelesen, geloescht).")


# --------------------------------------------------------------------------

async def main() -> None:
    if DRY_RUN:
        dry_run()
        return

    if not TOKEN or not CHANNEL_ID:
        sys.exit("Bitte DISCORD_TOKEN und TEST_CHANNEL_ID setzen.")

    app = hikari.RESTApp()
    await app.start()
    try:
        async with app.acquire(TOKEN, hikari.TokenType.BOT) as rest:
            probe = Probe(rest, CHANNEL_ID)
            length_results = await phase_length(probe)
            await phase_charset(probe)
            analyse(length_results)
            budget(length_results)
            print(f"\n{probe.sent} Requests gesendet.")
    finally:
        await app.close()


if __name__ == "__main__":
    asyncio.run(main())