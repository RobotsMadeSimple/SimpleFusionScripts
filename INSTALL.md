# Installing the add-ins

A step-by-step guide for **BuildBook**, **Browser+** and **Hole & Thread Callouts**. No
programming tools are needed: no Git, no PowerShell, no Terminal.

- [What you need](#what-you-need)
- [Windows: the installer (easiest)](#windows-the-installer-easiest)
- [Mac: copy the add-ins into Fusion](#mac-copy-the-add-ins-into-fusion)
- [Windows without the installer](#windows-without-the-installer)
- [Finding the add-ins in Fusion](#finding-the-add-ins-in-fusion)
- [Updating](#updating)
- [Uninstalling](#uninstalling)
- [Troubleshooting](#troubleshooting)
- [For developers: install with Git](#for-developers-install-with-git)

---

## What you need

- **Autodesk Fusion**, installed and signed in (any recent version, on Windows 10 / 11 or macOS).
- An internet connection to download the add-ins.
- About 5 minutes.

Everything is free (MIT licence). The add-ins only add to Fusion: they never change your
designs' geometry.

---

## Windows: the installer (easiest)

### 1. Download

1. Open the **[latest release page](https://github.com/RobotsMadeSimple/SimpleFusionScripts/releases/latest)**.
2. Under **Assets**, click **`SimpleFusionScripts-Setup-<version>.exe`**. It downloads to your
   **Downloads** folder.

### 2. Run it

1. Double-click the downloaded file.
2. Windows may show a blue box, **"Windows protected your PC"**. That's normal for small free
   tools without a paid code-signing certificate. Click **More info**, then **Run anyway**.
3. No administrator password is needed: it installs for your Windows account only.

### 3. Pick the add-ins

1. Accept the licence (MIT: free to use) and click **Next**.
2. Choose **All add-ins**, or **Choose add-ins** and tick the ones you want:
   - **BuildBook**: build manuals with exploded views, pictures, parts lists, PDFs and videos
   - **Browser+**: find and tidy joints, a part tree with folders, a BOM
   - **Hole & Thread Callouts**: one picture showing which holes to tap
3. Click **Next**, then **Install**. If Fusion is open, the installer reminds you to restart it.
4. Click **Finish**.

### 4. Restart Fusion

Close Fusion completely (**File → Exit**, or the X on its window), then open it again. The
add-ins start by themselves. See [Finding the add-ins in Fusion](#finding-the-add-ins-in-fusion).

---

## Mac: copy the add-ins into Fusion

There's no Mac installer yet; installing is copying a folder.

### 1. Download

1. Open the **[latest release page](https://github.com/RobotsMadeSimple/SimpleFusionScripts/releases/latest)**.
2. Under **Assets**, click the zip of each add-in you want: **`BuildBook.zip`**,
   **`BrowserPlus.zip`**, **`HoleThreadCallouts.zip`**.
3. Open your **Downloads** folder and double-click each zip. Each one becomes a folder
   (e.g. **BuildBook**).

### 2. Open Fusion's add-ins folder

1. In **Finder**, choose **Go → Go to Folder…** from the menu bar (or press **⇧⌘G**).
2. Paste this and press **Return**:

   ```
   ~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns
   ```

   (If it says the folder can't be found, open Fusion once, then try again.)

### 3. Copy the add-ins

Drag each unzipped add-in folder (**BuildBook**, **BrowserPlus**, **HoleThreadCallouts**) into
that **AddIns** window. If macOS asks to replace an older one, choose **Replace**.

> The folder name must stay exactly as it is (e.g. `BuildBook`, not `BuildBook 2` or
> `BuildBook-main`), and it must contain `BuildBook.py` directly inside it.

### 4. Restart Fusion

Quit Fusion (**⌘Q**) and open it again.

---

## Windows without the installer

The same as the Mac steps, with Windows' folder:

1. Download the add-in zips from the **[latest release](https://github.com/RobotsMadeSimple/SimpleFusionScripts/releases/latest)**.
2. Right-click each zip → **Extract All…** → **Extract**.
3. Press **Windows + R**, paste this, press **Enter**:

   ```
   %APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns
   ```

4. Copy the extracted add-in folders (e.g. **BuildBook**) into that window. Make sure you copy the
   folder that has `BuildBook.py` directly inside it, not a folder around it.
5. Restart Fusion.

---

## Finding the add-ins in Fusion

All three add buttons to the **UTILITIES** tab, in the **ADD-INS** panel (in the Design
workspace):

| Add-in | Button | Opens |
|---|---|---|
| BuildBook | **BuildBook** | A panel docked under the Browser: sections, steps, parts, export |
| Browser+ | **Browser+** | A panel with the joints finder, part tree and BOM |
| Hole & Thread Callouts | **Hole & Thread Callouts** | A panel to make the callout picture |

Click a button once to open its panel. The panel remembers where you put it.

**To check they're running:** **UTILITIES → ADD-INS → Scripts and Add-Ins** (or press
**Shift + S**), then the **Add-Ins** tab. Each one should show as running, with
**Run on Startup** ticked. If one isn't, select it and click **Run**.

---

## Updating

- **Windows installer:** download the newer `SimpleFusionScripts-Setup-<version>.exe` and run
  it. It replaces the old versions; your manuals and settings are kept.
- **Copied folders (Mac or Windows):** download the new zips and copy the folders over the old
  ones (choose **Replace**).
- Then restart Fusion. (BuildBook can also reload itself without a restart: **Settings tab →
  Reload BuildBook**.)

Your build manuals live inside your Fusion designs, so updating never touches them.

---

## Uninstalling

- **Windows installer:** **Start → Settings → Apps → Installed apps**, find **Robots Made Simple
  Fusion Add-ins**, click **⋯ → Uninstall**.
- **Copied folders:** delete the add-in folders from Fusion's add-ins folder (paths above).
- Restart Fusion.

BuildBook keeps some files of its own (logs, picture previews, your defaults) in
`%APPDATA%\RobotsMadeSimple` on Windows or `~/Library/Application Support/RobotsMadeSimple` on a
Mac. Uninstalling leaves them, in case you reinstall; delete that folder to remove them too.
Your manuals stay in your designs either way.

---

## Troubleshooting

**"Windows protected your PC" when running the installer.**
Click **More info → Run anyway**. The installer isn't signed with a paid certificate; the
source code is all on GitHub.

**There's no button after restarting Fusion.**
Open **Scripts and Add-Ins** (**Shift + S**) → **Add-Ins** tab.
- Listed but stopped: select it, click **Run**, and tick **Run on Startup**.
- Not listed: the folder is in the wrong place or has the wrong name. Check that the add-ins
  folder (paths above) contains e.g. `BuildBook\BuildBook.py`, not
  `BuildBook-main\BuildBook\BuildBook.py`.

**The installer says Fusion is open.**
You can carry on; the new versions start the next time Fusion opens. Or close Fusion first.

**An add-in reports "another copy of this add-in is already running".**
It's installed twice, e.g. once by the installer and once by hand or with Git. Keep one: open
**Scripts and Add-Ins**, stop and remove the extra entry, or delete the extra folder.

**A panel opens blank or grey.**
Close the panel and click its button again. If it stays blank, restart Fusion.

**Something else.**
Each add-in writes a log. BuildBook's is in `%APPDATA%\RobotsMadeSimple\BuildBook\logs`
(Windows) or `~/Library/Application Support/RobotsMadeSimple/BuildBook/logs` (Mac). Open an
issue on [GitHub](https://github.com/RobotsMadeSimple/SimpleFusionScripts/issues) with what
happened and the end of that log.

---

## For developers: install with Git

If you have Git and want to follow the latest changes, the scripts in [`install/`](install/)
clone the repository and link the add-ins into Fusion (run again to update). See the
[README](README.md#installing).
