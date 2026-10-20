"""Collect original notices from the exact, locally supplied release inputs.

This does not install packages or download inputs. Review the resulting inventory
alongside the corresponding-source manifest before distributing a binary.
"""
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile


LICENSE_NAMES = re.compile(r"(?:^|[._-])(licen[cs]e|copying|notice|copyright|authors)([._-].*)?$", re.I)
LICENSE_BODIES = re.compile(r"(?:^|[._-])(licen[cs]e|copying)([._-].*)?$", re.I)
LABELS = {
    "blinker": "MIT", "h2": "MIT", "hyperframe": "MIT",
    "itsdangerous": "BSD-3-Clause", "jinja2": "BSD-3-Clause",
    "mitmproxy": "MIT", "mitmproxy-rs": "MIT", "pyopenssl": "Apache-2.0",
    "pyperclip": "BSD-3-Clause", "wcwidth": "MIT",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def notice(name):
    return bool(LICENSE_NAMES.search(PurePosixPath(name).name))


def license_body(name, data):
    return bool(LICENSE_BODIES.search(PurePosixPath(name).name) and data.strip())


def store(root, relative, data):
    relative = PurePosixPath(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe archive path: {relative}")
    path = root.joinpath(*relative.parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": relative.as_posix(), "sha256": digest(data), "bytes": len(data)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("macos", "windows"), required=True)
    parser.add_argument("--wheels", type=Path, action="append", required=True)
    parser.add_argument("--python-license", type=Path)
    parser.add_argument("--python-archive", type=Path)
    parser.add_argument("--tcl-license", type=Path)
    parser.add_argument("--tk-license", type=Path)
    parser.add_argument("--tcl-tk-version", required=True)
    parser.add_argument("--nsis-license", type=Path)
    parser.add_argument("--supplements", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an existing license directory")
    args.output.mkdir(parents=True)
    components = []
    missing = []
    for folder in args.wheels:
        wheels = sorted(folder.glob("*.whl"))
        if not wheels:
            raise SystemExit(f"No wheel inputs in {folder}")
        for wheel in wheels:
            with zipfile.ZipFile(wheel) as archive:
                metadata_name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
                metadata = BytesParser().parsebytes(archive.read(metadata_name))
                name, version = metadata["Name"], metadata["Version"]
                key = normalized(name)
                texts = []
                has_license = False
                for n in archive.namelist():
                    if not n.endswith("/") and notice(n):
                        data = archive.read(n)
                        texts.append(store(args.output, f"wheels/{wheel.name}/{n}", data))
                        has_license = has_license or license_body(n, data)
                supplement = args.supplements / f"{key}-{version}"
                if supplement.is_dir():
                    for path in sorted(supplement.rglob("*")):
                        if path.is_file():
                            data = path.read_bytes()
                            texts.append(store(args.output, f"supplements/{supplement.name}/{path.relative_to(supplement).as_posix()}", data))
                            has_license = has_license or license_body(path.name, data)
                if not has_license:
                    missing.append(f"{name}=={version} ({wheel.name})")
                label = metadata.get("License-Expression") or LABELS.get(key) or metadata.get("License", "unspecified in wheel metadata")
                components.append({"component": name, "version": version, "license": label,
                                   "source": f"https://pypi.org/project/{name}/{version}/",
                                   "upstream_links": metadata.get_all("Project-URL", []) or [metadata.get("Home-page", "")],
                                   "input": wheel.name, "input_sha256": digest(wheel.read_bytes()),
                                   "license_body_present": has_license, "notices": texts})
    runtime_notices = []
    if args.python_archive:
        with zipfile.ZipFile(args.python_archive) as archive:
            for name in archive.namelist():
                if not name.endswith("/") and (notice(name) or name == "Doc/html/license.html"):
                    runtime_notices.append(store(args.output, f"runtime/python/{name}", archive.read(name)))
                if name.endswith(".whl") and "ensurepip/" in name:
                    import io
                    with zipfile.ZipFile(io.BytesIO(archive.read(name))) as nested:
                        for entry in nested.namelist():
                            if not entry.endswith("/") and notice(entry):
                                runtime_notices.append(store(args.output, f"runtime/ensurepip/{entry}", nested.read(entry)))
    for option, target in ((args.python_license, "python/LICENSE.txt"),
                           (args.tcl_license, "tcl/license.terms"),
                           (args.tk_license, "tk/license.terms"),
                           (args.nsis_license, "nsis/COPYING.txt")):
        if option:
            runtime_notices.append(store(args.output, "runtime/" + target, option.read_bytes()))
    source_manifest = json.loads(args.sources.read_text())
    store(args.output, "CORRESPONDING-SOURCES.json", (json.dumps(source_manifest, indent=2) + "\n").encode())
    inventory = {"platform": args.platform, "wheel_inputs": len(components),
                 "unique_packages": len({(normalized(c['component']), c['version']) for c in components}),
                 "components": components, "runtime_components": [
                     {"component": "CPython", "version": "3.13.16", "license": "PSF-2.0 and incorporated notices", "source": "https://www.python.org/downloads/release/python-31316/"},
                     {"component": "Tcl/Tk", "version": args.tcl_tk_version, "license": "Tcl/Tk license.terms", "source": "https://www.tcl-lang.org/software/tcltk/8.6.html"},
                 ], "runtime_notices": runtime_notices, "missing_notices": missing,
                 "scope": "Original notices supplied in the exact wheels/runtime, plus versioned upstream supplements. Not a claim of an independent audit of every statically linked subcomponent."}
    if args.nsis_license:
        inventory["runtime_components"].append({"component": "NSIS", "version": "3.13", "license": "component-specific terms in COPYING.txt", "source": "https://nsis.sourceforge.io/License"})
    if args.python_archive:
        inventory["python_archive"] = {"input": args.python_archive.name, "sha256": digest(args.python_archive.read_bytes())}
        for version in ("1.3.0", "2.2.2"):
            inventory["runtime_components"].append({"component": "WinDivert", "version": version,
                "license": "LGPL-3.0-or-later (selected from upstream's dual LGPLv3/GPLv2 terms)",
                "source": f"https://github.com/basil00/WinDivert/tree/v{version}"})
    (args.output / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    readme = "# Third-party licenses and corresponding source\n\nOriginal notice bytes are preserved under `wheels/`, `runtime/` and `supplements/`.\nThe inventory identifies every supplied wheel and its hash; upstream license terms apply separately from the project's MIT license.\n\nLGPL components are used as separately replaceable Python packages/DLLs. You may modify or replace those components and debug such modifications under their respective terms. No project restriction is imposed on those rights.\n\nVersioned source archives for LGPL components, the MPL-covered data/packages and mitmproxy's native components are provided alongside the installers in `Qoder-Model-Bridge-1.1.4-beta.1-third-party-sources.zip`. `CORRESPONDING-SOURCES.json` lists the original upstream URLs, versions and SHA-256 hashes. The source archive includes upstream build instructions and the WinDivert sources; no unsupported promise to supply source later is used.\n\nThis is an inventory of the upstream notices carried by the selected inputs, not a complete independent legal audit of their embedded native dependencies.\n"
    (args.output / "README.md").write_text(readme)
    print(json.dumps({"platform": args.platform, "wheels": len(components), "packages": inventory['unique_packages'], "runtime_notices": len(runtime_notices), "missing_notices": missing}))
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
