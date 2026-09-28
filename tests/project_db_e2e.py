"""Variables, folders and data types read and written directly in the .vdz (no CSV / data-types import).

Offline: the reader matches Vijeo's own variable export field by field (when VIJEO_MCP_TEST_CSV is the
export of the same project). Vijeo: the edited project is imported, re-saved, and every variable,
element, folder and data type read back from Vijeo's save must equal ours.
"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402
from vijeo_mcp import graphics as gfx
from vijeo_mcp.variables import VariableTable

VDZ = _paths.VDZ()
fails = []
def check(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else "")); fails.append(n) if not c else None

s = gfx.GraphicsSession(VDZ)
rows = {r["name"]: r for r in s.project_variables()}
check("variables read from the .vdz", len(rows) > 1000, str(len(rows)))
try:
    t = VariableTable(_paths.CSV())
    common = [n for n in rows if n in t.vars]
    bad = [n for n in common if rows[n]["type"] != t.vars[n].dtype or rows[n]["source"] != t.vars[n].source
           or rows[n].get("description", "") != t.field(t.vars[n], "Description").strip('"')]
    check("reader agrees with Vijeo's CSV export", len(common) > 900 and not bad, f"{len(common)} compared, {bad[:3]}")
except SystemExit:
    print("SKIP CSV comparison (VIJEO_MCP_TEST_CSV not set)")

# ---------------- variables and folders
s.set_project_variable("GENERAL.EMFM.EMFM1FLOW", description="Tower outlet flow (edited)", address="%MW5",
                       alarm={"group": "AlarmGroup1", "message": "Tower outlet flow alarm", "severity": 2},
                       logging_group="GrupoDeRegistros01", input_range=[0, 2500])
s.add_project_variable("DEMO.PUMP9.Running", "BOOL", "External", scan_group="EquipoModbus01", address="%M2001",
                       description="Pump 9 running", alarm={"group": "AlarmGroup1", "message": "Pump 9 running"})
s.add_project_variable("DEMO.PUMP9.SpeedSP", "REAL", "Internal", initial_value=12.5)
s.add_project_variable("DEMO.PUMP9.Tag", "STRING", "Internal", string_bytes=32, initial_value="P9")
s.add_project_structure("VA_External.VSDs.P9T9", like="VA_External.VSDs.P1T1", addresses={"Running": "%MW9001"})
s.add_project_folder("DEMO2.SUB"); s.delete_project_folder("DEMO2.SUB")
T = s.tagdb
unused = next(n for n, v in sorted(T.vars.items()) if n.startswith("GENIOS.") and not s._usage(v["oid"]))
s.delete_project_variable(unused)
try:
    s.delete_project_variable("GENERAL.EMFM.EMFM1FLOW"); check("refuses deleting a used variable", False)
except ValueError:
    check("refuses deleting a used variable", True)
# ---------------- data types
s.project_data_type_edit("add_member", "AI", "Quality", "INT")
s.set_project_variable("VA_External.AIs.LTT1.Quality", address="%MW5001")
try:
    s.project_data_type_edit("remove_member", "AI", "ActiveValue"); check("refuses removing a used member", False)
except ValueError:
    check("refuses removing a used member", True)
s.project_data_type_edit("add_type", "PUMP_DEMO", members=[["Running", "BOOL"], ["Speed", "REAL"], ["Starts", "INT"]])
s.new_project_structure("DEMO.P1", "PUMP_DEMO", "External", addresses={"Running": "%M3001", "Speed": "%MW3002"})
# ---------------- read back
rows = {r["name"]: r for r in s.project_variables()}
check("update read back", rows["GENERAL.EMFM.EMFM1FLOW"]["address"] == "%MW5" and
      rows["GENERAL.EMFM.EMFM1FLOW"]["alarm"]["message"] == "Tower outlet flow alarm")
check("new variables", rows["DEMO.PUMP9.Running"]["address"] == "%M2001" and rows["DEMO.PUMP9.SpeedSP"]["initial_value"] == 12.5)
check("cloned structure with element address", rows["VA_External.VSDs.P9T9.Running"]["address"] == "%MW9001")
check("deleted variable gone", unused not in rows)
check("new member on every AI", all(f"{i}.Quality" in rows for i in (n for n, r in rows.items() if r["type"] == "AI")))
check("new type instance", rows["DEMO.P1.Speed"]["address"] == "%MW3002" and rows["DEMO.P1"]["type"] == "PUMP_DEMO")
# a panel object bound to a new variable
lamp = next(o for o in s.objects("VA_Panel", 0) if o["type"] == "Lamp")
s.copy("VA_Panel", lamp["path"], new_name="Lamp_P9", dx=0, dy=40, rebind={lamp["variables"][0]: "DEMO.PUMP9.Running"})
check("panel binds to a new variable", [o for o in s.objects("VA_Panel", 0) if o["path"] == "Lamp_P9"][0]["variables"] == ["DEMO.PUMP9.Running"])

out = os.path.join(tempfile.mkdtemp(prefix="vjmcp_db_"), "VJMCP-DB-HMI.vdz")
s.save_as(out, "VJMCP-DB-HMI", active_panel="VA_Panel")
v = gfx.verify_project(out, VDZ)
check("offline verify", v["verdict"] == "PASS", str(v["errors"][:3]))
val = gfx.validate_session(s)
for p, r in val["panels"].items():
    print(p, json.dumps(r.get("round_trip_identical")), r.get("variables_compared"))
    check(f"Vijeo round trip: {p}", r["verdict"].startswith("PASS"), r["verdict"])
print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
