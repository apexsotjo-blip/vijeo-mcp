"""MCP server for Vijeo Designer HMI projects.

Transport: stdio (all logs to stderr). Safety model: .vdz backups are only READ
(in memory); every edit writes a NEW import file (variables CSV or
.VJDDataTypes) that the designer imports through Vijeo Designer, which
validates it. Inputs are never overwritten.
"""

from __future__ import annotations

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

from . import __version__
from . import analysis
from .automation import diagnose
from .datatypes import DataTypes
from .project import VijeoBackup
from .safety import check_output
from .variables import VariableTable

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vijeo-mcp")

mcp = FastMCP(
    "Vijeo Designer",
    instructions=(
        "Tools for Vijeo Designer HMI projects. Two inputs: a project backup (.vdz, File > Backup in Vijeo) "
        "for everything about the GRAPHICS, and a variable export (.csv, Variables > Export) for the "
        "variable DEFINITIONS; optionally the user data types export (.VJDDataTypes). Typical flow: "
        "open_backup + load_variables -> analyze_usage / find_variable_usage / find_broken_references -> "
        "edit with set_external_addresses / remove_variables / remove_type_members -> validate_variables -> "
        "the designer imports the new files in Vijeo (data types first, then variables) and builds.\n"
        "Safety: backups are never modified; every edit writes a NEW file and refuses to overwrite inputs. "
        "Importing a CSV never deletes variables in Vijeo: removals (variables, structure-type members) are "
        "done in the Vijeo editor or by importing the edited data-types file; the edited CSV is the clean "
        "re-import set. Vijeo's COM automation is broken on some installations - run diagnose_automation "
        "before relying on it.\n"
        "Graphics: open_graphics(vdz) -> list_panel_objects -> move_object / rename_object / rebind_variable "
        "-> save_edited_project(out_vdz, NEW project_name, validate=True). Edits are length-safe (every "
        "enclosing record is resized) and validated by Vijeo itself re-saving the panel byte-identical."
    ),
)
mcp._mcp_server.version = __version__


class _State:
    backup: VijeoBackup | None = None
    table: VariableTable | None = None
    types_path: str | None = None


S = _State()


def _backup() -> VijeoBackup:
    if S.backup is None:
        raise RuntimeError("No backup open. Call open_backup(path_to_vdz) first.")
    return S.backup


def _table(csv_path: str = "") -> VariableTable:
    if csv_path:
        return VariableTable(csv_path)
    if S.table is None:
        raise RuntimeError("No variable export loaded. Call load_variables(path_to_csv) first.")
    return S.table


# ------------------------------------------------------------------ project
@mcp.tool()
def open_backup(path: str) -> dict:
    """Open a Vijeo Designer project backup (.vdz) READ-ONLY (in memory, never modified) and index its
    panels, popups, templates and every variable reference in graphics, scripts, alarms and recipes."""
    S.backup = VijeoBackup(path)
    return S.backup.summary()


@mcp.tool()
def list_panels(kind: str = "") -> list[dict]:
    """List panels of the open backup with their designer names. kind: '' (all), 'base', 'popup' or
    'template'. 'references' = number of distinct names the panel references."""
    return _backup().list_panels(kind)


@mcp.tool()
def panel_variables(panel: str) -> dict:
    """Variables referenced by one panel (by designer name, e.g. 'Measures', or id from list_panels).
    Also lists quoted literals (popup selector values such as "P1T1")."""
    p = _backup().find_panel(panel)
    return {"panel": p.name, "id": p.id, "kind": p.kind, "group": p.group,
            "variables": sorted(p.references), "literals": sorted(p.literals)}


# ---------------------------------------------------------------- variables
@mcp.tool()
def load_variables(csv_path: str) -> dict:
    """Load a Vijeo variable export (.csv from the Variables node > Export). Used as the variable
    definitions for analysis and as the source for edits."""
    S.table = VariableTable(csv_path)
    return S.table.summary()


@mcp.tool()
def list_variables(pattern: str = "", source: str = "", kind: str = "", limit: int = 200) -> list[dict]:
    """List variables from the loaded export. pattern: regex on the full name; source: Internal |
    External | InternalReference; kind: Variable | DDTVariable | SubVariable."""
    return _table().list(pattern, source, kind, limit)


@mcp.tool()
def find_variable_usage(pattern: str, limit: int = 100) -> list[dict]:
    """Where are variables matching `pattern` (regex) used in the open backup? With a loaded export,
    each result also says WHY it counts as used (graphic, script/other, indirect popup reference,
    alarm, logging) or 'unused'."""
    return analysis.variable_usage(_backup(), S.table, pattern, limit)


@mcp.tool()
def analyze_usage(prefix: str = "", report_path: str = "", overwrite: bool = False) -> dict:
    """Which variables are used/unused in the project (needs open_backup + load_variables).
    prefix limits the scope (e.g. 'VA_External.'). Returns counts, the unused list, and structure-type
    members unused on EVERY instance (removable from the data type itself). report_path (.xlsx)
    optionally writes a full per-variable report."""
    rep = analysis.usage_report(_backup(), _table(), prefix)
    reasons = rep.pop("_reasons")
    if report_path:
        from openpyxl import Workbook
        out = check_output(report_path, overwrite=overwrite)
        wb = Workbook(); ws = wb.active; ws.title = "Usage"
        ws.append(["Variable", "Kind", "Type", "Source", "Category", "Used because"])
        t = _table()
        for n in sorted(reasons):
            v = t.vars[n]
            ws.append([n, v.kind, v.dtype, v.source, analysis.category(reasons[n]), ", ".join(sorted(set(reasons[n])))])
        ws2 = wb.create_sheet("Unused type members")
        ws2.append(["Data type", "Member (unused on every instance)"])
        for ty, mem in rep["unused_type_members"].items():
            for m in mem:
                ws2.append([ty, m])
        wb.save(out)
        rep["report"] = out
    if len(rep["unused"]) > 300:
        rep["unused_truncated"] = True
        rep["unused"] = rep["unused"][:300]
    return rep


@mcp.tool()
def find_broken_references() -> list[dict]:
    """TagDB.<variable> expressions in the open backup that match no variable in the loaded export
    (typically deleted/renamed variables still used by a panel)."""
    return analysis.broken_references(_backup(), _table())


@mcp.tool()
def set_external_addresses(assignments: dict[str, str], scan_group: str, out_path: str,
                           csv_path: str = "", overwrite: bool = False) -> dict:
    """Write a NEW variable CSV where the given variables are External on `scan_group` with the given
    device addresses ({"GENERAL.X.Y": "%MW101", "VA_External.VSDs.P1T1.Running": "%M1010", ...}).
    Structure elements also switch their structure to External. Formats follow Vijeo's own External
    rows (INT: BIN/2sComplement/16Bits, etc.). Import the result in Vijeo."""
    return _table(csv_path).set_external(assignments, scan_group, out_path, overwrite)


@mcp.tool()
def remove_variables(names: list[str], out_path: str, csv_path: str = "",
                     include_indirect_refs: bool = True, overwrite: bool = False) -> dict:
    """Write a NEW variable CSV without the given variables (a structure removes its elements).
    include_indirect_refs also drops InternalReference variables left pointing at nothing.
    Importing never deletes inside Vijeo: delete them in the editor too."""
    return _table(csv_path).remove(names, out_path, overwrite, include_indirect_refs)


@mcp.tool()
def validate_variables(csv_path: str = "", datatypes_path: str = "") -> dict:
    """Check a variable CSV before importing: row structure, duplicates, undeclared folders, missing
    parents, External without address/scan group, formats, shared addresses; with datatypes_path, also
    that every structure's elements match its data type (names and order)."""
    types = DataTypes(datatypes_path).types() if datatypes_path else None
    return _table(csv_path).validate(types)


@mcp.tool()
def edit_variables(out_path: str, add: list[dict] | None = None, update: dict[str, dict] | None = None,
                   add_structures: list[dict] | None = None, datatypes_path: str = "", csv_path: str = "",
                   overwrite: bool = False) -> dict:
    """Add / change variables in a NEW variable CSV (import it in Vijeo afterwards).
    add: [{"name": "PLANT.PUMP5.Running", "type": "BOOL", "source": "External", "scan_group": "EquipoModbus01",
           "address": "%M2001", "description": "...", "alarm": true, "alarm_message": "...", "alarm_group": "AlarmGroup1"}, ...]
         types: BOOL INT UINT DINT UDINT REAL STRING; sources: Internal / External; missing folders are created.
    update: {"GENERAL.EMFM.EMFM1FLOW": {"description": "...", "address": "%MW3", "alarm": true, ...}}
            fields: description, initial_value, address, scan_group, source, alarm, alarm_message, alarm_group,
            severity, trigger, logging_group, min, max, input_range, retentive, bytes, ...
    add_structures: [{"name": "VA_External.AIs.PT3T1", "type": "AI", "source": "External", "scan_group": "...",
                      "addresses": {"Alarm": "%M3001", ...}}]  (needs datatypes_path for the field list)
    New rows are cloned from existing rows of the same kind, so they match Vijeo's own export layout.
    The result is validated (with datatypes_path: structures against their types)."""
    from .varedit import VariableEditor
    ed = VariableEditor(csv_path or _table().path)
    for spec in add or []:
        spec = dict(spec)
        ed.add_variable(spec.pop("name"), spec.pop("type"), spec.pop("source", "Internal"), **spec)
    for name, fields in (update or {}).items():
        ed.update_variable(name, **fields)
    if add_structures:
        if not datatypes_path:
            raise ValueError("add_structures needs datatypes_path (the .VJDDataTypes export) for the field list.")
        types = DataTypes(datatypes_path).types()
        for spec in add_structures:
            if spec["type"] not in types:
                raise KeyError(f"Type {spec['type']} not in {datatypes_path}")
            ed.add_structure_instance(spec["name"], spec["type"], types[spec["type"]], spec.get("source", "Internal"),
                                      spec.get("scan_group", ""), spec.get("addresses"), spec.get("description", ""))
    res = ed.save(out_path, overwrite)
    res["validation"] = VariableTable(res["written"]).validate(DataTypes(datatypes_path).types() if datatypes_path else None)
    return res


@mcp.tool()
def edit_data_types(path: str, operations: list[dict], out_path: str, overwrite: bool = False) -> dict:
    """Edit a .VJDDataTypes export into a NEW file. operations (applied in order):
      {"op":"add_type","type":"PUMP","fields":[["Running","BOOL"],["Speed","REAL"],["Tag","STRING",20]]}
      {"op":"add_members","type":"AI","fields":[["Quality","INT"]],"after":"ActiveValue"}
      {"op":"remove_members","type":"AI","members":["TotAbs"]}
      {"op":"rename_member","type":"AI","member":"TotAbs","new_name":"Total"}
      {"op":"retype_member","type":"AI","member":"Quality","new_type":"DINT"}
      {"op":"delete_type","type":"PUMP"}
    The result is self-checked against the intended types before writing. After importing it in Vijeo,
    import a variable CSV whose structure elements match (validate_variables with datatypes_path)."""
    return DataTypes(path).edit(operations, out_path, overwrite)


# --------------------------------------------------------------- data types
@mcp.tool()
def list_data_types(path: str) -> list[dict]:
    """List the named structure types in a .VJDDataTypes export with their field counts."""
    S.types_path = path
    return DataTypes(path).summary()


@mcp.tool()
def data_type_fields(path: str, type_name: str) -> list[dict]:
    """Fields (name, type, in order) of one structure type."""
    t = DataTypes(path).types()
    if type_name not in t:
        raise KeyError(f"Type '{type_name}' not found. Types: {sorted(t)}")
    return [{"field": f, "type": ty} for f, ty in t[type_name]]


@mcp.tool()
def remove_type_members(path: str, removals: dict[str, list[str]], out_path: str, overwrite: bool = False) -> dict:
    """Write a NEW .VJDDataTypes without the given members ({"VSD": ["SpdRef", ...], "AI": [...]}).
    Everything else stays byte-identical; the result is self-checked before writing. Importing it in
    Vijeo removes those members from every instance of the type."""
    return DataTypes(path).remove_members(removals, out_path, overwrite)


# ----------------------------------------------------------------- graphics
from . import graphics as gfx  # noqa: E402

G: dict = {"session": None}


def _g() -> "gfx.GraphicsSession":
    if G["session"] is None:
        raise RuntimeError("No graphics session. Call open_graphics(path_to_vdz) first.")
    return G["session"]


@mcp.tool()
def open_graphics(vdz_path: str) -> dict:
    """Open a .vdz backup for GRAPHICS EDITING (in memory). Edits accumulate until save_edited_project,
    which writes a NEW .vdz under a new project name; the source file is never modified."""
    G["session"] = gfx.GraphicsSession(vdz_path)
    s = G["session"]
    return {"project": s.project, "panels": sorted(s.panels), "note": "edits are in memory until save_edited_project"}


@mcp.tool()
def list_panel_objects(panel: str, max_depth: int = 1) -> list[dict]:
    """Objects of a panel as paths ('Tank01/BarGraph01'), with type, rectangle [left, top, right, bottom]
    and the variables each object is bound to. max_depth: 0 = top level only."""
    return _g().objects(panel, max_depth)


@mcp.tool()
def move_object(panel: str, object_path: str, dx: int = 0, dy: int = 0,
                x: int | None = None, y: int | None = None) -> dict:
    """Move an object (a group moves with all its children) by (dx, dy) pixels, or to absolute x/y
    (top-left corner)."""
    return _g().move(panel, object_path, dx, dy, x, y)


@mcp.tool()
def rename_object(panel: str, object_path: str, new_name: str) -> dict:
    """Rename an object (names must be unique among its siblings)."""
    return _g().rename(panel, object_path, new_name)


@mcp.tool()
def rebind_variable(panel: str, old_variable: str, new_variable: str, object_path: str = "") -> dict:
    """Re-point references from one variable to another, e.g. 'GENERAL.EMFM.EMFM1FLOW' ->
    'GENERAL.EMFM.EMFM2FLOW', in one object (object_path) or everywhere on the panel (object_path='').
    Updates the binding (with the project's internal ids), the runtime expressions and the panel's
    dependency list. The new variable must already exist in the project."""
    return _g().rebind(panel, old_variable, new_variable, object_path)


@mcp.tool()
def delete_object(panel: str, object_path: str) -> dict:
    """Delete an object (a group with all its children). Enclosing groups shrink, and variables no
    longer used anywhere on the panel leave its dependency list. The last child of a group can't be
    deleted on its own - delete the group."""
    return _g().delete(panel, object_path)


@mcp.tool()
def copy_object(panel: str, object_path: str, new_name: str = "", dx: int = 20, dy: int = 20,
                to_panel: str = "", rebind: dict[str, str] | None = None) -> dict:
    """Duplicate an object (a group with its whole subtree) - on the same panel right after the original,
    or onto another panel (to_panel, drawn on top). The copy gets new unique object ids, a unique name
    (new_name or '<name>_Copy'), is offset by (dx, dy), and `rebind` {old variable: new variable}
    re-points the COPY only - e.g. copy Pump 1's graphics as Pump 2: rebind={"VA_External.VSDs.P1T1":
    "VA_External.VSDs.P2T1"}. Use this to 'add' objects from an existing template object."""
    return _g().copy(panel, object_path, new_name, dx, dy, to_panel, rebind)


@mcp.tool()
def resize_object(panel: str, object_path: str, width: int | None = None, height: int | None = None,
                  x: int | None = None, y: int | None = None) -> dict:
    """Set an object's size and/or position (pixels). A group scales its whole subtree; lines, polylines
    and polygons scale their points. Omitted values are kept."""
    return _g().resize(panel, object_path, width, height, x, y)


@mcp.tool()
def object_style(panel: str, object_path: str) -> dict:
    """Current colours and line width of a shape or text: Rectangle / Ellipse / Polygon (fill, line,
    pattern, line_width), Line / Polyline / Arc (line, line_width), Text (text, fill, pattern, border)."""
    return _g().style(panel, object_path)


@mcp.tool()
def set_object_style(panel: str, object_path: str, fill: str | None = None, line: str | None = None,
                     text: str | None = None, border: str | None = None, pattern: str | None = None,
                     line_width: float | None = None) -> dict:
    """Change colours ('#RRGGBB' or 'none') and/or line width. Which colours exist depends on the type
    (see object_style). 'none' (transparent) is supported for Rectangle fill/line/pattern and Text
    fill/border. Switches, lamps and data displays: use their texts (set_object_text)."""
    colors = {k: v for k, v in dict(fill=fill, line=line, text=text, border=border, pattern=pattern).items()
              if v is not None}
    return _g().set_style(panel, object_path, line_width=line_width, **colors)


@mcp.tool()
def object_texts(panel: str, object_path: str) -> list[dict]:
    """The texts an object displays - a Text object has one, a switch/lamp/data display has one per state
    (index 0, 1, ...) - with each text's font (face, height in px, bold)."""
    return _g().texts(panel, object_path)


@mcp.tool()
def set_object_text(panel: str, object_path: str, text: str, index: int = 0) -> dict:
    """Change a displayed text (index = state for multi-state objects). Texts live in the project's
    language table; if another object shares the same text, this object first gets its own copy."""
    return _g().set_text(panel, object_path, text, index)


@mcp.tool()
def set_object_font(panel: str, object_path: str, face: str | None = None, height_px: int | None = None,
                    bold: bool | None = None, index: int | None = None) -> dict:
    """Change the font of an object's text (index) or of all its texts (index omitted): face name
    (e.g. 'Arial'), height in pixels (8 pt = 11 px, 14 pt = 19 px), bold."""
    return _g().set_font(panel, object_path, face, height_px, bold, index)


def _logging_note(variable: str) -> str | None:
    """Trend channels plot only variables that belong to a data logging group."""
    if not variable:
        return None
    if S.table is None:                              # read the project itself
        row = next((r for r in _g().project_variables(variable, True) if r["name"] == variable), None)
        if row is not None and not row.get("logging_group"):
            return (f"'{variable}' has no data logging group: the trend will stay empty until it gets one "
                    "(set_project_variable logging_group=...).")
        return None
    v = S.table.vars.get(variable)
    if v is None:
        return f"'{variable}' is not in the loaded variable export - can't check its data logging group."
    if not S.table.field(v, "LoggingGroup"):
        return (f"'{variable}' has no data logging group: the trend will stay empty until it gets one "
                "(edit_variables update logging_group=..., then import the CSV in Vijeo).")
    return None


@mcp.tool()
def trend_settings(panel: str, object_path: str) -> dict:
    """A trend graph's settings: value range, display range, label decimals, data/time axis divisions,
    and its 8 channels (enabled, variable, colour, line width, marks, out-of-range min/max/colour)."""
    return _g().trend(panel, object_path)


@mcp.tool()
def set_trend_settings(panel: str, object_path: str, value_range: list[float] | None = None,
                       display_range: list[float] | None = None, label_decimals: int | None = None,
                       data_axis_divisions: int | None = None, time_axis_divisions: int | None = None) -> dict:
    """Graph-wide trend settings: value_range [from, to] of the data, display_range [min, max] of the data
    axis, decimals of the axis labels, number of data-axis and time-axis divisions."""
    return _g().set_trend(panel, object_path, value_range, display_range, label_decimals,
                          data_axis_divisions, time_axis_divisions)


@mcp.tool()
def set_trend_channel(panel: str, object_path: str, channel: int, variable: str | None = None,
                      enabled: bool | None = None, color: str | None = None, line_width: float | None = None,
                      marks: bool | None = None, out_of_range_min: float | None = None,
                      out_of_range_max: float | None = None, out_of_range_color: str | None = None) -> dict:
    """Configure trend channel 1..8. variable: any project variable or structure element, e.g.
    'VA_External.VSDs.P1T1.Voltage' ('' empties the channel; giving a variable enables the channel).
    color '#RRGGBB', line_width, marks (dots at samples), out_of_range_min/max (values outside are drawn
    with out_of_range_color). Omitted settings are kept. If a variable export is loaded (load_variables),
    the result warns when the variable has no data logging group (the trend would stay empty)."""
    res = _g().set_trend_channel(panel, object_path, channel, variable, enabled, color, line_width, marks,
                                 out_of_range_min, out_of_range_max, out_of_range_color)
    note = _logging_note(res["to"]["variable"])
    if note:
        res["warning"] = note
    return res


@mcp.tool()
def copy_panel(panel: str, new_name: str) -> dict:
    """Duplicate a base panel or a popup (a popup stays in its popup group): all objects, variable
    bindings, dependency list and its own copies of every text, registered like Vijeo does (storage,
    panel list, new PanelID, name id). Then edit it like any panel, e.g. rebind_variable to point the
    copy at other equipment."""
    return _g().copy_panel(panel, new_name)


@mcp.tool()
def add_object(panel: str, kind: str, x: int = 0, y: int = 0, width: int = 100, height: int = 40, name: str = "",
               points: list[list[float]] | None = None, text: str | None = None, fill: str | None = None,
               line: str | None = None, text_color: str | None = None, border: str | None = None,
               line_width: float | None = None) -> dict:
    """Create a NEW drawing object on a panel: kind Rectangle, Ellipse, Line, Polygon or Text. Rectangles,
    ellipses and texts use x, y, width, height; lines and polygons use points [[x, y], ...]. Colours
    '#RRGGBB' (or 'none' where supported), line_width; text for Text objects (plain label: no frame, no
    background unless given). For switches, lamps, displays, graphs etc. use copy_object, or
    copy_object_from_project to take them from a library project."""
    return _g().add_object(panel, kind, x, y, width, height, name, points, text, fill=fill, line=line,
                           text_color=text_color, border=border, line_width=line_width)


@mcp.tool()
def copy_object_from_project(src_vdz: str, src_panel: str, object_path: str, to_panel: str, new_name: str = "",
                             x: int | None = None, y: int | None = None,
                             rebind: dict[str, str] | None = None) -> dict:
    """Copy an object of ANY type (lamp, switch, display, trend, group, ...) from another project's .vdz -
    e.g. a library project of standard parts - into the open project. Variable bindings are re-created
    by name here (rebind {source variable: variable here} re-points them); texts come with their fonts.
    Objects that use the source project's images are refused."""
    return _g().copy_object_from_project(src_vdz, src_panel, object_path, to_panel, new_name, x, y, rebind)


@mcp.tool()
def object_state_colors(panel: str, object_path: str) -> dict:
    """Per-state colours of a switch, lamp, data display or bar graph: one table per state ('off' / 'on'
    for two-state switches and lamps, 'normal' for data displays and bar graphs, other tables by tag),
    with text / text_3d / frame / fore / back / background / indicator / plate / scale / marker colours."""
    return _g().object_colors(panel, object_path)


@mcp.tool()
def set_object_state_color(panel: str, object_path: str, color: str, value: str, state: str | None = None,
                           table: int | None = None) -> dict:
    """Set one state colour, e.g. a lamp's 'fore' (lamp colour) for state 'on' to '#FF0000', a switch's
    'text' for 'off', a data display's 'background' for 'normal', a bar graph's 'indicator'. Without
    state/table every state table that has that colour is changed."""
    return _g().set_object_color(panel, object_path, color, value, state, table)


@mcp.tool()
def object_expressions(panel: str, object_path: str) -> list[dict]:
    """All expressions of an object: variable bindings, animation conditions (visibility, colour) and
    constants such as data-display / bar-graph limits and animation state values. Re-point variables with
    rebind_variable; change constants with set_object_constant."""
    return _g().object_expressions(panel, object_path)


@mcp.tool()
def set_object_constant(panel: str, object_path: str, index: int, value: float) -> dict:
    """Change a constant expression of an object (index from object_expressions): a limit, min / max,
    threshold or animation state value."""
    return _g().set_object_constant(panel, object_path, index, value)


# ------------------------------------------------ project variables, directly in the .vdz (no CSV import)
def _settings(**kw) -> dict:
    return {k: v for k, v in kw.items() if v is not None}


@mcp.tool()
def project_variables(prefix: str = "", elements: bool = True) -> list[dict]:
    """Variables of the open project (open_graphics), read from the .vdz itself: name, kind (Variable /
    Structure / Element), type, source (Internal / External / InternalReference), description, initial
    value, scan group and address, alarm (group, severity, message), logging group, input range, data
    sharing, string length. prefix filters by name ('VA_External.VSDs.')."""
    return _g().project_variables(prefix, elements)


@mcp.tool()
def project_folders_and_groups() -> dict:
    """Variable folders, alarm / logging / scan groups and structure data types of the open project."""
    T = _g().tagdb
    return {"folders": T.folder_list(), "groups": {k: sorted(v.values()) for k, v in T.groups().items()},
            "data_types": {n: [f"{m}: {t}" for m, t in mem] for n, mem in T.data_types().items()}}


@mcp.tool()
def set_project_variable(name: str, description: str | None = None, initial_value: str | float | None = None,
                         source: str | None = None, scan_group: str | None = None, address: str | None = None,
                         alarm: dict | bool | None = None, logging_group: str | None = None,
                         input_range: list[float] | None = None, sharing: str | None = None,
                         string_bytes: int | None = None) -> dict:
    """Change settings of a variable or structure element directly in the project (no CSV import).
    source 'Internal' / 'External' (External needs scan_group + address); alarm: false to disable, or
    {"group": "AlarmGroup1", "severity": 1, "message": "..."}; logging_group '' removes it; input_range
    [min, max] or []; sharing 'None' / 'Read Only'. Structure elements take address, alarm, logging,
    description, initial value; their source / scan group come from the structure instance."""
    s = _settings(description=description, initial_value=initial_value, source=source, scan_group=scan_group,
                  address=address, alarm=alarm, logging_group=logging_group, input_range=input_range,
                  sharing=sharing, string_bytes=string_bytes)
    return _g().set_project_variable(name, **s)


@mcp.tool()
def add_project_variable(name: str, data_type: str, source: str = "Internal", description: str | None = None,
                         initial_value: str | float | None = None, scan_group: str | None = None,
                         address: str | None = None, alarm: dict | bool | None = None,
                         logging_group: str | None = None, input_range: list[float] | None = None,
                         sharing: str | None = None, string_bytes: int | None = None) -> dict:
    """Add a variable (BOOL, INT, UINT, DINT, UDINT, REAL, STRING) directly to the project; missing folders
    in the name ('PUMPS.P5.Running') are created. Same settings as set_project_variable. The variable can
    be bound on panels right away (rebind_variable / copy_object rebind)."""
    s = _settings(description=description, initial_value=initial_value, scan_group=scan_group, address=address,
                  alarm=alarm, logging_group=logging_group, input_range=input_range, sharing=sharing,
                  string_bytes=string_bytes)
    return _g().add_project_variable(name, data_type, source, **s)


@mcp.tool()
def add_project_structure(name: str, like: str, addresses: dict[str, str] | None = None) -> dict:
    """Add a structure instance (e.g. a new VSD) by cloning an existing instance `like` - same type and
    element settings - with addresses {member: address} for its elements, e.g.
    add_project_structure("VA_External.VSDs.P3T1", like="VA_External.VSDs.P1T1",
                          addresses={"Running": "%MW3001", "Speed": "%MW3002"})."""
    return _g().add_project_structure(name, like, addresses)


@mcp.tool()
def edit_project_data_type(op: str, type_name: str, member: str = "", member_type: str = "",
                           members: list[list[str]] | None = None, force: bool = False) -> dict:
    """Edit structure data types directly in the project (no .VJDDataTypes import):
    op 'add_member' (member, member_type: BOOL/INT/UINT/DINT/UDINT/REAL/STRING - every instance gets the
    element; set addresses with set_project_variable 'Instance.Member'), 'remove_member' (refused while
    panels use it unless force), 'add_type' (members [[name, type], ...]), 'delete_type' (no instances)."""
    return _g().project_data_type_edit(op, type_name, member, member_type, members, force)


@mcp.tool()
def new_project_structure(name: str, type_name: str, source: str = "Internal", scan_group: str = "",
                          addresses: dict[str, str] | None = None) -> dict:
    """Create a structure instance from its data type (no existing instance needed), e.g.
    new_project_structure("PUMPS.P5", "PUMP", "External", "EquipoModbus01", {"Running": "%M5001"})."""
    return _g().new_project_structure(name, type_name, source, scan_group, addresses)


@mcp.tool()
def delete_project_variable(name: str, force: bool = False) -> dict:
    """Delete a variable or structure instance from the project. Refused while panels are bound to it
    (the result names them) unless force=True."""
    return _g().delete_project_variable(name, force)


@mcp.tool()
def add_project_folder(path: str) -> dict:
    """Create a variable folder (and missing parents), e.g. 'PUMPS.P5'."""
    return _g().add_project_folder(path)


@mcp.tool()
def delete_project_folder(path: str) -> dict:
    """Delete an empty variable folder."""
    return _g().delete_project_folder(path)


@mcp.tool()
def pending_graphics_edits() -> dict:
    """The edits made in this session so far (not yet saved)."""
    s = _g()
    return {"project": s.project, "edits": s.log, "streams": sorted(s.edits)}


@mcp.tool()
def save_edited_project(out_vdz: str, project_name: str, validate: bool = False, overwrite: bool = False,
                        screenshot_dir: str = "") -> dict:
    """Write the edited project to a NEW .vdz with a NEW project name (import it in Vijeo with
    File > Import). An offline check (no Vijeo, no UI) always runs. validate=True additionally checks
    EVERY edited panel in Vijeo itself: a separate Vijeo instance imports a throw-away test copy, opens
    the panel (popups: through a host base panel), saves, and its save must be byte-identical to ours
    (texts compared in Vijeo's own table order); ~1 min per panel, a Vijeo window appears meanwhile,
    and the test instance/project are removed. screenshot_dir (with validate): one PNG per panel."""
    s = _g()
    if not s.edits:
        raise RuntimeError("No edits to save.")
    edited_panels = [n for n, i in s.panels.items() if i["root"] + "/GraphicalObject" in s.edits]
    base = [n for n in edited_panels if not s.panels[n]["popup"]]
    res = s.save_as(out_vdz, project_name, overwrite, active_panel=base[0] if base else "")
    res["verification"] = gfx.verify_project(res["written"], s.src)      # always: offline, no UI
    if validate:
        res["validation"] = gfx.validate_session(s, screenshot_dir=screenshot_dir)
    return res


@mcp.tool()
def verify_edited_project(vdz_path: str, original_vdz: str = "") -> dict:
    """Check a .vdz WITHOUT Vijeo and without any UI: every panel parses completely, every variable
    binding's ids match the project databases, dependency lists match the graphics, and (with
    original_vdz) every stream that was not edited is byte-identical to the original."""
    return gfx.verify_project(vdz_path, original_vdz)


# --------------------------------------------------------------- automation
@mcp.tool()
def diagnose_automation(live: bool = False) -> dict:
    """Check whether Vijeo Designer's COM automation is usable on this PC (registry consistency).
    live=True also starts a separate Vijeo instance, calls one harmless method and closes ONLY that
    instance (other open Vijeo windows are never touched)."""
    return diagnose(live)


def main() -> None:
    log.info("vijeo-mcp %s starting (stdio)", __version__)
    mcp.run()


if __name__ == "__main__":
    main()
