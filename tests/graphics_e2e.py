"""End-to-end graphics edit on the reference project backup, validated by Vijeo itself (opens a Vijeo window ~30 s).

Picks a panel that is NOT the one Vijeo opens by default (so the active-panel switch is exercised)
and that has a top-level group, a text object and a bound variable with a sibling variable to re-bind to.
"""
import os, sys, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402
from vijeo_mcp import graphics as gfx

VDZ = _paths.VDZ()
mtime = os.path.getmtime(VDZ)
s = gfx.GraphicsSession(VDZ)
R = s.resolver
plan = None
for panel in sorted(s.panels):
    if panel == "VA_Panel" or s.panels[panel]["popup"]:
        continue
    objs = s.objects(panel, max_depth=3)
    groups = [o for o in objs if o["type"] == "Group" and "/" not in o["path"]]
    texts = [o for o in objs if o["type"] == "Text" and "/" not in o["path"]]
    for o in objs:
        for v in o.get("variables", []):
            if v.count(".") != 2 or R.oid_of.get(v, ("", 0))[0] != "T":
                continue
            folder = v.rsplit(".", 1)[0]
            sib = [k for k, (kind, _) in R.oid_of.items() if kind == "T" and k.rsplit(".", 1)[0] == folder and k != v]
            if groups and texts and sib:
                plan = (panel, o["path"], v, sorted(sib)[0], groups[0]["path"], texts[0]["path"])
                break
        if plan: break
    if plan: break
panel, obj, old, new, grp, txt = plan
print(f"panel={panel}\n  rebind {obj}: {old} -> {new}\n  move group {grp}\n  rename {txt}")
print(json.dumps(s.rebind(panel, old, new, obj)))
print(s.move(panel, grp, dx=20, dy=10))
print(s.rename(panel, txt, txt + "_MCP"))
print("after edit bound to:", [o.get("variables") for o in s.objects(panel, 3) if o["path"] == obj])

out = os.path.join(tempfile.mkdtemp(prefix="vjmcp_e2e_"), "VJMCP-E2E-HMI.vdz")
res = s.save_as(out, "VJMCP-E2E-HMI", active_panel=panel)
print("saved:", res["written"])
check = {p: d for p, d in s.edits.items() if p.endswith(("/GraphicalObject", "/StatusFlags"))}
v = gfx.validate_in_vijeo(out, panel, check)
print(json.dumps(v, indent=1))
print("source backup untouched:", os.path.getmtime(VDZ) == mtime)
sys.exit(0 if v["verdict"].startswith("PASS") else 1)
