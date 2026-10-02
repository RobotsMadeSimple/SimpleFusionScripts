<#
.SYNOPSIS
  Install or update the SimpleFusionScripts add-ins for Autodesk Fusion (Windows).

.DESCRIPTION
  From Git (default): clones the repository (or updates it with git pull if it's already
  there) and links the add-ins you pick into Fusion's add-ins folder. Fusion loads them on its
  next start (they're set to run on startup). Run it again any time to update.

  From zips (-Zip): installs add-ins from release zips downloaded from GitHub (no Git needed):
  each zip is unpacked into Fusion's add-ins folder, replacing an older copy.

.EXAMPLE
  .\install\install.ps1                      # in a clone: update it, pick add-ins to link
.EXAMPLE
  .\install.ps1 -Dir C:\Tools\SimpleFusionScripts -All
.EXAMPLE
  .\install.ps1 -Zip $HOME\Downloads\BuildBook.zip, $HOME\Downloads\BrowserPlus.zip
#>
[CmdletBinding()]
param(
    [string]$Repo = "https://github.com/RobotsMadeSimple/SimpleFusionScripts.git",
    [string]$Dir,                  # where the clone lives (default: this script's repo, else ~\SimpleFusionScripts)
    [string[]]$AddIns,             # add-in folder names to install (default: ask)
    [switch]$All,                  # install every add-in without asking
    [string[]]$Zip                 # release zips to install instead of using Git
)

$ErrorActionPreference = "Stop"
$fusionAddIns = Join-Path $env:APPDATA "Autodesk\Autodesk Fusion 360\API\AddIns"

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

function Invoke-Git {
    # Git reports progress on stderr, which PowerShell 5 can turn into errors: judge by the exit code.
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $git @args 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
    finally { $ErrorActionPreference = $saved }
    return $LASTEXITCODE
}

function Test-Link($path) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    return $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Remove-Existing($target, $name) {
    # A link from an earlier install is just replaced; a real folder is kept as a backup.
    if (-not (Test-Path -LiteralPath $target)) { return }
    if (Test-Link $target) {
        [IO.Directory]::Delete($target, $false)       # removes the link only, never the files it points to
    } else {
        $backup = "$target.backup-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        Write-Host "    $name is already installed as a folder; moving it to $(Split-Path $backup -Leaf)" -ForegroundColor Yellow
        Rename-Item -LiteralPath $target -NewName (Split-Path $backup -Leaf)
    }
}

function Get-AddInFolders($root) {
    # An add-in is a folder holding <name>.py and <name>.manifest.
    Get-ChildItem -LiteralPath $root -Directory | Where-Object {
        (Test-Path (Join-Path $_.FullName "$($_.Name).manifest")) -and (Test-Path (Join-Path $_.FullName "$($_.Name).py"))
    } | Sort-Object Name
}

function Get-Description($folder) {
    try {
        # "description": { "": "..." } -- PowerShell 5's ConvertFrom-Json can't read an empty key.
        $text = Get-Content -Raw (Join-Path $folder.FullName "$($folder.Name).manifest")
        if ($text -match '"description"\s*:\s*\{\s*""\s*:\s*"([^"]*)"') { return $Matches[1] }
        return ""
    } catch { return "" }
}

function Select-AddIns($folders) {
    if ($All) { return $folders }
    if ($AddIns) {
        $chosen = $folders | Where-Object { $AddIns -contains $_.Name }
        $unknown = $AddIns | Where-Object { ($folders.Name) -notcontains $_ }
        if ($unknown) { Write-Host "    Not found: $($unknown -join ', ')" -ForegroundColor Yellow }
        return $chosen
    }
    Write-Host ""
    for ($i = 0; $i -lt $folders.Count; $i++) {
        $desc = Get-Description $folders[$i]
        Write-Host ("  [{0}] {1}" -f ($i + 1), $folders[$i].Name) -ForegroundColor White -NoNewline
        if ($desc) { Write-Host "  - $desc" -ForegroundColor DarkGray } else { Write-Host "" }
    }
    try { $answer = Read-Host "`nWhich add-ins? Numbers separated by commas, or Enter for all" }
    catch { $answer = ""; Write-Host "    (no prompt available: installing all)" }
    if ([string]::IsNullOrWhiteSpace($answer)) { return $folders }
    $picked = @()
    foreach ($part in $answer -split "[,\s]+") {
        $n = 0
        if ([int]::TryParse($part, [ref]$n) -and $n -ge 1 -and $n -le $folders.Count) { $picked += $folders[$n - 1] }
    }
    return $picked
}

New-Item -ItemType Directory -Force -Path $fusionAddIns | Out-Null

# ---------------------------------------------------------------- from zips
if ($Zip) {
    foreach ($z in $Zip) {
        $zipPath = (Resolve-Path -LiteralPath $z).Path
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ("sfs-" + [guid]::NewGuid().ToString("N"))
        Write-Step "Unpacking $(Split-Path $zipPath -Leaf)"
        Expand-Archive -LiteralPath $zipPath -DestinationPath $tmp
        foreach ($folder in Get-AddInFolders $tmp) {
            $target = Join-Path $fusionAddIns $folder.Name
            Remove-Existing $target $folder.Name
            Move-Item -LiteralPath $folder.FullName -Destination $target
            Write-Host "    Installed $($folder.Name)" -ForegroundColor Green
        }
        Remove-Item -Recurse -Force $tmp
    }
    Write-Host "`nDone. Restart Fusion (or run the add-ins from Utilities > Scripts and Add-Ins)." -ForegroundColor Green
    return
}

# ---------------------------------------------------------------- from Git
# Git: on the PATH, else where Git for Windows installs it.
$git = (Get-Command git -ErrorAction SilentlyContinue).Source
if (-not $git) {
    $git = @("$env:ProgramFiles\Git\cmd\git.exe", "${env:ProgramFiles(x86)}\Git\cmd\git.exe",
             "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $git) {
    throw "Git isn't installed. Install it from https://git-scm.com (or use -Zip with release zips)."
}
if (-not $Dir) {
    # Run from inside a clone (install\install.ps1): use that clone. Piped into iex there's no
    # script folder ($PSScriptRoot is empty): use ~\SimpleFusionScripts.
    if ($PSScriptRoot) {
        $here = Split-Path -Parent $PSScriptRoot
        if (Test-Path (Join-Path $here ".git")) { $Dir = $here }
    }
    if (-not $Dir) { $Dir = Join-Path $HOME "SimpleFusionScripts" }
}
if (Test-Path (Join-Path $Dir ".git")) {
    Write-Step "Updating $Dir"
    if ((Invoke-Git -C $Dir pull --ff-only -q) -ne 0) { Write-Host "    (couldn't update; installing what's there)" -ForegroundColor Yellow }
} else {
    Write-Step "Cloning $Repo into $Dir"
    if ((Invoke-Git clone -q $Repo $Dir) -ne 0) { throw "git clone failed (the repository is private: sign in with an account that has access)." }
}

$folders = @(Get-AddInFolders $Dir)
if (-not $folders) { throw "No add-ins found in $Dir." }
$chosen = @(Select-AddIns $folders)
if (-not $chosen) { Write-Host "Nothing chosen." -ForegroundColor Yellow; return }

Write-Step "Linking into $fusionAddIns"
foreach ($folder in $chosen) {
    $target = Join-Path $fusionAddIns $folder.Name
    Remove-Existing $target $folder.Name
    New-Item -ItemType Junction -Path $target -Target $folder.FullName | Out-Null
    Write-Host "    $($folder.Name)" -ForegroundColor Green
}
Write-Host "`nDone. Restart Fusion: the add-ins load on startup (Utilities > Add-Ins shows their buttons)." -ForegroundColor Green
Write-Host "To update later, run this script again." -ForegroundColor DarkGray
