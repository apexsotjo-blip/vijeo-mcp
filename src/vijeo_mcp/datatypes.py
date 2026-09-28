"""Vijeo Designer user data types export (.VJDDataTypes, XML).

Edits are text-level (the exact <field> blocks are cut out) so everything else
in the file stays byte-identical, then the result is re-parsed and checked.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .safety import check_output


def _x(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


class DataTypes:
    def __init__(self, path: str) -> None:
        self.path = path
        with open(path, "rb") as f:
            self.raw = f.read().decode("utf-8")
        self.root = ET.fromstring(self.raw)
        self.eol = "\r\n" if "\r\n" in self.raw else "\n"

    def types(self) -> dict[str, list[tuple[str, str]]]:
        """Named (non-anonymous) structures -> [(field, type), ...] in order."""
        return {s.get("name"): [(f.get("name"), f.get("type")) for f in s.findall("field")]
                for s in self.root.findall("structure") if s.get("isAnonymous") != "true"}

    def summary(self) -> list[dict]:
        return [{"type": n, "fields": len(f)} for n, f in self.types().items()]

    # -------------------------------------------------------------- general editor
    VIJEO_TYPES = {"BOOL", "INT", "UINT", "DINT", "UDINT", "REAL", "String"}

    def _field_xml(self, name, ftype, comment="", string_bytes=20):
        e, ind = self.eol, "    "
        ftype = "String" if ftype.upper() == "STRING" else ftype
        props = (f"{ind}  <configuredProperties>{e}{ind}    <stringNoOfBytes>{e}{ind}      <pvInt>{string_bytes}</pvInt>{e}"
                 f"{ind}    </stringNoOfBytes>{e}{ind}  </configuredProperties>{e}") if ftype == "String" \
            else f"{ind}  <configuredProperties/>{e}"
        return (f'{ind}<field name="{name}" type="{ftype}">{e}{ind}  <comment>{_x(comment)}</comment>{e}'
                f"{props}{ind}</field>{e}")

    def _block(self, text, tname):
        m = re.search(rf'[ \t]*<structure name="{re.escape(tname)}" isAnonymous="false"[^>]*>.*?</structure>{re.escape(self.eol)}',
                      text, re.S)
        if not m:
            raise KeyError(f"Type '{tname}' not found. Types: {sorted(self.types())}")
        return m

    def edit(self, ops: list[dict], out_path: str, overwrite: bool = False) -> dict:
        """Apply several operations, self-check, write a NEW file. Operations:
          {"op": "add_type", "type": "PUMP", "fields": [["Run","BOOL"], ["Speed","REAL"], ...], "comment": ""}
          {"op": "add_members", "type": "AI", "fields": [["Quality","INT"]], "after": "ActiveValue"}  (after optional)
          {"op": "remove_members", "type": "AI", "members": ["TotAbs"]}
          {"op": "rename_member", "type": "AI", "member": "TotAbs", "new_name": "Total"}
          {"op": "retype_member", "type": "AI", "member": "Quality", "new_type": "DINT"}
          {"op": "delete_type", "type": "PUMP"}
        Field types: BOOL, INT, UINT, DINT, UDINT, REAL, STRING (optionally ['Name','STRING',bytes]) or another
        named data type."""
        out = check_output(out_path, self.path, overwrite=overwrite)
        text, model = self.raw, {k: list(v) for k, v in self.types().items()}
        e = self.eol
        valid = lambda t: t in self.VIJEO_TYPES or t.upper() == "STRING" or t in model   # noqa: E731
        name_ok = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")
        for op in ops:
            kind, t = op["op"], op.get("type")
            if kind == "add_type":
                if t in model or not name_ok.fullmatch(t):
                    raise ValueError(f"Type '{t}' exists or has an invalid name.")
                fields = [tuple(f) for f in op["fields"]]
                for f in fields:
                    if not valid(f[1]):
                        raise ValueError(f"Unknown field type {f[1]}")
                tid = max(int(s.get("typeID")) for s in ET.fromstring(text).findall("structure")) + 1
                body = "".join(self._field_xml(f[0], f[1], string_bytes=f[2] if len(f) > 2 else 20) for f in fields)
                block = (f'  <structure name="{t}" isAnonymous="false" typeID="{tid}">{e}    <comment>{_x(op.get("comment", ""))}'
                         f"</comment>{e}{body}  </structure>{e}")
                text = text.replace("</typeList>", block + "</typeList>", 1)
                model[t] = [(f[0], "String" if f[1].upper() == "STRING" else f[1]) for f in fields]
            elif kind == "add_members":
                m = self._block(text, t); blk = m.group(0)
                new = [tuple(f) for f in op["fields"]]
                for f in new:
                    if f[0] in [x for x, _ in model[t]] or not valid(f[1]):
                        raise ValueError(f"{t}.{f[0]}: exists or unknown type {f[1]}")
                xml = "".join(self._field_xml(f[0], f[1], string_bytes=f[2] if len(f) > 2 else 20) for f in new)
                after = op.get("after")
                if after:
                    fm = re.search(rf'[ \t]*<field name="{re.escape(after)}"[^>]*>.*?</field>{re.escape(e)}', blk, re.S)
                    if not fm:
                        raise KeyError(f"{t} has no member {after}")
                    nb = blk[:fm.end()] + xml + blk[fm.end():]
                    pos = [x for x, _ in model[t]].index(after) + 1
                else:
                    nb = blk.replace("  </structure>", xml + "  </structure>", 1)
                    pos = len(model[t])
                text = text.replace(blk, nb, 1)
                model[t][pos:pos] = [(f[0], "String" if f[1].upper() == "STRING" else f[1]) for f in new]
            elif kind == "remove_members":
                m = self._block(text, t); blk = nb = m.group(0)
                for mem in op["members"]:
                    fm = re.search(rf'[ \t]*<field name="{re.escape(mem)}"[^>]*>.*?</field>{re.escape(e)}', nb, re.S)
                    if not fm:
                        raise KeyError(f"{t} has no member {mem}")
                    nb = nb.replace(fm.group(0), "", 1)
                text = text.replace(blk, nb, 1)
                model[t] = [x for x in model[t] if x[0] not in set(op["members"])]
            elif kind in ("rename_member", "retype_member"):
                m = self._block(text, t); blk = m.group(0)
                mem = op["member"]
                fm = re.search(rf'<field name="{re.escape(mem)}" type="([^"]+)">', blk)
                if not fm:
                    raise KeyError(f"{t} has no member {mem}")
                if kind == "rename_member":
                    nn = op["new_name"]
                    if not name_ok.fullmatch(nn) or nn in [x for x, _ in model[t]]:
                        raise ValueError(f"Invalid or duplicate member name {nn}")
                    nb = blk.replace(fm.group(0), f'<field name="{nn}" type="{fm.group(1)}">', 1)
                    model[t] = [(nn if x == mem else x, y) for x, y in model[t]]
                else:
                    nt = op["new_type"]
                    if not valid(nt) or nt.upper() == "STRING":
                        raise ValueError("retype_member: use a numeric/BOOL type (remove + add for STRING).")
                    nb = blk.replace(fm.group(0), f'<field name="{mem}" type="{nt}">', 1)
                    model[t] = [(x, nt if x == mem else y) for x, y in model[t]]
                text = text.replace(blk, nb, 1)
            elif kind == "delete_type":
                users = [n for n, fl in model.items() if n != t and any(ft == t for _, ft in fl)]
                if users:
                    raise ValueError(f"Type {t} is used by {users}")
                m = self._block(text, t)
                text = text.replace(m.group(0), "", 1)
                del model[t]
            else:
                raise ValueError(f"Unknown op {kind}")
        after = {s.get("name"): [(f.get("name"), f.get("type")) for f in s.findall("field")]
                 for s in ET.fromstring(text).findall("structure") if s.get("isAnonymous") != "true"}
        if after != model:
            raise RuntimeError("Self-check failed: the edited file does not match the intended types; nothing written.")
        with open(out, "wb") as fh:
            fh.write(text.encode("utf-8"))
        return {"written": out, "operations": len(ops), "types": {k: len(v) for k, v in model.items()}}

    def remove_members(self, removals: dict[str, list[str]], out_path: str, overwrite: bool = False) -> dict:
        """removals: {type name: [member, ...]}. Writes a new file; self-checks before writing."""
        out = check_output(out_path, self.path, overwrite=overwrite)
        types = self.types()
        for t, mem in removals.items():
            if t not in types:
                raise KeyError(f"Type '{t}' not found. Types: {sorted(types)}")
            unknown = sorted(set(mem) - {f for f, _ in types[t]})
            if unknown:
                raise KeyError(f"{t} has no member(s) {unknown}")
        e = re.escape(self.eol)
        text = self.raw
        for t, mem in removals.items():
            m = re.search(rf'[ \t]*<structure name="{re.escape(t)}"[^>]*>.*?</structure>{e}', text, re.S)
            block = new = m.group(0)
            for f in re.finditer(rf'[ \t]*<field name="(\w+)"[^>]*>.*?</field>{e}', block, re.S):
                if f.group(1) in mem:
                    new = new.replace(f.group(0), "", 1)
            text = text.replace(block, new, 1)
        after = {s.get("name"): [x.get("name") for x in s.findall("field")]
                 for s in ET.fromstring(text).findall("structure") if s.get("isAnonymous") != "true"}
        report = []
        for n, flds in types.items():
            expected = [f for f, _ in flds if f not in set(removals.get(n, []))]
            if after.get(n) != expected:
                raise RuntimeError(f"Self-check failed on type {n}; nothing written.")
            if n in removals:
                report.append({"type": n, "fields_before": len(flds), "fields_after": len(expected),
                               "removed": sorted(removals[n])})
        with open(out, "wb") as fh:
            fh.write(text.encode("utf-8"))
        return {"written": out, "types": report}
