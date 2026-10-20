"""Assemble a public DMG from a prepared, audited runtime and public sources.

The runtime input must already contain relocated universal2 Python/Tcl/Tk and
both dependency trees. This script does not download or prepare those inputs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNTIME_TREES = ("Contents/Frameworks", "Contents/Resources/python", "Contents/Resources/dependencies")
APP_FILES = ("app_entry.py", "launcher.py", "qoder_to_internal.py", "idea_bridge.py",
             "qoder_clients.py", "qoder_control.py", "qoder_protocol.py", "client_api.js",
             "client_relay.cjs", "config.example.json", "requirements.txt")


def inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = {"link": os.readlink(path)}
        elif path.is_file():
            result[relative] = {"bytes": path.stat().st_size,
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--runtime-app", type=Path, required=True, help="clean prepared runtime .app; application code is ignored")
parser.add_argument("--python-headers", type=Path, required=True, help="matching official Python 3.13 Headers directory")
parser.add_argument("--licenses", type=Path, required=True, help="complete macOS third-party license directory")
parser.add_argument("--bridge", type=Path, required=True, help="public-source IDEA bridge ZIP")
parser.add_argument("--build-dir", type=Path, required=True, help="new isolated directory; must not exist")
parser.add_argument("--release-version", required=True, help="asset version, for example 1.1.4-beta.1")
args = parser.parse_args()
for name in ("runtime_app", "python_headers", "licenses", "bridge", "build_dir"):
    setattr(args, name, getattr(args, name).resolve())
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", args.release_version):
    parser.error("Invalid release version")
if args.build_dir.exists():
    parser.error("Build directory already exists; preserve the previous result")
if not (args.python_headers / "Python.h").is_file() or not args.bridge.is_file():
    parser.error("Matching Python headers and bridge ZIP are required")
if not args.licenses.is_dir() or not any(args.licenses.iterdir()):
    parser.error("Complete third-party licenses are required before signing")
for relative in RUNTIME_TREES:
    if not (args.runtime_app / relative).is_dir():
        parser.error("Missing runtime tree: " + relative)
for source in (args.runtime_app, args.python_headers, args.licenses, args.bridge):
    if args.build_dir.is_relative_to(source):
        parser.error("Build directory must be outside all input trees")

args.build_dir.mkdir(parents=True)
reports = args.build_dir / "reports"
reports.mkdir()
commands = []


def run(command):
    process = subprocess.run([str(value) for value in command], capture_output=True, text=True)
    commands.append({"args": [str(value) for value in command], "exit": process.returncode,
                     "stdout": process.stdout, "stderr": process.stderr})
    (reports / "commands.json").write_text(json.dumps(commands, ensure_ascii=False, indent=2) + "\n")
    if process.returncode:
        raise RuntimeError(f"{command[0]} exited {process.returncode}; see reports/commands.json")


metadata = plistlib.loads((HERE / "Info.plist").read_bytes())
if metadata["CFBundleIdentifier"] != "local.qoder.internalproxy" or metadata["CFBundleExecutable"] != "QoderInternalProxy":
    raise ValueError("Unexpected application identity")
if args.release_version.split("-", 1)[0] != metadata["CFBundleShortVersionString"]:
    raise ValueError("Release version differs from Info.plist")
stage = args.build_dir / "stage"
app = stage / (metadata["CFBundleName"] + ".app")
contents = app / "Contents"
runtime_manifests = {}
for relative in RUNTIME_TREES:
    source = args.runtime_app / relative
    target = app / relative
    runtime_manifests[relative] = inventory(source)
    shutil.copytree(source, target, symlinks=True)
    if inventory(target) != runtime_manifests[relative]:
        raise ValueError("Runtime copy mismatch: " + relative)

# Inputs must be clean build products rather than a user's running installation.
for path in app.rglob("*"):
    if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"} or path.name == "direct_url.json":
        raise ValueError("Runtime contains installation/cache metadata: " + str(path.relative_to(app)))
    if path.is_symlink() and (not path.exists() or not path.resolve().is_relative_to(app)):
        raise ValueError("Runtime contains an external or broken symlink")

application = contents / "Resources/app"
application.mkdir(parents=True)
source_hashes = {}
for name in APP_FILES:
    source = ROOT / "app" / name
    shutil.copy2(source, application / name)
    source_hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
example = json.loads((application / "config.example.json").read_text())
if example.get("base_url") != "http://127.0.0.1:3000/v1":
    raise ValueError("Public example must use the documented localhost address")

resources = contents / "Resources"
shutil.copytree(args.licenses, resources / "ThirdPartyLicenses")
shutil.copy2(ROOT / "LICENSE", resources / "LICENSE")
shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", resources / "THIRD_PARTY_NOTICES.md")
bridge_dir = resources / "idea-bridge"
bridge_dir.mkdir()
shutil.copy2(args.bridge, bridge_dir / args.bridge.name)
documentation = stage / "Documentation"
documentation.mkdir()
for name in ("README.md", "README.en.md", "BUILD.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
    shutil.copy2(ROOT / name, documentation / name)
shutil.copytree(ROOT / "docs", documentation / "docs")
document_hashes = inventory(documentation)
extra = stage / "IDEA Bridge"
extra.mkdir()
shutil.copy2(args.bridge, extra / args.bridge.name)
(stage / "Applications").symlink_to("/Applications")
with (contents / "Info.plist").open("wb") as stream:
    plistlib.dump(metadata, stream)
executable = contents / "MacOS" / metadata["CFBundleExecutable"]
executable.parent.mkdir()
run(["/usr/bin/xcrun", "clang", "-fobjc-arc", "-arch", "arm64", "-arch", "x86_64",
     "-mmacosx-version-min=12.0", "-I", args.python_headers, "-F", contents / "Frameworks",
     "-framework", "Cocoa", "-framework", "Python", "-Wl,-rpath,@executable_path/../Frameworks",
     HERE / "main.m", "-o", executable])
run([sys.executable, "-B", HERE / "audit_and_sign.py", app, "--report-dir", reports])
for name, digest in source_hashes.items():
    if hashlib.sha256((ROOT / "app" / name).read_bytes()).hexdigest() != digest:
        raise ValueError("Public source changed during build: " + name)
for name, item in document_hashes.items():
    if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != item["sha256"]:
        raise ValueError("Public documentation changed during build: " + name)
if inventory(args.licenses) != inventory(resources / "ThirdPartyLicenses"):
    raise ValueError("License input changed during build")
artifacts = args.build_dir / "artifacts"
artifacts.mkdir()
dmg = artifacts / f"Qoder-Model-Bridge-{args.release_version}-macos-universal.dmg"
run(["/usr/bin/hdiutil", "create", "-fs", "HFS+", "-format", "UDZO", "-volname",
     "Qoder Model Bridge " + args.release_version, "-srcfolder", stage, dmg])
run(["/usr/bin/hdiutil", "verify", dmg])
report = {"release_version": args.release_version, "bundle_version": metadata["CFBundleVersion"],
          "app": str(app), "dmg": str(dmg), "bytes": dmg.stat().st_size,
          "sha256": hashlib.sha256(dmg.read_bytes()).hexdigest(), "source_sha256": source_hashes,
          "runtime_copy_verified_before_signing": True, "runtime_input": str(args.runtime_app),
          "runtime_manifests": runtime_manifests, "licenses": inventory(resources / "ThirdPartyLicenses"),
          "documentation": document_hashes,
          "bridge_sha256": hashlib.sha256(args.bridge.read_bytes()).hexdigest(),
          "app_manifest": inventory(app), "signature": "ad-hoc; not Developer ID signed or notarized"}
(reports / "build.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key: report[key] for key in ("release_version", "app", "dmg", "bytes", "sha256", "signature")}, ensure_ascii=False))
