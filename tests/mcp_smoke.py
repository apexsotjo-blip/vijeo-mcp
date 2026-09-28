"""End-to-end smoke test: start the MCP server over stdio and call tools like a client would."""

import asyncio
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: E402

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client



async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "vijeo_mcp"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            print("server:", init.serverInfo.name, init.serverInfo.version)
            tools = [t.name for t in (await s.list_tools()).tools]
            print("tools:", len(tools), tools)

            async def call(name, **kw):
                res = await s.call_tool(name, kw)
                if res.isError:
                    raise RuntimeError(f"{name}: {res.content[0].text if res.content else ''}")
                sc = getattr(res, "structuredContent", None)
                if sc is not None:
                    return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
                items = [json.loads(c.text) for c in res.content]
                return items[0] if len(items) == 1 and isinstance(items[0], dict) else items

            b = await call("open_backup", path=_paths.VDZ())
            print("open_backup panels:", b["panels"])
            v = await call("load_variables", csv_path=_paths.CSV())
            print("load_variables:", v["rows"], "external addressed:", v["external_addressed"])
            u = await call("find_variable_usage", pattern=r"^VA_External\.VSDs\.P1T1\.Running$")
            print("usage P1T1.Running:", u[0]["category"], sorted(set(u[0]["used_because"]))[:4], u[0]["place_count"], "places")
            val = await call("validate_variables", datatypes_path=_paths.UDT())
            print("validate:", val["ok"], val["error_count"], "errors;", val["warnings"])
            p = await call("panel_variables", panel="Measures")
            print("panel 'Measures':", len(p["variables"]), "variables, e.g.", p["variables"][:3])
            d = await call("diagnose_automation")
            print("automation:", d["verdict"][:60])
            try:
                await call("remove_variables", names=["VA_TagPrefix.AIs"], out_path=_paths.CSV())
                print("SAFETY FAIL: overwrite allowed"); return 1
            except RuntimeError as e:
                print("safety refusal OK:", str(e)[:90])
    return 0

sys.exit(asyncio.run(main()))
