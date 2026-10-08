# Hole & Thread Callouts

A Fusion add-in that makes one image showing which holes to tap.

Orbit the part to an angle and add a **thread view**. In each thread view, the
threaded holes it can see are filled in their size's colour, in Fusion's
*wireframe with visible edges* style, with one callout per size ("2x M3") and an
arrow to one of those holes. Each hole is called out once, in the first view that
shows it. Callouts can be dragged, re-aimed and re-worded, and you can add your
own arrows, text and shapes. A **shaded view** (*shaded with visible edges*) ends
the row. **Export image** stitches the views into one PNG and copies it to the
clipboard.

Colours are from the colour-blind-safe Okabe-Ito palette: M3 yellow, M4 sky
blue, M5 orange, M6 reddish purple (M2 bluish green, M2.5 blue, M8 vermillion;
any size can be recoloured). Dowel holes get the quartered-circle symbol and a
"2x Ø5 dowel" callout.

## Which holes

- Thread features on hole faces (internal threads) and tapped hole features:
  the size comes from the thread (M3x0.5 → M3).
- Optionally, plain holes whose diameter is a tap drill (2.5 mm → M3, 3.3 → M4,
  4.2 → M5, 5.0 → M6...), for imported parts without modelled threads.
- Select hole faces or edges in Fusion and mark them with a size, as a dowel,
  as not threaded, or back to automatic.

A hole shows in a view when one of its openings faces the camera and nothing is
in front of it.

## Agent access (MCP)

An AI agent (e.g. Claude Code) can drive the tool: tick **Agent access (MCP)** in the panel, then
**Copy command** and run it (`claude mcp add --transport http holethreadcallouts http://127.0.0.1:8766/mcp/<token>`).
It listens on this computer only, at a private address (**New address** makes a new one). Off until switched on;
the setting is kept per user in `%APPDATA%\RobotsMadeSimple\HoleThreadCallouts\mcp.json`.

| Tool | What it does |
|------|--------------|
| `list_parts` | Components with bodies, how many times each is used, and their threaded holes by size |
| `get_holes` | A part's threaded / dowel holes: id, size, diameter, where the size came from |
| `list_views` / `add_view` / `delete_view` | A part's views (the same ones the panel shows) |
| `auto_views` | Thread views that between them show every threaded hole, plus a shaded view |
| `mark_holes` | Mark holes with a size, as dowels, as not threaded, or back to automatic |
| `export_image` | The callout image as a PNG (sets up views first if the part has none) |
| `reload_addin` | Reload the add-in's code from disk |

A part is an occurrence path from `list_parts` (e.g. `Servo Base:1`); `bodies` limits it to some of its bodies.
While a tool looks at a part only that part is shown, and everything's visibility is put back afterwards.
The panel stitches the exported image, so it opens if it was closed.

## Install

In Fusion: **Utilities → Scripts and Add-Ins → Add-Ins → +**, choose the
`HoleThreadCallouts` folder, then **Run**. Open the panel with **Hole & Thread Callouts**
in **Utilities → Add-Ins**.

## Layout

```
HoleThreadCallouts.py  entry point + controller (views, rendering, editor, export)
lib/holes.py          finds the threaded / dowel holes, and where they are in a view
lib/callouts.py       sizes, colours, which view calls out which hole, the annotations (pure Python)
lib/clipboard.py      the exported image on the clipboard (PNG + DIB, Windows)
lib/mcp_server.py     agent access: a local MCP server (HTTP on 127.0.0.1, standard library only)
lib/mcp_tools.py      the tools agents can call
palette/              the panel (index.html, app.js) and the callout editor (annotate.*);
                      annot_draw.js draws the annotations for both
tests/                python tests/test_callouts.py
```
