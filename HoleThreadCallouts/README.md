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
palette/              the panel (index.html, app.js) and the callout editor (annotate.*);
                      annot_draw.js draws the annotations for both
tests/                python tests/test_callouts.py
```
