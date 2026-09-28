"""Demo: edit VA_Panel of the reference project backup THROUGH THE MCP SERVER, exactly as an AI assistant would.

Writes to Vijeo_MCP\\demo\\ :
  VJMCP-DEMO-HMI.vdz   the edited project (import it in Vijeo: File > Import, or double-click)
  before.png / after.png   VA_Panel as Vijeo shows it, before and after the edits
  DEMO.md              every tool call made, with its result
"""
import asyncio, json, os, sys, time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "demo")
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
import _paths  # noqa: E402
VDZ = _paths.VDZ()
os.makedirs(OUT, exist_ok=True)
log = []


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "vijeo_mcp"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(title, tool, **args):
                t0 = time.time()
                res = await s.call_tool(tool, args)
                sc = getattr(res, "structuredContent", None)
                data = sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
                if data is None:
                    data = [json.loads(c.text) for c in res.content] if res.content else None
                if res.isError:
                    data = {"error": res.content[0].text}
                log.append({"title": title, "tool": tool, "args": args, "result": data, "seconds": round(time.time() - t0, 1)})
                print(f"[{tool}] {title}  ({time.time() - t0:.1f}s)")
                if res.isError:
                    raise RuntimeError(data)
                return data

            await call("Open the backup for graphics editing (read-only source, edits in memory)", "open_graphics", vdz_path=VDZ)
            objs = await call("List VA_Panel's top-level objects", "list_panel_objects", panel="VA_Panel", max_depth=0)
            await call("Inspect Tank01 with its children and their variables", "list_panel_objects", panel="VA_Panel", max_depth=1)
            for name in ("Texto35", "Texto36", "Image08", "Lamp28"):
                await call(f"Move '{name}' (part of the Main Power indicator) 200 px down", "move_object",
                           panel="VA_Panel", object_path=name, dy=200)
            await call("Give the lamp a meaningful name", "rename_object", panel="VA_Panel", object_path="Lamp28", new_name="MainPower_Lamp")
            await call("Give the label a meaningful name", "rename_object", panel="VA_Panel", object_path="Texto36", new_name="MainPower_Label")
            await call("Re-point the lamp to another variable", "rebind_variable", panel="VA_Panel",
                       old_variable="GENERAL.STATION_SIGNALS.MAINPOWERFAIL", new_variable="GENERAL.STATION_SIGNALS.PLCA_RUN",
                       object_path="MainPower_Lamp")
            await call("Device swap: Tank 1 graphics from level transmitter LTT1 to LTT2", "rebind_variable", panel="VA_Panel",
                       old_variable="VA_External.AIs.LTT1", new_variable="VA_External.AIs.LTT2", object_path="Tank01")
            await call("Check the result: objects and their variables after the edits", "list_panel_objects", panel="VA_Panel", max_depth=1)
            await call("Review pending edits", "pending_graphics_edits")
            await call("Save as a NEW project and validate it in Vijeo (screenshot of the edited panel)", "save_edited_project",
                       out_vdz=os.path.join(OUT, "VJMCP-DEMO-HMI.vdz"), project_name="VJMCP-DEMO-HMI",
                       validate=True, overwrite=True, screenshot_path=os.path.join(OUT, "after.png"))


asyncio.run(main())
json.dump(log, open(os.path.join(OUT, "demo_log.json"), "w"), indent=1, default=str)
print("log written")
