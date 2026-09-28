"""Where the tests find the reference project files.

Set environment variables, or put them in tests/paths_local.json (git-ignored):
  VIJEO_MCP_TEST_VDZ   project backup (.vdz)
  VIJEO_MCP_TEST_CSV   variable export (.csv)
  VIJEO_MCP_TEST_UDT   data-types export (.VJDDataTypes)
  VIJEO_MCP_TEST_ORIG_CSV / VIJEO_MCP_TEST_ORIG_UDT   the exports before editing (regression test only)
"""
import json
import os

_local = os.path.join(os.path.dirname(__file__), "paths_local.json")
_cfg = json.load(open(_local, encoding="utf-8")) if os.path.exists(_local) else {}


def path(name: str) -> str:
    p = os.environ.get(name) or _cfg.get(name)
    if not p:
        raise SystemExit(f"Set {name} (environment variable or tests/paths_local.json) to run this test.")
    return p


VDZ = lambda: path("VIJEO_MCP_TEST_VDZ")          # noqa: E731
CSV = lambda: path("VIJEO_MCP_TEST_CSV")          # noqa: E731
UDT = lambda: path("VIJEO_MCP_TEST_UDT")          # noqa: E731
