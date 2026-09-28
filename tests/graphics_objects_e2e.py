"""Delete + copy (same panel, with rebind) and cross-panel copy - validated by Vijeo round trip + screenshots."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402
from vijeo_mcp import graphics as gfx

VDZ = _paths.VDZ()
OUT = os.path.join(os.path.dirname(__file__), "..", "demo")
fails = []
def check(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else "")); fails.append(n) if not c else None

# ---------- run 1: VA_Panel - delete + same-panel copy with rebind
s = gfx.GraphicsSession(VDZ)
n0 = sum(1 for _ in gfx.walk(gfx.parse_panel(s._read(s.panels["VA_Panel"]["root"] + "/GraphicalObject"))))
print(s.delete("VA_Panel", "Image08"))
r = s.copy("VA_Panel", "Level_Tx01", new_name="Level_Tx_Middle", dx=-130, dy=120,
           rebind={"VA_External.AIs.LTT2": "VA_External.AIs.LTT1"})
print(r)
objs = s.objects("VA_Panel", 3)
cp = [o for o in objs if o["path"].startswith("Level_Tx_Middle")]
check("copy exists with children", len(cp) == r["objects"] and cp[0]["rect"][0] == 614 - 130, str(cp[0]))
check("copy re-pointed to LTT1", any(v and any("LTT1" in x for x in v) for v in (o.get("variables") for o in cp))
      and not any(v and any("LTT2" in x for x in v) for v in (o.get("variables") for o in cp)))
orig = [o for o in objs if o["path"].startswith("Level_Tx01")]
check("original untouched (still LTT2)", any(v and any("LTT2" in x for x in v) for v in (o.get("variables") for o in orig)))
ids = [x.id for _, x in gfx.walk(gfx.parse_panel(s._read(s.panels["VA_Panel"]["root"] + "/GraphicalObject")))]
check("object ids unique", len(ids) == len(set(ids)), f"{len(ids)} objects (was {n0})")
tmp = tempfile.mkdtemp(prefix="vjmcp_obj_")
out1 = os.path.join(tmp, "VJMCP-OBJ1-HMI.vdz")
s.save_as(out1, "VJMCP-OBJ1-HMI", active_panel="VA_Panel")
v = gfx.verify_project(out1, VDZ); check("offline verify", v["verdict"] == "PASS", v["errors"][:2])
check1 = {p: d for p, d in s.edits.items() if p.endswith(("/GraphicalObject", "/StatusFlags"))}
val = gfx.validate_in_vijeo(out1, "VA_Panel", check1, screenshot=os.path.join(OUT, "objects_va_panel.png"))
print(json.dumps({k: val[k] for k in val if k != "dialogs"}, indent=1))
check("Vijeo round trip: delete + copy + rebind", val["verdict"].startswith("PASS"))

# ---------- run 2: cross-panel copy (VA_Panel Tank01 -> Home), validated on Home
s = gfx.GraphicsSession(VDZ)
r = s.copy("VA_Panel", "Tank01", to_panel="Home", dx=0, dy=150)
print(r)
out2 = os.path.join(tmp, "VJMCP-OBJ2-HMI.vdz")
s.save_as(out2, "VJMCP-OBJ2-HMI", active_panel="Home")
v = gfx.verify_project(out2, VDZ); check("offline verify (cross-panel)", v["verdict"] == "PASS", v["errors"][:2])
check2 = {p: d for p, d in s.edits.items() if p.endswith(("/GraphicalObject", "/StatusFlags"))}
val = gfx.validate_in_vijeo(out2, "Home", check2, screenshot=os.path.join(OUT, "objects_home.png"))
print(json.dumps({k: val[k] for k in val if k != "dialogs"}, indent=1))
check("Vijeo round trip: cross-panel copy", val["verdict"].startswith("PASS"))
print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
