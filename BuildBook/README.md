# BuildBook

A Fusion add-in for making build-manual exploded views without Fusion's Animation
workspace. A docked panel organises the manual into **sections → steps**, each step
explodes its parts with **explode moves** and dashed **trail lines**, and every step
exports as a PNG from its own saved camera view.

The model never moves: exploded parts are drawn on top of the design as custom
graphics, and the real parts are hidden only while a step is shown. The manual is
stored inside the design (as an attribute), so it travels with the `.f3d`.

## Quick start

1. Open an assembly and click **BuildBook** (Utilities → Add-Ins). The panel opens under
   the browser.
2. **Steps** tab: click **+ Add section** (e.g. "Frame"), then **+ Add step** under it.
3. **Step** tab → **Parts**: **Pick** parts on the model (click picks a whole assembly,
   a sub-assembly or a single part; hover + H hides a part to reach behind it), or tick
   parts in the **Parts** tab and **Add to step**.
4. **Explode moves** → **+ Move**: choose a direction and drag the arrow. Trail lines
   are drawn from where each part sits to where it's exploded.
5. **View & image** → **Save view** at a good angle, then **Annotate** if you want
   arrows, notes or numbered callouts.
6. **Export** tab: choose the folder, then **Export all** (one PNG per step) or
   **Export PDF** (the whole manual with parts lists).

Click **Assembled** (top right) before editing the model: the step view only draws
over it.

## Features

- **Sections and steps**: drag to reorder (also between sections); per-step display of
  earlier and later parts (shown, ghosted or hidden); repeat counts ("do this 4 times");
  preparation steps for sub-assemblies built separately.
- **Parts**: part pictures in every list; assemblies listed as a tree you can add whole
  or part by part; split a multi-body part into separate parts; short hardware names
  (`M3x12 SHCS`); mark parts that shouldn't count in the parts lists.
- **Explode moves**: an ordered list per step, along an axis, X/Y/Z amounts or a picked
  edge / face / axis. Moves chain (slide out, then drop), with stacked spacing,
  per-part distances, trail lines and "show the step up to this move".
- **Trail lines**: dashed lines from each part's home to where it's exploded; colour,
  weight and style in Settings; click lines to hide them; a custom start point per part.
- **Images**: a saved camera per step, a fixed crop ratio (4:3 by default) with an
  on-screen crop frame and centre ticks, PNG export of one or all steps.
- **Annotations**: arrows, lines, boxes, ellipses, text (with leader arrows that stay
  put while you move the text) and numbered callouts, drawn in a separate editor
  window and onto exported pictures.
- **PDF manual**: a cover with a picture of the whole assembly and contents, each
  section's page with its own picture and parts list, every step (number, name,
  notes, picture, parts) and the full parts list, with hardware listed separately.

## Install

- **Autodesk App Store**: search for BuildBook (when it's listed).
- **Installer**: see the [repository README](../README.md) for the one-line install
  on Windows and Mac.
- **By hand**: copy this folder, then in Fusion **Utilities → Scripts and Add-Ins →
  Add-Ins → +**, choose the `BuildBook` folder and **Run**.

## Privacy

BuildBook works entirely on your computer. It sends nothing anywhere: no accounts,
no analytics, no network access. The manual is stored inside your design.

## Layout

```
BuildBook.py              entry point + controller (panel actions, document events)
commands/explode_cmd.py   Explode Move editor
commands/pick_cmd.py      Pick Parts
commands/lines_cmd.py     Edit Trail Lines
commands/anchor_cmd.py    Line Start (where a part's trail lines begin)
lib/model.py              manual data model (pure Python)
lib/explode.py            explode vector maths (pure Python)
lib/crop.py               crop ratios + PNG cropping (pure Python)
lib/hardware.py           short hardware names, natural sort (pure Python)
lib/scene.py              step renderer: drawn copies, ghosts, trail lines, visibility
lib/capture.py            cameras, PNG export, thumbnails
lib/overlay.py            optional on-screen crop frame
lib/refs.py               part references and lookups
lib/pictures.py           cover / section / step pictures and the annotation editor window
lib/pdf.py                small PDF writer (pure Python)
lib/manual_pdf.py         the manual's PDF layout (pure Python)
palette/                  the panel (HTML/CSS/JS); annotate.* = the annotation editor,
                          annot_draw.js = the annotation renderer (editor + PNG exports)
tests/                    offline tests for the pure-Python modules
```

## Tests

The pure-Python modules run without Fusion:

```
python tests/test_core.py
python tests/test_crop.py
python tests/test_hardware.py
python tests/test_pdf.py [sample.pdf]
```

## Runtime files

BuildBook keeps a log, a hardware-name cache and step thumbnails in a per-user
folder (not in the add-in folder, which an update may replace):

- Windows: `%APPDATA%\RobotsMadeSimple\BuildBook`
- macOS: `~/Library/Application Support/RobotsMadeSimple/BuildBook`

They can contain your design's part names and pictures and never leave your computer.
Deleting the folder is safe (thumbnails can be taken again from Settings).

## Packaging for the App Store

`python store/build_bundle.py BuildBook` (from the repository root) builds
`dist/BuildBook.bundle` and an upload-ready zip; see `store/BuildBook/` for the listing.
