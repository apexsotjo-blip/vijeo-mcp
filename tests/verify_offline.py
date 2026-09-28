"""The UI-free verifier must PASS good edits and FAIL broken ones (no Vijeo started)."""
import os, struct, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402
from vijeo_mcp import graphics as gfx

VDZ = _paths.VDZ()
DEMO = os.path.join(os.path.dirname(__file__), "..", "demo", "VJMCP-DEMO-HMI.vdz")
tmp = tempfile.mkdtemp(prefix="vjmcp_verify_")
fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(name)


t = time.time(); r = gfx.verify_project(DEMO, VDZ)
check("demo project (validated earlier by Vijeo) verifies offline", r["verdict"] == "PASS",
      f"{r['panels']} panels, {r['objects']} objects, changed {len(r['changed_streams'])} streams, {time.time()-t:.1f}s; {r['errors'][:2]}")

# broken 1: corrupt a record size inside an edited panel
s = gfx.GraphicsSession(VDZ)
s.move("VA_Panel", "Texto36", dy=10)
p = [k for k in s.edits if k.endswith("/GraphicalObject")][0]
b = bytearray(s.edits[p]); o = gfx._find(gfx.parse_panel(bytes(b)), "Tank01")
struct.pack_into("<I", b, o.start, struct.unpack_from("<I", b, o.start)[0] + 4); s.edits[p] = bytes(b)
out = os.path.join(tmp, "BROKEN1.vdz"); s.save_as(out, "VJMCP-BROKEN1")
r = gfx.verify_project(out, VDZ); check("detects a corrupted record size", r["verdict"] == "FAIL", r["errors"][:1])

# broken 2: binding text points to a variable but with another variable's id
s = gfx.GraphicsSession(VDZ)
s.rebind("VA_Panel", "GENERAL.STATION_SIGNALS.MAINPOWERFAIL", "GENERAL.STATION_SIGNALS.PLCA_RUN", "Lamp28")
b = s.edits[p].replace("{T,2:1.11924,PLCA_RUN}".encode("utf-16-le"), "{T,2:1.11925,PLCA_RUN}".encode("utf-16-le"))
s.edits[p] = b
out = os.path.join(tmp, "BROKEN2.vdz"); s.save_as(out, "VJMCP-BROKEN2")
r = gfx.verify_project(out, VDZ); check("detects a binding with a wrong id", r["verdict"] == "FAIL", r["errors"][:1])

# broken 3: re-bind but forget the dependency list
s = gfx.GraphicsSession(VDZ)
s.rebind("VA_Panel", "GENERAL.STATION_SIGNALS.MAINPOWERFAIL", "GENERAL.STATION_SIGNALS.PLCA_RUN", "Lamp28")
del s.edits[[k for k in s.edits if k.endswith("/StatusFlags")][0]]
out = os.path.join(tmp, "BROKEN3.vdz"); s.save_as(out, "VJMCP-BROKEN3")
r = gfx.verify_project(out, VDZ); check("detects a stale dependency list", r["verdict"] == "FAIL", r["errors"][:1])

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
