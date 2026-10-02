"""Package an add-in as an Autodesk App Store bundle.

    python store/build_bundle.py BuildBook

Makes dist/BuildBook.bundle/ (and dist/BuildBook-<version>.bundle.zip to upload):

    BuildBook.bundle/
      PackageContents.xml        what Fusion / the store reads (name, version, OS, entry point)
      Contents/                  the add-in itself: BuildBook.py, .manifest, lib/, palette/, ...

Development-only and per-machine files are left out (tests, caches, logs, thumbnails,
__pycache__). Details for each add-in (company, links, package ids) are in
store/<name>/bundle.json; the version comes from the add-in's .manifest, so bump it there.

Check PackageContents.xml against Autodesk's current packaging guide before uploading.
"""

import json
import os
import shutil
import sys
import uuid
import zipfile
from xml.sax.saxutils import quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKIP_DIRS = {"tests", "__pycache__", "logs", "cache", "thumbs", ".git", ".vscode", ".idea"}
SKIP_EXT = {".pyc", ".pyo", ".log"}


def package_contents(name, manifest, info):
    version = manifest["version"]
    upgrade = info["upgradeCode"]
    product = "{" + str(uuid.uuid5(uuid.UUID(upgrade.strip("{}")), version)).upper() + "}"
    desc = info.get("description") or manifest["description"][""]
    os_list = "Win64|Mac" if "mac" in manifest.get("supportedOS", "") else "Win64"
    a = quoteattr
    return """<?xml version="1.0" encoding="utf-8"?>
<ApplicationPackage SchemaVersion="1.0"
    AutodeskProduct="Fusion360"
    ProductType="Application"
    Name={name}
    Description={desc}
    AppVersion={ver}
    FriendlyVersion={ver}
    ProductCode={product}
    UpgradeCode={upgrade}
    Author={author}
    AppNameSpace={ns}
    OnlineDocumentation={docs}
    HelpFile="">
  <CompanyDetails Name={company} Url={url} Email={email} />
  <RuntimeRequirements OS={os} Platform="Fusion360" />
  <Components Description={name}>
    <RuntimeRequirements OS={os} Platform="Fusion360" />
    <ComponentEntry AppName={name} Version={ver} ModuleName="./Contents/" AppDescription={desc}
        LoadOnStartup="True" AppType="ScriptAddin" />
  </Components>
</ApplicationPackage>
""".format(name=a(info.get("displayName", name)), desc=a(desc), ver=a(version), product=a(product),
           upgrade=a(upgrade), author=a(info["company"]), ns=a(info["namespace"]), docs=a(info["documentation"]),
           company=a(info["company"]), url=a(info["url"]), email=a(info.get("email", "")), os=a(os_list))


def build(name):
    src = os.path.join(ROOT, name)
    manifest_path = os.path.join(src, name + ".manifest")
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    with open(os.path.join(HERE, name, "bundle.json"), encoding="utf-8") as handle:
        info = json.load(handle)
    dist = os.path.join(ROOT, "dist")
    bundle = os.path.join(dist, name + ".bundle")
    if os.path.isdir(bundle):
        shutil.rmtree(bundle)
    contents = os.path.join(bundle, "Contents")
    files = 0
    for folder, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(folder, src)
        for n in names:
            if os.path.splitext(n)[1].lower() in SKIP_EXT:
                continue
            target = os.path.normpath(os.path.join(contents, rel, n))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(os.path.join(folder, n), target)
            files += 1
    with open(os.path.join(bundle, "PackageContents.xml"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(package_contents(name, manifest, info))
    zip_path = os.path.join(dist, "{}-{}.bundle.zip".format(name, manifest["version"]))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, _, names in os.walk(bundle):
            for n in names:
                full = os.path.join(folder, n)
                z.write(full, os.path.relpath(full, dist))
    print("{} {}: {} files -> {}".format(name, manifest["version"], files, os.path.relpath(zip_path, ROOT)))
    return zip_path


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "BuildBook")
