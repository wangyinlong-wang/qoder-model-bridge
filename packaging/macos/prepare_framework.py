"""Bundle the official universal2 Python runtime and relocate its Mach-O libraries."""
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess

BUILD = Path(__file__).resolve().parent
SOURCE = BUILD / "python-pkg/Python_Framework.pkg/Payload/Versions/3.13"
APP = BUILD / "Qoder 内网模型接入.app"
CONTENTS = APP / "Contents"
FRAMEWORKS = CONTENTS / "Frameworks"
PYTHON = FRAMEWORKS / "Python.framework/Versions/3.13"
RUNTIME = CONTENTS / "Resources/python"
assert not APP.exists(), "The app already exists; preserve it before making a new build."

for name, version, origin in [("Python", "3.13", SOURCE),
                              ("Tcl", "8.6", SOURCE / "Frameworks/Tcl.framework/Versions/8.6"),
                              ("Tk", "8.6", SOURCE / "Frameworks/Tk.framework/Versions/8.6")]:
    framework = FRAMEWORKS / (name + ".framework")
    target = framework / "Versions" / version
    target.mkdir(parents=True)
    shutil.copy2(origin / name, target / name)
    shutil.copytree(origin / "Resources", target / "Resources", symlinks=True)
    (framework / "Versions/Current").symlink_to(version)
    (framework / name).symlink_to("Versions/Current/" + name)
    (framework / "Resources").symlink_to("Versions/Current/Resources")

(RUNTIME / "lib").mkdir(parents=True)
shutil.copytree(SOURCE / "lib/python3.13", RUNTIME / "lib/python3.13", symlinks=True,
                ignore=shutil.ignore_patterns("*.a", "*.o", "__pycache__"))
library_link = RUNTIME / "lib/python3.13/config-3.13-darwin/libpython3.13.dylib"
assert library_link.is_symlink()
library_link.unlink()
library_link.symlink_to(os.path.relpath(PYTHON / "Python", library_link.parent))
for path in (SOURCE / "lib").glob("*.dylib"):
    if path.name == "libpython3.13.dylib":
        continue
    target = RUNTIME / "lib" / path.name
    if path.is_symlink():
        target.symlink_to(os.readlink(path))
    else:
        shutil.copy2(path, target)
(RUNTIME / "bin").mkdir()
shutil.copy2(SOURCE / "bin/python3.13", RUNTIME / "bin/python3.13")
(RUNTIME / "bin/python3").symlink_to("python3.13")
(CONTENTS / "MacOS").mkdir()
with (CONTENTS / "Info.plist").open("wb") as output:
    plistlib.dump({"CFBundleExecutable": "QoderInternalProxy", "CFBundleIdentifier": "local.qoder.internalproxy",
                  "CFBundleName": "Qoder 内网模型接入", "CFBundleDisplayName": "Qoder 内网模型接入",
                  "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "1.0.0", "CFBundleVersion": "1.0.0",
                  "LSMinimumSystemVersion": "12.0", "NSHighResolutionCapable": True,
                  "NSPrincipalClass": "NSApplication"}, output)

magic = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
         b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
original = "/Library/Frameworks/Python.framework/Versions/3.13/"

def bundled_path(dependency):
    relative = dependency[len(original):]
    if relative.startswith("Frameworks/"):
        return FRAMEWORKS / relative[len("Frameworks/"):]
    if relative == "Python" or relative.startswith("Resources/"):
        return PYTHON / relative
    if relative == "lib/libpython3.13.dylib":
        return PYTHON / "Python"
    return RUNTIME / relative

relocated = []
machos = []
for path in CONTENTS.rglob("*"):
    if not path.is_file() or path.is_symlink():
        continue
    with path.open("rb") as source:
        if source.read(4) not in magic:
            continue
    machos.append(path)
    output = subprocess.check_output(["/usr/bin/otool", "-L", str(path)], text=True)
    dependencies = set(re.findall(r"^\s+(.+?) \(compatibility version", output, re.MULTILINE))
    identifiers = subprocess.check_output(["/usr/bin/otool", "-D", str(path)], text=True)
    ids = {line for line in identifiers.splitlines() if line.startswith(original)}
    command = ["/usr/bin/install_name_tool"]
    changes = []
    for dependency in sorted(dependencies - ids):
        if dependency.startswith(original):
            target = bundled_path(dependency)
            assert target.exists(), dependency
            replacement = "@loader_path/" + os.path.relpath(target.resolve(), path.parent)
            command += ["-change", dependency, replacement]
            changes.append([dependency, replacement])
        else:
            assert dependency.startswith(("/usr/lib/", "/System/Library/", "@")), dependency
    if ids:
        assert len(ids) == 1
        target = bundled_path(next(iter(ids))).resolve()
        if target.is_relative_to(FRAMEWORKS):
            identifier = "@rpath/" + target.relative_to(FRAMEWORKS).as_posix()
        else:
            identifier = "@loader_path/" + target.name
        command += ["-id", identifier]
    if len(command) > 1:
        result = subprocess.run(command + [str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    relocated.append({"file": str(path.relative_to(CONTENTS)), "changes": changes})

for path in machos:
    result = subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
(BUILD / "framework-relocation.json").write_text(json.dumps(relocated, ensure_ascii=False, indent=2))
print(json.dumps({"application": str(APP), "runtime_machos": len(machos)}, ensure_ascii=False))
