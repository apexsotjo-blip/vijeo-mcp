"""Vijeo language table ('LangManager/LangManagerData'): the displayed texts and their fonts.

Objects do not hold their texts; they hold text ids into this table (verified on all 11,058 entries
of the reference project, which parse exactly):

  header ... u32 @0x14 = next free text id ... u32 count ... entries ... 21-byte footer
  entry  = u32 id | u32 2 | u32 byteLen | UTF-16 text + NUL | u8 hasFont | [font]
  font   = u32 4 | u32 | u32 | i32 height(px, negative) | u32 weight(400/700) | u32 len | face UTF-16 + NUL |
           u32 | u32 | u32 | u8 | i32 height (copy) | u32
"""

from __future__ import annotations

import struct

NEXT_ID_AT = 0x14


class LangTable:
    def __init__(self, data: bytes):
        self.b = bytearray(data)
        self.count_at = self._find_count()
        self._index()

    def _find_count(self) -> int:
        # the count sits right before the first entry; the entry grammar must consume exactly `count` entries
        for at in range(0x40, 0x400):
            n = struct.unpack_from("<I", self.b, at)[0]
            if 0 < n < 1_000_000 and self._walk(at + 4, n, probe=True) is not None:
                return at
        raise ValueError("LangManagerData: unknown layout")

    def _walk(self, pos, n, probe=False):
        b, out = self.b, {}
        try:
            for _ in range(n):
                start = pos
                tid, two, ln = struct.unpack_from("<III", b, pos)
                if two != 2 or ln % 2 or ln > 100_000:
                    return None
                text_at = pos + 8
                pos += 12 + ln
                flag = b[pos]
                if flag not in (0, 1):
                    return None
                pos += 1
                font_at = None
                if flag:
                    font_at = pos
                    flen = struct.unpack_from("<I", b, pos + 20)[0]
                    pos += 24 + flen + 21
                out[tid] = (start, text_at, font_at, pos)
                if probe and len(out) > 50:
                    return out
        except struct.error:
            return None
        return out if pos <= len(b) else None

    def _index(self):
        n = struct.unpack_from("<I", self.b, self.count_at)[0]
        self.entries = self._walk(self.count_at + 4, n)
        if self.entries is None or len(self.entries) != n:
            raise ValueError("LangManagerData does not parse completely - not editing it")
        self.end = max(e[3] for e in self.entries.values())

    # ------------------------------------------------------------------ read
    def __contains__(self, tid):
        return tid in self.entries

    def text(self, tid) -> str:
        _, at, _, _ = self.entries[tid]
        ln = struct.unpack_from("<I", self.b, at)[0]
        return self.b[at + 4:at + 2 + ln].decode("utf-16-le") if ln else ""

    def font(self, tid) -> dict | None:
        f = self.entries[tid][2]
        if f is None:
            return None
        h, w, flen = struct.unpack_from("<iII", self.b, f + 12)
        face = self.b[f + 24:f + 22 + flen].decode("utf-16-le")
        return {"face": face, "height_px": -h if h < 0 else h, "bold": w >= 700, "weight": w}

    # ------------------------------------------------------------------ write
    def set_text(self, tid, text: str) -> None:
        _, at, _, _ = self.entries[tid]
        old = struct.unpack_from("<I", self.b, at)[0]
        new = text.encode("utf-16-le") + b"\x00\x00"
        self.b[at:at + 4 + old] = struct.pack("<I", len(new)) + new
        self._index()

    def set_font(self, tid, face: str | None = None, height_px: int | None = None, bold: bool | None = None) -> None:
        f = self.entries[tid][2]
        if f is None:
            raise ValueError(f"text {tid} has no font of its own")
        h, w, flen = struct.unpack_from("<iII", self.b, f + 12)
        if height_px is not None:
            if not 4 <= height_px <= 400:
                raise ValueError("height_px must be 4..400")
            tail_h = f + 24 + flen + 13
            same = struct.unpack_from("<i", self.b, tail_h)[0] == h
            struct.pack_into("<i", self.b, f + 12, -height_px)
            if same:
                struct.pack_into("<i", self.b, tail_h, -height_px)
        if bold is not None:
            struct.pack_into("<I", self.b, f + 16, 700 if bold else 400)
        if face:
            new = face.encode("utf-16-le") + b"\x00\x00"
            self.b[f + 20:f + 24 + flen] = struct.pack("<I", len(new)) + new
        self._index()

    def clone(self, tid, text: str | None = None) -> int:
        """New entry (same font) with the next free id; returns the id. It is placed at the end of its
        hash bucket (id >> 4), where Vijeo's own map keeps it."""
        new_id = struct.unpack_from("<I", self.b, NEXT_ID_AT)[0]
        while new_id in self.entries:
            new_id += 1
        s, at, _, e = self.entries[tid]
        rec = bytearray(self.b[s:e])
        struct.pack_into("<I", rec, 0, new_id)
        after = [v[3] for k, v in self.entries.items() if k >> 4 <= new_id >> 4]
        at_pos = max(after) if after else self.count_at + 4
        self.b[at_pos:at_pos] = rec
        struct.pack_into("<I", self.b, self.count_at, struct.unpack_from("<I", self.b, self.count_at)[0] + 1)
        struct.pack_into("<I", self.b, NEXT_ID_AT, new_id + 1)
        self._index()
        if text is not None:
            self.set_text(new_id, text)
        return new_id

    def data(self) -> bytes:
        return bytes(self.b)

    def vijeo_resave(self) -> bytes:
        """The bytes Vijeo writes when it re-saves this table: entries grouped by hash bucket (id >> 4,
        the table has >= 3045 buckets), each bucket in the reverse of the order it was read in.
        (Derived from Vijeo's output and confirmed exactly against it.)"""
        order = sorted(self.entries, key=lambda t: self.entries[t][0])
        buckets: dict[int, list[int]] = {}
        for t in order:
            buckets.setdefault(t >> 4, []).append(t)
        first = min(v[0] for v in self.entries.values())
        body = b"".join(bytes(self.b[self.entries[t][0]:self.entries[t][3]])
                        for k in sorted(buckets) for t in reversed(buckets[k]))
        return bytes(self.b[:first]) + body + bytes(self.b[self.end:])
