# Browser+

A Fusion add-in that makes joints and relationships easy to find. Fusion keeps
every joint in one long browser folder and the timeline; Browser+ answers the
questions you actually have.

## What it does

- **Tree**: your parts in folders of your own (nested, drag and drop), which
  live only in Browser+ and are saved in the design, so Fusion's components,
  joints and timeline are untouched. Hardware (screws, nuts, washers...) is sorted
  into an automatic Hardware folder by kind and size, with short names like
  "M5x12 SHCS". Show/hide, select or isolate a folder or part; folders flag
  parts nothing holds; double-click a part to see what holds it.
- **Part**: select a part in the canvas or browser and see everything that holds
  it (joints, as-built joints, relationships, rigid groups, motion links), grouped
  by the part on the other end. Joints on a parent assembly are listed separately.
  Click the other part's name to jump to it.
- **All**: every joint and relationship in the design with search (names and
  parts) and filters for kind, type and status, optionally grouped by part pair,
  type or subassembly.
- **Health**: errors and warnings with Fusion's messages, floating parts (not
  grounded and held by nothing), and part pairs joined more than once.
- **Map**: a connection diagram (parts as circles, joints as lines coloured by
  kind, red for errors). Show the neighbourhood of the selected part or the whole
  assembly; click a part to focus it, click a line to highlight that joint; drag,
  pan and zoom.

Each joint row can be highlighted in Fusion (click), zoomed to, opened in
Fusion's own edit dialog, suppressed or unsuppressed, renamed (double-click),
deleted, or rolled to in the timeline ("Roll to end" returns). Clicking a
relationship shows its mated faces in colour, with the parts see-through and,
optionally, everything else hidden.

- **Part mode**: in a one-part design, or with a component activated in an
  assembly, the Tree shows that component's features in timeline order with the
  sketches each one uses nested inside, in folders of your own; rename, suppress,
  roll to, show/hide sketches, delete (one Ctrl+Z to undo). Only Tree, Health
  (features with errors or warnings) and BOM (the bodies) are shown.
- **BOM**: a parts list with quantities (one line per part used), grouped by
  your folders or flat, with part number, description, material, mass on
  request, and columns of your own (vendor, cost, notes...) saved in the design.
  Export to CSV.

## Install

In Fusion: **Utilities → Scripts and Add-Ins → Add-Ins → +**, choose the
`BrowserPlus` folder, then **Run**. Open the panel with the **Browser+**
button in **Utilities → Add-Ins**.

## Layout

```
BrowserPlus.py        entry point + controller (panel actions, selection following)
lib/collect.py        reads joints, as-built joints, relationships, rigid groups, motion links
lib/analysis.py       what holds a part, floating parts, duplicates, map data (pure Python)
lib/actions.py        highlight, zoom, edit, suppress, rename, delete, roll timeline
lib/textselect.py     selects relationships through Fusion's text commands (for Edit)
lib/faces.py          draws a relationship's mated faces (mate view)
lib/layout.py         folders, placement rules and BOM columns, kept in the design (pure Python)
lib/bom.py            the parts list and CSV (pure Python)
lib/hardware.py       short hardware names and kinds (shared with BuildBook)
lib/components.py     per-component facts (part number, material, mass), cached
lib/visibility.py     temporary hiding that restores exactly what it changed
lib/features.py       part mode: the active component's features and the sketches they use
palette/              the panel (app.js + tree.js + features.js + bom.js, with their CSS)
tests/                python -m unittest discover -s tests
```
