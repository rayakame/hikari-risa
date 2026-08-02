"""uro14 -- Bytes <-> CJK-URO, 14 Bit pro Zeichen.

Nutzt den Block CJK Unified Ideographs (U+4E00..U+9FFF) als Ziffernvorrat.
Bei Discords Limit von 100 Code Points ergibt das 175 Bytes Payload,
gegenueber 80 bei Base85 und 75 bei Base64url.

Layout: das erste Zeichen kodiert die Payload-Laenge in Bytes (0..16383),
danach folgt der Bitstream in 14-Bit-Bloecken, am Ende mit Nullen aufgefuellt.
Das Laengenpraefix macht das Padding eindeutig -- ohne das kann man nicht
unterscheiden, ob die letzten Nullbits Daten oder Fuellung sind.
"""

from __future__ import annotations

BASE = 0x4E00           # Blockanfang
BITS = 14
MASK = (1 << BITS) - 1  # 16383
BLOCK_END = 0x9FFF


def encode(data: bytes) -> str:
    if len(data) > MASK:
        raise ValueError(f"payload zu gross: {len(data)} > {MASK}")

    out = [chr(BASE + len(data))]
    acc = 0
    nbits = 0
    for byte in data:
        acc = (acc << 8) | byte
        nbits += 8
        while nbits >= BITS:
            nbits -= BITS
            out.append(chr(BASE + ((acc >> nbits) & MASK)))
    if nbits:
        out.append(chr(BASE + ((acc << (BITS - nbits)) & MASK)))
    return "".join(out)


def decode(text: str) -> bytes:
    if not text:
        raise ValueError("leerer input")

    vals = []
    for ch in text:
        cp = ord(ch)
        if not (BASE <= cp <= BLOCK_END):
            raise ValueError(f"zeichen ausserhalb des blocks: U+{cp:04X}")
        vals.append(cp - BASE)

    length, vals = vals[0], vals[1:]
    out = bytearray()
    acc = 0
    nbits = 0
    for v in vals:
        acc = (acc << BITS) | v
        nbits += BITS
        while nbits >= 8 and len(out) < length:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
    if len(out) != length:
        raise ValueError(f"unvollstaendig: {len(out)} von {length} bytes")
    return bytes(out)


def capacity(max_codepoints: int = 100) -> int:
    """Wieviele Nutzbytes passen in N Code Points (inkl. Laengenpraefix)."""
    return ((max_codepoints - 1) * BITS) // 8


# --------------------------------------------------------------------------

if __name__ == "__main__":
    import base64
    import os
    import unicodedata as ud

    print(f"Kapazitaet bei 100 CP: {capacity()} Bytes\n")

    # Round-Trip ueber alle relevanten Laengen
    for n in range(0, 200):
        data = os.urandom(n)
        assert decode(encode(data)) == data, f"roundtrip failed at n={n}"
    print("roundtrip 0..199 bytes: ok")

    # Alle Bytewerte, inkl. Nullbytes und 0xFF
    for probe in (b"\x00" * 32, b"\xff" * 32, bytes(range(256))):
        assert decode(encode(probe)) == probe
    print("randfaelle (nullbytes, 0xff, alle bytewerte): ok")

    # Normalisierungsstabilitaet des tatsaechlich erzeugten Outputs
    sample = encode(os.urandom(capacity()))
    for form in ("NFC", "NFD", "NFKC", "NFKD"):
        assert ud.normalize(form, sample) == sample, f"{form} veraendert output"
    print("normalisierung NFC/NFD/NFKC/NFKD: stabil")

    # Alle 16384 benutzten Zeichen einzeln pruefen
    bad = [cp for cp in range(BASE, BASE + (1 << BITS))
           if ud.category(chr(cp)) in ("Cn", "Cs") or ud.combining(chr(cp))]
    print(f"unassigned/surrogate/combining im genutzten range: {len(bad)}")

    # Limit einhalten
    payload = os.urandom(capacity())
    enc = encode(payload)
    print(f"\n{capacity()} bytes -> {len(enc)} code points "
          f"({len(enc.encode('utf-8'))} utf-8 bytes, "
          f"{len(enc.encode('utf-16-le'))//2} utf-16 units)")
    assert len(enc) <= 100

    # Vergleich
    print(f"\nzum vergleich, dieselben {capacity()} bytes:")
    print(f"  base64url -> {len(base64.urlsafe_b64encode(payload))} code points")
    print(f"  base85    -> {len(base64.b85encode(payload))} code points")
    print(f"  uro14     -> {len(enc)} code points")

    print(f"\nbeispiel: {encode(b'user=1234;action=delete')}")