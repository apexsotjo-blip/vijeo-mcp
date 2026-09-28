"""Structure-instance re-bind (device swap) validated by Vijeo: Tank01 LTT1 -> LTT2 on VA_Panel."""
import os, sys, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402
from vijeo_mcp import graphics as gfx

VDZ = _paths.VDZ()
s = gfx.GraphicsSession(VDZ)
before = {o["path"]: o.get("variables") for o in s.objects("VA_Panel", 3) if o["path"].startswith("Tank01/")}
r = s.rebind("VA_Panel", "VA_External.AIs.LTT1", "VA_External.AIs.LTT2", "Tank01")
print(json.dumps(r, indent=1))
after = {o["path"]: o.get("variables") for o in s.objects("VA_Panel", 3) if o["path"].startswith("Tank01/")}
for k in before:
    if before[k] != after[k]:
        print(f"  {k}: {before[k]} -> {after[k]}")
stale = [k for k, v in after.items() if v and any("LTT1" in x for x in v)]
print("objects still on LTT1 inside Tank01:", stale)
raw = s.edits[[p for p in s.edits if p.endswith("/GraphicalObject")][0]]
print("stale element ids {T,3:1.16342.*} left in panel:", raw.count("{T,3:1.16342.".encode("utf-16-le")),
      "(Tank01 had them; other groups may legitimately still use LTT1)")
out = os.path.join(tempfile.mkdtemp(prefix="vjmcp_struct_"), "VJMCP-STRUCT-HMI.vdz")
s.save_as(out, "VJMCP-STRUCT-HMI", active_panel="VA_Panel")
check = {p: d for p, d in s.edits.items() if p.endswith(("/GraphicalObject", "/StatusFlags"))}
v = gfx.validate_in_vijeo(out, "VA_Panel", check)
print(json.dumps(v, indent=1))
sys.exit(0 if v["verdict"].startswith("PASS") and not stale else 1)
