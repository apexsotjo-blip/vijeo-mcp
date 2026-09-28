"""Read-only reader for Vijeo Designer project backups (.vdz).

A .vdz is a zip holding one OLE compound file (*.SwxCF). The compound file is
opened in memory straight from the zip - nothing is extracted to disk and
nothing is ever written back.

Graphics reference variables as UTF-16 strings inside each panel's
'GraphicalObject' stream, e.g. 'TagDB.GENERAL.EMFM.EMFM1FLOW' in expressions or
the plain dotted name. Quoted literals ("P1T1") are collected too: popups that
use indirect variables pick their device by writing such a literal into a
selector variable.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field

import olefile

_U16 = re.compile(rb"(?:[\x20-\x7e]\x00){2,}")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_QUOTED = re.compile(r'"([A-Za-z0-9_]+)"')

# Streams that DEFINE things (variable database, texts, images) - scanning them
# would make every variable look "used".
_DEFINITION_PARTS = ("Services/", "/BitmapManager/", "/JpegList", "/LangManager/", "/JpegThumbnails")


_ACCESSOR = re.compile(r"^(get|set|is)[A-Z]")


def _u16_strings(data: bytes) -> list[str]:
    return [m.group().decode("utf-16-le") for m in _U16.finditer(data)]


def _clean(name: str) -> tuple[str, bool]:
    """'TagDB.A.B.getIntValue' -> ('A.B', True). Second value: came from a TagDB. expression,
    i.e. certainly a variable reference."""
    tagdb = name.startswith("TagDB.")
    parts = name[6:].split(".") if tagdb else name.split(".")
    for i, p in enumerate(parts):
        if i and _ACCESSOR.match(p):
            parts = parts[:i]
            break
    return ".".join(parts), tagdb


@dataclass
class Panel:
    id: str                     # stable internal path, e.g. 'Target 1/Base/Panel18'
    name: str                   # designer-visible name, e.g. 'Measures'
    kind: str                   # 'base' | 'popup' | 'template'
    group: str = ""             # popup window group name (e.g. 'VA_AI')
    target: str = ""
    references: set[str] = field(default_factory=set)
    tagdb: set[str] = field(default_factory=set)       # subset that came from TagDB.<name> expressions
    literals: set[str] = field(default_factory=set)


class VijeoBackup:
    """Loaded, read-only view of one .vdz backup."""

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        if not zipfile.is_zipfile(self.path):
            raise ValueError(f"{path} is not a Vijeo Designer .vdz backup (not a zip archive).")
        with zipfile.ZipFile(self.path) as z:
            cf = [n for n in z.namelist() if n.lower().endswith(".swxcf")]
            if not cf:
                raise ValueError(f"{path}: no .SwxCF project container inside the backup.")
            self.container = cf[0]
            blob = z.read(cf[0])
        self._ole = olefile.OleFileIO(io.BytesIO(blob))
        self.streams = ["/".join(p) for p in self._ole.listdir(streams=True, storages=False)]
        self.panels: dict[str, Panel] = {}
        self.other_refs: dict[str, set[str]] = defaultdict(set)   # stream -> names (scripts, alarms, recipes...)
        self.other_tagdb: dict[str, set[str]] = defaultdict(set)
        self.literals: dict[str, int] = defaultdict(int)
        self._index()

    # ---------------------------------------------------------------- loading
    def _text(self, stream: str) -> list[str]:
        return _u16_strings(self._ole.openstream(stream).read()) if self._ole.exists(stream) else []

    def _first(self, stream: str) -> str:
        s = self._text(stream)
        return s[0] if s else ""

    def _index(self) -> None:
        panel_re = re.compile(
            r"^Targets/(?P<t>[^/]+)/Component \d+/(?:"
            r"WindowList/BasePanelsList/(?P<base>Panel\d+)"
            r"|PopupWindowList/(?P<grp>[^/]+)/(?P<pop>Panel\d+)"
            r"|WindowList/DefinitionNodeStorage/(?P<fold>[^/]+)/(?P<tpl>Panel\d+)"
            r")/GraphicalObject$")
        for s in self.streams:
            if any(p in "/" + s for p in _DEFINITION_PARTS):
                continue
            strings = self._text(s)
            names, tagdb = set(), set()
            for text in strings:
                for raw in _NAME.findall(text):
                    n, is_tag = _clean(raw)
                    if "." in n:
                        names.add(n)
                        if is_tag:
                            tagdb.add(n)
                for q in _QUOTED.findall(text):
                    self.literals[q] += 1
            m = panel_re.match(s)
            if m:
                root = s[: -len("/GraphicalObject")]
                if m.group("base"):
                    kind, group = "base", ""
                elif m.group("pop"):
                    kind = "popup"
                    group = self._first(root.rsplit("/", 1)[0] + "/PageListProperties") or m.group("grp")
                else:
                    kind, group = "template", m.group("fold")
                pid = f"{m.group('t')}/{kind}/{group + '/' if group else ''}{root.rsplit('/', 1)[1]}"
                p = self.panels.get(pid) or Panel(pid, self._first(root + "/WindowObject") or root.rsplit("/", 1)[1],
                                                  kind, group, m.group("t"))
                p.references |= names
                p.tagdb |= tagdb
                p.literals |= {q for t in strings for q in _QUOTED.findall(t)}
                self.panels[pid] = p
            elif names:
                self.other_refs[s] |= names
                self.other_tagdb[s] |= tagdb

    # ---------------------------------------------------------------- queries
    def all_references(self) -> dict[str, list[str]]:
        """name -> list of places (panel names or stream paths) that reference it."""
        where: dict[str, list[str]] = defaultdict(list)
        for p in self.panels.values():
            for n in p.references:
                where[n].append(f"{p.kind}:{p.name}" + (f" ({p.group})" if p.group else ""))
        for s, names in self.other_refs.items():
            for n in names:
                where[n].append("other:" + s)
        return where

    def summary(self) -> dict:
        kinds = defaultdict(int)
        for p in self.panels.values():
            kinds[p.kind] += 1
        return {
            "backup": self.path,
            "container": self.container,
            "streams": len(self.streams),
            "panels": dict(kinds),
            "popup_groups": sorted({p.group for p in self.panels.values() if p.kind == "popup"}),
            "targets": sorted({p.target for p in self.panels.values()}),
            "distinct_names_referenced": len(self.all_references()),
            "note": "Read-only: the backup is opened in memory and never modified.",
        }

    def list_panels(self, kind: str = "") -> list[dict]:
        return [{"id": p.id, "name": p.name, "kind": p.kind, "group": p.group, "references": len(p.references)}
                for p in sorted(self.panels.values(), key=lambda x: (x.kind, x.group, x.name))
                if not kind or p.kind == kind]

    def find_panel(self, name_or_id: str) -> Panel:
        hits = [p for p in self.panels.values() if name_or_id in (p.id, p.name)]
        if not hits:
            hits = [p for p in self.panels.values() if name_or_id.lower() in p.name.lower()]
        if not hits:
            raise KeyError(f"No panel named '{name_or_id}'. Use list_panels to see the names.")
        if len(hits) > 1 and not any(name_or_id in (p.id, p.name) for p in hits):
            raise KeyError(f"'{name_or_id}' matches several panels: {[p.name for p in hits]}")
        return hits[0]
