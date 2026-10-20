"""Build and inspect a Windows installer from public source and verified local inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
APP_FILES = (
    "launcher.py", "qoder_to_internal.py", "qoder_protocol.py", "qoder_control.py",
    "qoder_clients.py", "idea_bridge.py", "client_api.js", "client_relay.cjs",
    "requirements.txt", "config.example.json", "bootstrap.py", "start.cmd",
    "requirements-windows-offline.txt",
)
DOCUMENTS = ("README.md", "README.en.md", "BUILD.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
             "docs/INSTALLATION.md", "docs/COMPATIBILITY.md", "docs/TROUBLESHOOTING.md")
PYTHON_ZIP = "python-3.13.16-amd64.zip"
PYTHON_SHA256 = "bbf675bb5e763c1efbb09a3a461b259d81598a63c30c4b0d7ea11b9f063df159"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("runtime-input", "licenses", "bridge", "build-dir", "output", "makensis", "nsisdir", "sevenzip"):
        parser.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args()
    args.build_dir = args.build_dir.resolve()
    args.output = args.output.resolve()
    if args.build_dir.exists() or args.output.exists():
        raise SystemExit("Refusing an existing build directory or output file")
    python_zip = args.runtime_input / "installers" / PYTHON_ZIP
    if sha256(python_zip) != PYTHON_SHA256:
        raise SystemExit("Official Python ZIP hash does not match the frozen runtime")
    wheels = sorted((args.runtime_input / "wheels").glob("*.whl"))
    expected = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", (ROOT / "app/requirements-windows-offline.txt").read_text()))
    if len(wheels) != 43 or {sha256(p) for p in wheels} != expected:
        raise SystemExit("The 43 wheel inputs do not match the offline lock file")
    coverage = json.loads((args.licenses / "inventory.json").read_text())
    if coverage["platform"] != "windows" or coverage["missing_notices"] or not all(c["license_body_present"] for c in coverage["components"]):
        raise SystemExit("Windows license coverage is incomplete")
    args.build_dir.mkdir(parents=True)
    payload = args.build_dir / "payload"
    payload.mkdir()
    origins = {}

    def copy(source, destination, origin):
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Not an ordinary input file: {source}")
        target = payload / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        origins[destination] = origin

    for name in APP_FILES:
        copy(ROOT / "app" / name, name, "app/" + name)
    for name in DOCUMENTS:
        copy(ROOT / name, name, name)
    for name in ("INSTALLER-README.txt", "NSIS-COPYING.txt"):
        copy(ROOT / "packaging/windows" / name, name, "packaging/windows/" + name)
    copy(python_zip, "installers/" + PYTHON_ZIP, "verified official CPython archive")
    for wheel in wheels:
        copy(wheel, "wheels/" + wheel.name, "verified wheel: " + wheel.name)
    copy(args.bridge, "idea-bridge/qoder-local-model-bridge-0.1.2.zip", "shared public bridge")
    for path in sorted(args.licenses.rglob("*")):
        if path.is_file():
            relative = path.relative_to(args.licenses).as_posix()
            copy(path, "ThirdPartyLicenses/" + relative, "shared licenses/" + relative)
    files = [{"path": name, "bytes": (payload / name).stat().st_size,
              "sha256": sha256(payload / name), "source": origins[name]} for name in sorted(origins)]
    (args.build_dir / "payload-files.json").write_text(json.dumps(files, indent=2) + "\n")
    nsis = args.build_dir / "nsis"
    nsis.mkdir()
    shutil.copyfile(ROOT / "packaging/windows/installer.nsi", nsis / "installer.nsi")
    install, uninstall = ["; Generated from the explicit public payload; do not hand-edit."], []
    previous = None
    for item in files:
        relative = Path(item["path"])
        if any(character in item["path"] for character in ('"', "$", "\n", "\r")):
            raise ValueError("Unsupported NSIS input filename")
        parent = relative.parent.as_posix()
        if parent != previous:
            directory = "" if parent == "." else "\\" + parent.replace("/", "\\")
            install.append('SetOutPath "$INSTDIR' + directory + '"')
            previous = parent
        if item["path"] == "config.example.json":
            install.append("SetOverwrite off")
        install.append('File "${PAYLOAD_DIR}/' + item["path"] + '"')
        if item["path"] == "config.example.json":
            install.append("SetOverwrite on")
        uninstall += ['Push "$INSTDIR\\' + item["path"].replace("/", "\\") + '"', "Call un.DeletePackageFile"]
    (nsis / "install-files.nsh").write_text("\n".join(install) + "\n")
    (nsis / "uninstall-files.nsh").write_text("\n".join(uninstall) + "\n")
    checks = []

    def run(name, command, environment=None):
        result = subprocess.run(command, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (args.build_dir / (name + ".log")).write_text(result.stdout)
        checks.append({"check": name, "exit": result.returncode})
        print(name, "exit", result.returncode, flush=True)
        if result.returncode:
            raise SystemExit(f"{name} failed; inspect its isolated build log")

    environment = os.environ.copy()
    environment["NSISDIR"] = str(args.nsisdir.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run("makensis", [str(args.makensis.resolve()), "-NOCONFIG", "-WX", "-V3",
        "-DPAYLOAD_DIR=" + str(payload), "-DOUTPUT_FILE=" + str(args.output), str(nsis / "installer.nsi")], environment)
    run("7z-test", [str(args.sevenzip.resolve()), "t", str(args.output)])
    extracted = args.build_dir / "extracted"
    run("7z-extract", [str(args.sevenzip.resolve()), "x", "-y", "-o" + str(extracted), str(args.output)])
    roots = [p.parent for p in extracted.rglob("launcher.py") if all((p.parent / f["path"]).is_file() for f in files)]
    if len(roots) != 1:
        raise SystemExit("Could not identify exactly one complete extracted payload")
    extracted_root = roots[0]
    mismatches = [f["path"] for f in files if (extracted_root / f["path"]).stat().st_size != f["bytes"] or sha256(extracted_root / f["path"]) != f["sha256"]]
    if mismatches:
        raise SystemExit("Extracted payload differs: " + repr(mismatches))
    extra = [p.relative_to(extracted_root).as_posix() for p in extracted_root.rglob("*") if p.is_file() and p.relative_to(extracted_root).as_posix() not in origins]
    if any(not (name.startswith("$PLUGINSDIR/") or name == "Uninstall.exe") for name in extra):
        raise SystemExit("Unexpected extracted files: " + repr(extra))
    report = {"release": "v1.1.4-beta.1", "numeric_version": "1.1.4", "asset": args.output.name,
              "bytes": args.output.stat().st_size, "sha256": sha256(args.output), "checks": checks,
              "payload_files": len(files), "matched_payload_files": len(files), "mismatches": mismatches,
              "installer_extra_files": extra, "python_archive_sha256": PYTHON_SHA256,
              "wheel_count": len(wheels), "windows_machine_validation": "not performed",
              "authenticode_signature": "not signed", "gateway_api_called": False}
    (args.build_dir / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
