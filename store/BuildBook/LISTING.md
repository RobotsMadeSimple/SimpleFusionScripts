# BuildBook — Autodesk App Store listing

Everything to paste into the publisher portal (apps.autodesk.com → Publisher Corner),
plus what still needs doing before submitting. Price: **Free**.

## Basics

| Field | Value |
|---|---|
| App name | BuildBook |
| Publisher | Robots Made Simple |
| Product | Autodesk Fusion |
| Operating systems | Windows 64-bit, macOS |
| Version | 1.0.0 (from `BuildBook/BuildBook.manifest`) |
| Price | Free |
| Category | Documentation / Productivity (pick the closest the portal offers) |
| Languages | English |
| Website / docs | https://github.com/RobotsMadeSimple/SimpleFusionScripts/tree/main/BuildBook#readme |
| Support | GitHub issues: https://github.com/RobotsMadeSimple/SimpleFusionScripts/issues (+ a support email: fill in) |
| Icon | `icon-512.png` (also `icon-120.png`, `icon-1024.png`) |
| Package | `dist/BuildBook-1.0.0.bundle.zip` (`python store/build_bundle.py BuildBook`) |

## Short description (one line)

Make step-by-step build manuals from your Fusion assembly: exploded views, trail lines,
annotated pictures, parts lists and a PDF manual.

## Description

BuildBook turns a Fusion assembly into a step-by-step build manual, without the
Animation workspace and without moving your model.

Organise the build into sections and steps, add each step's parts, and explode them
with simple moves along an axis, an edge or a face. Dashed trail lines show where every
part goes. Save a camera view for each step, draw arrows, notes and numbered callouts
on its picture, and export everything: a PNG per step, or a complete PDF manual with a
cover, contents, section pages and parts lists.

Your model never moves: exploded parts are drawn on top of the design, and the manual
is saved inside the design file, so it travels with it and updates as the design
changes.

**Features**

- Sections and steps you can reorder by dragging, with repeat counts and preparation
  steps for sub-assemblies
- Part pictures, assembly trees, and picking a whole assembly, a sub-assembly or a
  single part with a click
- Explode moves that chain (slide out, then drop), with stacked spacing and per-part
  distances
- Trail lines with your own colour, weight and style
- Earlier and later steps shown, ghosted or hidden in each step's view
- A saved view per step, a fixed crop ratio with an on-screen crop frame
- Annotations: arrows, lines, boxes, circles, text with leaders, numbered callouts
- Parts list per step and section (hardware listed separately, short names like
  "M3x12 SHCS")
- Export one or all steps as PNG, or a full PDF manual
- Works offline; nothing leaves your computer

**Getting started**

Open an assembly and click BuildBook (Utilities → Add-Ins). In the Steps tab add a
section and a step, pick the step's parts, add an explode move, save the view, then
export from the Export tab. Click "Assembled" before editing the model.

## Release notes — 1.0.0

First public release.

## Privacy policy

BuildBook runs entirely on your computer. It does not collect, store or send any
personal data, has no accounts, analytics or tracking, and makes no network
connections. The manual is stored inside your Fusion design. A log, a cache of
hardware names and step thumbnails are kept in a folder on your computer
(Windows: %APPDATA%\RobotsMadeSimple\BuildBook, macOS:
~/Library/Application Support/RobotsMadeSimple/BuildBook) and can be deleted at any
time.

## Screenshots (take in Fusion, 1920×1080 or larger)

1. **Hero**: an exploded step with trail lines in the canvas, the BuildBook panel docked
   under the browser on the Step tab. Caption: "Exploded views with trail lines,
   without moving your model".
2. **Steps tab**: a few sections with step thumbnails and section pictures. Caption:
   "Organise the build into sections and steps".
3. **Explode moves**: the Explode Move dialog with the drag arrow, parts highlighted.
   Caption: "Explode parts along an axis, edge or face".
4. **Annotate**: the annotation editor with arrows, a text leader and numbered callouts.
   Caption: "Annotate each step's picture".
5. **PDF**: two pages of an exported manual side by side (a section page and a step
   page). Caption: "Export a complete PDF manual with parts lists".
6. **Parts**: the Parts tab with part pictures and an assembly opened. Caption: "Add
   whole assemblies or individual parts".

A 30–60 s video (optional, recommended): add a step, pick parts, explode, save the
view, export the PDF.

## Before submitting (checklist)

- [ ] Fill in a support email in `store/BuildBook/bundle.json` and above (not a
      personal address you don't want public)
- [ ] Check `PackageContents.xml` against Autodesk's current Fusion packaging guide:
      in particular whether the add-in files go straight in `Contents/` (as built) or
      in a named sub-folder; adjust `store/build_bundle.py` if needed
- [ ] Install the bundle locally (copy `dist/BuildBook.bundle` to
      `%APPDATA%\Autodesk\ApplicationPlugins\`, restart Fusion) with the GitHub copy
      stopped, and run through the Getting started steps
- [ ] Test on a Mac: open panel, pick parts, explode, save view, annotate, export PNG
      and PDF, "Open folder"
- [ ] Take the screenshots above (and the video)
- [ ] Register as a publisher at apps.autodesk.com, create the app, paste this listing,
      upload the zip and icons, submit for review
- [ ] After approval: link the store page from the repository README
