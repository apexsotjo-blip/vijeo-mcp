"""Vijeo Designer variable CSV exports: read, edit (into a NEW file), validate.

Row types in an export: Folder, Variable, DDTVariable (structure instance),
SubVariable (structure element). Column positions come from the header row, so
exports from other Vijeo versions still work as long as the column names match.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from . import csvtok
from .safety import check_output

ROW_TYPES = {"Folder", "Variable", "DDTVariable", "SubVariable", "StructureVariable", "ArrayVariable"}
SIMPLE_TYPES = {"BOOL", "INT", "UINT", "DINT", "UDINT", "Integer", "REAL", "STRING"}
_REF = re.compile(r'^"?(?P<target>[A-Za-z0-9_.%]+)\((?P<selector>[A-Za-z0-9_.]+)\s*\)"?$')


def split_element(name: str) -> tuple[str, str]:
    """'A.B.member' -> ('A.B', 'member');  'LAMPS01[3]' -> ('LAMPS01', '[3]');  'X' -> ('', 'X')."""
    if name.endswith("]") and "[" in name:
        i = name.rindex("[")
        if "." not in name[i:]:
            return name[:i], name[i:]
    if "." in name:
        p, m = name.rsplit(".", 1)
        return p, m
    return "", name


def is_child(name: str, parent: str) -> bool:
    return name.startswith(parent + ".") or name.startswith(parent + "[")


@dataclass
class Var:
    kind: str
    name: str
    dtype: str
    source: str
    line_no: int
    tokens: list[str]

    def get(self, table: "VariableTable", column: str) -> str:
        i = table.col.get(column)
        return csvtok.unquote(self.tokens[i]) if i is not None and i < len(self.tokens) else ""


class VariableTable:
    def __init__(self, path: str) -> None:
        self.path = path
        self.lines = csvtok.read_lines(path)
        if len(self.lines) < 2 or not self.lines[1].startswith("Type,Name"):
            raise ValueError(f"{path} does not look like a Vijeo Designer variable export (line 2 must be the header).")
        self.header = csvtok.split_raw(self.lines[1])
        self.col = {h: i for i, h in enumerate(self.header)}
        self.vars: dict[str, Var] = {}
        self.folders: set[str] = set()
        for n, ln in enumerate(self.lines[2:], start=2):
            t = csvtok.split_raw(ln)
            if len(t) < 4 or t[0] not in ROW_TYPES:
                continue
            if t[0] == "Folder":
                self.folders.add(t[1])
                continue
            self.vars[t[1]] = Var(t[0], t[1], t[2], t[3], n, t)

    # ------------------------------------------------------------ read helpers
    def v(self, name: str) -> Var:
        if name not in self.vars:
            raise KeyError(f"Variable '{name}' is not in {self.path}.")
        return self.vars[name]

    def field(self, var: Var, column: str) -> str:
        return var.get(self, column)

    def indirect_reference(self, var: Var) -> tuple[str, str] | None:
        """(target template, selector variable) for InternalReference variables."""
        if var.source != "InternalReference":
            return None
        m = _REF.match(self.field(var, "Device Address").strip())
        return (m.group("target"), m.group("selector")) if m else None

    def summary(self) -> dict:
        kinds = Counter(v.kind for v in self.vars.values())
        sources = Counter(v.source for v in self.vars.values())
        ext = [v for v in self.vars.values() if v.source == "External" and v.kind in ("Variable", "SubVariable")]
        return {"file": self.path, "rows": dict(kinds), "data_sources": dict(sources), "folders": len(self.folders),
                "external_addressed": sum(1 for v in ext if self.field(v, "Device Address")),
                "scan_groups": dict(Counter(self.field(v, "Scan Group") for v in ext))}

    def list(self, pattern: str = "", source: str = "", kind: str = "", limit: int = 200) -> list[dict]:
        rx = re.compile(pattern, re.I) if pattern else None
        out = []
        for v in self.vars.values():
            if rx and not rx.search(v.name):
                continue
            if source and v.source != source:
                continue
            if kind and v.kind != kind:
                continue
            out.append({"name": v.name, "kind": v.kind, "type": v.dtype, "source": v.source,
                        "address": self.field(v, "Device Address"), "scan_group": self.field(v, "Scan Group"),
                        "alarm": self.field(v, "Alarm"), "description": self.field(v, "Description")})
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------ edits
    def _structure_of(self, name: str) -> str | None:
        parent = split_element(name)[0]
        return parent if parent in self.vars and self.vars[parent].kind in (
            "DDTVariable", "StructureVariable", "ArrayVariable") else None

    def set_external(self, assignments: dict[str, str], scan_group: str, out_path: str, overwrite: bool = False) -> dict:
        """Make variables External on `scan_group` with the given device addresses.

        assignments: {variable name: device address, e.g. '%MW1801' or '%M1001'}.
        Structure elements switch their parent structure to External too (Vijeo
        applies a structure's data source and scan group to all its elements).
        Field formats follow Vijeo's own External rows: INT/UINT get BIN, signed
        per type, 16Bits; DINT/UDINT get 32Bits; initial value and Retentive are
        cleared (not applicable to External).
        """
        out = check_output(out_path, self.path, overwrite=overwrite)
        missing = [n for n in assignments if n not in self.vars]
        if missing:
            raise KeyError(f"Not in the export: {missing[:20]}" + (" ..." if len(missing) > 20 else ""))
        c = self.col
        lines = list(self.lines)
        structs = set()
        for name, addr in assignments.items():
            v = self.vars[name]
            if v.kind not in ("Variable", "SubVariable"):
                raise ValueError(f"{name} is a {v.kind}; assign addresses to its elements instead.")
            t = list(v.tokens)
            t[c["Data Source"]] = "External"
            t[c["Initial Value"]] = ""
            t[c["Scan Group"]] = scan_group
            t[c["Device Address"]] = csvtok.quote(addr)
            if v.dtype in ("INT", "UINT", "DINT", "UDINT", "Integer"):
                t[c["Data Format"]] = "BIN"
                t[c["Signed"]] = "Unsigned" if v.dtype.startswith("U") else "2sComplement"
                t[c["Data Length"]] = "32Bits" if v.dtype in ("DINT", "UDINT") else "16Bits"
            if v.dtype in ("INT", "UINT", "DINT", "UDINT", "Integer", "REAL"):
                t[c["DataScaling"]] = t[c["DataScaling"]] or "Disable"
            t[c["IndirectEnabled"]] = "Disable"
            t[c["Retentive"]] = ""
            lines[v.line_no] = csvtok.join_raw(t)
            s = self._structure_of(name)
            if s:
                structs.add(s)
        for s in structs:
            v = self.vars[s]
            t = list(v.tokens)
            t[c["Data Source"]] = "External"
            t[c["Scan Group"]] = scan_group
            lines[v.line_no] = csvtok.join_raw(t)
        csvtok.write_lines(out, lines)
        return {"written": out, "variables_addressed": len(assignments), "structures_set_external": sorted(structs)}

    def remove(self, names: list[str], out_path: str, overwrite: bool = False,
               include_indirect_refs: bool = True) -> dict:
        """Write a copy of the export without the given variables.

        Removing a structure also removes its elements. With include_indirect_refs,
        InternalReference variables whose target template points only at removed
        elements (e.g. 'VA_External.VSDs.%s.SpdRef(...)' once every *.SpdRef is
        gone) are removed as well, so no dangling references remain.
        NOTE: importing a CSV never deletes variables inside Vijeo - delete them
        (or the structure-type members) in the editor; this file is the clean
        re-import set.
        """
        out = check_output(out_path, self.path, overwrite=overwrite)
        missing = [n for n in names if n not in self.vars]
        if missing:
            raise KeyError(f"Not in the export: {missing[:20]}")
        drop = set(names)
        for n in list(drop):
            if self.vars[n].kind in ("DDTVariable", "StructureVariable", "ArrayVariable"):
                drop |= {m for m in self.vars if is_child(m, n)}
        refs_removed = []
        if include_indirect_refs:
            remaining = set(self.vars) - drop
            for v in self.vars.values():
                ir = self.indirect_reference(v)
                if not ir or v.name in drop:
                    continue
                rx = re.compile("^" + re.escape(ir[0]).replace("%s", "[A-Za-z0-9_]+") + "$")
                had = any(rx.match(m) for m in self.vars)
                still = any(rx.match(m) for m in remaining)
                if had and not still:
                    drop.add(v.name)
                    refs_removed.append(v.name)
        keep = [ln for i, ln in enumerate(self.lines)
                if i < 2 or not (len(t := csvtok.split_raw(ln)) > 1 and t[0] in ROW_TYPES - {"Folder"} and t[1] in drop)]
        csvtok.write_lines(out, keep)
        return {"written": out, "removed": len(drop), "indirect_refs_removed": refs_removed}

    # ------------------------------------------------------------- validation
    def validate(self, datatypes: dict[str, list[tuple[str, str]]] | None = None) -> dict:
        """Structural checks before importing into Vijeo. Errors block an import;
        warnings are worth a look."""
        errors, warnings = [], []
        c = self.col
        width = len(self.header)
        seen = Counter()
        for i, ln in enumerate(self.lines[2:], start=3):
            t = csvtok.split_raw(ln)
            if len(t) <= 1:
                continue
            if t[0] not in ROW_TYPES:
                errors.append(f"line {i}: unknown row type '{t[0]}'")
                continue
            seen[t[1]] += 1
            if t[0] != "Folder" and len(t) < width - 1:
                errors.append(f"line {i} ({t[1]}): {len(t)} fields, header has {width}")
        for n, k in seen.items():
            if k > 1:
                errors.append(f"duplicate name: {n} ({k} rows)")
        addr_use = defaultdict(list)
        for v in self.vars.values():
            parent = split_element(v.name)[0]
            if v.kind == "SubVariable":
                if parent not in self.vars:
                    errors.append(f"{v.name}: parent structure {parent} missing")
            elif parent and parent not in self.folders:
                errors.append(f"{v.name}: folder {parent} is not declared")
            if v.kind in ("Variable", "SubVariable") and v.dtype not in SIMPLE_TYPES and not v.dtype.startswith("Block"):
                errors.append(f"{v.name}: unknown data type {v.dtype}")
            eff_source = v.source
            if v.kind == "SubVariable" and parent in self.vars:
                eff_source = self.vars[parent].source
                if v.source != eff_source:
                    errors.append(f"{v.name}: source {v.source} differs from its structure ({eff_source})")
            if v.kind in ("Variable", "SubVariable") and eff_source == "External":
                a, g = v.get(self, "Device Address"), v.get(self, "Scan Group")
                if not a:
                    errors.append(f"{v.name}: External without a device address")
                if not g:
                    errors.append(f"{v.name}: External without a scan group")
                if v.get(self, "Initial Value"):
                    warnings.append(f"{v.name}: External with an initial value (ignored by Vijeo)")
                if v.dtype in ("INT", "UINT", "DINT", "UDINT") and not v.get(self, "Data Length"):
                    warnings.append(f"{v.name}: {v.dtype} without Data Format/Signed/Length")
                if a:
                    addr_use[(g, a)].append(v.name)
                    if not re.fullmatch(r"%MW?\d+(\.\d+)?|%[IQ]W?\d+|\d+", a):
                        warnings.append(f"{v.name}: unusual device address {a}")
            if v.source == "InternalReference":
                ir = self.indirect_reference(v)
                if ir and "%s" not in ir[0] and ir[0] not in self.vars:
                    errors.append(f"{v.name}: references missing variable {ir[0]}")
        for (g, a), names in addr_use.items():
            if len(names) > 1:
                warnings.append(f"address {a} on {g} shared by {len(names)} variables: {names[:4]}")
        if datatypes:
            members = defaultdict(list)
            for v in self.vars.values():
                if v.kind == "SubVariable":
                    p, m = split_element(v.name)
                    members[p].append(m)
            for s in (v for v in self.vars.values() if v.kind == "DDTVariable"):
                if s.dtype in datatypes:
                    want = [f for f, _ in datatypes[s.dtype]]
                    if members[s.name] != want:
                        extra = sorted(set(members[s.name]) - set(want))
                        miss = sorted(set(want) - set(members[s.name]))
                        errors.append(f"{s.name} ({s.dtype}): elements differ from the type"
                                      + (f"; not in type: {extra}" if extra else "")
                                      + (f"; missing: {miss}" if miss else "")
                                      + ("; order differs" if not extra and not miss else ""))
        return {"file": self.path, "ok": not errors, "errors": errors[:200], "error_count": len(errors),
                "warnings": warnings[:200], "warning_count": len(warnings)}
