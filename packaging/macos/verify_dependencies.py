"""Check each macOS architecture's complete, hashed binary dependency set."""
import argparse
from collections import deque
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import zipfile

from pip._internal.models.target_python import TargetPython
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--build-dir", type=Path, default=Path(__file__).resolve().parent)
args = parser.parse_args()
BUILD = args.build_dir.resolve()
reports = {}
for resolver, architecture in [("aarch64", "arm64"), ("x86_64", "x86_64")]:
    target_tags = set(TargetPython(platforms=["macosx_12_0_" + architecture], py_version_info=(3, 13, 16),
                                   abis=["cp313"], implementation="cp").get_sorted_tags())
    expected = {}
    for line in (BUILD / ("resolved-" + resolver + ".txt")).read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9._-]+)==([^\s\\]+)", line)
        if match:
            current = canonicalize_name(match[1])
            expected[current] = {"version": match[2], "hashes": set()}
        elif "--hash=sha256:" in line:
            expected[current]["hashes"].add(line.split("--hash=sha256:")[1].split()[0].rstrip("\\"))
    packages = {}
    for wheel in sorted((BUILD / ("wheels-" + architecture)).glob("*.whl")):
        name, version, _, tags = parse_wheel_filename(wheel.name)
        name = canonicalize_name(name)
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        assert name not in packages and str(version) == expected[name]["version"], wheel.name
        assert digest in expected[name]["hashes"], wheel.name
        assert tags & target_tags, wheel.name
        with zipfile.ZipFile(wheel) as archive:
            assert archive.testzip() is None, wheel.name
            assert all(not p.startswith(("/", "\\")) and ".." not in p.replace("\\", "/").split("/") for p in archive.namelist())
            path = [p for p in archive.namelist() if p.endswith(".dist-info/METADATA")]
            assert len(path) == 1
            metadata = BytesParser().parsebytes(archive.read(path[0]))
        assert canonicalize_name(metadata["Name"]) == name and metadata["Version"] == str(version)
        assert not metadata["Requires-Python"] or SpecifierSet(metadata["Requires-Python"]).contains("3.13.16"), wheel.name
        packages[name] = {"version": str(version), "filename": wheel.name, "sha256": digest,
                          "bytes": wheel.stat().st_size, "requires_dist": metadata.get_all("Requires-Dist", [])}
    assert set(packages) == set(expected)
    environment = {"implementation_name": "cpython", "implementation_version": "3.13.16", "os_name": "posix",
                   "platform_machine": architecture, "platform_release": "21.0.0", "platform_system": "Darwin",
                   "platform_version": "Darwin Kernel Version 21.0.0", "platform_python_implementation": "CPython",
                   "python_full_version": "3.13.16", "python_version": "3.13", "sys_platform": "darwin"}
    requested = {"mitmproxy": set()}
    queue = deque(["mitmproxy"])
    edges = set()
    while queue:
        parent = queue.popleft()
        for value in packages[parent]["requires_dist"]:
            requirement = Requirement(value)
            if requirement.marker and not any(requirement.marker.evaluate({**environment, "extra": extra})
                                              for extra in {""} | requested[parent]):
                continue
            name = canonicalize_name(requirement.name)
            assert name in packages and requirement.url is None, (parent, value)
            assert requirement.specifier.contains(packages[name]["version"]), (parent, value)
            edges.add((parent, name, str(requirement.specifier)))
            if name not in requested or not requirement.extras.issubset(requested[name]):
                requested.setdefault(name, set()).update(requirement.extras)
                queue.append(name)
    assert set(requested) == set(packages)
    lock = "# macOS 12+ / CPython 3.13.16 / " + architecture + ". Local files from official PyPI.\n"
    for name, package in sorted(packages.items()):
        lock += f"./wheels-{architecture}/{package['filename']} --hash=sha256:{package['sha256']}\n"
    (BUILD / ("install-" + architecture + ".txt")).write_text(lock)
    reports[architecture] = {"packages": packages, "package_count": len(packages), "dependency_edges": len(edges),
                             "wheel_bytes": sum(p["bytes"] for p in packages.values()), "verified": True}
(BUILD / "dependencies-verified.json").write_text(json.dumps(reports, indent=2))
print(json.dumps({arch: {key: value for key, value in report.items() if key != "packages"} for arch, report in reports.items()}))
