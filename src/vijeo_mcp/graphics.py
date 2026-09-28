"""Graphics editing for Vijeo Designer projects (.vdz) - reverse-engineered format, validated by Vijeo.

How panels are stored (verified on all 67 panels / 2,907 objects of the reference project, and by
Vijeo re-saving edited panels byte-for-byte identical):

  Panel stream 'GraphicalObject' = 1 byte, chain of object records, 9-byte footer.
  Object record = [u32 size][16 bytes][payload(size)]            (length = 20 + size)
  Payload       = class GUID(16) | 03 | u32 id | rect 4 x i16 | u32 nameBytes | name UTF-16 |
                  13 bytes | u32 typecode | properties... | (groups: chain of child records)
  Strings       = [u32 byte length incl. terminator][UTF-16]
  Variable binding (identity) : {F,2:1.<folderOid>,Folder}.{T,2:1.<varOid>,Var}   (struct element:
                  ...{T,3:1.<instOid>.<memberIndex>,Member}); plus runtime expressions 'TagDB.<name>...'
  Ids come from Services/TagDatabase (records [oid][type][EF EF EF EF]; folders: parent, container
  nsid, name nsid; variables: property (4, 0x10) = name nsid) + Services/NameServer (names).
  'StatusFlags' = panel dependency list: u16 count at 6, then count x '(1)<leaf oid>' entries, then RL: entries.

Editing rules: every string edit rewrites its length prefix and the size of EVERY enclosing record;
edits are applied to an in-memory copy and written as a NEW .vdz (never the source) through Windows
structured storage. validate_in_vijeo() imports the result into a separate Vijeo instance under a
test project name, makes Vijeo open the edited panel and save, and checks that Vijeo's own
serialization equals ours.
"""

from __future__ import annotations

import io
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import uuid
import zipfile

import olefile

from .safety import SafetyError, check_output
from .texts import LangTable

CLASS_NAMES = {
    "cfd4b0cc-678f-4f78-980c-24c14cbe68e1": "Text",
    "347cced5-5e26-497a-a140-ea017d0272ba": "Switch",
    "3be64a91-d329-4653-9891-aa4ee97464c2": "Rectangle",
    "c29c3b73-7ad6-4e52-a271-30a8376e05c2": "Image",
    "5fd5a5e2-2428-4024-9029-eef4a8ff82a4": "DataDisplay",
    "78914787-36a8-4096-9256-df9bd6c180aa": "Line",
    "3e217838-cb27-4ae0-9cbc-f7c1d1336474": "Group",
    "6334d9f0-692c-4022-b6ac-d89f10aa26bb": "Lamp",
    "8fdb4a8b-c40f-4336-a1ea-bfda2a4545b7": "Polygon",
    "22a41009-62e3-4612-b997-032976965397": "Ellipse",
    "7c30e94e-fe74-483d-a082-4e5636774390": "TrendGraph",
    "1231ae6c-e4db-415c-b71b-086b3bc2788b": "BarGraph",
    "23969996-f01c-4dea-9110-6ef30136b91d": "MessageDisplay",
    "86c17f2f-f0df-4bc0-9dc0-68ab418587c3": "AlarmSummary",
    "8ccd8ce7-3ac7-447a-bf36-c3a7a9fa73f1": "Polyline",
    "94122865-3de8-4419-9fc8-fdbfc6ad2092": "Arc",
}
GROUP = "3e217838-cb27-4ae0-9cbc-f7c1d1336474"
_U16 = re.compile(rb"(?:[\x20-\x7e]\x00){3,}")
_CHAIN = re.compile(r"\{[FT],[23]:[\d.]+,[^{}]+\}(?:\.\{[FT],[23]:[\d.]+,[^{}]+\})*(?:\[\d+\])?")
EF = b"\xEF\xEF\xEF\xEF"
VIJEO = r"C:\Program Files (x86)\Schneider Electric\Vijeo-Designer 6.2\Vijeo-Frame\Vijeo-Frame.exe"
WORKSPACE = r"C:\Users\Public\Documents\Vijeo-Designer 6.2\Vijeo-Manager"
LANG = "Targets/Target 1/Component 1/LangManager/LangManagerData"


# ============================================================ object tree
class Obj:
    __slots__ = ("start", "length", "cls", "id", "rect", "name", "children", "name_at")

    @property
    def kind(self):
        return CLASS_NAMES.get(self.cls, self.cls[:8])


def _record(b, s):
    size = struct.unpack_from("<I", b, s)[0]
    p = s + 20
    if p + 33 > len(b) or b[p + 16] != 3:
        return None
    nl = struct.unpack_from("<I", b, p + 29)[0]
    if not (2 <= nl <= 512 and nl % 2 == 0) or b[p + 33 + nl - 2:p + 33 + nl] != b"\x00\x00":
        return None
    try:
        name = b[p + 33:p + 33 + nl - 2].decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    o = Obj()
    o.start, o.length, o.children, o.name_at = s, 20 + size, [], p + 29
    o.cls = str(uuid.UUID(bytes_le=b[p:p + 16]))
    o.id = struct.unpack_from("<I", b, p + 17)[0]
    o.rect = struct.unpack_from("<4h", b, p + 21)
    o.name = name
    if o.cls == GROUP:
        o.children = _chain(b, p + 33 + nl + 17, s + o.length)
    return o


def _chain(b, pos, limit):
    out = []
    while pos + 40 <= limit:
        size = struct.unpack_from("<I", b, pos)[0]
        if size < 40 or pos + 20 + size > limit:
            break
        o = _record(b, pos)
        if o is None:
            break
        out.append(o)
        pos += 20 + size
    return out


def parse_panel(b: bytes) -> list[Obj]:
    objs = _chain(b, 1, len(b))
    end = objs[-1].start + objs[-1].length if objs else 1
    if len(b) - end != 9:
        raise ValueError(f"panel stream does not parse completely ({len(b) - end} bytes left) - not editing it")
    return objs


def walk(objs, prefix=""):
    for o in objs:
        yield (prefix + o.name, o)
        yield from walk(o.children, prefix + o.name + "/")


def _ancestors(objs, pos):
    chain, level = [], objs
    while True:
        hit = [o for o in level if o.start <= pos < o.start + o.length]
        if not hit:
            return chain
        chain.append(hit[0]); level = hit[0].children


_POINT_KINDS = ("Line", "Polygon", "Polyline")
_SHAPE_MARK = bytes.fromhex("da50ed1c")


def _points(b, o) -> list[int]:
    """Absolute offsets of the (x, y) double pairs of a Line / Polygon / Polyline. Vijeo draws these
    shapes from their points; the header rect is only the (truncated) bounding box. Line: 2 points
    after the name; Polygon / Polyline: u32 count + points (after the animation block, if any).
    The array is located by matching its bounding box to the rect (checked on all 279 in the reference project)."""
    if o.kind not in _POINT_KINDS:
        return []
    own_end = o.children[0].start if o.children else o.start + o.length
    p0 = o.name_at + 4 + struct.unpack_from("<I", b, o.name_at)[0]
    l, t, r, bt = o.rect

    def fits(pts):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return all(math.isfinite(v) and abs(v) < 32768 for v in xs + ys) and \
            abs(min(xs) - l) <= 1.5 and abs(max(xs) - r) <= 1.5 and abs(min(ys) - t) <= 1.5 and abs(max(ys) - bt) <= 1.5

    if o.kind == "Line":              # 0x20 after the shape-properties marker; the rect may be stale by ~2 px
        m = b.rfind(_SHAPE_MARK, p0, own_end)
        i = m + 0x20
        if m != -1 and i + 32 <= own_end:
            pts = struct.unpack_from("<4d", b, i)
            if all(math.isfinite(v) and abs(v) < 32768 for v in pts) and \
                    abs(min(pts[0], pts[2]) - l) <= 4 and abs(min(pts[1], pts[3]) - t) <= 4:
                return [i, i + 16]
        return []
    for i in range(p0, own_end - 36):
        n = struct.unpack_from("<I", b, i)[0]
        if 2 <= n <= 4096 and i + 4 + 16 * n <= own_end:
            offs = [i + 4 + 16 * k for k in range(n)]
            if fits([struct.unpack_from("<2d", b, p) for p in offs]):
                return offs
    return []


# Appearance, relative to the shape-properties marker (checked by rendering in Vijeo, see README):
# colour = R, G, B, flag (flag 08 = "None"); some "None" states also set a companion byte.
_RECT = {"fill": (0x08, 0x34), "line": (0x0C, 0x38), "pattern": (0x10, 0x30)}
_SHAPE = {"fill": (0x08, None), "line": (0x0C, None), "pattern": (0x10, None)}
_STROKE = {"line": (0x08, None)}
STYLE = {  # kind: (block version, colours {name: (offset, companion)}, line-width double offset)
    "Rectangle": (2, _RECT, 0x1A), "Ellipse": (2, _SHAPE, 0x1A), "Polygon": (3, _SHAPE, 0x1A),
    "Line": (2, _STROKE, 0x14), "Polyline": (3, _STROKE, 0x14), "Arc": (3, _STROKE, 0x14),
    "Text": (11, {"text": (0x1A, None), "fill": (0x1E, None), "pattern": (0x22, None), "border": (0x3A, 0x3E)}, None),
}
_NONE_OK = {("Text", "fill")}          # "None" verified with the flag alone


def _color(v) -> tuple[int, int, int] | None:
    if isinstance(v, str) and v.lower() == "none":
        return None
    if isinstance(v, (list, tuple)) and len(v) == 3:
        rgb = tuple(int(x) for x in v)
    else:
        h = str(v).lstrip("#")
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", h):
            raise ValueError(f"colour {v!r}: use '#RRGGBB', [r, g, b] or 'none'")
        rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    if not all(0 <= x <= 255 for x in rgb):
        raise ValueError(f"colour {v!r} out of range")
    return rgb


def _style_block(b, o):
    if o.kind not in STYLE:
        raise ValueError(f"{o.name}: appearance editing supports {sorted(STYLE)}, not {o.kind}")
    ver, colors, width = STYLE[o.kind]
    own_end = o.children[0].start if o.children else o.start + o.length
    mk = b.rfind(_SHAPE_MARK, o.start, own_end)
    if mk == -1 or struct.unpack_from("<I", b, mk + 4)[0] != ver or mk + 0x40 > own_end:
        raise ValueError(f"{o.name}: unexpected property layout - not editing it")
    return mk, colors, width


def _read_style(b, o) -> dict:
    mk, colors, width = _style_block(b, o)
    out = {}
    for name, (off, comp) in colors.items():
        r, g, bl, flag = b[mk + off:mk + off + 4]
        none = flag & 0x08 or (comp is not None and b[mk + comp])
        out[name] = "none" if none else f"#{r:02X}{g:02X}{bl:02X}"
    if width is not None:
        out["line_width"] = struct.unpack_from("<d", b, mk + width)[0]
    return out


# ============================================================ trend graphs
_EXPR = struct.pack("<I", 0x3F3)          # expression object marker; a trend has 1 header + 8 channel blocks


def _lp(t: str) -> bytes:
    d = t.encode("utf-16-le") + b"\x00\x00"
    return struct.pack("<I", len(d)) + d


def _lp_at(b, p):
    n = struct.unpack_from("<I", b, p)[0]
    if n % 2 or n > 8000 or p + 4 + n > len(b):
        raise ValueError("trend layout not recognised")
    return (b[p + 4:p + 2 + n].decode("utf-16-le") if n >= 2 else ""), p + 4 + n


def _expr_block(b, p):
    """f3 03 | 3 strings | u16 | u32 0x80 | u32 0x2002 | u32 kind | 7 strings  -> (strings, u16, kind, end)"""
    if b[p:p + 4] != _EXPR:
        raise ValueError("trend layout not recognised")
    q, strs = p + 4, []
    for _ in range(3):
        t, q = _lp_at(b, q); strs.append(t)
    flag = struct.unpack_from("<H", b, q)[0]
    kind = struct.unpack_from("<I", b, q + 10)[0]
    q += 14
    for _ in range(7):
        t, q = _lp_at(b, q); strs.append(t)
    return strs, flag, kind, q


def _channel_bytes(var: str, chain: str) -> bytes:
    """A channel's variable block exactly as Vijeo writes it (checked against every populated channel)."""
    if not var:
        return _EXPR + _lp("") * 3 + struct.pack("<HIII", 1, 0x80, 0x2002, 0x20) + _lp("") * 7
    e = "TagDB." + var
    return (_EXPR + _lp(chain) + _lp(e) + _lp(e) + struct.pack("<HIII", 0, 0x80, 0x2002, 2)
            + _lp(var + "\n") + _lp(e + "\n") + _lp("") + _lp(e + "\n") + _lp("") * 3)


def _trend_layout(b, o) -> dict:
    """Offsets of a trend graph's editable fields (absolute in b). Verified on all 17 trends of the reference project."""
    end = o.start + o.length
    marks, p = [], b.find(_EXPR, o.start, end)
    hdr_strs, _, _, h = _expr_block(b, p)
    # after the header strings: ranges @H+9..H+40, label decimals/digits @H+79/81, then 4 axis blocks
    # (data axis, data grid, time axis, time grid) = u32 4 | u16 enabled | u32 count | u32 1 | colour ...
    if any(struct.unpack_from("<I", b, h + a)[0] != 4 for a in (88, 110, 132, 154)):
        raise ValueError("trend layout not recognised (axis settings)")
    lay = {"header": p, "H": h, "channels": []}
    q = h
    for k in range(8):
        m = b.find(_EXPR, q, end)
        if m == -1:
            raise ValueError("trend layout not recognised (fewer than 8 channels)")
        strs, flag, kind, t = _expr_block(b, m)
        if struct.unpack_from("<I", b, m - 12)[0] != 3 or struct.unpack_from("<I", b, t)[0] not in (0x80, 0x20) \
                or struct.unpack_from("<I", b, t + 13)[0] != 2 or struct.unpack_from("<I", b, t + 65)[0] != 2:
            raise ValueError("trend layout not recognised (channel settings)")
        lay["channels"].append({"marker": m, "tail": t, "strings": strs, "empty": flag == 1 or not strs[0]})
        q = t + 121
    return lay


_LABELS = re.compile(rb"\x00\x01\x00\x00\x00(?:\x01\x00\x00\x00)?([\x01-\x20])\x00(?=\x01)", re.S)


def _text_refs(b, o, lang) -> list[int]:
    """Absolute offsets of the u32 text ids an object uses (its own bytes, not its children's).
    Text: fixed slot 0x32 after the shape marker; Switch / Lamp / DataDisplay labels: a list
    u16 count + count x (01, u32 id) - one id per state. Every id must exist in the language table."""
    own_end = o.children[0].start if o.children else o.start + o.length
    out = []
    if o.kind == "Text":
        mk = b.rfind(_SHAPE_MARK, o.start, own_end)
        if mk != -1 and struct.unpack_from("<I", b, mk + 0x32)[0] in lang:
            out.append(mk + 0x32)
    for m in _LABELS.finditer(b, o.start, own_end):
        n, p = m.group(1)[0], m.end()
        offs = [p + 5 * k + 1 for k in range(n)]
        if all(b[q - 1] == 1 and q + 4 <= own_end and struct.unpack_from("<I", b, q)[0] in lang for q in offs):
            out.extend(offs)
    return out


def _transform(b, o, f) -> None:
    """Apply f(x, y) -> (x, y) to one object's rect and (if it has them) its points."""
    l, t, r, bt = o.rect
    offs = _points(b, o)
    (nl, nt), (nr, nb) = f(l, t), f(r, bt)
    struct.pack_into("<4h", b, o.start + 41, round(nl), round(nt), round(nr), round(nb))
    for p in offs:
        x, y = struct.unpack_from("<2d", b, p)
        struct.pack_into("<2d", b, p, *f(x, y))


def _find(objs, path):
    level, node = objs, None
    for part in path.split("/"):
        hits = [o for o in level if o.name == part]
        if len(hits) != 1:
            raise KeyError(f"{len(hits)} objects named '{part}' at that level of '{path}' "
                           "(use list_panel_objects to see paths)")
        node = hits[0]; level = node.children
    return node


# ============================================================ NameServer (panel names)
def _ns_nodes(ns: bytes):
    """First namespace of Services/NameServer: u16 01 | u16 | nodes | u32 last id. Node = u16 1 | u32 id |
    u16 kind | u16 n | n x (u32 id, u32 len, UTF-16 name) | u16. Yields (node_at, id, kids, kids_end);
    returns the offset of the last-id counter."""
    pos, out = 4, []
    while pos + 10 <= len(ns):
        one, nid, kind, n = struct.unpack_from("<HIHH", ns, pos)
        if one != 1 or kind > 4:
            break
        p, kids, ok = pos + 10, [], True
        for _ in range(n):
            if p + 8 > len(ns):
                ok = False; break
            cid, ln = struct.unpack_from("<II", ns, p)
            if not (2 <= ln <= 1024 and ln % 2 == 0) or ns[p + 6 + ln:p + 8 + ln] != b"\x00\x00":
                ok = False; break
            kids.append(cid); p += 8 + ln
        if not ok:
            break
        out.append((pos, nid, kids, p))
        pos = p + 2
    return out, pos


def _ns_find_parent(ns: bytes, child_id: int):
    nodes, counter_at = _ns_nodes(ns)
    ids = [x[1] for x in nodes] + [k for x in nodes for k in x[2]]
    if not nodes or struct.unpack_from("<I", ns, counter_at)[0] != max(ids):
        raise ValueError("NameServer layout not recognised - not registering a new panel")
    for node_at, nid, kids, kids_end in nodes:
        if child_id in kids:
            return node_at, kids_end, counter_at
    raise ValueError(f"name id {child_id} not found in the NameServer")


# ============================================================ variable ids
class Resolver:
    def __init__(self, nameserver: bytes, tagdb: bytes):
        self.names, ns_parent = {}, {}
        hdr = re.compile(rb"\x00\x00\x01\x00(.{4})\x00\x00(.{2})", re.S)
        b = nameserver
        for m in hdr.finditer(b):
            fns, n = struct.unpack("<I", m.group(1))[0], struct.unpack("<H", m.group(2))[0]
            p, kids, ok = m.end(), [], True
            for _ in range(n):
                if p + 8 > len(b):
                    ok = False; break
                ns, ln = struct.unpack_from("<II", b, p)
                if not (2 <= ln <= 512) or ln % 2 or b[p + 8 + ln - 2:p + 8 + ln] != b"\x00\x00":
                    ok = False; break
                try:
                    kids.append((ns, b[p + 8:p + 6 + ln].decode("utf-16-le")))
                except UnicodeDecodeError:
                    ok = False; break
                p += 8 + ln
            if ok and n:
                for ns, nm in kids:
                    self.names[ns] = nm; ns_parent[ns] = fns
        for m in re.finditer(rb"(?:[\x20-\x7e]\x00)+\x00\x00", b):
            s = m.start()
            if s >= 8 and struct.unpack_from("<I", b, s - 4)[0] == m.end() - s:
                self.names.setdefault(struct.unpack_from("<I", b, s - 8)[0], m.group()[:-2].decode("utf-16-le"))
        recs = []
        for m in re.finditer(re.escape(EF), tagdb):
            i = m.start() - 8
            if i >= 0:
                oid, typ = struct.unpack_from("<II", tagdb, i)
                if 3990 < typ < 4100 and oid:
                    recs.append((i, oid, typ))
        folders, tags = {}, {}
        for k, (i, oid, typ) in enumerate(recs):
            end = recs[k + 1][0] if k + 1 < len(recs) else len(tagdb)
            if typ == 4012 and struct.unpack_from("<I", tagdb, i + 12)[0] == oid:
                folders[oid] = struct.unpack_from("<III", tagdb, i + 16)       # parent, container ns, name ns
            else:
                j = tagdb.find(bytes.fromhex("0400000010000000"), i + 12, end)
                if j != -1:
                    tags[oid] = struct.unpack_from("<I", tagdb, j + 8)[0]
        by_container = {cns: oid for oid, (par, cns, nns) in folders.items()}

        def fpath(oid, d=0):
            if oid not in folders or d > 30:
                return []
            par, cns, nns = folders[oid]
            if nns not in self.names:
                return []
            return (fpath(par, d + 1) if par else []) + [self.names[nns]]

        self.oid_of = {}
        for oid in folders:
            p = fpath(oid)
            if p:
                self.oid_of[".".join(p)] = ("F", oid)
        for oid, ns in tags.items():
            parent = by_container.get(ns_parent.get(ns))
            if ns in self.names:
                full = (fpath(parent) if parent else []) + [self.names[ns]]
                self.oid_of[".".join(full)] = ("T", oid)
        # structure types (TagDatabase records 4003, kind 5): key -> {member: index}; instance -> type key
        self.members, self.type_names, self.inst_type = {}, {}, {}
        starts = sorted(i for i, _, _ in recs)
        for m in re.finditer(re.escape(struct.pack("<I", 4003) + EF), tagdb):
            p = m.start() + 8
            kind, n = struct.unpack_from("<II", tagdb, p)
            if kind != 5 or not 0 < n < 64:
                continue
            name = tagdb[p + 8:p + 8 + 2 * n].decode("utf-16-le", "replace")
            q = p + 8 + 2 * n
            while q < len(tagdb) and tagdb[q] == 0:              # padding after the name varies
                q += 1
            key, _, count = struct.unpack_from("<III", tagdb, q)
            end = next((s for s in starts if s > m.start()), len(tagdb))
            nxt = tagdb.find(struct.pack("<I", 4003) + EF, m.end())
            end = min(end, nxt if nxt != -1 else len(tagdb))
            mem = {}                                          # member = u32 index | u32 chars | UTF-16 name | ...
            for mm in re.finditer(rb"(?:[A-Za-z_]\x00)(?:[A-Za-z0-9_]\x00)*", tagdb[q + 12:end]):
                st = q + 12 + mm.start()
                if st - 8 < q + 12:
                    continue
                idx, ln = struct.unpack_from("<II", tagdb, st - 8)
                if ln * 2 == len(mm.group()) and 0 < idx <= count + 64:
                    mem.setdefault(mm.group().decode("utf-16-le"), idx)
            if mem:
                self.members[key], self.type_names[key] = mem, name
        for i, oid, typ in recs:
            if typ == 4005 and tagdb[i + 12:i + 24] == struct.pack("<III", 5, 1, 1):
                k = struct.unpack_from("<I", tagdb, i + 24)[0]
                if k in self.members:
                    self.inst_type[oid] = k

    def binding(self, full_name: str) -> str:
        parts, segs = full_name.split("."), []
        for k in range(1, len(parts) + 1):
            key = ".".join(parts[:k])
            if key not in self.oid_of:
                prev = self.oid_of.get(".".join(parts[:k - 1]))
                if k == len(parts) and prev and prev[0] == "T" and prev[1] in self.inst_type:
                    mem = self.members[self.inst_type[prev[1]]]
                    if parts[-1] in mem:                    # structure element: {T,3:1.<inst>.<index>,Member}
                        segs.append(f"{{T,3:1.{prev[1]}.{mem[parts[-1]]},{parts[-1]}}}")
                        return ".".join(segs)
                    raise KeyError(f"'{parts[-1]}' is not a member of {self.type_names[self.inst_type[prev[1]]]}")
                raise KeyError(f"'{key}' is not a folder, variable or structure element of this project")
            kind, oid = self.oid_of[key]
            segs.append(f"{{{kind},2:1.{oid},{parts[k - 1]}}}")
        return ".".join(segs)


def _leaf(chain: str) -> str:
    return re.findall(r"\{T,[23]:1\.([\d.]+),", chain)[-1]


# ============================================================ string edits
def _replace_lp(b: bytearray, objs, len_at: int, text: str) -> int:
    old_len = struct.unpack_from("<I", b, len_at)[0]
    new = text.encode("utf-16-le") + b"\x00\x00"
    delta = len(new) - old_len
    anc = _ancestors(objs, len_at)
    if not anc:
        raise ValueError("string is not inside an object record")
    b[len_at + 4:len_at + 4 + old_len] = new
    struct.pack_into("<I", b, len_at, len(new))
    for o in anc:
        struct.pack_into("<I", b, o.start, struct.unpack_from("<I", b, o.start)[0] + delta)
    return delta


def _mfc_prefix(nchars: int) -> bytes:
    """MFC CArchive Unicode CString prefix: FF FE FF + length (u8 | FF u16 | FF FF FF u32)."""
    if nchars < 0xFF:
        return b"\xFF\xFE\xFF" + bytes([nchars])
    if nchars < 0xFFFE:
        return b"\xFF\xFE\xFF\xFF" + struct.pack("<H", nchars)
    return b"\xFF\xFE\xFF\xFF\xFF\xFF" + struct.pack("<I", nchars)


def _strings(b, start, end, key_text):
    """Strings in [start, end) containing key_text, in BOTH encodings Vijeo uses:
       'lp'  = [u32 byte length incl. terminator][UTF-16 text][00 00]
       'mfc' = [FF FE FF][length in chars: u8 | FF u16 | FF FF FF u32][UTF-16 text]
    Returns (span_start, span_end, text, kind)."""
    key = key_text.encode("utf-16-le")
    out, seen, i = [], set(), b.find(key, start, end)
    while i != -1 and i < end:
        found = None
        for back in range(0, 4096, 2):
            s = i - back
            if s < start + 4:
                break
            # MFC CString
            for plen, fmt, off in ((4, "B", 3), (6, "<H", 4), (10, "<I", 6)):
                ps = s - plen
                if ps >= start and b[ps:ps + 3] == b"\xFF\xFE\xFF":
                    n = struct.unpack_from(fmt, b, ps + off)[0]
                    if (plen == 4 and n < 0xFF) or (plen == 6 and b[ps + 3] == 0xFF and n < 0xFFFE) or \
                       (plen == 10 and b[ps + 3:ps + 6] == b"\xFF\xFF\xFF"):
                        if 2 * n >= back + len(key) and s + 2 * n <= end:
                            try:
                                found = (ps, s + 2 * n, bytes(b[s:s + 2 * n]).decode("utf-16-le"), "mfc")
                            except UnicodeDecodeError:
                                pass
                if found:
                    break
            if found:
                break
            # u32-length-prefixed, NUL-terminated
            ln = struct.unpack_from("<I", b, s - 4)[0]
            if ln >= back + len(key) and ln % 2 == 0 and s + ln <= end and b[s + ln - 2:s + ln] == b"\x00\x00":
                try:
                    t = bytes(b[s:s + ln - 2]).decode("utf-16-le")
                except UnicodeDecodeError:
                    continue
                if "\x00" not in t:
                    found = (s - 4, s + ln, t, "lp")
                    break
        if found and found[0] not in seen:
            seen.add(found[0]); out.append(found)
        i = b.find(key, i + 2, end)
    return out


def _encode(kind: str, text: str) -> bytes:
    data = text.encode("utf-16-le")
    if kind == "mfc":
        return _mfc_prefix(len(text)) + data
    return struct.pack("<I", len(data) + 2) + data + b"\x00\x00"


def _replace_span(b: bytearray, objs, span_start: int, span_end: int, new: bytes) -> int:
    anc = _ancestors(objs, span_start)
    if not anc:
        raise ValueError("string is not inside an object record")
    delta = len(new) - (span_end - span_start)
    b[span_start:span_end] = new
    for o in anc:
        struct.pack_into("<I", b, o.start, struct.unpack_from("<I", b, o.start)[0] + delta)
    return delta


def _statusflags(sf: bytes, graphics: bytes, old_leaf: str, new_leaf: str) -> bytes:
    b = bytearray(sf)
    n, pos, entries = struct.unpack_from("<H", b, 6)[0], 8, []
    for _ in range(n):
        ln = struct.unpack_from("<I", b, pos)[0]
        entries.append((pos, ln, b[pos + 4:pos + 2 + ln].decode("utf-16-le")))
        pos += 4 + ln
    names = [e[2] for e in entries]
    if "(1)" + new_leaf not in names:
        data = ("(1)" + new_leaf).encode("utf-16-le") + b"\x00\x00"
        b[pos:pos] = struct.pack("<I", len(data)) + data
        n += 1
    if ("1." + old_leaf + ",").encode("utf-16-le") not in graphics and "(1)" + old_leaf in names:
        p, ln, _ = entries[names.index("(1)" + old_leaf)]
        del b[p:p + 4 + ln]
        n -= 1
    struct.pack_into("<H", b, 6, n)
    return bytes(b)


# ============================================================ session
class GraphicsSession:
    """One .vdz loaded for editing. Edits live in memory until save_as()."""

    def __init__(self, vdz: str):
        self.src = os.path.abspath(vdz)
        with zipfile.ZipFile(self.src) as z:
            self.cf_name = [n for n in z.namelist() if n.lower().endswith(".swxcf")][0]
            self._blob = z.read(self.cf_name)
        self.project = self.cf_name[:-6]
        self._ole = olefile.OleFileIO(io.BytesIO(self._blob))
        self.edits: dict[str, bytes] = {}
        self.log: list[str] = []
        self.panels = {}
        for p in self._ole.listdir():
            s = "/".join(p)
            if p[-1] == "GraphicalObject" and ("BasePanelsList/" in s or "PopupWindowList/" in s):
                root = s[: -len("/GraphicalObject")]
                wo = self._ole.openstream(root + "/WindowObject").read() if self._exists(root + "/WindowObject") else b""
                name, pid = root.rsplit("/", 1)[1], None
                if len(wo) > 12:
                    nl = struct.unpack_from("<I", wo, 8)[0]
                    if 2 <= nl < 400:
                        name = wo[12:12 + nl - 2].decode("utf-16-le", "replace")
                        pid = struct.unpack_from("<I", wo, 12 + nl)[0] if len(wo) >= 16 + nl else None
                self.panels[name] = {"root": root, "id": pid, "popup": "PopupWindowList/" in s}
        self._resolver = None
        self._lang = None

    def _read(self, path):
        return self.edits[path] if path in self.edits else self._ole.openstream(path).read()

    def _exists(self, path) -> bool:
        return path in self.edits or self._ole.exists(path)

    def _stream_paths(self) -> list[str]:
        return sorted({"/".join(p) for p in self._ole.listdir()} | set(self.edits))

    @property
    def lang(self) -> LangTable:
        if self._lang is None:
            self._lang = LangTable(self._read(LANG))
        return self._lang

    def _lang_commit(self):
        self.edits[LANG] = self._lang.data()

    def _all_text_refs(self) -> dict[int, int]:
        """text id -> number of objects using it, over every panel stream of the project."""
        count: dict[int, int] = {}
        for path in self._stream_paths():
            if not path.endswith("/GraphicalObject"):
                continue
            try:
                b = self._read(path); objs = parse_panel(b)
            except Exception:
                continue
            for _, o in walk(objs):
                for q in _text_refs(b, o, self.lang):
                    tid = struct.unpack_from("<I", b, q)[0]
                    count[tid] = count.get(tid, 0) + 1
        return count

    @property
    def resolver(self):
        if self._resolver is None:
            self._resolver = Resolver(self._ole.openstream("Services/NameServer").read(),
                                      self._ole.openstream("Services/TagDatabase").read())
        return self._resolver

    def _panel(self, name):
        if name not in self.panels:
            raise KeyError(f"No panel '{name}'. Panels: {sorted(self.panels)[:40]}")
        return self.panels[name]["root"]

    # ---------------------------------------------------------------- reading
    def objects(self, panel: str, max_depth: int = 1, with_bindings: bool = True) -> list[dict]:
        root = self._panel(panel)
        b = self._read(root + "/GraphicalObject")
        out = []
        for path, o in walk(parse_panel(b)):
            depth = path.count("/")
            if depth > max_depth:
                continue
            row = {"path": path, "type": o.kind, "rect": list(o.rect)}
            if with_bindings:
                own_end = o.children[0].start if o.children else o.start + o.length
                txt = " ".join(m.group().decode("utf-16-le") for m in _U16.finditer(b, o.start, own_end))
                names = sorted({".".join(x[2] for x in re.findall(r"\{([FT]),[23]:([\d.]+),([^{}]+)\}", c))
                                for c in _CHAIN.findall(txt)})
                if names:
                    row["variables"] = names
                tx = [self.lang.text(struct.unpack_from("<I", b, q)[0]) for q in _text_refs(b, o, self.lang)]
                if tx:
                    row["texts"] = tx
            out.append(row)
        return out

    # ---------------------------------------------------------------- edits
    def _begin(self, panel):
        root = self._panel(panel)
        path = root + "/GraphicalObject"
        b = bytearray(self._read(path))
        return root, path, b, parse_panel(bytes(b))

    def _commit(self, path, b):
        parse_panel(bytes(b))                                  # must still parse completely
        self.edits[path] = bytes(b)

    def move(self, panel, obj_path, dx=0, dy=0, x=None, y=None) -> dict:
        root, path, b, objs = self._begin(panel)
        o = _find(objs, obj_path)
        l, t, r, bt = o.rect
        if x is not None:
            dx = x - l
        if y is not None:
            dy = y - t
        targets = [o] + [c for _, c in walk(o.children)]      # a group's children move with it
        for t_ in targets:
            _transform(b, t_, lambda x_, y_: (x_ + dx, y_ + dy))
        self._commit(path, b)
        self.log.append(f"{panel}: moved {obj_path} by ({dx},{dy})")
        return {"object": obj_path, "from": [l, t, r, bt], "to": [l + dx, t + dy, r + dx, bt + dy],
                "objects_moved": len(targets)}

    def resize(self, panel, obj_path, width=None, height=None, x=None, y=None) -> dict:
        """Set an object's position and/or size (a group scales its whole subtree, lines and polygons
        scale their points). Omitted values keep the current ones."""
        root, path, b, objs = self._begin(panel)
        o = _find(objs, obj_path)
        l, t, r, bt = o.rect
        w0, h0 = r - l, bt - t
        nx, ny = (l if x is None else x), (t if y is None else y)
        w, h = (w0 if width is None else width - 1), (h0 if height is None else height - 1)
        if w < 0 or h < 0 or (width is not None and w0 == 0 and w != 0) or (height is not None and h0 == 0 and h != 0):
            raise ValueError(f"Cannot size {obj_path} ({w0 + 1}x{h0 + 1}) to {w + 1}x{h + 1}.")
        sx, sy = (w / w0 if w0 else 1.0), (h / h0 if h0 else 1.0)
        f = lambda x_, y_: (nx + (x_ - l) * sx, ny + (y_ - t) * sy)
        for t_ in [o] + [c for _, c in walk(o.children)]:
            _transform(b, t_, f)
        self._commit(path, b)
        new = list(_find(parse_panel(bytes(b)), obj_path).rect)
        self.log.append(f"{panel}: {obj_path} {[l, t, r, bt]} -> {new}")
        return {"object": obj_path, "from": [l, t, r, bt], "to": new}

    def rename(self, panel, obj_path, new_name) -> dict:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", new_name):
            raise ValueError("Object names: letters, digits, underscore; must not start with a digit.")
        root, path, b, objs = self._begin(panel)
        o = _find(objs, obj_path)
        siblings = _ancestors(objs, o.start)[-2].children if "/" in obj_path else objs
        if any(s.name == new_name for s in siblings):
            raise ValueError(f"'{new_name}' already exists at that level.")
        _replace_lp(b, objs, o.name_at, new_name)
        self._commit(path, b)
        self.log.append(f"{panel}: renamed {obj_path} -> {new_name}")
        return {"renamed": obj_path, "to": new_name}

    def rebind(self, panel, old, new, obj_path: str = "") -> dict:
        """Re-point references to variable `old` -> `new` in one object (obj_path) or the whole panel."""
        R = self.resolver
        old_chain, new_chain = R.binding(old), R.binding(new)
        root, path, b, objs = self._begin(panel)
        if obj_path:
            o = _find(objs, obj_path); start, end = o.start, o.start + o.length
        else:
            start, end = 1, len(b) - 9
        hits = _strings(b, start, end, old.split(".")[-1])
        old_oid, new_oid = _leaf(old_chain), _leaf(new_chain)
        # structure instance: its element segments repeat the instance oid -> {T,3:1.<inst>.<k>,Member}
        elem_old = re.compile(r"(\{T,3:1\.)" + re.escape(old_oid) + r"(\.\d+,)")
        changed, leaves = 0, set()
        for span_start, span_end, text, kind in sorted(hits, reverse=True):
            if old_chain not in text and "TagDB." + old not in text:
                continue
            t2 = text
            for m in re.finditer(re.escape(old_chain) + r"((?:\.\{T,3:1\.[\d.]+,[^{}]+\})*)", text):
                tail = m.group(1)
                leaves.add(_leaf(old_chain + tail))
                t2 = t2.replace(m.group(0), new_chain + elem_old.sub(r"\g<1>" + new_oid + r"\g<2>", tail), 1)
            t2 = re.sub(r"TagDB\." + re.escape(old) + r"(?![A-Za-z0-9_])", "TagDB." + new, t2)
            if t2 != text:
                _replace_span(b, parse_panel(bytes(b)), span_start, span_end, _encode(kind, t2))
                changed += 1
        if not changed:
            raise ValueError(f"No reference to {old} found in {panel}{'/' + obj_path if obj_path else ''}.")
        self._commit(path, b)
        sfp = root + "/StatusFlags"
        if self._exists(sfp):
            sf = self._read(sfp)
            for lf in sorted(leaves or {old_oid}):
                new_lf = new_oid + lf[len(old_oid):] if lf.startswith(old_oid) else lf
                sf = _statusflags(sf, bytes(b), lf, new_lf)
            self.edits[sfp] = sf
        self.log.append(f"{panel}: {old} -> {new} ({changed} strings{', in ' + obj_path if obj_path else ''})")
        return {"panel": panel, "object": obj_path or "(whole panel)", "strings_changed": changed,
                "binding": {"old": old_chain, "new": new_chain}}

    # ---------------------------------------------------------------- appearance
    def style(self, panel, obj_path) -> dict:
        root, path, b, objs = self._begin(panel)
        o = _find(objs, obj_path)
        return {"object": obj_path, "type": o.kind, **_read_style(b, o)}

    def set_style(self, panel, obj_path, line_width: float | None = None, **colors) -> dict:
        """Colours (fill / line / pattern / text / border, as the object type has them: '#RRGGBB',
        [r, g, b] or 'none') and line width of a shape or text."""
        root, path, b, objs = self._begin(panel)
        o = _find(objs, obj_path)
        mk, table, width = _style_block(b, o)
        before = _read_style(b, o)
        for name, value in colors.items():
            if value is None:
                continue
            if name not in table:
                raise KeyError(f"{o.kind} has colours {sorted(table)}; not '{name}'")
            off, comp = table[name]
            rgb = _color(value)
            if rgb is None:
                if comp is None and (o.kind, name) not in _NONE_OK:
                    raise ValueError(f"'none' for {o.kind} {name} is not supported (not verified in Vijeo)")
                b[mk + off + 3] = 0x08
                if comp is not None:
                    b[mk + comp] = 1
            else:
                b[mk + off:mk + off + 3] = bytes(rgb)
                b[mk + off + 3] = 0x00
                if comp is not None:
                    b[mk + comp] = 0
        if line_width is not None:
            if width is None:
                raise ValueError(f"{o.kind} has no line width")
            if not 0 <= line_width <= 100:
                raise ValueError("line_width must be 0..100")
            struct.pack_into("<d", b, mk + width, float(line_width))
        self._commit(path, b)
        after = _read_style(b, o)
        self.log.append(f"{panel}: {obj_path} style {({k: v for k, v in after.items() if before.get(k) != v})}")
        return {"object": obj_path, "from": before, "to": after}

    # ---------------------------------------------------------------- trend graphs
    def _trend_obj(self, panel, obj_path):
        root, path, b, objs = self._begin(panel)
        o = _find(objs, obj_path)
        if o.kind != "TrendGraph":
            raise ValueError(f"{obj_path} is a {o.kind}, not a trend graph")
        return root, path, b, objs, o, _trend_layout(b, o)

    def trend(self, panel, obj_path) -> dict:
        root, path, b, objs, o, L = self._trend_obj(panel, obj_path)
        H = L["H"]
        d = lambda at: struct.unpack_from("<d", b, at)[0]
        rgb = lambda at: "#" + b[at:at + 3].hex().upper()
        chans = []
        for k, c in enumerate(L["channels"], 1):
            m, t = c["marker"], c["tail"]
            chans.append({
                "channel": k,
                "enabled": struct.unpack_from("<H", b, m - 12 + 4)[0] != 0,
                "variable": "" if c["empty"] else c["strings"][1].replace("TagDB.", "", 1),
                "color": rgb(t + 19), "line_width": d(t + 31), "marks": struct.unpack_from("<H", b, m - 12 + 8)[0] != 0,
                "out_of_range": {"min": d(t + 101), "max": d(t + 109), "color": rgb(t + 71), "line_width": d(t + 83)},
            })
        u16 = lambda at: struct.unpack_from("<H", b, at)[0]
        u32 = lambda at: struct.unpack_from("<I", b, at)[0]
        return {"object": obj_path, "value_range": [d(H + 9), d(H + 17)], "display_range": [d(H + 25), d(H + 33)],
                "label_decimals": u16(H + 79), "data_axis_divisions": u32(H + 94),
                "time_axis_divisions": u32(H + 138), "channels": chans}

    _GRAPH_KEYS = ("value_range", "display_range", "label_decimals", "data_axis_divisions", "time_axis_divisions")

    def set_trend(self, panel, obj_path, value_range: list | None = None, display_range: list | None = None,
                  label_decimals: int | None = None, data_axis_divisions: int | None = None,
                  time_axis_divisions: int | None = None) -> dict:
        """Graph-wide settings: value range (from, to) of the channel data, display range (min, max) of the
        data axis, data-axis label decimals and the number of data / time axis divisions."""
        root, path, b, objs, o, L = self._trend_obj(panel, obj_path)
        before = self.trend(panel, obj_path)
        H = L["H"]
        for rng, at in ((value_range, H + 9), (display_range, H + 25)):
            if rng is not None:
                lo, hi = float(rng[0]), float(rng[1])
                if not lo < hi:
                    raise ValueError(f"range {rng}: min must be below max")
                struct.pack_into("<dd", b, at, lo, hi)
        if display_range is not None:            # label width: integer digits, as Vijeo stores it (min 2)
            lo, hi = float(display_range[0]), float(display_range[1])
            struct.pack_into("<H", b, H + 81, max(2, len(str(int(max(abs(lo), abs(hi)))))))
        if label_decimals is not None:
            if not 0 <= label_decimals <= 6:
                raise ValueError("label_decimals must be 0..6")
            struct.pack_into("<H", b, H + 79, label_decimals)
        for n, at in ((data_axis_divisions, H + 94), (time_axis_divisions, H + 138)):
            if n is not None:
                if not 1 <= n <= 100:
                    raise ValueError("divisions must be 1..100")
                struct.pack_into("<I", b, at, n)
        self._commit(path, b)
        after = self.trend(panel, obj_path)
        changed = {k: after[k] for k in self._GRAPH_KEYS if before[k] != after[k]}
        self.log.append(f"{panel}: {obj_path} {changed}")
        return {"object": obj_path, "from": {k: before[k] for k in changed}, "to": changed}

    def set_trend_channel(self, panel, obj_path, channel: int, variable: str | None = None,
                          enabled: bool | None = None, color=None, line_width: float | None = None,
                          marks: bool | None = None, out_of_range_min: float | None = None,
                          out_of_range_max: float | None = None, out_of_range_color=None) -> dict:
        """Configure channel 1..8: variable ('' clears the channel), enabled, line colour / width, marks,
        and the out-of-range limits and colour. Setting a variable on an empty channel also enables it."""
        if not 1 <= channel <= 8:
            raise ValueError("channel must be 1..8")
        root, path, b, objs, o, L = self._trend_obj(panel, obj_path)
        before_leaves = _leaves(bytes(b))
        before = self.trend(panel, obj_path)["channels"][channel - 1]
        c = L["channels"][channel - 1]
        if variable is not None:
            chain = self.resolver.binding(variable) if variable else ""
            new = _channel_bytes(variable, chain)
            old_len = c["tail"] - c["marker"]
            b[c["marker"]:c["tail"]] = new
            delta = len(new) - old_len
            for a in _ancestors(objs, o.start):
                struct.pack_into("<I", b, a.start, struct.unpack_from("<I", b, a.start)[0] + delta)
            o = _find(parse_panel(bytes(b)), obj_path)
            L = _trend_layout(b, o)
            c = L["channels"][channel - 1]
            if enabled is None:
                enabled = bool(variable)
        m, t = c["marker"], c["tail"]
        if enabled is not None:
            struct.pack_into("<H", b, m - 12 + 4, 0xFFFF if enabled else 0)
        if marks is not None:
            struct.pack_into("<H", b, m - 12 + 8, 0xFFFF if marks else 0)
        for value, at in ((color, t + 19), (out_of_range_color, t + 71)):
            if value is not None:
                rgb = _color(value)
                if rgb is None:
                    raise ValueError("trend colours can't be 'none'")
                b[at:at + 3] = bytes(rgb)
        if line_width is not None:
            if not 0 < line_width <= 20:
                raise ValueError("line_width must be 0..20")
            struct.pack_into("<d", b, t + 31, float(line_width))
        lo = struct.unpack_from("<d", b, t + 101)[0] if out_of_range_min is None else float(out_of_range_min)
        hi = struct.unpack_from("<d", b, t + 109)[0] if out_of_range_max is None else float(out_of_range_max)
        if out_of_range_min is not None or out_of_range_max is not None:
            if not lo < hi:
                raise ValueError("out_of_range_min must be below out_of_range_max")
            struct.pack_into("<dd", b, t + 101, lo, hi)
        self._commit(path, b)
        self._sf_sync(root, bytes(b), before_leaves)
        after = self.trend(panel, obj_path)["channels"][channel - 1]
        changed = {k: v for k, v in after.items() if before.get(k) != v}
        self.log.append(f"{panel}: {obj_path} channel {channel} {changed}")
        return {"object": obj_path, "channel": channel, "from": before, "to": after}

    # ---------------------------------------------------------------- texts / fonts
    def texts(self, panel, obj_path) -> list[dict]:
        """The texts an object displays (one per state for switches / lamps), with their fonts."""
        root, path, b, objs = self._begin(panel)
        o = _find(objs, obj_path)
        out = []
        for k, q in enumerate(_text_refs(b, o, self.lang)):
            tid = struct.unpack_from("<I", b, q)[0]
            out.append({"index": k, "text_id": tid, "text": self.lang.text(tid), "font": self.lang.font(tid)})
        return out

    def _own_text_id(self, panel, obj_path, index) -> int:
        """The object's text id #index - made private to this object first if another object shares it."""
        root, path, b, objs = self._begin(panel)
        o = _find(objs, obj_path)
        refs = _text_refs(b, o, self.lang)
        if not refs:
            raise ValueError(f"{obj_path} ({o.kind}) has no text of its own.")
        if not 0 <= index < len(refs):
            raise IndexError(f"{obj_path} has {len(refs)} text(s); index 0..{len(refs) - 1}.")
        tid = struct.unpack_from("<I", b, refs[index])[0]
        if self._all_text_refs().get(tid, 0) > 1:
            tid = self.lang.clone(tid)
            struct.pack_into("<I", b, refs[index], tid)
            self._commit(path, b)
        return tid

    def set_text(self, panel, obj_path, text: str, index: int = 0) -> dict:
        tid = self._own_text_id(panel, obj_path, index)
        old = self.lang.text(tid)
        self.lang.set_text(tid, text)
        self._lang_commit()
        self.log.append(f"{panel}: {obj_path} text[{index}] {old!r} -> {text!r}")
        return {"object": obj_path, "index": index, "text_id": tid, "from": old, "to": text}

    def set_font(self, panel, obj_path, face: str | None = None, height_px: int | None = None,
                 bold: bool | None = None, index: int | None = None) -> dict:
        """Font of one text (index) or of all the object's texts (index None)."""
        n = len(self.texts(panel, obj_path))
        done = []
        for k in ([index] if index is not None else range(n)):
            tid = self._own_text_id(panel, obj_path, k)
            before = self.lang.font(tid)
            self.lang.set_font(tid, face, height_px, bold)
            done.append({"index": k, "from": before, "to": self.lang.font(tid)})
        self._lang_commit()
        self.log.append(f"{panel}: {obj_path} font {face or ''} {height_px or ''} {'' if bold is None else ('bold' if bold else 'regular')}")
        return {"object": obj_path, "fonts": done}

    # ---------------------------------------------------------------- delete / copy
    def _sf_sync(self, root: str, graphics: bytes, before_leaves: set[str]) -> None:
        """Dependency list: add leaves now referenced, drop leaves no longer referenced."""
        sfp = root + "/StatusFlags"
        if not self._exists(sfp):
            return
        now = _leaves(graphics)
        b = bytearray(self._read(sfp))
        n, pos, entries = struct.unpack_from("<H", b, 6)[0], 8, []
        for _ in range(n):
            ln = struct.unpack_from("<I", b, pos)[0]
            entries.append((pos, ln, b[pos + 4:pos + 2 + ln].decode("utf-16-le"))); pos += 4 + ln
        have = {e[2][3:] for e in entries if e[2].startswith("(1)")}
        for p, ln, txt in sorted(entries, reverse=True):
            if txt.startswith("(1)") and txt[3:] in before_leaves and txt[3:] not in now:
                del b[p:p + 4 + ln]; n -= 1; pos -= 4 + ln
        for lf in sorted(now - have):
            data = ("(1)" + lf).encode("utf-16-le") + b"\x00\x00"
            b[pos:pos] = struct.pack("<I", len(data)) + data; pos += 4 + len(data); n += 1
        struct.pack_into("<H", b, 6, n)
        self.edits[sfp] = bytes(b)

    def delete(self, panel, obj_path) -> dict:
        root, path, b, objs = self._begin(panel)
        o = _find(objs, obj_path)
        anc = _ancestors(objs, o.start)[:-1]
        if anc and len(anc[-1].children) == 1:
            raise ValueError(f"'{obj_path}' is the only child of '{anc[-1].name}'; delete the group instead.")
        removed = [p for p, _ in walk([o])]
        before = _leaves(bytes(b))
        del b[o.start:o.start + o.length]
        for a in anc:
            struct.pack_into("<I", b, a.start, struct.unpack_from("<I", b, a.start)[0] - o.length)
        self._commit(path, b)
        self._sf_sync(root, bytes(b), before)
        self.log.append(f"{panel}: deleted {obj_path} ({len(removed)} object(s))")
        return {"deleted": obj_path, "objects_removed": len(removed)}

    def copy(self, panel, obj_path, new_name: str = "", dx: int = 20, dy: int = 20, to_panel: str = "",
             rebind: dict | None = None) -> dict:
        """Duplicate an object (with its whole subtree) on the same panel (right after the original, same
        group level) or onto another panel (top level, drawn on top). New unique object ids, new name,
        offset (dx, dy); `rebind` {old variable: new variable} is applied to the copy only."""
        src_root, src_path, sb, sobjs = self._begin(panel)
        o = _find(sobjs, obj_path)
        rec = bytearray(sb[o.start:o.start + o.length])
        dest = to_panel or panel
        root, path, b, objs = self._begin(dest)
        before = _leaves(bytes(b))
        # --- fresh ids + offset for the whole subtree (parse the copy on its own)
        wrapped = bytearray(b"\x00" + bytes(rec) + b"\x00" * 9)
        sub = _chain(bytes(wrapped), 1, len(wrapped) - 9)
        next_id = max([x.id for _, x in walk(objs)] + [0]) + 1
        new_texts = 0
        for _, x in walk(sub):
            struct.pack_into("<I", wrapped, x.start + 37, next_id); next_id += 1
            _transform(wrapped, x, lambda x_, y_: (x_ + dx, y_ + dy))
            for q in _text_refs(wrapped, x, self.lang):         # the copy gets its own texts (as in Vijeo)
                struct.pack_into("<I", wrapped, q, self.lang.clone(struct.unpack_from("<I", wrapped, q)[0]))
                new_texts += 1
        if new_texts:
            self._lang_commit()
        rec = bytearray(wrapped[1:-9])
        # --- unique name at the insertion level
        if to_panel and to_panel != panel:
            level, anc, insert_at = objs, [], objs[-1].start + objs[-1].length if objs else 1
        else:
            anc = _ancestors(objs, o.start)[:-1]
            level = anc[-1].children if anc else objs
            insert_at = o.start + o.length
        names = {x.name for x in level}
        base = new_name or o.name
        name = new_name or f"{base}_Copy"
        k = 2
        while name in names:
            if new_name:
                raise ValueError(f"'{new_name}' already exists at that level.")
            name = f"{base}_Copy{k}"; k += 1
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_À-ɏ]{0,63}", name):
            raise ValueError("Object names: letters, digits, underscore.")
        nl = struct.unpack_from("<I", rec, 49)[0]
        newn = name.encode("utf-16-le") + b"\x00\x00"
        rec[53:53 + nl] = newn
        struct.pack_into("<I", rec, 49, len(newn))
        struct.pack_into("<I", rec, 0, struct.unpack_from("<I", rec, 0)[0] + len(newn) - nl)
        # --- insert and grow the enclosing groups
        b[insert_at:insert_at] = rec
        for a in anc:
            struct.pack_into("<I", b, a.start, struct.unpack_from("<I", b, a.start)[0] + len(rec))
        self._commit(path, b)
        new_path = "/".join([a.name for a in anc] + [name])
        rebound = []
        for old, new in (rebind or {}).items():
            rebound.append(self.rebind(dest, old, new, new_path)["strings_changed"])
        b2 = self._read(path)
        self._sf_sync(root, b2, before)
        # resource links (RL:) named inside the copied record
        if dest != panel and self._exists(root + "/StatusFlags") and self._exists(src_root + "/StatusFlags"):
            src_sf = [m.group().decode("utf-16-le") for m in _U16.finditer(self._read(src_root + "/StatusFlags"))]
            dst = bytearray(self.edits.get(root + "/StatusFlags") or self._read(root + "/StatusFlags"))
            have = {m.group().decode("utf-16-le") for m in _U16.finditer(dst)}
            need = [e for e in src_sf if e.startswith("RL:") and e not in have and e[3:].encode("utf-16-le") in rec]
            if need:
                # layout: u32 | u16 2 | u16 nVars | vars | u16 nX | nX x 8 bytes | u16 nRL | RL entries | 00
                pos = 8
                for _ in range(struct.unpack_from("<H", dst, 6)[0]):
                    pos += 4 + struct.unpack_from("<I", dst, pos)[0]
                pos += 2 + 8 * struct.unpack_from("<H", dst, pos)[0]
                rl_at, n_rl = pos, struct.unpack_from("<H", dst, pos)[0]
                pos += 2
                for _ in range(n_rl):
                    pos += 4 + struct.unpack_from("<I", dst, pos)[0]
                ins = b"".join(struct.pack("<I", len(d)) + d for d in (e.encode("utf-16-le") + b"\x00\x00" for e in need))
                dst[pos:pos] = ins
                struct.pack_into("<H", dst, rl_at, n_rl + len(need))
                self.edits[root + "/StatusFlags"] = bytes(dst)
        self.log.append(f"{dest}: copied {panel}/{obj_path} -> {new_path} (+{dx},+{dy})" +
                        (f", rebound {rebind}" if rebind else ""))
        return {"copy": new_path, "panel": dest, "objects": sum(1 for _ in walk(sub)),
                "offset": [dx, dy], "new_texts": new_texts, "rebind_strings_changed": rebound}

    # ---------------------------------------------------------------- whole panels
    def copy_panel(self, panel: str, new_name: str) -> dict:
        """Duplicate a base panel (all its objects, bindings, dependency list) as a new base panel.
        Registers it the way Vijeo does: new storage PanelN (next index from the list's editor
        properties), entry in the list's WindowList, new PanelID, name id in the NameServer's base-panel
        node, and its own copies of every displayed text."""
        info = self.panels.get(panel)
        if info is None:
            raise KeyError(f"No panel '{panel}'.")
        if info["popup"]:
            raise ValueError("copy_panel supports base panels (popups live in popup groups).")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", new_name):
            raise ValueError("Panel names: letters, digits, underscore; must not start with a digit.")
        if new_name.lower() in {n.lower() for n in self.panels}:
            raise ValueError(f"A panel named '{new_name}' already exists.")
        src = info["root"]
        lst = src.rsplit("/", 1)[0]                                   # .../BasePanelsList
        wep_p, wl_p, ns_p = lst + "/WindowEditorProperties", lst + "/WindowList", "Services/NameServer"
        wep, wl = bytearray(self._read(wep_p)), bytearray(self._read(wl_p))
        # --- storage name from the list's next-index counter
        idx = struct.unpack_from("<I", wep, 0x42)[0]
        n = struct.unpack_from("<I", wl, 0)[0]
        p, names = 4, []
        for _ in range(n):
            ln = struct.unpack_from("<I", wl, p)[0]; names.append(wl[p + 4:p + 2 + ln].decode("utf-16-le")); p += 4 + ln
        while f"Panel{idx}" in names or self._exists(f"{lst}/Panel{idx}/WindowObject"):
            idx += 1
        store = f"Panel{idx}"
        struct.pack_into("<I", wep, 0x42, idx + 1)
        entry = store.encode("utf-16-le") + b"\x00\x00"
        wl[p:p] = struct.pack("<I", len(entry)) + entry
        struct.pack_into("<I", wl, 0, n + 1)
        # --- NameServer: new id under the node that holds the source panel's name id
        wo = bytearray(self._read(src + "/WindowObject"))
        src_nsid = struct.unpack_from("<I", wo, 4)[0]
        ns = bytearray(self._read(ns_p))
        node_at, kids_end, counter_at = _ns_find_parent(ns, src_nsid)
        new_id = struct.unpack_from("<I", ns, counter_at)[0] + 1
        struct.pack_into("<I", ns, counter_at, new_id)
        nm = new_name.encode("utf-16-le") + b"\x00\x00"
        ns[kids_end:kids_end] = struct.pack("<II", new_id, len(nm)) + nm
        struct.pack_into("<H", ns, node_at + 8, struct.unpack_from("<H", ns, node_at + 8)[0] + 1)
        # --- WindowObject: name id, name, PanelID
        old_nl = struct.unpack_from("<I", wo, 8)[0]
        pid = max([i["id"] or 0 for i in self.panels.values() if not i["popup"]] + [0]) + 1
        wo[8:12 + old_nl + 4] = struct.pack("<I", len(nm)) + nm + struct.pack("<I", pid)
        struct.pack_into("<I", wo, 4, new_id)
        # --- copy every stream of the panel storage; the panel gets its own texts
        root = f"{lst}/{store}"
        copied = []
        for path in self._stream_paths():
            if path.startswith(src + "/"):
                rel = path[len(src) + 1:]
                data = bytes(wo) if rel == "WindowObject" else self._read(path)
                if rel == "GraphicalObject":
                    g = bytearray(data)
                    for _, o in walk(parse_panel(data)):
                        for q in _text_refs(g, o, self.lang):
                            struct.pack_into("<I", g, q, self.lang.clone(struct.unpack_from("<I", g, q)[0]))
                    data = bytes(g)
                self.edits[f"{root}/{rel}"] = data
                copied.append(rel)
        self._lang_commit()
        self.edits[wep_p], self.edits[wl_p], self.edits[ns_p] = bytes(wep), bytes(wl), bytes(ns)
        self.panels[new_name] = {"root": root, "id": pid, "popup": False}
        self.log.append(f"copied panel {panel} -> {new_name} ({store}, PanelID {pid})")
        return {"panel": new_name, "storage": store, "panel_id": pid, "name_id": new_id, "streams": copied}

    # ---------------------------------------------------------------- output
    def save_as(self, out_vdz: str, project_name: str, overwrite: bool = False, active_panel: str = "") -> dict:
        import pythoncom
        from win32com import storagecon
        if not out_vdz.lower().endswith(".vdz"):
            raise ValueError("out_vdz must end with .vdz")
        out = check_output(out_vdz, self.src, overwrite=overwrite, allow_vdz=True)
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", project_name):
            raise ValueError("project_name: letters, digits, '-' and '_' only.")
        if project_name.lower() == self.project.lower():
            raise SafetyError(f"Use a NEW project name: importing '{project_name}' would offer to overwrite the "
                              "existing project in Vijeo.")
        edits = dict(self.edits)
        if active_panel:
            info = self.panels[active_panel]
            wep = "Targets/Target 1/Component 1/WindowList/BasePanelsList/WindowEditorProperties"
            if info["id"] is not None and not info["popup"] and self._exists(wep):
                w = bytearray(self._read(wep)); struct.pack_into("<I", w, 0x30, info["id"]); edits[wep] = bytes(w)
        tmpd = tempfile.mkdtemp(prefix="vijeo_mcp_")
        cf = os.path.join(tmpd, project_name + ".SwxCF")
        with open(cf, "wb") as f:
            f.write(self._blob)
        rw = storagecon.STGM_READWRITE | storagecon.STGM_SHARE_EXCLUSIVE

        mk = rw | storagecon.STGM_CREATE

        def write_stream(root, spath, data):          # own scope: every sub-storage is released on return
            parts, chain = spath.split("/"), [root]
            for p in parts[:-1]:                      # new panels: storages / streams are created
                try:
                    chain.append(chain[-1].OpenStorage(p, None, rw, None, 0))
                except pythoncom.com_error:
                    chain.append(chain[-1].CreateStorage(p, mk, 0, 0))
            try:
                st = chain[-1].OpenStream(parts[-1], None, rw, 0)
            except pythoncom.com_error:
                st = chain[-1].CreateStream(parts[-1], mk, 0, 0)
            st.SetSize(len(data)); st.Seek(0, 0); st.Write(data); st.Commit(0)
            del st
            for s in reversed(chain[1:]):
                s.Commit(0)
            chain.clear()

        stg = pythoncom.StgOpenStorage(cf, None, storagecon.STGM_READWRITE | storagecon.STGM_SHARE_DENY_WRITE
                                       | storagecon.STGM_TRANSACTED, None, 0)
        for spath, data in edits.items():
            write_stream(stg, spath, data)
        stg.Commit(0); del stg
        with zipfile.ZipFile(self.src) as zin, zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():
                if info.filename == self.cf_name:
                    ni = zipfile.ZipInfo(project_name + ".SwxCF", date_time=info.date_time)
                    ni.compress_type, ni.external_attr = info.compress_type, info.external_attr
                    with open(cf, "rb") as f:
                        zout.writestr(ni, f.read())
                else:
                    zout.writestr(info, zin.read(info))
        shutil.rmtree(tmpd, ignore_errors=True)
        return {"written": out, "project_name": project_name, "streams_changed": sorted(self.edits),
                "edits": list(self.log)}


# ============================================================ UI-free verification
_CHAIN2 = re.compile(r"\{[FT],2:1\.\d+,[^{}]+\}(?:\.\{[FT],2:1\.\d+,[^{}]+\})*")


def _leaves(raw: bytes) -> set[str]:
    out = set()
    for m in _U16.finditer(raw):
        for c in _CHAIN.findall(m.group().decode("utf-16-le")):
            last = re.findall(r"\{([FT]),[23]:1\.([\d.]+),", c)
            if last and last[-1][0] == "T":
                out.add(last[-1][1])
    return out


def _sf_vars(sf: bytes) -> tuple[int, list[str]]:
    n, pos, out = struct.unpack_from("<H", sf, 6)[0], 8, []
    for _ in range(n):
        ln = struct.unpack_from("<I", sf, pos)[0]
        out.append(sf[pos + 4:pos + 2 + ln].decode("utf-16-le")); pos += 4 + ln
    return n, out


def verify_project(vdz: str, original_vdz: str = "") -> dict:
    """Check an (edited) .vdz WITHOUT Vijeo and without any UI. Uses the rules that Vijeo's own
    load/save round trips confirmed: complete record tiling, binding ids consistent with the
    project's databases, dependency lists consistent with the graphics, untouched streams identical."""
    s = GraphicsSession(vdz)
    ref = GraphicsSession(original_vdz) if original_vdz else None
    errors, notes, objects = [], [], 0
    R = s.resolver
    changed_streams = []
    if ref:
        for p in ref._ole.listdir():
            path = "/".join(p)
            if not s._ole.exists(path):
                errors.append(f"stream missing: {path}")
            elif ref._ole.openstream(path).read() != s._ole.openstream(path).read():
                changed_streams.append(path)
        new_roots = {i["root"] for n, i in s.panels.items() if n not in ref.panels}
        for p in s._ole.listdir():
            path = "/".join(p)
            if not ref._ole.exists(path) and not any(path.startswith(r + "/") for r in new_roots):
                errors.append(f"unexpected new stream: {path}")
        allowed = ("/GraphicalObject", "/StatusFlags", "BasePanelsList/WindowEditorProperties", LANG)
        if new_roots:
            allowed += ("BasePanelsList/WindowList", "Services/NameServer")
        bad = [c for c in changed_streams if not c.endswith(allowed)]
        if bad:
            errors.append(f"streams changed that graphics edits never touch: {bad[:5]}")
        if new_roots:                                  # new panels: registration must be complete
            ns = s._read("Services/NameServer")
            try:
                nodes, counter_at = _ns_nodes(ns)
                ns_ids = {k for x in nodes for k in x[2]}
                last = struct.unpack_from("<I", ns, counter_at)[0]
            except Exception as e:  # noqa: BLE001
                errors.append(f"NameServer unreadable ({e})"); ns_ids, last = set(), 0
            wl = s._read("Targets/Target 1/Component 1/WindowList/BasePanelsList/WindowList")
            pids = [i["id"] for i in s.panels.values() if not i["popup"]]
            if len(pids) != len(set(pids)):
                errors.append("two base panels share a PanelID")
            for r in new_roots:
                wo = s._read(r + "/WindowObject")
                nsid = struct.unpack_from("<I", wo, 4)[0]
                if nsid not in ns_ids or nsid > last:
                    errors.append(f"{r}: panel name id {nsid} is not registered in the NameServer")
                if r.rsplit("/", 1)[1].encode("utf-16-le") + b"\x00\x00" not in wl:
                    errors.append(f"{r}: storage not listed in BasePanelsList/WindowList")
    try:
        lang = s.lang
    except ValueError as e:
        errors.append(f"language table: {e}"); lang = None
    if lang is not None and ref and LANG in changed_streams:
        old = ref.lang
        lost = [t for t in old.entries if t not in lang.entries]
        if lost:
            errors.append(f"language table: {len(lost)} existing text(s) removed, e.g. {lost[:5]}")
        nxt = struct.unpack_from("<I", lang.b, 0x14)[0]
        if lang.entries and nxt <= max(lang.entries):
            errors.append("language table: next free text id is not above the highest id")
    for pname, info in s.panels.items():
        g = s._read(info["root"] + "/GraphicalObject")
        try:
            objects += sum(1 for _ in walk(parse_panel(g)))
        except ValueError as e:
            errors.append(f"{pname}: {e}"); continue
        is_new = bool(ref) and pname not in ref.panels
        if ref and not is_new and info["root"] + "/GraphicalObject" not in changed_streams:
            continue                                   # untouched panel: byte-identical to the original
        if lang is not None:
            seen_t = {}
            for opath, o in walk(parse_panel(g)):
                for q in _text_refs(g, o, lang):
                    tid = struct.unpack_from("<I", g, q)[0]
                    if tid in seen_t:
                        errors.append(f"{pname}: {opath} shares text {tid} with {seen_t[tid]}")
                    seen_t[tid] = opath
                mk_own = o.children[0].start if o.children else o.start + o.length
                if o.kind == "Text":
                    mk = g.rfind(_SHAPE_MARK, o.start, mk_own)
                    if mk != -1 and struct.unpack_from("<I", g, mk + 0x32)[0] not in lang:
                        errors.append(f"{pname}: {opath} refers to a text id missing from the language table")
        for m in _U16.finditer(g):
            for c in _CHAIN2.findall(m.group().decode("utf-16-le")):
                if re.findall(r"\{([FT])", c)[-1] != "T":
                    continue
                name = ".".join(re.findall(r",([^,{}]+)\}", c))
                try:
                    if R.binding(name) != c:
                        errors.append(f"{pname}: binding ids do not match the project database: {c}")
                except KeyError:
                    errors.append(f"{pname}: binding to a variable not in the project: {name}")
        sfp = info["root"] + "/StatusFlags"
        if s._ole.exists(sfp):
            try:
                n, sf = _sf_vars(s._read(sfp))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{pname}: StatusFlags unreadable ({e})"); continue
            entries = {e[3:] for e in sf if e.startswith("(1)")}
            if ref:
                old_l = set() if is_new else _leaves(ref._read(info["root"] + "/GraphicalObject"))
                new_l = _leaves(g)
                missing = (new_l - old_l) - entries
                stale = (old_l - new_l) & entries
                if missing:
                    errors.append(f"{pname}: new variable references missing from StatusFlags: {sorted(missing)[:5]}")
                if stale:
                    errors.append(f"{pname}: StatusFlags still lists variables no longer used: {sorted(stale)[:5]}")
    verdict = "PASS" if not errors else "FAIL"
    return {"verdict": verdict, "panels": len(s.panels), "objects": objects,
            "changed_streams": changed_streams, "errors": errors[:50],
            "method": "offline (no Vijeo, no UI): tiling, id consistency, dependency lists, untouched streams"}


# ============================================================ validation through Vijeo itself
def validate_session(s: "GraphicsSession", panels: list[str] | None = None, screenshot_dir: str = "",
                     timeout_s: int = 120) -> dict:
    """Validate the session's edits through Vijeo, one throw-away import per edited panel.
    Base panel: Vijeo opens it and re-saves it. Popup: Vijeo never opens popups on load, so for the check
    ONLY (in the throw-away test project) the popup's edited objects, dependency list and aliases are
    placed in a base 'host' panel that Vijeo does open; object records are the same format in both.
    The language table is compared in Vijeo's own entry order. Nothing is written into your output."""
    edited = [n for n, i in s.panels.items() if i["root"] + "/GraphicalObject" in s.edits]
    host = min((n for n, i in s.panels.items() if not i["popup"]),
               key=lambda n: len(s._read(s.panels[n]["root"] + "/GraphicalObject")))
    panels = panels or edited or ([host] if LANG in s.edits else [])
    if not panels:
        raise RuntimeError("No edits to validate.")
    results, saved_edits = {}, s.edits
    for k, panel in enumerate(panels):
        info = s.panels[panel]
        test_edits = dict(saved_edits)
        check_panel, expected = panel, {}
        if info["popup"]:
            check_panel = host
            h = s.panels[host]["root"]
            for leaf in ("GraphicalObject", "StatusFlags", "PropertyAlias"):
                src = info["root"] + "/" + leaf
                if s._ole.exists(src) and s._ole.exists(h + "/" + leaf):
                    test_edits[h + "/" + leaf] = s._read(src)
                    if leaf != "PropertyAlias":
                        expected[h + "/" + leaf] = s._read(src)
        else:
            for leaf in ("GraphicalObject", "StatusFlags"):
                p = info["root"] + "/" + leaf
                if p in saved_edits:
                    expected[p] = saved_edits[p]
            if not s._ole.exists(info["root"] + "/WindowObject"):       # copied panel: its registration too
                lst = info["root"].rsplit("/", 1)[0]
                for p in (info["root"] + "/WindowObject", lst + "/WindowList", "Services/NameServer"):
                    expected[p] = saved_edits[p]
        if LANG in saved_edits:
            expected[LANG] = saved_edits[LANG]
        tmpd = tempfile.mkdtemp(prefix="vijeo_mcp_val_")
        name = f"VJMCP-CHECK-{os.getpid()}-{int(time.time())}-{k}"
        try:
            s.edits = test_edits
            s.save_as(os.path.join(tmpd, name + ".vdz"), name, active_panel=check_panel)
        finally:
            s.edits = saved_edits
        shot = os.path.join(screenshot_dir, f"check_{panel}.png") if screenshot_dir else ""
        r = validate_in_vijeo(os.path.join(tmpd, name + ".vdz"), check_panel, expected, timeout_s, shot)
        r["method"] = (f"popup: objects loaded in base panel '{host}' of the test project (Vijeo does not open "
                       "popups on load)" if info["popup"] else "base panel opened and re-saved by Vijeo")
        shutil.rmtree(tmpd, ignore_errors=True)
        results[panel] = r
    ok = all(r["verdict"].startswith("PASS") for r in results.values())
    return {"verdict": "PASS" if ok else "FAIL", "panels": results}



def _window_png(hwnd: int, path: str) -> None:
    """Capture ONE window (even if covered by others) with PrintWindow; no focus change, no screen grab."""
    ps = (
        "Add-Type -AssemblyName System.Drawing;"
        "Add-Type 'using System;using System.Runtime.InteropServices;public class PW{"
        "[DllImport(\"user32.dll\")]public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint f);"
        "[DllImport(\"user32.dll\")]public static extern bool GetWindowRect(IntPtr h,out RECT r);"
        "public struct RECT{public int L,T,R,B;}}';"
        f"$h=[IntPtr]{int(hwnd)};$r=New-Object PW+RECT;[PW]::GetWindowRect($h,[ref]$r)|Out-Null;"
        "$bmp=New-Object System.Drawing.Bitmap ($r.R-$r.L),($r.B-$r.T);$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$dc=$g.GetHdc();[PW]::PrintWindow($h,$dc,2)|Out-Null;$g.ReleaseHdc($dc);"
        f"$bmp.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png)")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)


def validate_in_vijeo(vdz: str, check_panel: str, expected: dict[str, bytes], timeout_s: int = 120,
                      screenshot: str = "", dump_dir: str = "") -> dict:
    """Import `vdz` into a SEPARATE Vijeo instance, let it open `check_panel` and save, then compare the
    streams Vijeo wrote with ours. Only the instance and the test project created here are removed.
    Saving is focus-independent (File>Save command posted to that window); keystrokes are a fallback
    used only when that Vijeo window is verifiably in the foreground. screenshot: PNG of the window."""
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    result_extra: dict = {}
    with zipfile.ZipFile(vdz) as z:
        name = [n for n in z.namelist() if n.lower().endswith(".swxcf")][0][:-6]
    proj_dir = os.path.join(WORKSPACE, name)
    if os.path.exists(proj_dir):
        raise SafetyError(f"A Vijeo project named '{name}' already exists; not importing over it.")

    def windows(pid):
        res = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(h, _):
            p = wt.DWORD(); user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == pid and user32.IsWindowVisible(h):
                t, c = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(128)
                user32.GetWindowTextW(h, t, 512); user32.GetClassNameW(h, c, 128)
                res.append((h, c.value, t.value))
            return True
        user32.EnumWindows(cb, 0)
        return res

    def texts(h):
        res = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def cb(ch, _):
            t = ctypes.create_unicode_buffer(1024); user32.GetWindowTextW(ch, t, 1024)
            if t.value.strip():
                res.append(t.value.strip())
            return True
        user32.EnumChildWindows(h, cb, 0)
        return res

    proc = subprocess.Popen([VIJEO, "/import", vdz])
    events, opened, saved, created = [], False, False, False
    try:
        t0 = time.time()
        while time.time() - t0 < timeout_s and proc.poll() is None:
            for h, cls, title in windows(proc.pid):
                if cls == "#32770":
                    tx = texts(h)
                    if any("will only be imported" in x for x in tx):
                        user32.PostMessageW(h, 0x0111, 1, 0)
                    elif tx and (title, tx[0]) not in events:
                        events.append((title, tx[0]))
                if cls == "AfxMDIFrame80u":
                    result_extra["vijeo_title"] = title
                    if check_panel in title:
                        opened = True
            created = created or os.path.exists(proj_dir)
            if opened:
                break
            time.sleep(1)
        if opened:
            frame = [h for h, c, t in windows(proc.pid) if c == "AfxMDIFrame80u"][0]
            time.sleep(2)                                              # let the panel finish rendering

            def is_saved():
                ts = [t for h, c, t in windows(proc.pid) if c == "AfxMDIFrame80u"]
                return bool(ts) and not ts[0].endswith("*")

            if screenshot:
                _window_png(frame, screenshot)
                result_extra["screenshot"] = screenshot
            # 1) focus-independent: MFC's standard File>Save command posted to the Vijeo frame
            user32.PostMessageW(frame, 0x0111, 0xE103, 0)
            for _ in range(15):
                time.sleep(1)
                if is_saved():
                    saved = True; result_extra["save_method"] = "WM_COMMAND ID_FILE_SAVE"; break
            # 2) fallback: Ctrl+S, ONLY if Vijeo really is the foreground window (never type into another app)
            if not saved:
                user32.ShowWindow(frame, 9); user32.SetForegroundWindow(frame); time.sleep(1)
                if user32.GetForegroundWindow() == frame:
                    user32.keybd_event(0x11, 0, 0, 0); user32.keybd_event(0x53, 0, 0, 0)
                    user32.keybd_event(0x53, 0, 2, 0); user32.keybd_event(0x11, 0, 2, 0)
                    for _ in range(20):
                        time.sleep(1)
                        if is_saved():
                            saved = True; result_extra["save_method"] = "Ctrl+S (Vijeo was foreground)"; break
                else:
                    result_extra["save_method"] = "not saved: Vijeo could not take focus, no keys were sent"
        elif screenshot:
            fr = [h for h, c, t in windows(proc.pid) if c == "AfxMDIFrame80u"]
            if fr:
                _window_png(fr[0], screenshot)
                result_extra["screenshot"] = screenshot
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        time.sleep(2)
    result = {"imported": created, "panel_opened": opened, "saved_by_vijeo": saved, "dialogs": events, **result_extra}
    if saved:
        o = olefile.OleFileIO(os.path.join(proj_dir, name + ".SwxCF"))
        # the language table is a hash map: Vijeo re-saves it in its own entry order -> compare with that
        expected = {p: (LangTable(d).vijeo_resave() if p == LANG else d) for p, d in expected.items()}
        result["round_trip_identical"] = {p: o.openstream(p).read() == data for p, data in expected.items()}
        if dump_dir:                                  # keep Vijeo's version of any stream that differs
            os.makedirs(dump_dir, exist_ok=True)
            for p, same in result["round_trip_identical"].items():
                if not same:
                    f = os.path.join(dump_dir, p.replace("/", "__") + ".vijeo")
                    with open(f, "wb") as fh:
                        fh.write(o.openstream(p).read())
                    result.setdefault("vijeo_versions", []).append(f)
        o.close()
    if created:
        shutil.rmtree(proj_dir, ignore_errors=True)
        result["test_project_removed"] = not os.path.exists(proj_dir)
    result["verdict"] = ("PASS - Vijeo loaded the edited panel and re-saved it byte-identical"
                         if saved and all(result["round_trip_identical"].values()) else "FAIL - see details")
    return result
