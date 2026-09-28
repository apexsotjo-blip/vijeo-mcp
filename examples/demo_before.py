"""'Before' screenshot for the demo: import an UNEDITED copy into a separate Vijeo instance and capture VA_Panel."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
import _paths  # noqa: E402
from vijeo_mcp import graphics as gfx

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
os.makedirs(OUT, exist_ok=True)
s = gfx.GraphicsSession(_paths.VDZ())
tmp = os.path.join(tempfile.mkdtemp(prefix="vjmcp_before_"), "VJMCP-BEFORE-HMI.vdz")
s.save_as(tmp, "VJMCP-BEFORE-HMI", active_panel="VA_Panel")
r = gfx.validate_in_vijeo(tmp, "VA_Panel", {}, screenshot=os.path.join(OUT, "before.png"))
print({k: v for k, v in r.items() if k != "round_trip_identical"})
