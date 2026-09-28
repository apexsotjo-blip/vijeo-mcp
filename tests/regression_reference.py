"""Regression test on the reference project the tool was built on.

The tool must independently reproduce results that were verified by hand on 2026-09-27:
usage counts, the 40 removable structure members, and byte-identical import files.
Run:  .venv\\Scripts\\python.exe tests\\regression_reference.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402
from vijeo_mcp import analysis                      # noqa: E402
from vijeo_mcp.automation import diagnose           # noqa: E402
from vijeo_mcp.datatypes import DataTypes           # noqa: E402
from vijeo_mcp.project import VijeoBackup           # noqa: E402
from vijeo_mcp.safety import SafetyError            # noqa: E402
from vijeo_mcp.variables import VariableTable       # noqa: E402

VDZ = _paths.VDZ()
CSV = _paths.path("VIJEO_MCP_TEST_ORIG_CSV")
UDT = _paths.path("VIJEO_MCP_TEST_ORIG_UDT")
DELIVERED_CSV = _paths.CSV()
DELIVERED_UDT = _paths.UDT()
PLAN = os.environ.get("VIJEO_MCP_TEST_POINTS")  # points3.json from the mapping session (optional)

fails = []
def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(name)

tmp = tempfile.mkdtemp(prefix="vijeo_mcp_test_")
vdz_mtime = os.path.getmtime(VDZ)

b = VijeoBackup(VDZ)
s = b.summary()
check("backup opens, panels indexed", s["panels"].get("base", 0) > 20 and s["panels"].get("popup", 0) > 10, str(s["panels"]))
check("panel names resolved", any(p["name"] == "Measures" for p in b.list_panels("base")))
check("popup group names resolved", "VA_AI" in s["popup_groups"], str(s["popup_groups"][:8]))
check("method accessors stripped", not any(r.endswith(("getIntValue", "getFloatValue")) for p in b.panels.values() for r in p.references))

t = VariableTable(CSV)
rep = analysis.usage_report(b, t, "VA_External.")
c = rep["counts"]
check("usage counts 578/17/178", (c.get("used"), c.get("alarm/event only"), c.get("unused")) == (578, 17, 178), str(c))
n_members = sum(len(v) for v in rep["unused_type_members"].values())
check("40 removable type members", n_members == 40, str({k: len(v) for k, v in rep["unused_type_members"].items()}))
check("no partially-unused members", not rep["type_members_unused_on_some_instances_only"])

broken = analysis.broken_references(b, t)
print(f"     info: broken TagDB references in project: {len(broken)}", broken[:5])

# ---- data types: reproduce the delivered file byte-for-byte
dt = DataTypes(UDT)
out_udt = os.path.join(tmp, "udt.VJDDataTypes")
dt.remove_members(rep["unused_type_members"], out_udt)
check("UDT output byte-identical to delivered", open(out_udt, "rb").read() == open(DELIVERED_UDT, "rb").read())

# ---- variables: reproduce the delivered CSV byte-for-byte (needs the address plan)
if PLAN and os.path.exists(PLAN):
    pts = json.load(open(PLAN))
    assign = {f"VA_External.{p['htype']}.{p['hname']}.{p['member']}": p["hmi_addr"] for p in pts}
    step1 = os.path.join(tmp, "step1.csv")
    t.set_external(assign, "EquipoModbus01", step1)
    step2 = os.path.join(tmp, "step2.csv")
    r = VariableTable(step1).remove(rep["unused"], step2)
    check("indirect refs auto-removed = 40", len(r["indirect_refs_removed"]) == 40, str(len(r["indirect_refs_removed"])))
    check("CSV output byte-identical to delivered", open(step2, "rb").read() == open(DELIVERED_CSV, "rb").read())
else:
    print("SKIP CSV byte-identity (set VIJEO_MCP_TEST_POINTS to points3.json)")

v = VariableTable(DELIVERED_CSV).validate(DataTypes(DELIVERED_UDT).types())
check("delivered CSV validates against delivered UDT", v["ok"], f"{v['error_count']} errors, {v['warning_count']} warnings; {v['errors'][:3]}")
v_bad = VariableTable(DELIVERED_CSV).validate(DataTypes(UDT).types())
check("validation catches CSV/UDT mismatch", not v_bad["ok"] and v_bad["error_count"] == 35, str(v_bad["error_count"]))

# ---- safety
try:
    t.remove(["VA_TagPrefix.AIs"], CSV); check("refuses to overwrite input", False)
except SafetyError:
    check("refuses to overwrite input", True)
try:
    dt.remove_members({}, os.path.join(tmp, "x.vdz")); check("refuses to write .vdz", False)
except SafetyError:
    check("refuses to write .vdz", True)
check("backup untouched", os.path.getmtime(VDZ) == vdz_mtime)

d = diagnose(live=False)
print("     info: automation:", d["verdict"])

# ---- graphics (no Vijeo needed; tests/graphics_e2e.py and graphics_struct_e2e.py validate through Vijeo)
import re as _re
from vijeo_mcp import graphics as gfx      # noqa: E402
gs = gfx.GraphicsSession(VDZ)
n_obj, n_fail = 0, []
for pname, info in gs.panels.items():
    try:
        n_obj += sum(1 for _ in gfx.walk(gfx.parse_panel(gs._read(info["root"] + "/GraphicalObject"))))
    except ValueError as e:
        n_fail.append(pname)
check("every panel parses completely (67 panels, 2909 objects incl. 2 nested Arcs)", not n_fail and len(gs.panels) == 67 and n_obj == 2909,
      f"{len(gs.panels)} panels, {n_obj} objects, failed {n_fail[:3]}")
chains = set()
for info in gs.panels.values():
    raw = gs._read(info["root"] + "/GraphicalObject")
    for m in gfx._U16.finditer(raw):
        chains |= set(_re.findall(r"\{[FT],2:1\.\d+,[^{}]+\}(?:\.\{[FT],2:1\.\d+,[^{}]+\})*", m.group().decode("utf-16-le")))
ok_c = sum(1 for c in chains if _re.findall(r"\{([FT])", c)[-1] == "T"
           and gs.resolver.binding(".".join(x for x in _re.findall(r",([^,{}]+)\}", c))) == c)
n_c = sum(1 for c in chains if _re.findall(r"\{([FT])", c)[-1] == "T")
check("all variable bindings rebuilt exactly from TagDatabase/NameServer", ok_c == n_c and n_c > 300, f"{ok_c}/{n_c}")
r = gs.rebind("VA_Panel", "VA_External.AIs.LTT1", "VA_External.AIs.LTT2", "Tank01")
after = {o["path"]: o.get("variables") for o in gs.objects("VA_Panel", 3) if o["path"].startswith("Tank01/")}
check("structure-instance rebind covers both string encodings",
      r["strings_changed"] == 20 and not any(v and any("LTT1" in x for x in v) for v in after.values()))
mv = gs.move("VA_Panel", "Tank02", dx=5)
check("group move carries children", mv["objects_moved"] > 10, str(mv["objects_moved"]))
check("backup still untouched after graphics edits", os.path.getmtime(VDZ) == vdz_mtime)

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
