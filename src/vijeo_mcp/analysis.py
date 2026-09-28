"""Usage analysis: which variables a Vijeo project actually uses.

A variable counts as USED when any of these holds:
  graphic   - a panel/popup/template references it (directly, or via its structure)
  other     - a script, recipe, alarm or other project stream references it
  indirect  - an InternalReference variable that IS used resolves to it:
              'PREFIX.%s.MEMBER(Selector)' reaches element PREFIX.<X>.MEMBER when
              the literal "X" is written somewhere in the graphics (the popup
              selector); a fixed target (no %s) always resolves
  alarm     - alarm/event enabled on it (the alarm summary/event log uses it)
  logging   - it belongs to a logging group (trends / data logging)
Otherwise it is UNUSED.

Limits (stated in every result): names assembled at run time in scripts
from string pieces, other than the %s selector mechanism, cannot be seen by a
static analysis.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from .project import VijeoBackup
from .variables import VariableTable, is_child, split_element

_HOLDERS = ("DDTVariable", "StructureVariable", "ArrayVariable")

LIMITS = ("Static analysis of the backup: a variable name built at run time from string pieces "
          "(other than the %s popup-selector mechanism) cannot be detected.")


def _resolve(name: str, known: set[str]) -> str | None:
    """Longest dotted prefix of `name` that is a known variable."""
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in known:
            return cand
    return None


def analyze(backup: VijeoBackup, table: VariableTable) -> dict[str, list[str]]:
    """variable name -> list of reasons it is used (empty list = unused)."""
    known = set(table.vars)
    where = backup.all_references()
    reasons: dict[str, list[str]] = {n: [] for n in known}
    for ref, places in where.items():
        hit = _resolve(ref, known)
        if not hit:
            continue
        tag = "graphic" if any(not p.startswith("other:") for p in places) else "other"
        reasons[hit].append(tag)
        v = table.vars[hit]
        if v.kind in _HOLDERS:                                        # whole structure/array referenced
            for m in known:
                if is_child(m, hit):
                    reasons[m].append(tag + " (structure)")
    selected = {lit for lit, n in backup.literals.items() if n}
    for v in table.vars.values():
        ir = table.indirect_reference(v)
        if not ir or not any(r.startswith(("graphic", "other")) for r in reasons[v.name]):
            continue
        target = ir[0]
        if "%s" in target:
            rx = re.compile("^" + re.escape(target).replace("%s", "([A-Za-z0-9_]+)") + "$")
            for m in known:
                mm = rx.match(m)
                if mm and mm.group(1) in selected:
                    reasons[m].append(f"indirect via {v.name}")
        elif target in known:
            reasons[target].append(f"indirect via {v.name}")
    for v in table.vars.values():
        if table.field(v, "Alarm") == "Enable":
            reasons[v.name].append("alarm:" + (table.field(v, "Alarm Group") or "?"))
        if table.field(v, "LoggingGroup"):
            reasons[v.name].append("logging:" + table.field(v, "LoggingGroup"))
    for v in table.vars.values():                                    # structure holder used if an element is
        if v.kind in _HOLDERS and not reasons[v.name]:
            if any(reasons[m] for m in known if is_child(m, v.name)):
                reasons[v.name].append("has used elements")
    return reasons


def category(r: list[str]) -> str:
    if any(x.startswith(("graphic", "other", "indirect", "has used")) for x in r):
        return "used"
    if any(x.startswith("alarm") for x in r):
        return "alarm/event only"
    if any(x.startswith("logging") for x in r):
        return "logging only"
    return "unused"


def usage_report(backup: VijeoBackup, table: VariableTable, prefix: str = "") -> dict:
    reasons = analyze(backup, table)
    scope = {n: r for n, r in reasons.items() if n.startswith(prefix)
             and table.vars[n].kind in ("Variable", "SubVariable")}
    cats = Counter(category(r) for r in scope.values())
    unused = sorted(n for n, r in scope.items() if category(r) == "unused")
    # structure-type members unused on EVERY instance -> removable from the type itself
    by_type = defaultdict(lambda: defaultdict(list))
    for n in scope:
        v = table.vars[n]
        if v.kind == "SubVariable":
            parent, member = split_element(n)
            if parent in table.vars:
                by_type[table.vars[parent].dtype][member].append(category(scope[n]))
    type_members = {t: sorted(m for m, cs in mem.items() if all(c == "unused" for c in cs))
                    for t, mem in by_type.items()}
    partly = {t: sorted(m for m, cs in mem.items() if "unused" in cs and any(c != "unused" for c in cs))
              for t, mem in by_type.items()}
    return {"scope": prefix or "(all variables)", "counts": dict(cats), "unused": unused,
            "unused_type_members": {t: m for t, m in type_members.items() if m},
            "type_members_unused_on_some_instances_only": {t: m for t, m in partly.items() if m},
            "limits": LIMITS, "_reasons": scope}


def broken_references(backup: VijeoBackup, table: VariableTable) -> list[dict]:
    """TagDB.<name> expressions in the project that match no variable in the export."""
    known = set(table.vars)
    out = []
    for p in backup.panels.values():
        for ref in sorted(p.tagdb):
            if not _resolve(ref, known):
                out.append({"reference": ref, "where": f"{p.kind}:{p.name}" + (f" ({p.group})" if p.group else "")})
    for s, refs in backup.other_tagdb.items():
        for ref in sorted(refs):
            if not _resolve(ref, known):
                out.append({"reference": ref, "where": "other:" + s})
    return out


def variable_usage(backup: VijeoBackup, table: VariableTable | None, pattern: str, limit: int = 100) -> list[dict]:
    rx = re.compile(pattern, re.I)
    where = backup.all_references()
    known = set(table.vars) if table else set()
    out = []
    names = sorted({*(n for n in where if rx.search(n)), *(n for n in known if rx.search(n))})
    reasons = analyze(backup, table) if table else {}
    for n in names:
        places = sorted(set(where.get(n, [])))
        row = {"name": n, "places": places[:30], "place_count": len(places)}
        if table and n in reasons:
            row["used_because"] = reasons[n]
            row["category"] = category(reasons[n])
        out.append(row)
        if len(out) >= limit:
            break
    return out
