"""Full variable / data-type manipulation: build new files and check them."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402
from vijeo_mcp.datatypes import DataTypes
from vijeo_mcp.varedit import VariableEditor
from vijeo_mcp.variables import VariableTable
from vijeo_mcp import csvtok

CSV = _paths.CSV()
UDT = _paths.UDT()
tmp = tempfile.mkdtemp(prefix="vjmcp_varedit_")
fails = []
def check(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else "")); fails.append(n) if not c else None

# ---------------- data types
out_udt = os.path.join(tmp, "types.VJDDataTypes")
r = DataTypes(UDT).edit([
    {"op": "add_type", "type": "PUMP_DEMO", "fields": [["Running", "BOOL"], ["Speed", "REAL"], ["Starts", "UDINT"], ["Tag", "STRING", 24]]},
    {"op": "add_members", "type": "AI", "fields": [["Quality", "INT"]], "after": "ActiveValue"},
    {"op": "rename_member", "type": "AI", "member": "Quality", "new_name": "SignalQuality"},
    {"op": "retype_member", "type": "AI", "member": "SignalQuality", "new_type": "DINT"},
    {"op": "add_type", "type": "TMP_DEL", "fields": [["X", "BOOL"]]},
    {"op": "delete_type", "type": "TMP_DEL"},
], out_udt)
t = DataTypes(out_udt).types()
check("add_type", t.get("PUMP_DEMO") == [("Running", "BOOL"), ("Speed", "REAL"), ("Starts", "UDINT"), ("Tag", "String")], str(t.get("PUMP_DEMO")))
check("add/rename/retype member at position", t["AI"][2] == ("SignalQuality", "DINT") and len(t["AI"]) == 20, str(t["AI"][:4]))
check("delete_type", "TMP_DEL" not in t)
orig = open(UDT, encoding="utf-8").read(); new = open(out_udt, encoding="utf-8").read()
for other in ("DI", "MOV", "VSD", "FCV"):
    import re
    blk = lambda s: re.search(rf'<structure name="{other}".*?</structure>', s, re.S).group(0)
    check(f"type {other} byte-identical", blk(orig) == blk(new))
try:
    DataTypes(UDT).edit([{"op": "delete_type", "type": "AI"}, {"op": "add_members", "type": "AI", "fields": [["Z", "BOOL"]]}], os.path.join(tmp, "x.VJDDataTypes"))
    check("refuses ops on a deleted type", False)
except KeyError:
    check("refuses ops on a deleted type", True)

# ---------------- variables
ed = VariableEditor(CSV)
ed.add_variable("DEMO.PUMP5.Running", "BOOL", "External", scan_group="EquipoModbus01", address="%M2001",
                description="Pump 5 running", alarm=True, alarm_message="Pump 5 running", alarm_group="AlarmGroup1")
ed.add_variable("DEMO.PUMP5.Speed", "REAL", "External", scan_group="EquipoModbus01", address="%MW2001", description="Pump 5 speed")
ed.add_variable("DEMO.PUMP5.Starts", "INT", "External", scan_group="EquipoModbus01", address="%MW2003")
ed.add_variable("DEMO.Note", "STRING", "Internal", bytes=32, initial_value="hello")
ed.add_variable("DEMO.Counter", "DINT", "Internal")
ed.update_variable("GENERAL.EMFM.EMFM1FLOW", description="Tower Outlet flow (edited)", alarm=True,
                   alarm_message="EMFM1 flow alarm", alarm_group="AlarmGroup1")
ed.add_structure_instance("VA_External.AIs.PT9T9", "AI", DataTypes(UDT).types()["AI"], "External", "EquipoModbus01",
                          {f: f"%MW{3001 + 2 * i}" for i, (f, _) in enumerate(DataTypes(UDT).types()["AI"])})
out_csv = os.path.join(tmp, "vars.csv")
res = ed.save(out_csv)
v = VariableTable(out_csv)
val = v.validate(DataTypes(UDT).types())
check("result validates (incl. structures vs types)", val["ok"], f"{val['error_count']} errors {val['errors'][:3]}")
check("folders created", {"DEMO", "DEMO.PUMP5"} <= v.folders)
rb = v.vars["DEMO.PUMP5.Running"]
check("external BOOL row", (v.field(rb, "Data Source"), v.field(rb, "Device Address"), v.field(rb, "Alarm"), v.field(rb, "Trigger Condition"))
      == ("External", "%M2001", "Enable", "when high"))
ri = v.vars["DEMO.PUMP5.Starts"]
check("external INT formats", (v.field(ri, "Data Format"), v.field(ri, "Signed"), v.field(ri, "Data Length")) == ("BIN", "2sComplement", "16Bits"))
rs = v.vars["DEMO.Note"]
check("internal STRING", (v.field(rs, "NumofBytes"), v.field(rs, "Initial Value")) == ("32", "hello"))
ru = v.vars["GENERAL.EMFM.EMFM1FLOW"]
check("update existing", (v.field(ru, "Description"), v.field(ru, "Alarm"), v.field(ru, "Device Address")) == ("Tower Outlet flow (edited)", "Enable", "%MW3"))
st = [n for n in v.vars if n.startswith("VA_External.AIs.PT9T9.")]
check("structure instance with all elements", len(st) == 19 and v.vars["VA_External.AIs.PT9T9"].source == "External", str(len(st)))
check("alarm messages renamed from template instance", "PT9T9" in v.field(v.vars["VA_External.AIs.PT9T9.Alarm"], "Alarm Message") or
      v.field(v.vars["VA_External.AIs.PT9T9.Alarm"], "Alarm") != "Enable", v.field(v.vars["VA_External.AIs.PT9T9.Alarm"], "Alarm Message"))
old = open(CSV, "rb").read().decode("latin-1").split("\r\n"); newl = open(out_csv, "rb").read().decode("latin-1").split("\r\n")
untouched = [l for l in old if "EMFM1FLOW," not in l]
check("every other original line unchanged", all(l in set(newl) for l in untouched), f"{len(untouched)} lines")
for bad in (lambda e: e.add_variable("DEMO.PUMP5.Running", "BOOL"), lambda e: e.add_variable("X Y", "BOOL"),
            lambda e: e.add_variable("DEMO.Z", "BOOL", "External")):
    try:
        bad(VariableEditor(out_csv)); check("rejects invalid add", False)
    except ValueError:
        check("rejects invalid add", True)
print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
print("sample new rows:")
for l in newl:
    if l.startswith(("Variable,DEMO.PUMP5.Running,", "Variable,DEMO.PUMP5.Starts,", "Folder,DEMO", "SubVariable,VA_External.AIs.PT9T9.Alarm,")):
        print("  ", l)
sys.exit(1 if fails else 0)
