# BuildBook

A Fusion add-in for making build-manual exploded views without Fusion's Animation
workspace. A docked panel organises the manual into **sections → steps**, each step
explodes its parts with **explode moves** and dashed **trail lines**, and every step
exports as a PNG from its own saved camera view.

The model never moves: exploded parts are drawn on top of the design as custom
graphics, and the real parts are hidden only while a step is shown. The manual is
stored inside the design (as an attribute), so it travels with the `.f3d`.

## Features

- **Sections and steps**: drag to reorder; per-step context display for earlier and
  later parts (shown, ghosted or hidden); preparation steps.
- **Explode moves**: an ordered list per step. Each move moves a group of parts along
  an axis, X/Y/Z amounts, or a picked edge/face/axis. Moves chain, so a part can slide
  out and then drop. Stacked spacing, per-part distances and trail lines, and "show
  the step up to this move".
- **Parts**: pick parts on the full model (click to pick, hover + H to hide), short
  hardware names (`M3x12 SHCS`), a line start point per part (e.g. a hole).
- **Trail lines**: dashed lines from each part's home to where it's exploded; click to
  hide individual lines.
- **Images**: saved camera per step, fixed crop ratio, export one or all steps as
  `Section N - Step N - Name.png`, thumbnails and progress in the step list.

## Install

1. Copy or clone this folder.
2. In Fusion: **Utilities → Scripts and Add-Ins → Add-Ins → +**, choose the
   `BuildBook` folder, then **Run**.
3. Click **BuildBook** in the **Utilities → Add-Ins** panel to open the panel.

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
palette/                  the panel (HTML/CSS/JS)
tests/                    offline tests for the pure-Python modules
```

## Tests

The pure-Python modules run without Fusion:

```
python tests/test_core.py
python tests/test_crop.py
python tests/test_hardware.py
```

## Runtime files

The add-in writes `logs/`, `thumbs/` and `cache/` next to itself. They're
machine-local, can contain your design's part names and pictures, and are
git-ignored.
