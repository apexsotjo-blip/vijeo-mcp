"""Variables, structure elements and folders read and written directly in a Vijeo project (.vdz).

Reverse-engineered from Services/TagDatabase and Services/NameServer (all 456 variables, 613 structure
elements and 32 folders of the reference project parse; every setting was matched against Vijeo's own
CSV export of the same project):

  TagDatabase  u32 8 | u32 length-9 | ... data types (4003) ... variable template (4004)
               u32 count | count x variable, ascending id | u32 next id | ...
               4010 group tables (alarm / logging / scan groups) | 4011 folders: u32 last id, u32 count,
               count x folder (4012) | ... 4007 usage cache (Vijeo itself saves it empty) ...
  variable     u32 id | u32 4005 | EF EF EF EF | u32 n | n x (u32 prop, u32 type, value) |
               u32 dynamic count | [dynamic properties] | u32 child count | children | DA 50 ED 1C
               (structure instance: children = one element record per member, key = member index)
  value types  2 = byte, 5 = double, 4 / 0x17 = u32 chars + UTF-16, 0x13 = 12 bytes, 0 = none, others u32
  folder       u32 id | 4012 | EF | id | parent (0 = top) | NameServer container node | name id | 0 | 1 | end
  names        NameServer first namespace: the folder's container node lists (name id, name) of its
               variables and sub-folders; its last-id counter allocates name ids.
"""

from __future__ import annotations

import struct

from .graphics import _ns_nodes

EF = b"\xef" * 4
MARK = bytes.fromhex("da50ed1c")
VAR = struct.pack("<I", 4005) + EF
FOLDER = struct.pack("<I", 4012) + EF
SIZE = {0: 0, 2: 1, 5: 8, 0x13: 12}
STR = (4, 0x17)

# property ids (matched against Vijeo's CSV export)
P = {"type": 1, "sharing": 6, "initial": 8, "alarm": 0x0A, "alarm_message": 0x0B, "input_range": 0x14,
     "min": 0x15, "max": 0x16, "severity": 0x26, "bytes": 0x29, "address": 0x2B, "alarm_group": 0x4D,
     "logging_group": 0x4E, "scan_group": 0x4F, "parent": 0x51, "external": 0x56, "indirect": 0x5F,
     "indirect_address": 0x60, "name": 4, "description": 5}
SIMPLE = {1: "BOOL", 2: "REAL", 3: "STRING", 8: "INT", 9: "UINT", 10: "DINT", 11: "UDINT"}
SIMPLE_ID = {v: k for k, v in SIMPLE.items()}
INT_TYPES = {"INT", "UINT", "DINT", "UDINT", "BOOL"}


class Prop:
    __slots__ = ("pid", "ptype", "raw")

    def __init__(self, pid, ptype, raw):
        self.pid, self.ptype, self.raw = pid, ptype, raw

    @property
    def value(self):
        if self.ptype in STR:
            return self.raw[4:].decode("utf-16-le")
        if self.ptype == 5:
            return struct.unpack("<d", self.raw)[0]
        if len(self.raw) == 1:
            return self.raw[0]
        if len(self.raw) == 4:
            return struct.unpack("<I", self.raw)[0]
        return self.raw.hex() if self.raw else None

    @staticmethod
    def make(pid, ptype, value):
        if ptype in STR:
            raw = struct.pack("<I", len(value)) + value.encode("utf-16-le")
        elif ptype == 5:
            raw = struct.pack("<d", float(value))
        elif SIZE.get(ptype, 4) == 1:
            raw = bytes([int(value)])
        else:
            raw = struct.pack("<I", int(value))
        return Prop(pid, ptype, raw)

    def encode(self):
        return struct.pack("<II", self.pid, self.ptype) + self.raw


def _props(b, p):
    n = struct.unpack_from("<I", b, p)[0]
    p += 4
    out = []
    for _ in range(n):
        pid, pt = struct.unpack_from("<II", b, p)
        p += 8
        if pt in STR:
            sz = 4 + 2 * struct.unpack_from("<I", b, p)[0]
        else:
            sz = SIZE.get(pt, 4)
        out.append(Prop(pid, pt, bytes(b[p:p + sz])))
        p += sz
    return out, p


def _encode_props(props):
    props = sorted(props, key=lambda x: x.pid)
    return struct.pack("<I", len(props)) + b"".join(x.encode() for x in props)


class TagDB:
    def __init__(self, tagdb: bytes, nameserver: bytes, lang=None):
        self.b, self.ns, self.lang = bytearray(tagdb), bytearray(nameserver), lang
        self._index()

    # ------------------------------------------------------------------ indexing
    def _names(self):
        nodes, counter_at = _ns_nodes(bytes(self.ns))
        names, where, node_of = {}, {}, {}
        for at, nid, kids, kids_end in nodes:
            p = at + 10
            for _ in kids:
                cid, ln = struct.unpack_from("<II", self.ns, p)
                names[cid] = self.ns[p + 8:p + 6 + ln].decode("utf-16-le")
                where[cid] = (nid, p, 8 + ln)
                p += 8 + ln
            node_of[nid] = (at, kids_end)
        return names, where, node_of, counter_at

    def _index(self):
        b = self.b
        if struct.unpack_from("<I", b, 4)[0] != len(b) - 9:
            raise ValueError("TagDatabase length field not recognised - not editing it")
        self.names, self.name_at, self.nodes, self.ns_counter_at = self._names()
        # folders
        fh = b.find(struct.pack("<I", 4011) + EF)
        self.folder_hdr = fh + 8
        self.folders = {}
        p = fh + 16
        for _ in range(struct.unpack_from("<I", b, fh + 12)[0]):
            oid, cls = struct.unpack_from("<II", b, p)
            if b[p + 4:p + 12] != FOLDER:
                raise ValueError("folder list not recognised")
            _, parent, cont, name_ns, z, one = struct.unpack_from("<6I", b, p + 12)
            self.folders[oid] = {"at": p, "parent": parent, "container": cont, "name_ns": name_ns}
            p += 36 + 4
        self.folders_end = p
        for f in self.folders.values():
            f["name"] = self.names.get(f["name_ns"], "?")
        self.folder_path = {oid: self._fpath(oid) for oid in self.folders}
        self.folder_by_path = {v: k for k, v in self.folder_path.items()}
        # variables: count | records in ascending id | next id
        tops = []
        for m in _find_all(b, VAR):
            i = m - 4
            try:
                pr, _ = _props(b, i + 12)
            except struct.error:
                continue
            if any(x.pid == 4 for x in pr) and i > 8:
                tops.append(i)
        tops.sort()
        self.count_at = tops[0] - 4
        if struct.unpack_from("<I", b, self.count_at)[0] != len(tops):
            raise ValueError("variable list not recognised")
        # the list is followed by u32 next id | end | end | object 4009
        c4009 = b.find(struct.pack("<I", 4009) + EF, tops[-1])
        if c4009 == -1 or b[c4009 - 8:c4009] != MARK + MARK:
            raise ValueError("end of the variable list not recognised")
        self.next_id_at = c4009 - 12
        # the list itself is closed by an end marker, and the next-id counter follows it
        self.list_end = self.next_id_at - 4
        if b[self.list_end:self.next_id_at] != MARK:
            raise ValueError("end of the variable list not recognised")
        self.vars = {}
        ends = tops[1:] + [self.list_end]
        for i, end in zip(tops, ends):
            oid = struct.unpack_from("<I", b, i)[0]
            pr, pe = _props(b, i + 12)
            d = {x.pid: x for x in pr}
            name = self.names.get(d[4].value, f"#{oid}")
            parent = d[0x51].value if 0x51 in d else 0
            full = (self.folder_path[parent] + "." if parent in self.folder_path else "") + name
            self.vars[full] = {"oid": oid, "at": i, "end": end, "props": pr, "props_end": pe}
        if struct.unpack_from("<I", b, self.next_id_at)[0] <= max(v["oid"] for v in self.vars.values()):
            raise ValueError("next-id counter not found after the variable list")

    def _fpath(self, oid, depth=0):
        f = self.folders.get(oid)
        if not f or depth > 30:
            return ""
        up = self._fpath(f["parent"], depth + 1) if f["parent"] else ""
        return (up + "." if up else "") + f["name"]

    def _rec_end(self, i):
        pr, q = _props(self.b, i + 12)
        ndyn = struct.unpack_from("<I", self.b, q)[0]
        if ndyn == 0:
            n = struct.unpack_from("<I", self.b, q + 4)[0]
            q += 8
            for _ in range(n):
                q = self._rec_end(q)
            if self.b[q:q + 4] != MARK:
                raise ValueError("variable record not recognised")
            return q + 4
        m = self.b.find(MARK, q)
        return m + 4

    def _elements(self, v):
        """(member index, start, props, props_end, end) of a structure instance's element records."""
        b, out = self.b, []
        q = v["props_end"]
        ndyn, n = struct.unpack_from("<II", b, q)
        if ndyn:
            return out
        q += 8
        for _ in range(n):
            key = struct.unpack_from("<I", b, q)[0]
            pr, pe = _props(b, q + 12)
            end = self._rec_end(q)
            out.append((key, q, pr, pe, end))
            q = end
        return out

    # ------------------------------------------------------------------ groups
    def groups(self) -> dict:
        b = self.b
        g0 = b.find(struct.pack("<I", 4010) + EF)
        g1 = b.find(struct.pack("<I", 4011) + EF)
        blk, out, section = bytes(b[g0:g1]), {"alarm": {}, "logging": {}, "scan": {}}, None
        sec_of = {0x4D: "alarm", 0x4E: "logging", 0x4F: "scan"}
        p = 8
        while p < len(blk) - 12:
            pid = struct.unpack_from("<I", blk, p)[0]
            if pid in sec_of and blk[p + 8:p + 20] == b"\x00" * 12:
                section = sec_of[pid]
                p += 20
                continue
            gid, nsid, n = struct.unpack_from("<III", blk, p)
            if section and 0 < n < 64 and p + 12 + 2 * n <= len(blk):
                try:
                    name = blk[p + 12:p + 12 + 2 * n].decode("utf-16-le")
                except UnicodeDecodeError:
                    name = ""
                if name.isprintable() and name and name != "None":
                    out[section][gid] = name
                    p += 12 + 2 * n
                    continue
            p += 1
        return out

    # ------------------------------------------------------------------ reading
    def _describe(self, props, dtype_hint=None) -> dict:
        d = {x.pid: x for x in props}
        g = self._groups
        v = lambda k: d[P[k]].value if P[k] in d else None
        tid = v("type")
        if tid is None:
            dtype = "BOOL"
        elif tid in SIMPLE:
            dtype = SIMPLE[tid]
        elif tid in self._type_names:
            dtype = self._type_names[tid]
        else:                                          # built-in structure types keep their key elsewhere
            key = struct.pack("<I", tid)
            dtype = next((n for n, w in self._alt_window.items() if key in w), f"#{tid}")
        if dtype_hint:
            dtype = dtype_hint
        src = "InternalReference" if v("indirect") else ("External" if P["scan_group"] in d else "Internal")
        out = {"type": dtype, "source": src, "description": v("description") or ""}
        if P["initial"] in d:
            out["initial_value"] = v("initial")
        if src == "External":
            out["scan_group"] = g["scan"].get(v("scan_group"), v("scan_group"))
            if P["address"] in d:
                out["address"] = v("address")
        if v("indirect_address"):
            out["indirect_address"] = v("indirect_address")
        if P["address"] in d and src != "External":
            out["address"] = v("address")
        if v("alarm"):
            out["alarm"] = {"group": g["alarm"].get(v("alarm_group"), v("alarm_group")), "severity": v("severity"),
                            "message": (self.lang.text(v("alarm_message")) if self.lang and v("alarm_message") in self.lang
                                        else v("alarm_message"))}
        if P["logging_group"] in d:
            out["logging_group"] = g["logging"].get(v("logging_group"), v("logging_group"))
        if v("input_range"):
            out["input_range"] = [v("min") or 0, v("max") or 0]          # absent = 0
        if v("bytes") is not None:
            out["string_bytes"] = v("bytes")
        out["sharing"] = "Read Only" if v("sharing") == 2 else ("None" if v("sharing") is None else v("sharing"))
        return out

    @property
    def _groups(self):
        if not hasattr(self, "_gcache"):
            self._gcache = self.groups()
        return self._gcache

    @property
    def _type_names(self):
        """Data type records (4003): key -> name; structure members: key -> {index: name}, member types."""
        if not hasattr(self, "_tcache"):
            import re
            b = bytes(self.b)
            self._tcache, self._members, self._member_types = {}, {}, {}
            starts = [m.start() for m in re.finditer(re.escape(struct.pack("<I", 4003) + EF), b)]
            first_var = self.count_at
            for k, s in enumerate(starts):
                p = s + 8
                kind, n = struct.unpack_from("<II", b, p)
                if not 0 < n < 64:
                    continue
                name = b[p + 8:p + 8 + 2 * n].decode("utf-16-le", "replace")
                if kind not in (5, 6):
                    continue
                q = p + 8 + 2 * n
                while q < len(b) and b[q] == 0:
                    q += 1
                key = struct.unpack_from("<I", b, q)[0]
                end = starts[k + 1] if k + 1 < len(starts) else first_var
                self._tcache[key] = name
                self._alt_window = getattr(self, "_alt_window", {})   # built-in types keep their key further on
                self._alt_window[name] = b[q:min(q + 400, end)]
                mem, mtype = {}, {}
                for mm in re.finditer(rb"(?:[A-Za-z_]\x00)(?:[A-Za-z0-9_]\x00)*", b[q + 12:end]):
                    st = q + 12 + mm.start()
                    idx, ln = struct.unpack_from("<II", b, st - 8)
                    if ln * 2 == len(mm.group()) and 0 < idx < 1000 and idx not in mem:
                        mem[idx] = mm.group().decode("utf-16-le")
                        t = struct.unpack_from("<I", b, st + 2 * ln)[0]
                        mtype[idx] = SIMPLE.get(t, self._tcache.get(t, f"#{t}"))
                self._members[key], self._member_types[key] = mem, mtype
        return self._tcache

    def variables(self, prefix: str = "", elements: bool = True) -> list[dict]:
        out = []
        for name in sorted(self.vars):
            if prefix and not name.startswith(prefix):
                continue
            v = self.vars[name]
            row = {"name": name, "kind": "Variable", **self._describe(v["props"])}
            if row["type"] not in SIMPLE_ID and row["source"] != "InternalReference":
                row["kind"] = "Structure"
            out.append(row)
            if elements and row["kind"] == "Structure":
                tid = {x.pid: x for x in v["props"]}[1].value
                self._type_names
                mem = self._members.get(tid, {})
                mtypes = self._member_types.get(tid, {})
                for key, at, pr, pe, end in self._elements(v):
                    e = {"name": f"{name}.{mem.get(key, key)}", "kind": "Element",
                         **self._describe(pr, mtypes.get(key))}
                    if e["source"] == "Internal" and row["source"] == "External":
                        e["source"], e["scan_group"] = "External", row.get("scan_group")
                    out.append(e)
        return out

    def folder_list(self) -> list[str]:
        return sorted(self.folder_path.values())

    def data_types(self) -> dict:
        """Structure types defined in the project: name -> [(member, type), ...] in member order."""
        names = self._type_names
        return {names[k]: [(m[i], self._member_types[k].get(i)) for i in sorted(m)]
                for k, m in self._members.items() if m}

    # ------------------------------------------------------------------ writing helpers
    def _splice(self, start, end, new: bytes):
        self.b[start:end] = new
        struct.pack_into("<I", self.b, 4, len(self.b) - 9)

    def _set_props(self, start, props_end, props):
        new = _encode_props(props)
        self._splice(start + 12, props_end, new)

    def _find(self, name):
        if name in self.vars:
            v = self.vars[name]
            return v["at"], v["props"], v["props_end"], None
        inst, _, member = name.rpartition(".")
        if inst in self.vars:
            v = self.vars[inst]
            tid = {x.pid: x for x in v["props"]}.get(1)
            self._type_names
            mem = {n: i for i, n in self._members.get(tid.value if tid else None, {}).items()}
            if member in mem:
                for key, at, pr, pe, end in self._elements(v):
                    if key == mem[member]:
                        return at, pr, pe, inst
        raise KeyError(f"No variable or structure element '{name}' in the project")

    def _new_name_id(self):
        cur = struct.unpack_from("<I", self.ns, self.ns_counter_at)[0] + 1
        struct.pack_into("<I", self.ns, self.ns_counter_at, cur)
        return cur

    def _ns_add_child(self, node_id, child_id, name):
        at, kids_end = self.nodes[node_id]
        data = name.encode("utf-16-le") + b"\x00\x00"
        self.ns[kids_end:kids_end] = struct.pack("<II", child_id, len(data)) + data
        struct.pack_into("<H", self.ns, at + 8, struct.unpack_from("<H", self.ns, at + 8)[0] + 1)

    def _ns_remove_child(self, child_id):
        node_id, p, size = self.name_at[child_id]
        at, _ = self.nodes[node_id]
        del self.ns[p:p + size]
        struct.pack_into("<H", self.ns, at + 8, struct.unpack_from("<H", self.ns, at + 8)[0] - 1)

    def _group_id(self, kind, name):
        for gid, n in self._groups[kind].items():
            if n.lower() == str(name).lower():
                return gid
        raise KeyError(f"No {kind} group '{name}'. Groups: {sorted(self._groups[kind].values())}")

    # ------------------------------------------------------------------ settings
    def _apply(self, props, dtype, s: dict, template_props=None, element=False):
        d = {x.pid: x for x in props}
        if element:                          # source / scan group belong to the instance; address is per element
            s = dict(s)
            if "address" in s:
                addr = s.pop("address")
                if addr:
                    d[P["address"]] = Prop.make(P["address"], 4, str(addr))
                else:
                    d.pop(P["address"], None)

        def put(key, ptype, value):
            d[P[key]] = Prop.make(P[key], ptype, value)

        def drop(*keys):
            for k in keys:
                d.pop(P[k], None)

        if "description" in s:
            if s["description"]:
                put("description", 4, s["description"])
            else:
                drop("description")
        if "initial_value" in s:
            iv = s["initial_value"]
            if iv is None or iv == "":
                drop("initial")
            elif dtype == "STRING":
                put("initial", 4, str(iv))
            elif dtype == "REAL":
                put("initial", 5, float(iv))
            else:
                put("initial", 3, int(iv))
        if "source" in s or "scan_group" in s or "address" in s:
            src = s.get("source") or ("External" if P["scan_group"] in d or "scan_group" in s else "Internal")
            if src == "External":
                if "scan_group" in s or P["scan_group"] not in d:
                    gname = s.get("scan_group") or (next(iter(self._groups["scan"].values()), None))
                    put("scan_group", 0x14, self._group_id("scan", gname))
                d.setdefault(P["external"], Prop.make(P["external"], 7, 1))
                for x in (template_props or []):                # data-format props of external variables
                    if x.pid in (0x2F, 0x30, 0x40) and x.pid not in d:
                        d[x.pid] = x
                if "address" in s:
                    put("address", 4, s["address"])
                if P["address"] not in d:
                    raise ValueError("External variables need an address")
            elif src == "Internal":
                drop("scan_group", "external", "address")
            else:
                raise ValueError("source must be Internal or External")
        if "alarm" in s:
            a = s["alarm"]
            if not a:
                drop("alarm", "alarm_message", "severity", "alarm_group")
            else:
                a = {} if a is True else dict(a)
                put("alarm", 2, 1)
                put("severity", 2, int(a.get("severity", d[P["severity"]].value if P["severity"] in d else 1)))
                gname = a.get("group") or (self._groups["alarm"].get(d[P["alarm_group"]].value)
                                            if P["alarm_group"] in d else next(iter(self._groups["alarm"].values())))
                put("alarm_group", 0x14, self._group_id("alarm", gname))
                if "message" in a or P["alarm_message"] not in d:
                    msg = a.get("message", "")
                    if self.lang is None:
                        raise ValueError("alarm messages need the project's language table")
                    if P["alarm_message"] in d and d[P["alarm_message"]].value in self.lang:
                        self.lang.set_text(d[P["alarm_message"]].value, msg)
                    else:
                        some = next((x for x in self.lang.entries if x > 1000), None)
                        tid = self.lang.clone(some, msg)
                        put("alarm_message", 6, tid)
        if "logging_group" in s:
            if s["logging_group"]:
                put("logging_group", 0x14, self._group_id("logging", s["logging_group"]))
            else:
                drop("logging_group")
        if "input_range" in s:
            r = s["input_range"]
            if not r:
                drop("input_range", "min", "max")
            else:
                lo, hi = r
                if not float(lo) < float(hi):
                    raise ValueError("input_range: min must be below max")
                put("input_range", 2, 1)
                pt = 5 if dtype == "REAL" else 3
                put("min", pt, lo if pt == 5 else int(lo))
                put("max", pt, hi if pt == 5 else int(hi))
        if "sharing" in s:
            if s["sharing"] in (None, "", "None"):
                drop("sharing")
            elif str(s["sharing"]).lower() == "read only":
                put("sharing", 8, 2)
            else:
                raise ValueError("sharing: 'None' or 'Read Only'")
        if "string_bytes" in s:
            if dtype != "STRING":
                raise ValueError("string_bytes applies to STRING variables")
            put("bytes", 3, int(s["string_bytes"]))
        return list(d.values())

    def update(self, name: str, **settings) -> dict:
        at, props, pe, inst = self._find(name)
        before = next(r for r in self.variables() if r["name"] == name)
        dtype = before["type"]
        if inst and ("source" in settings or "scan_group" in settings):
            raise ValueError("Data source and scan group are set on the structure instance, not on its elements")
        tmpl = self._template_props(dtype, "External")
        new = self._apply(props, dtype, settings, tmpl, element=bool(inst))
        self._set_props(at, pe, new)
        self._reindex()
        after = next(r for r in self.variables() if r["name"] == name)
        return {"variable": name, "from": before, "to": after}

    def _template_props(self, dtype, source):
        for n, v in self.vars.items():
            r = self._describe(v["props"])
            if r["type"] == dtype and r["source"] == source:
                return v["props"]
        return None

    def _reindex(self):
        for attr in ("_gcache",):
            if hasattr(self, attr):
                delattr(self, attr)
        self._index()

    # ------------------------------------------------------------------ add / delete
    def add_variable(self, name: str, data_type: str, source: str = "Internal", **settings) -> dict:
        folder, _, short = name.rpartition(".")
        if name in self.vars:
            raise ValueError(f"'{name}' already exists")
        if not short.replace("_", "a").isalnum() or short[0].isdigit():
            raise ValueError(f"Invalid variable name '{short}'")
        dtype = data_type.upper()
        if dtype not in SIMPLE_ID:
            raise ValueError(f"data_type must be one of {sorted(SIMPLE_ID)} (structures: add_structure_instance)")
        if folder and folder not in self.folder_by_path:
            self.add_folder(folder)
        foid = self.folder_by_path.get(folder, 0)
        cont = self.folders[foid]["container"] if foid else self._root_node()
        if any(self.names.get(c) == short for c in self._children_of(cont)):
            raise ValueError(f"'{name}' already exists (as a folder or variable)")
        base = self._template_props(dtype, source) or self._template_props(dtype, "Internal") \
            or self._template_props("BOOL", "Internal")
        keep = {1, 4, 0x43, 0x44, 0x51}                        # identity / type / fixed flags of the template
        props = [x for x in base if x.pid in keep]
        oid = struct.unpack_from("<I", self.b, self.next_id_at)[0]
        nsid = self._new_name_id()
        d = {x.pid: x for x in props}
        d[4] = Prop.make(4, 0x10, nsid)
        if dtype == "BOOL":
            d.pop(1, None)
        else:
            d[1] = Prop.make(1, 1, SIMPLE_ID[dtype])
        if foid:
            d[0x51] = Prop.make(0x51, 0x15, foid)
        else:
            d.pop(0x51, None)
        tmpl_ext = self._template_props(dtype, "External")
        s = dict(settings)
        s["source"] = source
        if dtype == "STRING":
            s.setdefault("string_bytes", 20)
        new_props = self._apply(list(d.values()), dtype, s, tmpl_ext)
        rec = struct.pack("<I", oid) + VAR + _encode_props(new_props) + struct.pack("<II", 0, 0) + MARK
        self._splice(self.list_end, self.list_end, rec)
        at = self.next_id_at + len(rec)
        struct.pack_into("<I", self.b, at, oid + 1)
        struct.pack_into("<I", self.b, self.count_at, struct.unpack_from("<I", self.b, self.count_at)[0] + 1)
        self._ns_add_child(cont, nsid, short)
        self._reindex()
        return next(r for r in self.variables() if r["name"] == name)

    def _root_node(self):
        top = [f for f in self.folders.values() if not f["parent"]]
        return self.name_at[top[0]["name_ns"]][0]

    def _children_of(self, node_id):
        return [c for c, (nid, _, _) in self.name_at.items() if nid == node_id]

    def delete_variable(self, name: str) -> dict:
        if name not in self.vars:
            raise KeyError(f"No top-level variable '{name}' (elements are removed with their structure)")
        v = self.vars[name]
        d = {x.pid: x for x in v["props"]}
        self._splice(v["at"], v["end"], b"")
        struct.pack_into("<I", self.b, self.count_at, struct.unpack_from("<I", self.b, self.count_at)[0] - 1)
        self._ns_remove_child(d[4].value)
        self._reindex()
        return {"deleted": name, "id": v["oid"]}

    def add_structure_instance(self, name: str, like: str, addresses: dict | None = None) -> dict:
        """New structure instance cloned from an existing instance `like` (same type and element settings).
        addresses {member: address} replaces element addresses (otherwise they are the source's)."""
        r = self._clone_structure(name, like)
        for member, addr in (addresses or {}).items():
            self.update(f"{name}.{member}", address=addr)
        r["addresses_set"] = len(addresses or {})
        return r

    def _clone_structure(self, name: str, like: str) -> dict:
        if like not in self.vars:
            raise KeyError(f"No structure instance '{like}'")
        if name in self.vars:
            raise ValueError(f"'{name}' already exists")
        src = self.vars[like]
        folder, _, short = name.rpartition(".")
        if folder and folder not in self.folder_by_path:
            self.add_folder(folder)
        foid = self.folder_by_path.get(folder, 0)
        cont = self.folders[foid]["container"] if foid else self._root_node()
        oid = struct.unpack_from("<I", self.b, self.next_id_at)[0]
        nsid = self._new_name_id()
        rec = bytearray(self.b[src["at"]:src["end"]])
        struct.pack_into("<I", rec, 0, oid)
        d = {x.pid: x for x in src["props"]}
        d[4] = Prop.make(4, 0x10, nsid)
        if foid:
            d[0x51] = Prop.make(0x51, 0x15, foid)
        new_props = _encode_props(list(d.values()))
        rec[12:src["props_end"] - src["at"]] = new_props
        # element alarm messages: own texts
        self._splice(self.list_end, self.list_end, bytes(rec))
        at = self.next_id_at + len(rec)
        struct.pack_into("<I", self.b, at, oid + 1)
        struct.pack_into("<I", self.b, self.count_at, struct.unpack_from("<I", self.b, self.count_at)[0] + 1)
        self._ns_add_child(cont, nsid, short)
        self._reindex()
        v = self.vars[name]
        if self.lang is not None:
            for key, eat, pr, pe, end in self._elements(v):
                dd = {x.pid: x for x in pr}
                if P["alarm_message"] in dd and dd[P["alarm_message"]].value in self.lang:
                    old = self.lang.text(dd[P["alarm_message"]].value)
                    tid = self.lang.clone(dd[P["alarm_message"]].value, old.replace(like.rsplit(".", 1)[-1], short))
                    dd[P["alarm_message"]] = Prop.make(P["alarm_message"], 6, tid)
                    self._set_props(eat, pe, list(dd.values()))
                    self._reindex()
                    v = self.vars[name]
        return {"name": name, "like": like, "id": oid}

    # ------------------------------------------------------------------ folders
    def add_folder(self, path: str) -> dict:
        if path in self.folder_by_path:
            return {"folder": path, "created": False}
        parent, _, short = path.rpartition(".")
        if parent and parent not in self.folder_by_path:
            self.add_folder(parent)
        poid = self.folder_by_path.get(parent, 0)
        pnode = self.folders[poid]["container"] if poid else self._root_node()
        if any(self.names.get(c) == short for c in self._children_of(pnode)):
            raise ValueError(f"'{path}' already exists")
        last = struct.unpack_from("<I", self.b, self.folder_hdr)[0]
        oid = max(last, max(self.folders)) + 1
        cont, name_ns = self._new_name_id(), self._new_name_id()
        rec = struct.pack("<I", oid) + FOLDER + struct.pack("<6I", oid, poid, cont, name_ns, 0, 1) + MARK
        self._splice(self.folders_end, self.folders_end, rec)
        struct.pack_into("<I", self.b, self.folder_hdr, oid)
        struct.pack_into("<I", self.b, self.folder_hdr + 4, struct.unpack_from("<I", self.b, self.folder_hdr + 4)[0] + 1)
        # NameServer: the name in the parent's container, and a new (empty) container node for the folder
        self._ns_add_child(pnode, name_ns, short)
        self._reindex()
        at, kids_end = self.nodes[pnode]
        node = struct.pack("<HIHH", 1, cont, 0, 0) + struct.pack("<H", 0)
        # insert the new node right after the parent's node
        end_of_parent = kids_end + 2
        self.ns[end_of_parent:end_of_parent] = node
        self._reindex()
        return {"folder": path, "created": True, "id": oid}

    def delete_folder(self, path: str) -> dict:
        if path not in self.folder_by_path:
            raise KeyError(f"No folder '{path}'")
        oid = self.folder_by_path[path]
        f = self.folders[oid]
        if self._children_of(f["container"]):
            raise ValueError(f"Folder '{path}' is not empty")
        at, kids_end = self.nodes[f["container"]]
        del self.ns[at:kids_end + 2]
        self._reindex()
        self._ns_remove_child(f["name_ns"])
        start = f["at"]
        self._splice(start, start + 40, b"")
        struct.pack_into("<I", self.b, self.folder_hdr + 4, struct.unpack_from("<I", self.b, self.folder_hdr + 4)[0] - 1)
        self._reindex()
        return {"deleted": path}

    # ------------------------------------------------------------------ data types
    TYPE_CODES = {"BOOL": 1, "REAL": 2, "STRING": 3, "INT": 8, "UINT": 9, "DINT": 10, "UDINT": 11}

    def _type_records(self) -> dict:
        """Structure type records (4003, kind 5): name -> layout.
           u32 kind 5 | u32 chars | name | padding | u32 key | u32 next member index | u32 member count |
           members (u32 index | u32 chars | name | u32 type | 8 zero bytes) | u32 n | n x index (order) | end"""
        import re
        b = self.b
        cls = struct.pack("<I", 4003) + EF
        starts = [m.start() for m in re.finditer(re.escape(cls), bytes(b))]
        out = {}
        for s in starts:
            p = s + 8
            kind, n = struct.unpack_from("<II", b, p)
            if kind != 5 or not 0 < n < 64:
                continue
            name = bytes(b[p + 8:p + 8 + 2 * n]).decode("utf-16-le", "replace")
            q = p + 8 + 2 * n
            while b[q] == 0:
                q += 1
            key, next_idx, count = struct.unpack_from("<III", b, q)
            m, members = q + 12, []
            try:
                for _ in range(count):
                    idx, ln = struct.unpack_from("<II", b, m)
                    mname = bytes(b[m + 8:m + 8 + 2 * ln]).decode("utf-16-le")
                    mtype = struct.unpack_from("<I", b, m + 8 + 2 * ln)[0]
                    members.append({"index": idx, "name": mname, "type": mtype, "at": m, "end": m + 20 + 2 * ln})
                    m += 20 + 2 * ln
                order_n = struct.unpack_from("<I", b, m)[0]
                if order_n != count or b[m + 4 + 4 * order_n:m + 8 + 4 * order_n] != MARK:
                    continue
            except (struct.error, UnicodeDecodeError):
                continue
            out[name] = {"at": s, "key": key, "key_at": q, "members": members, "order_at": m,
                         "end": m + 8 + 4 * order_n}
        return out

    def _types_counter_at(self):
        recs = sorted(r["end"] for r in self._type_records().values())
        c = max(recs)
        # after the last type record: u32 next type key | end marker
        if self.b[c + 4:c + 8] != MARK:
            raise ValueError("end of the type list not recognised")
        return c

    def _instances_of(self, key):
        return [n for n, v in self.vars.items()
                if any(x.pid == 1 and x.value == key for x in v["props"]) and not any(x.pid == 0x5F for x in v["props"])]

    def add_type_member(self, type_name: str, member: str, member_type: str) -> dict:
        """Add a member to a structure type; every instance gets the new element (no address yet)."""
        types = self._type_records()
        if type_name not in types:
            raise KeyError(f"No structure type '{type_name}'. Types: {sorted(types)}")
        t = types[type_name]
        if any(m["name"] == member for m in t["members"]):
            raise ValueError(f"{type_name} already has a member '{member}'")
        code = self.TYPE_CODES.get(member_type.upper())
        if code is None:
            raise ValueError(f"member_type: {sorted(self.TYPE_CODES)}")
        next_idx, count = struct.unpack_from("<II", self.b, t["key_at"] + 4)
        idx = next_idx
        entry = struct.pack("<II", idx, len(member)) + member.encode("utf-16-le") + struct.pack("<III", code, 0, 0)
        # order list: append the index; then the member entry before the list
        order_at = t["order_at"]
        list_end = order_at + 4 + 4 * count
        self._splice(list_end, list_end, struct.pack("<I", idx))
        struct.pack_into("<I", self.b, order_at, count + 1)
        self._splice(order_at, order_at, entry)
        struct.pack_into("<II", self.b, t["key_at"] + 4, next_idx + 1, count + 1)
        self._reindex()
        # every instance: one more element record (cloned from a sibling of the same type if possible)
        done = []
        for inst in self._instances_of(t["key"]):
            self._add_element(inst, idx, code)
            done.append(inst)
        self._reindex()
        self.__dict__.pop("_tcache", None)
        return {"type": type_name, "member": member, "index": idx, "instances_updated": done}

    def _add_element(self, inst, idx, code):
        v = self.vars[inst]
        els = self._elements(v)
        mtypes = {}
        self._type_names
        key = {x.pid: x for x in v["props"]}[1].value
        mtypes = self._member_types.get(key, {})
        want = {v2: k2 for k2, v2 in SIMPLE.items()}.get
        sib = next((e for e in els if SIMPLE_ID.get(mtypes.get(e[0])) == code), None)
        keep = (0x43, 0x44, 0x2F, 0x30, 0x40)                    # structural flags, no settings
        props = [x for x in (sib[2] if sib else []) if x.pid in keep]
        rec = struct.pack("<I", idx) + VAR + _encode_props(props) + struct.pack("<II", 0, 0) + MARK
        # instance post: u32 0 | u32 child count | children | end marker
        q = v["props_end"]
        n = struct.unpack_from("<I", self.b, q + 4)[0]
        end_children = els[-1][4] if els else q + 8
        self._splice(end_children, end_children, rec)
        struct.pack_into("<I", self.b, q + 4, n + 1)
        self._reindex()

    def remove_type_member(self, type_name: str, member: str) -> dict:
        types = self._type_records()
        if type_name not in types:
            raise KeyError(f"No structure type '{type_name}'")
        t = types[type_name]
        m = next((x for x in t["members"] if x["name"] == member), None)
        if m is None:
            raise KeyError(f"{type_name} has no member '{member}'")
        instances = self._instances_of(t["key"])
        for inst in instances:                                   # remove the element everywhere
            v = self.vars[inst]
            for key, at, pr, pe, end in self._elements(v):
                if key == m["index"]:
                    q = v["props_end"]
                    n = struct.unpack_from("<I", self.b, q + 4)[0]
                    self._splice(at, end, b"")
                    struct.pack_into("<I", self.b, q + 4, n - 1)
                    self._reindex()
                    break
        t = self._type_records()[type_name]
        count = struct.unpack_from("<I", self.b, t["key_at"] + 8)[0]
        order = [struct.unpack_from("<I", self.b, t["order_at"] + 4 + 4 * i)[0] for i in range(count)]
        order.remove(m["index"])
        # members + order list are contiguous: rewrite them in one step
        start, stop = t["members"][0]["at"], t["order_at"] + 4 + 4 * count
        entries = b"".join(bytes(self.b[x["at"]:x["end"]]) for x in t["members"] if x["name"] != member)
        self._splice(start, stop, entries + struct.pack("<I", count - 1) + b"".join(struct.pack("<I", i) for i in order))
        struct.pack_into("<I", self.b, t["key_at"] + 8, count - 1)
        self._reindex()
        self.__dict__.pop("_tcache", None)
        return {"type": type_name, "removed": member, "instances_updated": instances}

    def add_type(self, type_name: str, members: list) -> dict:
        """New structure type: members [[name, type], ...] (BOOL, INT, UINT, DINT, UDINT, REAL, STRING)."""
        types = self._type_records()
        if type_name in types or type_name.upper() in self.TYPE_CODES:
            raise ValueError(f"type '{type_name}' already exists")
        counter_at = self._types_counter_at()
        key = struct.unpack_from("<I", self.b, counter_at)[0]
        ents, order = b"", b""
        for i, (mname, mtype) in enumerate(members, 1):
            code = self.TYPE_CODES.get(str(mtype).upper())
            if code is None:
                raise ValueError(f"member '{mname}': type must be one of {sorted(self.TYPE_CODES)}")
            ents += struct.pack("<II", i, len(mname)) + mname.encode("utf-16-le") + struct.pack("<III", code, 0, 0)
            order += struct.pack("<I", i)
        n = len(members)
        rec = (struct.pack("<I", 4003) + EF + struct.pack("<II", 5, len(type_name)) + type_name.encode("utf-16-le")
               + b"\x00" * 9 + struct.pack("<III", key, n + 1, n) + ents + struct.pack("<I", n) + order + MARK)
        self._splice(counter_at, counter_at, rec)
        struct.pack_into("<I", self.b, counter_at + len(rec), key + 1)
        struct.pack_into("<I", self.b, 0x18, struct.unpack_from("<I", self.b, 0x18)[0] + 1)
        self._reindex()
        self.__dict__.pop("_tcache", None)
        return {"type": type_name, "key": key, "members": n}

    def delete_type(self, type_name: str) -> dict:
        types = self._type_records()
        if type_name not in types:
            raise KeyError(f"No structure type '{type_name}'")
        t = types[type_name]
        if self._instances_of(t["key"]):
            raise ValueError(f"'{type_name}' still has instances: {self._instances_of(t['key'])}")
        self._splice(t["at"], t["end"], b"")
        struct.pack_into("<I", self.b, 0x18, struct.unpack_from("<I", self.b, 0x18)[0] - 1)
        self._reindex()
        self.__dict__.pop("_tcache", None)
        return {"deleted_type": type_name}

    def new_structure_instance(self, name: str, type_name: str, source: str = "Internal", scan_group: str = "",
                               addresses: dict | None = None) -> dict:
        """Structure instance built from the type definition (no existing instance needed)."""
        types = self._type_records()
        if type_name not in types:
            raise KeyError(f"No structure type '{type_name}'")
        t = types[type_name]
        if name in self.vars:
            raise ValueError(f"'{name}' already exists")
        folder, _, short = name.rpartition(".")
        if folder and folder not in self.folder_by_path:
            self.add_folder(folder)
        foid = self.folder_by_path.get(folder, 0)
        cont = self.folders[foid]["container"] if foid else self._root_node()
        oid = struct.unpack_from("<I", self.b, self.next_id_at)[0]
        nsid = self._new_name_id()
        props = [Prop.make(1, 1, t["key"]), Prop.make(4, 0x10, nsid)]
        if foid:
            props.append(Prop.make(0x51, 0x15, foid))
        if source == "External":
            gname = scan_group or next(iter(self._groups["scan"].values()))
            props += [Prop.make(0x4F, 0x14, self._group_id("scan", gname)), Prop.make(0x56, 7, 1)]
        addresses = addresses or {}
        kids = b""
        for m in t["members"]:
            ep = [Prop.make(0x2B, 4, addresses[m["name"]])] if m["name"] in addresses else []
            kids += struct.pack("<I", m["index"]) + VAR + _encode_props(ep) + struct.pack("<II", 0, 0) + MARK
        rec = (struct.pack("<I", oid) + VAR + _encode_props(props) + struct.pack("<II", 0, len(t["members"]))
               + kids + MARK)
        self._splice(self.list_end, self.list_end, rec)
        struct.pack_into("<I", self.b, self.next_id_at + len(rec), oid + 1)
        struct.pack_into("<I", self.b, self.count_at, struct.unpack_from("<I", self.b, self.count_at)[0] + 1)
        self._ns_add_child(cont, nsid, short)
        self._reindex()
        return {"name": name, "type": type_name, "id": oid, "elements": len(t["members"])}

    def data(self) -> tuple[bytes, bytes]:
        return bytes(self.b), bytes(self.ns)


def _find_all(b, pat):
    i = b.find(pat)
    while i != -1:
        yield i
        i = b.find(pat, i + 1)
