"""Appearance, texts/fonts, resize, popups and whole-panel copy - checked offline and by Vijeo itself.

Vijeo checks (one import per edited panel): Home (base panel), LogInOut (popup, via a host panel) and a
new panel copied from VA_Measures_2. Screenshots land in demo/.
"""
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

s = gfx.GraphicsSession(VDZ)

# ---------------- offline: geometry and text references are found everywhere
pts = tot = 0
for p in s.panels.values():
    b = s._read(p["root"] + "/GraphicalObject")
    for _, o in gfx.walk(gfx.parse_panel(b)):
        if o.kind in gfx._POINT_KINDS:
            tot += 1; pts += bool(gfx._points(b, o))
check("points found for every line/polyline/polygon", pts == tot, f"{pts}/{tot}")
refs = s._all_text_refs()
check("text references resolve, none shared", len(refs) > 1000 and max(refs.values()) == 1, str(len(refs)))

def place(pname, path, name, x, y):
    b = s._read(s.panels[pname]["root"] + "/GraphicalObject")
    o = gfx._find(gfx.parse_panel(b), path)
    return s.copy(pname, path, new_name=name, dx=x - o.rect[0], dy=y - o.rect[1], to_panel="Home")

# ---------------- Home (base panel): shapes, texts, fonts, sizes
r = place("General_Hydraulic_Schema", "Rectángulo02", "BoxA", 40, 60)
s.set_style("Home", "BoxA", fill="none", line="#0000FF", line_width=3)
place("General_Hydraulic_Schema", "Polígono06", "Arrow", 280, 60); s.resize("Home", "Arrow", width=60, height=60)
s.set_style("Home", "Arrow", fill="#FF0000", line="#000000")
place("Flow_Info", "Debit_TE_001/Ellipse04", "Dot", 380, 60); s.resize("Home", "Dot", width=60, height=60)
s.set_style("Home", "Dot", fill="#00C000", line="#FF0000", line_width=2)
place("Help", "LíneaPoligonal03", "Step", 480, 60); s.resize("Home", "Step", width=160, height=80)
s.set_style("Home", "Step", line="#FF0000", line_width=4)
place("Help", "LevelSensor/GroupObject07/Arc01", "Bow", 660, 60); s.resize("Home", "Bow", width=60, height=60)
s.set_style("Home", "Bow", line="#FF00FF", line_width=3)
place("Reservoir_Compartment_Info", "Línea03", "Rule", 40, 160); s.resize("Home", "Rule", width=600)
s.set_style("Home", "Rule", line="#FF8000", line_width=4)
t = place("Measures", "Text02", "Title", 740, 60)
check("copied text gets its own text id", t["new_texts"] == 1)
s.set_text("Home", "Title", "Vijeo_MCP appearance")
s.set_font("Home", "Title", face="Arial", height_px=19, bold=True)
s.set_style("Home", "Title", text="#FFFFFF", fill="#0060C0", border="none")
st = s.style("Home", "Title")
check("style read back", (st["text"], st["fill"], st["border"]) == ("#FFFFFF", "#0060C0", "none"), str(st))
f = s.texts("Home", "Title")[0]
check("text + font read back", f["text"] == "Vijeo_MCP appearance" and f["font"]["face"] == "Arial"
      and f["font"]["bold"] and f["font"]["height_px"] == 19, str(f))
orig = s.texts("Measures", "Text02")[0]["text"]
check("original text untouched", orig.strip() == "Tower Inlet Turbidity Transmitter", orig)
b = s._read(s.panels["Home"]["root"] + "/GraphicalObject")
rule = gfx._find(gfx.parse_panel(b), "Rule")
x1, y1, x2, y2 = [round(v) for p in gfx._points(b, rule) for v in __import__("struct").unpack_from("<2d", b, p)]
check("line points follow copy + resize", (x1 <= 41 and abs(x2 - x1) >= 590 and y1 in (160, 161)), f"{x1},{y1}-{x2},{y2}")
for bad in (lambda: s.set_style("Home", "Dot", fill="none"), lambda: s.set_style("Home", "Rule", fill="#FF0000")):
    try:
        bad(); check("rejects unsupported style", False)
    except (ValueError, KeyError):
        check("rejects unsupported style", True)

# ---------------- popup
s.set_text("LogInOut", "Texto01", "OPERATOR")
s.set_style("LogInOut", "Texto01", text="#FF0000")
s.move("LogInOut", "Interruptor05", dx=-8)

# ---------------- whole panel copy
pc = s.copy_panel("VA_Measures_2", "VA_Measures_3")
check("panel copied", pc["storage"] == "Panel48" and pc["panel_id"] == 29, str(pc))
first_text = [o["path"] for o in s.objects("VA_Measures_3", 0) if o["type"] == "Text" and o.get("texts")][0]
s.set_text("VA_Measures_3", first_text, "COPY OF VA_Measures_2")
check("copy's texts independent", s.texts("VA_Measures_2", first_text)[0]["text"] != "COPY OF VA_Measures_2")

# ---------------- save, offline verify, Vijeo
tmp = tempfile.mkdtemp(prefix="vjmcp_app_")
out = os.path.join(tmp, "VJMCP-APPEAR-HMI.vdz")
s.save_as(out, "VJMCP-APPEAR-HMI", active_panel="Home")
v = gfx.verify_project(out, VDZ)
check("offline verify", v["verdict"] == "PASS", str(v["errors"][:3]))
val = gfx.validate_session(s, screenshot_dir=OUT)
for p, r in val["panels"].items():
    print(p, json.dumps(r.get("round_trip_identical")), r.get("method"))
    check(f"Vijeo round trip: {p}", r["verdict"].startswith("PASS"), r["verdict"])
print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
