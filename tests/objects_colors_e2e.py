"""New objects, copying from another project, popup copy, state colours, constants, transparent shapes."""
import json, os, shutil, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402
from vijeo_mcp import graphics as gfx

VDZ = _paths.VDZ()
OUT = os.path.join(os.path.dirname(__file__), "..", "demo")
fails = []
def check(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else "")); fails.append(n) if not c else None

lib = os.path.join(tempfile.mkdtemp(prefix="vjmcp_lib_"), "LIBRARY.vdz")
shutil.copy(VDZ, lib)                                   # stands in for a separate library project
s = gfx.GraphicsSession(VDZ)

def place(pname, path, name, x, y):
    b = s._read(s.panels[pname]["root"] + "/GraphicalObject")
    o = gfx._find(gfx.parse_panel(b), path)
    s.copy(pname, path, new_name=name, dx=x - o.rect[0], dy=y - o.rect[1], to_panel="Home")

# new objects
s.add_object("Home", "Rectangle", 40, 60, 160, 60, name="NewBox", fill="#FFE0A0", line="#804000", line_width=3)
s.add_object("Home", "Ellipse", 220, 60, 60, 60, name="NewDot", fill="none", line="#FF0000", line_width=3)
s.add_object("Home", "Polygon", points=[[300, 120], [340, 60], [380, 120]], name="NewTri", fill="none", line="#0000FF")
s.add_object("Home", "Line", points=[[40, 140], [380, 140]], name="NewRule", line="#0000FF", line_width=4)
r = s.add_object("Home", "Text", 400, 60, 260, 24, name="NewLabel", text="Created by Vijeo_MCP", text_color="#FFFFFF",
                 fill="#004080")
check("new text", s.texts("Home", "NewLabel")[0]["text"] == "Created by Vijeo_MCP")
check("transparent ellipse / polygon", s.style("Home", "NewDot")["fill"] == "none" and s.style("Home", "NewTri")["fill"] == "none")
# from another project
s.copy_object_from_project(lib, "VA_Panel", "Lamp28", "Home", new_name="LibLamp", x=700, y=60)
check("copied from another project, still bound", [o for o in s.objects("Home", 0) if o["path"] == "LibLamp"][0]["variables"]
      == ["GENERAL.STATION_SIGNALS.MAINPOWERFAIL"])
# state colours and constants
s.set_object_color("Home", "LibLamp", "fore", "#0000FF", state="off")
s.set_object_color("Home", "LibLamp", "text", "#FFFF00", state="off")
off = next(t for t in s.object_colors("Home", "LibLamp")["tables"] if t["state"] == "off")
check("lamp off colours", off["colors"]["fore"] == "#0000FF" and off["colors"]["text"] == "#FFFF00", str(off))
place("LogInOut", "VisualizadorDeCadena01", "DDS", 360, 110)
s.set_object_color("Home", "DDS", "background", "#FF8000", state="normal")
place("Measures", "VisualizadorNumérico09", "DDN", 360, 150)
c = next(e for e in s.object_expressions("Home", "DDN") if e["kind"] == "constant")
s.set_object_constant("Home", "DDN", c["index"], 55.5)
check("constant changed", s.object_expressions("Home", "DDN")[c["index"]]["text"] == "55.5")
# popup copy
s.copy_panel("MOV_Info", "MOV_Info_Copy")
check("popup copied", s.panels["MOV_Info_Copy"]["popup"])

out = os.path.join(tempfile.mkdtemp(prefix="vjmcp_oc_"), "VJMCP-OBJCOL-HMI.vdz")
s.save_as(out, "VJMCP-OBJCOL-HMI", active_panel="Home")
v = gfx.verify_project(out, VDZ)
check("offline verify", v["verdict"] == "PASS", str(v["errors"][:3]))
val = gfx.validate_session(s, screenshot_dir=OUT)
for p, r in val["panels"].items():
    print(p, json.dumps(r.get("round_trip_identical")))
    check(f"Vijeo round trip: {p}", r["verdict"].startswith("PASS"), r["verdict"])
print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
