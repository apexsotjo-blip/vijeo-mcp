"""Trend graphs: channels (variable, enable, colour, width, marks, out-of-range) and graph settings.

Offline: every trend of the reference project parses, and the channel builder reproduces every populated channel byte for
byte. Vijeo: the edited trends are re-saved byte-identical; screenshots in demo/ show the result.
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
T = "GráficoDeTendencias01"

# ---------------- offline
trends = same = populated = 0
for pname, info in s.panels.items():
    b = s._read(info["root"] + "/GraphicalObject")
    for path, o in gfx.walk(gfx.parse_panel(b)):
        if o.kind != "TrendGraph":
            continue
        trends += 1
        for c in gfx._trend_layout(b, o)["channels"]:
            if c["empty"]:
                continue
            var = c["strings"][1].replace("TagDB.", "", 1)
            try:
                chain = s.resolver.binding(var)
            except KeyError:
                continue                                    # stale leftover channel (variable deleted)
            populated += 1
            same += b[c["marker"]:c["tail"]] == gfx._channel_bytes(var, chain)
check("all trend graphs parse", trends == 17, str(trends))
check("channel builder reproduces every populated channel", same == populated and populated > 80, f"{same}/{populated}")
t = s.trend("VA_Trends_P1T1_Tem", T)
check("read settings", t["display_range"] == [0.0, 150.0] and t["channels"][0]["variable"] == "VA_External.VSDs.P1T1.WindingUT"
      and t["channels"][0]["color"] == "#FF0000" and all(c["enabled"] for c in t["channels"]), str(t["channels"][0]))

# ---------------- edits
P = "VA_Trends_P1T1_parameters"
s.set_trend_channel(P, T, 4, enabled=False)
s.set_trend_channel(P, T, 3, marks=False)
s.set_trend_channel(P, T, 1, line_width=4, out_of_range_min=0, out_of_range_max=690)
r = s.set_trend_channel(P, T, 5, variable="VA_External.VSDs.P1T1.DEBearingT", color="#FF00FF")
check("empty channel filled and enabled", r["to"]["enabled"] and r["to"]["variable"].endswith("DEBearingT"), str(r["to"]))
s.set_trend(P, T, display_range=[0, 500])
r = s.set_trend_channel(P, T, 6, variable="")
check("channel cleared", r["to"]["variable"] == "" and not r["to"]["enabled"], str(r["to"]))
P2 = "Trends3"
s.set_trend(P2, T, value_range=[0, 20], display_range=[0, 20], label_decimals=0, data_axis_divisions=4, time_axis_divisions=6)
s.set_trend_channel(P2, T, 2, variable="GENERAL.CHLORINE_ANALYZER.RESIDUAL1CLT", color="#00FF00", line_width=2)
t2 = s.trend(P2, T)
check("graph settings read back", (t2["display_range"], t2["label_decimals"], t2["data_axis_divisions"], t2["time_axis_divisions"])
      == ([0.0, 20.0], 0, 4, 6), str({k: t2[k] for k in s._GRAPH_KEYS}))
for bad in (lambda: s.set_trend_channel(P, T, 9, enabled=True), lambda: s.set_trend_channel(P, T, 2, variable="NO.SUCH.VAR"),
            lambda: s.set_trend(P, T, display_range=[5, 1])):
    try:
        bad(); check("rejects invalid trend edit", False)
    except (ValueError, KeyError):
        check("rejects invalid trend edit", True)

tmp = tempfile.mkdtemp(prefix="vjmcp_trend_")
out = os.path.join(tmp, "VJMCP-TRENDS-HMI.vdz")
s.save_as(out, "VJMCP-TRENDS-HMI", active_panel=P)
v = gfx.verify_project(out, VDZ)
check("offline verify", v["verdict"] == "PASS", str(v["errors"][:3]))
val = gfx.validate_session(s, screenshot_dir=OUT)
for p, r in val["panels"].items():
    print(p, json.dumps(r.get("round_trip_identical")))
    check(f"Vijeo round trip: {p}", r["verdict"].startswith("PASS"), r["verdict"])
print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
