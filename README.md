# SimpleFusionScripts

Autodesk Fusion add-ins and scripts from Robots Made Simple.

| Add-in | What it does |
|---|---|
| [BuildBook](BuildBook/) | Build-manual exploded views: sections and steps, explode moves with trail lines, per-step camera views, annotations, PNG export and a PDF manual with parts lists. |
| [Browser+](BrowserPlus/) | Find, check and tidy joints and relationships: what holds a part, problems (errors, floating parts, duplicates), a clickable connection map; a part tree with folders, a BOM, and a feature tree in part mode. |
| [Hole & Thread Callouts](HoleThreadCallouts/) | One image showing which holes to tap: colour-coded (colour-blind-safe) thread holes with "2x M3" callouts in one or more views, dowels, and a shaded view, stitched into one PNG. |

## Installing

Fusion loads every add-in in its add-ins folder on startup, so installing is just putting
them there. The installer does it for you and lets you pick which add-ins to install.

**Windows** (PowerShell; needs [Git](https://git-scm.com)):

```powershell
irm https://raw.githubusercontent.com/RobotsMadeSimple/SimpleFusionScripts/main/install/install.ps1 | iex
```

**macOS** (Terminal; needs Git, e.g. `xcode-select --install`):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/RobotsMadeSimple/SimpleFusionScripts/main/install/install.sh)
```

It clones this repository to `~/SimpleFusionScripts` (or updates it if it's already there),
asks which add-ins you want, and links them into Fusion's add-ins folder. Restart Fusion and
they're in **Utilities → Add-Ins**. **To update, run the same command again.**
In a clone you can also run `install/install.ps1` or `install/install.sh` directly
(options: `-All` / `--all`, `-AddIns BuildBook,BrowserPlus` / `--addins "BuildBook BrowserPlus"`).

**Without Git:** download the add-in zips from the [latest release](https://github.com/RobotsMadeSimple/SimpleFusionScripts/releases/latest)
and either run `install.ps1 -Zip BuildBook.zip,BrowserPlus.zip` (or `install.sh --zip ...`), or unzip
them into Fusion's add-ins folder yourself:

- Windows: `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns`
- macOS: `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns`

Manually, from Fusion: **Utilities → Scripts and Add-Ins → Add-Ins → +**, choose an add-in's
folder (e.g. `BuildBook/`), then **Run** (tick **Run on Startup**).

New releases (one zip per add-in) are built by GitHub Actions when a version tag is pushed:
`git tag v1.1 && git push origin v1.1`.

Each add-in's folder has its own README.

## License

[MIT](LICENSE)
