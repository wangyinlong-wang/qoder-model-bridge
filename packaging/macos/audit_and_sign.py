"""Check native dependencies, deployment targets and the final local app signature."""
import argparse
import json
from pathlib import Path
import re
import subprocess

BUILD = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("app", nargs="?", type=Path, default=BUILD / "Qoder Model Bridge.app")
parser.add_argument("--report-dir", type=Path)
args = parser.parse_args()
APP = args.app.resolve()
REPORT = args.report_dir.resolve() if args.report_dir else APP.parent
REPORT.mkdir(parents=True, exist_ok=True)
MAIN = APP / "Contents/MacOS/QoderInternalProxy"
magic = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
         b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
records = []
machos = []
problems = []
for path in APP.rglob("*"):
    if path.is_symlink():
        if not path.exists() or not path.resolve().is_relative_to(APP):
            problems.append({"file": str(path.relative_to(APP)), "problem": "external or broken link"})
        continue
    if not path.is_file():
        continue
    with path.open("rb") as stream:
        if stream.read(4) not in magic:
            continue
    machos.append(path)
    architectures = subprocess.check_output(["/usr/bin/lipo", "-archs", str(path)], text=True).split()
    relative = path.relative_to(APP).as_posix()
    expected = {"arm64"} if "/dependencies/arm64/" in relative else {"x86_64"} if "/dependencies/x86_64/" in relative else {"arm64", "x86_64"}
    if not expected.issubset(architectures):
        problems.append({"file": relative, "problem": "missing architecture", "architectures": architectures})
    loads = subprocess.check_output(["/usr/bin/otool", "-L", str(path)], text=True)
    identifiers = set(subprocess.check_output(["/usr/bin/otool", "-D", str(path)], text=True).splitlines()[1:])
    dependencies = set(re.findall(r"^\s+(.+?) \(compatibility version", loads, re.MULTILINE)) - identifiers
    command_output = subprocess.check_output(["/usr/bin/otool", "-l", str(path)], text=True)
    rpaths = re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset", command_output)
    for dependency in dependencies:
        if dependency.startswith(("/usr/lib/", "/System/Library/")):
            continue
        if dependency.startswith("@loader_path/"):
            targets = [path.parent / dependency[len("@loader_path/"):]]
        elif dependency.startswith("@executable_path/"):
            targets = [MAIN.parent / dependency[len("@executable_path/"):], path.parent / dependency[len("@executable_path/"):]]
        elif dependency.startswith("@rpath/"):
            bases = [APP / "Contents/Frameworks"]
            for item in rpaths:
                if item.startswith("@loader_path/"):
                    bases.append(path.parent / item[len("@loader_path/"):])
                elif item.startswith("@executable_path/"):
                    bases.append(MAIN.parent / item[len("@executable_path/"):])
            targets = [base / dependency[len("@rpath/"):] for base in bases]
        else:
            problems.append({"file": relative, "problem": "external dependency", "dependency": dependency})
            continue
        if not any(target.exists() and target.resolve().is_relative_to(APP) for target in targets):
            problems.append({"file": relative, "problem": "unresolved dependency", "dependency": dependency})
    minimums = []
    for block in re.split(r"Load command \d+\n", command_output):
        field = "minos" if "cmd LC_BUILD_VERSION\n" in block else "version" if "cmd LC_VERSION_MIN_MACOSX\n" in block else None
        if field:
            match = re.search(r"^\s*" + field + r" (\d+\.\d+(?:\.\d+)?)\s*$", block, re.MULTILINE)
            assert match, (relative, block)
            minimums.append(match[1])
    if not minimums or not all(tuple(map(int, value.split('.'))) <= (12, 0, 0) for value in minimums):
        problems.append({"file": relative, "problem": "deployment target", "minimums": minimums})
    records.append({"file": relative, "architectures": architectures, "minimum_os_versions": minimums})

if problems:
    (REPORT / "native-problems.json").write_text(json.dumps(problems, ensure_ascii=False, indent=2))
    print(json.dumps({"problems": problems}, ensure_ascii=False))
    raise SystemExit(1)

signing = []
for path in machos:
    previous = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(path)], capture_output=True, text=True)
    if previous.returncode:
        result = subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(path)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        signing.append(str(path.relative_to(APP)))
bundles = [p for p in APP.rglob("*") if p.is_dir() and not p.is_symlink() and p.suffix in {".app", ".framework"}]
for path in sorted(bundles, key=lambda p: len(p.parts), reverse=True) + [APP]:
    result = subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
result = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(APP)],
                        capture_output=True, text=True)
assert result.returncode == 0, result.stderr
report = {"mach_o_files": len(records), "native_objects": records, "locally_signed_leaf_files": signing,
          "signature": "ad-hoc; no Developer ID and no notarization", "signature_validation": result.stderr.strip(),
          "bundle_dependencies": "PASS", "minimum_os": "12.0", "external_symlinks": 0}
(REPORT / "native-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({key: value for key, value in report.items() if key not in {"native_objects", "locally_signed_leaf_files"}}, ensure_ascii=False))
