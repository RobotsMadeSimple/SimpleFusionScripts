#!/usr/bin/env bash
# Install or update the SimpleFusionScripts add-ins for Autodesk Fusion (macOS).
#
# From Git (default): clones the repository (or updates it with git pull) and links the add-ins
# you pick into Fusion's add-ins folder. Fusion loads them on its next start. Run again to update.
# From zips: ./install.sh --zip BuildBook.zip BrowserPlus.zip   (release zips, no Git needed)
#
#   ./install/install.sh                 # in a clone
#   ./install.sh --dir ~/Tools/SimpleFusionScripts --all
#   ./install.sh --addins "BuildBook BrowserPlus"
set -euo pipefail

REPO="https://github.com/RobotsMadeSimple/SimpleFusionScripts.git"
DIR=""
ALL=0
ADDINS=""
ZIPS=()
FUSION_ADDINS="$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns"

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --dir) DIR="$2"; shift 2 ;;
    --all) ALL=1; shift ;;
    --addins) ADDINS="$2"; shift 2 ;;
    --zip) shift; while [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; do ZIPS+=("$1"); shift; done ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }

remove_existing() {
  # A link from an earlier install is replaced; a real folder is kept as a backup.
  local target="$1"
  if [ -L "$target" ]; then
    rm "$target"
  elif [ -e "$target" ]; then
    local backup="$target.backup-$(date +%Y%m%d-%H%M%S)"
    echo "    $(basename "$target") is already installed as a folder; moving it to $(basename "$backup")"
    mv "$target" "$backup"
  fi
}

addin_folders() {
  # An add-in is a folder holding <name>.py and <name>.manifest.
  local root="$1"
  for d in "$root"/*/; do
    local name; name="$(basename "$d")"
    if [ -f "$d/$name.manifest" ] && [ -f "$d/$name.py" ]; then echo "$name"; fi
  done
}

mkdir -p "$FUSION_ADDINS"

if [ ${#ZIPS[@]} -gt 0 ]; then
  for z in "${ZIPS[@]}"; do
    tmp="$(mktemp -d)"
    step "Unpacking $(basename "$z")"
    unzip -q "$z" -d "$tmp"
    for name in $(addin_folders "$tmp"); do
      remove_existing "$FUSION_ADDINS/$name"
      mv "$tmp/$name" "$FUSION_ADDINS/$name"
      echo "    Installed $name"
    done
    rm -rf "$tmp"
  done
  echo "Done. Restart Fusion (or run the add-ins from Utilities > Scripts and Add-Ins)."
  exit 0
fi

command -v git >/dev/null || { echo "Git isn't installed (xcode-select --install), or use --zip with release zips." >&2; exit 1; }
if [ -z "$DIR" ]; then
  here="$(cd "$(dirname "$0")/.." && pwd)"
  if [ -d "$here/.git" ]; then DIR="$here"; else DIR="$HOME/SimpleFusionScripts"; fi
fi
if [ -d "$DIR/.git" ]; then
  step "Updating $DIR"
  git -C "$DIR" pull --ff-only || echo "    (couldn't update; installing what's there)"
else
  step "Cloning $REPO into $DIR"
  git clone "$REPO" "$DIR" || { echo "git clone failed (the repository is private: sign in with an account that has access)." >&2; exit 1; }
fi

mapfile_compat() { local i=0; while IFS= read -r line; do FOLDERS[i]="$line"; i=$((i+1)); done; }
FOLDERS=()
mapfile_compat < <(addin_folders "$DIR")
[ ${#FOLDERS[@]} -gt 0 ] || { echo "No add-ins found in $DIR." >&2; exit 1; }

CHOSEN=()
if [ "$ALL" = 1 ]; then
  CHOSEN=("${FOLDERS[@]}")
elif [ -n "$ADDINS" ]; then
  for name in $ADDINS; do
    if [ -d "$DIR/$name" ]; then CHOSEN+=("$name"); else echo "    Not found: $name"; fi
  done
else
  echo
  for i in "${!FOLDERS[@]}"; do echo "  [$((i+1))] ${FOLDERS[$i]}"; done
  printf '\nWhich add-ins? Numbers separated by commas, or Enter for all: '
  read -r answer
  if [ -z "$answer" ]; then
    CHOSEN=("${FOLDERS[@]}")
  else
    for n in $(echo "$answer" | tr ',' ' '); do
      if [ "$n" -ge 1 ] 2>/dev/null && [ "$n" -le ${#FOLDERS[@]} ]; then CHOSEN+=("${FOLDERS[$((n-1))]}"); fi
    done
  fi
fi
[ ${#CHOSEN[@]} -gt 0 ] || { echo "Nothing chosen."; exit 0; }

step "Linking into $FUSION_ADDINS"
for name in "${CHOSEN[@]}"; do
  remove_existing "$FUSION_ADDINS/$name"
  ln -s "$DIR/$name" "$FUSION_ADDINS/$name"
  echo "    $name"
done
echo "Done. Restart Fusion: the add-ins load on startup. To update later, run this script again."
