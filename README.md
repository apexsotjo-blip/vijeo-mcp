# Vijeo MCP

An MCP server that lets an AI assistant (Claude Code, Claude Desktop, any MCP client) work on
**Vijeo Designer** HMI projects:
- analyse which variables the screens really use
- edit variables, folders and data types directly in the project
- edit panels: objects, colours, texts, trends, whole panels

Built and verified on Vijeo Designer 6.2 with a real site project (67 panels, about 2,900 objects).

## Safety model

- **The source `.vdz` is never modified.** Edits are made in memory and written to a **new** `.vdz`
  under a **new** project name, which you import in Vijeo (File > Import).
- **Other outputs:** the CSV / data-types tools write new files too. Inputs are never overwritten,
  and an existing output is only replaced with `overwrite=True`.
- **Offline check:** every saved project is checked without Vijeo or UI, for complete records,
  consistent ids and a readable variable database.
- **Vijeo check:** `validate=True` also lets Vijeo itself load and re-save a throw-away copy, and
  compares the result. Keep your original `.vdz` until the imported project builds.

## What you give it

| Input | How to get it in Vijeo Designer |
|---|---|
| Project backup `.vdz` | File > Backup project. This is all the tools need, including for variables and data types |
| Variable export `.csv` (optional) | Variables node > Export (only for the CSV tools below) |
| Data types `.VJDDataTypes` (optional) | Variables node > User Data Types > Export (only for the CSV tools) |

## Tools

| Tool | What it does |
|---|---|
| `open_backup` | Opens a `.vdz` read-only and indexes panels, popups, templates, scripts and alarms |
| `list_panels`, `panel_variables` | Panels by their designer names, and the variables each one uses |
| `load_variables`, `list_variables` | Loads a variable export and lists or filters variables |
| `find_variable_usage` | Where a variable is used, and **why** it counts as used |
| `analyze_usage` | Used/unused counts, the unused list, and structure members unused on every instance (optional `.xlsx` report) |
| `find_broken_references` | Screen expressions pointing at variables that don't exist |
| `set_external_addresses` | New CSV with variables set to External, with scan group and device addresses |
| `remove_variables` | New CSV without the given variables, including now-dangling indirect references |
| `validate_variables` | Pre-import checks, including structures against their data types |
| `edit_variables` | New CSV with variables added (internal or external, with alarm/logging fields), existing ones updated, and whole structure instances added |
| `list_data_types`, `data_type_fields`, `remove_type_members` | Reads and edits the data-types export |
| `edit_data_types` | New data-types file: add/delete types, add/remove/rename/retype members |
| `diagnose_automation` | Checks whether Vijeo's COM automation works on this PC |

### When a variable counts as "used"

A variable counts as used if at least one of these is true:

- **Screens:** a panel, popup or template references it.
- **Other project parts:** a script, recipe or alarm stream references it.
- **Popup selector:** it is reached through an `InternalReference` popup variable
  (`X.%s.Member(Selector)`), and the device name is written somewhere in the screens (e.g. `"P1T1"`).
- **Alarms:** it has an alarm or event enabled, so the alarm summary uses it.
- **Logging:** it is in a logging group, so trends or data logging use it.

Limit: this is a static analysis. A variable name built at run time from pieces of text, other
than through the popup selector, can't be detected.

## Typical workflows

**Remove unused variables.** Run `open_backup`, `load_variables` and `analyze_usage(prefix=...)`. Then:

1. `remove_type_members` writes a new data-types file.
2. `remove_variables` writes a new CSV.
3. `validate_variables(datatypes_path=...)` checks the pair.
4. In Vijeo, delete the listed variables. Importing a CSV never deletes anything.
5. Import the data types, then the CSV, then build.

**Connect variables to a PLC or RTU.** Use `set_external_addresses` with
`{name: address}` and a scan group, then `validate_variables`, then import the CSV.

## Variables, folders and data types in the project

With a project open (`open_graphics`), variables are read and written **inside the `.vdz`**. No CSV
or data-types import is needed; the new `.vdz` is imported as a whole.

| Tool | What it does |
|---|---|
| `project_variables` | Every variable and structure element with its settings: type, source, scan group and address, description, initial value, alarm (group, severity, message), logging group, input range, data sharing |
| `project_folders_and_groups` | Folders, alarm / logging / scan groups, and structure data types with their members |
| `set_project_variable` | Changes settings of a variable or structure element |
| `add_project_variable` | Adds BOOL / INT / UINT / DINT / UDINT / REAL / STRING variables; missing folders are created |
| `add_project_structure` | Adds a structure instance cloned from an existing one, with element addresses |
| `new_project_structure` | Adds a structure instance straight from its data type |
| `delete_project_variable` | Deletes a variable; refused while panels are bound to it |
| `add_project_folder`, `delete_project_folder` | Creates folders; deletes empty ones |
| `edit_project_data_type` | Adds or removes members of a structure type (every instance follows), adds or deletes types |

New variables can be bound on panels right away. For example, copy a lamp with
`rebind={"old": "PUMPS.P5.Running"}`.

**How it's verified:**
- **Offline:** the reader was compared with Vijeo's own CSV export of the same project. Across
  985 variables and elements, type, source, address, scan group, description, alarm (group,
  message, severity), logging group, data sharing and min/max all agree.
- **In Vijeo:** after edits, Vijeo loads the project and re-saves it. Every variable, element,
  folder and data type read back from Vijeo's save equals ours. Vijeo empties a usage cache and
  re-orders some editor state when it saves, so this comparison is by content.

## Graphics editing

Vijeo Designer has no public format or working automation for screens, so the panel format was
reverse-engineered. These edits are supported and validated:

| Tool | What it does |
|---|---|
| `open_graphics` | Loads a `.vdz` into memory for editing |
| `list_panel_objects` | Lists objects as paths (`Tank01/BarGraph01`), with type, rectangle, bound variables and displayed texts |
| `move_object` | Moves an object by (dx, dy) or to (x, y); a group moves with all its children |
| `resize_object` | Sets size and/or position; groups scale their subtree, lines and polygons their points |
| `rename_object` | Renames an object (names must be unique among siblings) |
| `rebind_variable` | Points one object, or the whole panel, at a different variable, e.g. `GENERAL.EMFM.EMFM1FLOW` → `EMFM2FLOW` |
| `delete_object` | Deletes an object or a whole group |
| `add_object` | Creates a new rectangle, ellipse, line, polygon or text (position/size or points, colours, text) |
| `copy_object` | Duplicates an object or group on the same panel or onto another one, optionally re-bound (Pump 1 → Pump 2) |
| `copy_object_from_project` | Copies an object of any type from **another** project (e.g. a library of standard parts); bindings are re-created by name |
| `copy_panel` | Duplicates a whole base panel or popup |
| `object_style`, `set_object_style` | Reads/sets colours (`#RRGGBB` or `none`) and line width of shapes and texts |
| `object_state_colors`, `set_object_state_color` | Reads/sets the per-state colours of switches, lamps, data displays and bar graphs |
| `object_expressions`, `set_object_constant` | Lists an object's expressions: bindings, visibility/colour animation conditions, limits. Changes constants such as limits, min/max and animation values |
| `object_texts`, `set_object_text`, `set_object_font` | Reads/sets displayed texts (one per state for switches and lamps) and their font |
| `save_edited_project` | Writes a **new** `.vdz` under a **new** project name; `validate=True` checks every edited panel in Vijeo itself |

`rebind_variable` also works for a whole structure instance, e.g. `VA_External.AIs.LTT1` → `LTT2`,
which moves every element such as `.ActiveValue` or `.HH_Alarm` to the other device.

**Colours by object type:**

| Type | Colours | Line width |
|---|---|---|
| Rectangle | fill, line, pattern (all may be `none`) | yes |
| Ellipse, Polygon | fill, line (both may be `none`), pattern | yes |
| Line, Polyline, Arc | line | yes |
| Text | text, fill, border (fill and border may be `none`), pattern | – |

**State colours** (`set_object_state_color`) keep one table per state:

| Type | Colours | States |
|---|---|---|
| Lamp, Switch | text, text_3d, frame, fore (the lamp/button colour), back | `off` (state 0), `on`; multi-state lamps: by table |
| Data display | text, text_3d, frame, background | `normal`; the other tables by index |
| Bar graph | indicator, frame, plate, text, text_3d, scale, marker | `normal`; threshold ranges by table |

Switches drawn with a 3D plate style take their face from the style image, so their `fore` colour
isn't visible; their text colour is.

**Animations and thresholds:** `object_expressions` lists what drives an object:
- variable bindings
- visibility and colour animation conditions (e.g. `VA_Internal.AIs.ForceEn != 0`)
- constants such as display limits, min/max and animation state values

Variables are re-pointed with `rebind_variable`, and constants changed with `set_object_constant`.

### Trend graphs

| Tool | What it does |
|---|---|
| `trend_settings` | Reads a trend: value range, display range, label decimals, axis divisions, and all 8 channels |
| `set_trend_settings` | Sets value range `[from, to]`, display range `[min, max]`, label decimals, data-axis and time-axis divisions |
| `set_trend_channel` | Sets channel 1–8: variable, enabled, colour, line width, marks, out-of-range min/max and colour |

Example: point channel 5 of a pump trend at a bearing temperature and give it its own colour.

```
set_trend_channel(panel="VA_Trends_P1T1_Tem", object_path="GráficoDeTendencias01", channel=5,
                  variable="VA_External.VSDs.P1T1.DEBearingT", color="#FF00FF")
set_trend_settings(panel="VA_Trends_P1T1_Tem", object_path="GráficoDeTendencias01",
                   value_range=[0, 150], display_range=[0, 150], data_axis_divisions=10)
```

- **Variables:** any variable or structure element of the project. Giving a variable to an empty
  channel also enables it; `variable=""` empties the channel.
- **Data logging:** Vijeo only plots variables that belong to a data logging group. With a variable
  export loaded (`load_variables`), `set_trend_channel` warns when the variable has none.
- **Legend:** the coloured squares and names next to a trend are ordinary objects. Change them with
  `set_object_style` and `set_object_text`.
- **Not supported yet:** number of samples, scroll, Fill display format, line style and Clear Trigger.

**Texts are shared data.** Objects don't hold their texts; they point into the project's language
table (`LangManager/LangManagerData`). Copies get their own text entries, as in Vijeo. If two
objects ever share a text, `set_object_text` first gives the edited object its own copy, so the
other object never changes with it.

### How edits are made safe

- **Every length change** rewrites the string's own length prefix and the size of every enclosing
  object or group, however deeply nested.
- **Re-binding** rebuilds the binding with the project's own internal IDs, read from
  `Services/TagDatabase` and `Services/NameServer`. On the reference project, every binding already in the
  project is rebuilt exactly this way.
- **Runtime expressions** (`TagDB.…`) and the panel's dependency list (`StatusFlags`) are updated too.
- **Both string encodings** Vijeo uses are handled: length-prefixed, and MFC `CString`.
- **Lines, polylines and polygons** are drawn from their own point lists, not from their rectangle,
  so moves, copies and resizes update both.
- **New panels** are registered where Vijeo registers them: panel storage, the panel list, a new
  PanelID, and a name ID in `Services/NameServer`.
- **After every edit** the panel must still parse completely, or nothing is saved.
- **`validate=True`** checks every edited panel in Vijeo itself, one throw-away import per panel
  (about a minute each; a Vijeo window appears meanwhile):
  1. imports a test copy into a separate Vijeo instance under a test project name
  2. makes Vijeo open the panel and save the project
  3. requires Vijeo's own save to be **byte-identical** to the tool's output. The language table is a
     hash map that Vijeo re-orders on every save, so it is compared with the exact order Vijeo writes.
  4. removes only that instance and the test project it created

  Vijeo never opens popups on load. For a popup, the test copy (never your output) places the
  popup's edited objects in a base panel that Vijeo does open. Object records have the same format
  in both panel types, so Vijeo still parses and re-writes exactly the edited objects.

### Checking without Vijeo or UI

`save_edited_project` always runs `verify_edited_project`, which takes about a second and never
starts Vijeo. It checks four things:

- Every panel parses completely: all record sizes and child chains fit.
- Every variable binding's IDs match the project's own databases.
- Dependency lists (`StatusFlags`) agree with the changed graphics.
- Every stream that wasn't edited is byte-identical to the original.

These are the same rules Vijeo's own load-and-save round trips confirmed. `tests/verify_offline.py`
shows the check passes a project that Vijeo validated, and catches a corrupted size, a wrong binding
ID and a stale dependency list.

**Why no headless Vijeo check yet.** Vijeo's panel code (`EditorDocument.dll`) can be loaded
in-process with no window (see `headless/`). However, loading and saving a panel needs Vijeo's
application framework services: the frame, the main document, the target and the storage service.
Those aren't available outside Vijeo. The optional `validate=True` check in the real Vijeo remains
for a final check; it shows a window for about 30 seconds.

### Verified on the reference project

All 67 panels and 2,909 objects parse completely; all 279 lines, polylines and polygons have their
points located, and all 1,386 text references resolve. Every edit type below passed the Vijeo round
trip byte-identically, and the colour offsets were confirmed by how Vijeo draws them:

- move, nested rename, resize (groups, polygons, polylines, arcs, lines)
- re-bind two groups deep, and a structure-instance re-bind (4 objects, 20 strings)
- delete; copy on the same panel with re-bind; copy onto another panel
- colours and line widths on rectangles, ellipses, polygons, lines, polylines, arcs and texts
- text and font changes, including on a popup (LogInOut)
- a whole-panel copy (VA_Measures_2 → VA_Measures_3), including its NameServer registration
- trend graphs: all 17 parse, and all 84 populated channels are rebuilt byte for byte. Filling an
  empty channel, enabling/disabling, colour, width, marks, ranges, decimals and divisions all passed
  the round trip and show up as expected in Vijeo's drawing. Out-of-range limits aren't drawn in the
  editor, so they are checked by the round trip only.
- **New objects:** rectangles, ellipses, polygons, lines and texts, plus a lamp and a trend graph
  copied from another project (still bound).
- **State colours:** lamp off fore/text, switch text, data-display background and bar-graph indicator,
  each confirmed by how Vijeo draws them.
- **Transparency:** ellipse and polygon fill/line `none`. The flags were found by rendering candidates.
- **Constants:** a data-display limit changed from 16000 to 55.5.
- **Popup copy:** registration byte-identical; objects re-saved with the same content (Vijeo re-orders
  some objects' internal colour maps outside their own popup).
- **Variable database:** updated, added and deleted variables; new and deleted folders; structure
  instances cloned and built from a type; members added to and removed from types; new types. After
  Vijeo's own save, all 1,245–1,330 variables and elements, the folders and the data types read back
  equal.

### Not supported yet

- **Other new object types:** switches, lamps, displays, meters and so on come from copying (from
  this or another project), not from scratch.
- **Multi-state lamps:** colour tables are addressed by table index, not state number. Two-state
  lamps and switches use `off` / `on`.
- **Bar-graph thresholds:** colours are editable, but the threshold *values* aren't mapped yet.
- **Adding animations** to an object that has none; existing ones can be re-pointed and their
  constants changed.
- **Images** of another project aren't copied (`copy_object_from_project` refuses objects with images).
- **Renaming** variables or structure members that panels use (bindings would have to be rewritten
  everywhere).

## Install

```powershell
cd <folder>\Vijeo_MCP
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

To register the server with Claude Code:

```powershell
claude mcp add vijeo -- "<folder>\Vijeo_MCP\.venv\Scripts\python.exe" -m vijeo_mcp
```

To register it with another MCP client, add this to its JSON configuration:

```json
{ "mcpServers": { "vijeo": { "command": "<folder>\\Vijeo_MCP\\.venv\\Scripts\\python.exe",
                             "args": ["-m", "vijeo_mcp"] } } }
```

## Tests

The tests run against a real project and its exports, which are not part of this repository. Point
them at your files with environment variables or a `tests/paths_local.json` file (git-ignored):

```json
{ "VIJEO_MCP_TEST_VDZ": "C:\\path\\to\\project.vdz",
  "VIJEO_MCP_TEST_CSV": "C:\\path\\to\\variables.csv",
  "VIJEO_MCP_TEST_UDT": "C:\\path\\to\\types.VJDDataTypes" }
```

The regression test also needs `VIJEO_MCP_TEST_ORIG_CSV` and `VIJEO_MCP_TEST_ORIG_UDT`, the exports
before editing. The tests check panel, variable and object names of the reference project, so on
another project they show you what to adapt rather than passing as they are.

```powershell
.\.venv\Scripts\python.exe tests\regression_reference.py # reproduces hand-verified results
.\.venv\Scripts\python.exe tests\verify_offline.py       # offline checker passes good / catches bad files
.\.venv\Scripts\python.exe tests\variables_udt_edit.py   # variable and data-type editing
.\.venv\Scripts\python.exe tests\mcp_smoke.py            # starts the server over stdio and calls tools
```

These open Vijeo (a window appears; a few minutes in total):

```powershell
.\.venv\Scripts\python.exe tests\graphics_e2e.py          # move, rename, re-bind
.\.venv\Scripts\python.exe tests\graphics_struct_e2e.py   # structure-instance re-bind
.\.venv\Scripts\python.exe tests\graphics_objects_e2e.py  # delete, copy, cross-panel copy
.\.venv\Scripts\python.exe tests\appearance_e2e.py        # colours, texts, fonts, resize, popup, panel copy
.\.venv\Scripts\python.exe tests\trends_e2e.py            # trend channels and graph settings
.\.venv\Scripts\python.exe tests\project_db_e2e.py        # variables, folders, data types in the .vdz
.\.venv\Scripts\python.exe tests\objects_colors_e2e.py    # new objects, library copy, popup copy, state colours
```

## About Vijeo's COM automation

`Vijeo-Frame.exe` registers automation classes (`VijeoFrame.VijeoDesigner2`: OpenProject, Build,
GetCrossReferences, panel Export/Import, Bind). On the reference PC they can't be used:

- The class behind the ProgID points to a type library (`{0B02AAD0-…}`) that isn't registered
  and isn't shipped.
- The `Vijeo-Frame.tlb` on disk describes interfaces that the running server doesn't implement.

Run `diagnose_automation(live=True)` on your PC, for example after a Vijeo repair or reinstall.
If it reports that automation is usable, that opens the way to build validation and editing
panel bindings from this tool. `live=True` starts its own Vijeo instance and closes only that
instance.
