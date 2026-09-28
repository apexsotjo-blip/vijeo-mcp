"""Full variable manipulation on a Vijeo variable CSV: add, update, add structure instances.

Every new row is CLONED from an existing row of the same kind (row type, data type, data source)
in the same export, then only the requested fields are changed - so the result has exactly the
layout Vijeo itself exported. The result is always a NEW file (inputs are never overwritten).
"""

from __future__ import annotations

import re

from . import csvtok
from .safety import check_output
from .variables import SIMPLE_TYPES, VariableTable, split_element

# friendly field name -> CSV header
FIELDS = {
    "description": "Description", "initial_value": "Initial Value", "bytes": "NumofBytes",
    "sharing": "Data Sharing", "alarm": "Alarm", "alarm_message": "Alarm Message", "alarm_type": "Alarm Type",
    "trigger": "Trigger Condition", "deadband": "Deadband", "target": "Target", "limits": "LoLo\\Lo\\Hi\\HiHi",
    "minor": "Minor", "major": "Major", "alarm_group": "Alarm Group", "severity": "Severity",
    "scan_group": "Scan Group", "address": "Device Address", "bit": "Bit Number", "data_format": "Data Format",
    "signed": "Signed", "data_length": "Data Length", "input_range": "InputRange", "min": "Min", "max": "Max",
    "scaling": "DataScaling", "raw_min": "RawMin", "raw_max": "RawMax", "scaled_min": "ScaledMin",
    "scaled_max": "ScaledMax", "indirect": "IndirectEnabled", "indirect_address": "IndirectAddress",
    "retentive": "Retentive", "logging_group": "LoggingGroup", "log_user_operations": "LogUserOperationsOnVariable",
    "source": "Data Source",
}
QUOTED = {"Description", "Initial Value", "Alarm Message", "Alarm Type", "Trigger Condition", "LoLo\\Lo\\Hi\\HiHi",
          "Vibration Pattern", "Sound File", "Play Mode", "Device Address"}
TYPE_FORMAT = {"INT": ("BIN", "2sComplement", "16Bits"), "UINT": ("BIN", "Unsigned", "16Bits"),
               "DINT": ("BIN", "2sComplement", "32Bits"), "UDINT": ("BIN", "Unsigned", "32Bits")}
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")


class VariableEditor:
    def __init__(self, csv_path: str):
        self.t = VariableTable(csv_path)
        self.lines = list(self.t.lines)
        self.col = self.t.col
        self.width = len(self.t.header)
        self.added: list[str] = []
        self.changed: list[str] = []

    # ------------------------------------------------------------------ helpers
    def _set(self, tokens, header, value):
        i = self.col[header]
        while len(tokens) <= i:
            tokens.append("")
        v = "" if value is None else str(value)
        tokens[i] = csvtok.quote(v) if (header in QUOTED and v != "") or (header in QUOTED and tokens[i].startswith('"')) else v

    def _template(self, kind, dtype, source):
        cands = [v for v in self.t.vars.values() if v.kind == kind and v.dtype == dtype and v.source == source]
        if not cands and kind == "Variable":
            cands = [v for v in self.t.vars.values() if v.kind == kind and v.source == source and v.dtype in SIMPLE_TYPES]
        if not cands:
            raise ValueError(f"No existing {kind} row with data source {source} to use as a template.")
        # prefer a template without alarm / logging so defaults stay neutral
        cands.sort(key=lambda v: (self.t.field(v, "Alarm") == "Enable", bool(self.t.field(v, "LoggingGroup"))))
        return list(cands[0].tokens)

    def _neutral(self, tok, dtype, source):
        for h in ("Description", "Alarm Message", "LoggingGroup", "Alarm Group", "Severity", "Min", "Max",
                  "RawMin", "RawMax", "ScaledMin", "ScaledMax"):
            if h in self.col:
                self._set(tok, h, "")
        if tok[self.col["Data Sharing"]]:
            self._set(tok, "Data Sharing", "None")
        if tok[self.col["InputRange"]]:
            self._set(tok, "InputRange", "Disable")
        if tok[self.col["DataScaling"]]:
            self._set(tok, "DataScaling", "Disable")
        if "Alarm" in self.col and tok[self.col["Alarm"]] == "Enable":
            for h in ("Alarm", "Trigger Condition", "LoLo\\Lo\\Hi\\HiHi", "Alarm Type", "Vibration Pattern",
                      "Sound File", "Play Mode"):
                self._set(tok, h, "Disable" if h == "Alarm" else "")
        if source == "External":
            self._set(tok, "Initial Value", "")
            fmt = TYPE_FORMAT.get(dtype)
            self._set(tok, "Data Format", fmt[0] if fmt else "")
            self._set(tok, "Signed", fmt[1] if fmt else "")
            self._set(tok, "Data Length", fmt[2] if fmt else "")

    def _ensure_folders(self, name):
        parts = split_element(name)[0].split(".") if "." in name else []
        missing = []
        for k in range(1, len(parts) + 1):
            f = ".".join(parts[:k])
            if f and f not in self.t.folders and f not in missing:
                missing.append(f)
        if missing:
            last_folder = max(i for i, ln in enumerate(self.lines) if ln.startswith("Folder,"))
            for k, f in enumerate(missing):
                self.lines.insert(last_folder + 1 + k, f'Folder,{f},,,,"",')
                self.t.folders.add(f)
            self.added += [f"folder {f}" for f in missing]

    def _append(self, tokens):
        pos = len(self.lines)
        while pos > 0 and self.lines[pos - 1] == "":        # keep the file's trailing line break(s)
            pos -= 1
        self.lines.insert(pos, csvtok.join_raw(tokens))

    def _apply(self, tok, dtype, fields: dict):
        for k, v in fields.items():
            header = FIELDS.get(k, k)
            if header not in self.col:
                raise KeyError(f"Unknown field '{k}'. Fields: {sorted(FIELDS)}")
            if k == "alarm" and isinstance(v, bool):
                v = "Enable" if v else "Disable"
            self._set(tok, header, v)
        if fields.get("alarm") in (True, "Enable"):
            if not tok[self.col["Trigger Condition"]] and dtype == "BOOL":
                self._set(tok, "Trigger Condition", "when high")
            if not tok[self.col["LoLo\\Lo\\Hi\\HiHi"]]:
                self._set(tok, "LoLo\\Lo\\Hi\\HiHi", "_\\_\\_\\_")
            for h in ("Alarm Type", "Vibration Pattern", "Sound File", "Play Mode"):
                if not tok[self.col[h]]:
                    self._set(tok, h, "")
                    tok[self.col[h]] = '""'
            if not tok[self.col["Severity"]]:
                self._set(tok, "Severity", "1")

    # ------------------------------------------------------------------ operations
    def add_variable(self, name: str, dtype: str, source: str = "Internal", **fields) -> None:
        if not _NAME.fullmatch(name):
            raise ValueError(f"Invalid variable name '{name}'.")
        if name in self.t.vars or any(ln.startswith(f"Variable,{name},") for ln in self.lines):
            raise ValueError(f"Variable '{name}' already exists.")
        if dtype not in SIMPLE_TYPES:
            raise ValueError(f"Type must be one of {sorted(SIMPLE_TYPES)} (use add_structure_instance for data types).")
        if source == "External" and not (fields.get("address") and fields.get("scan_group")):
            raise ValueError("External variables need address and scan_group.")
        tok = self._template("Variable", dtype, source)
        tok[0], tok[1], tok[2], tok[3] = "Variable", name, dtype, source
        self._neutral(tok, dtype, source)
        if dtype == "STRING" and "bytes" not in fields:
            fields["bytes"] = fields.get("bytes", 20)
        if dtype != "STRING":
            self._set(tok, "NumofBytes", "")
        if source == "Internal" and "initial_value" not in fields:
            fields["initial_value"] = "Off" if dtype == "BOOL" else ("" if dtype == "STRING" else "0")
        self._apply(tok, dtype, fields)
        self._ensure_folders(name)
        self._append(tok)
        self.added.append(name)

    def update_variable(self, name: str, **fields) -> None:
        idx = [i for i, ln in enumerate(self.lines) if len(t := csvtok.split_raw(ln)) > 1 and t[1] == name
               and t[0] in ("Variable", "SubVariable", "DDTVariable")]
        if not idx:
            raise KeyError(f"Variable '{name}' not found.")
        tok = csvtok.split_raw(self.lines[idx[0]])
        self._apply(tok, tok[2], fields)
        if fields.get("source") == "External":
            fmt = TYPE_FORMAT.get(tok[2])
            if fmt:
                for h, v in zip(("Data Format", "Signed", "Data Length"), fmt):
                    self._set(tok, h, v)
            self._set(tok, "Initial Value", "")
        self.lines[idx[0]] = csvtok.join_raw(tok)
        self.changed.append(name)

    def add_structure_instance(self, name: str, type_name: str, type_fields: list[tuple[str, str]],
                               source: str = "Internal", scan_group: str = "", addresses: dict | None = None,
                               description: str = "") -> None:
        """New DDTVariable + one SubVariable per data-type field (in the type's order)."""
        if name in self.t.vars:
            raise ValueError(f"'{name}' already exists.")
        addresses = addresses or {}
        if source == "External":
            missing = [f for f, _ in type_fields if f not in addresses]
            if missing or not scan_group:
                raise ValueError(f"External structure needs scan_group and an address for every field; missing {missing[:10]}")
        same = [v for v in self.t.vars.values() if v.kind == "DDTVariable" and v.dtype == type_name]
        head = list(same[0].tokens) if same else self._template("DDTVariable", self._any_ddt_type(), "Internal")
        head[0], head[1], head[2], head[3] = "DDTVariable", name, type_name, source
        self._set(head, "Description", description)
        self._set(head, "Scan Group", scan_group if source == "External" else "")
        self._ensure_folders(name)
        self._append(head)
        proto_inst = same[0].name if same else None
        proto_short = proto_inst.rsplit(".", 1)[-1] if proto_inst else None
        short = name.rsplit(".", 1)[-1]
        for fname, ftype in type_fields:
            dtype = {"String": "STRING"}.get(ftype, ftype)
            proto = self.t.vars.get(f"{proto_inst}.{fname}") if proto_inst else None
            if proto is not None:
                tok = list(proto.tokens)
                for h in ("Alarm Message", "Description"):          # "LTT1 Alarm" -> "<new> Alarm"
                    i = self.col[h]
                    if proto_short and proto_short in tok[i]:
                        tok[i] = tok[i].replace(proto_short, short)
            else:
                tok = self._template("SubVariable", dtype, "Internal") if any(
                    v.kind == "SubVariable" and v.dtype == dtype for v in self.t.vars.values()) else \
                    self._template("Variable", dtype, "Internal")
                self._neutral(tok, dtype, "Internal")
            tok[0], tok[1], tok[2], tok[3] = "SubVariable", f"{name}.{fname}", dtype, source
            if source == "External":
                self._set(tok, "Initial Value", "")
                self._set(tok, "Scan Group", scan_group)
                self._set(tok, "Device Address", addresses[fname])
                fmt = TYPE_FORMAT.get(dtype)
                if fmt:
                    for h, v in zip(("Data Format", "Signed", "Data Length"), fmt):
                        self._set(tok, h, v)
                self._set(tok, "IndirectEnabled", "Disable"); self._set(tok, "Retentive", "")
            else:
                self._set(tok, "Scan Group", ""); self._set(tok, "Device Address", "")
            self._append(tok)
        self.added.append(f"{name} ({type_name}, {len(type_fields)} elements)")

    def _any_ddt_type(self):
        t = [v.dtype for v in self.t.vars.values() if v.kind == "DDTVariable"]
        if not t:
            raise ValueError("The export has no structure instance to use as a template.")
        return t[0]

    def save(self, out_path: str, overwrite: bool = False) -> dict:
        out = check_output(out_path, self.t.path, overwrite=overwrite)
        csvtok.write_lines(out, self.lines)
        return {"written": out, "added": self.added, "changed": self.changed}
