"""Build against locally installed IDEA/Qoder APIs; no dependency downloads or vendor JAR bundling."""
import argparse
from pathlib import Path
import shutil
import subprocess
import os
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ide-home", type=Path, default=Path("/Applications/IntelliJ IDEA.app/Contents"))
    parser.add_argument("--qoder-plugin", type=Path, required=True)
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    ide = args.ide_home
    runtime = ide / "jbr" / ("Contents/Home" if (ide / "jbr/Contents/Home").is_dir() else "")
    suffix = ".exe" if os.name == "nt" else ""
    javac, java = runtime / ("bin/javac" + suffix), runtime / ("bin/java" + suffix)
    if not javac.is_file():
        raise SystemExit("IDEA bundled javac was not found; specify --ide-home")
    jars = sorted((ide / "lib").glob("*.jar")) + sorted((args.qoder_plugin / "lib").glob("*.jar"))
    classpath = os.pathsep.join(str(path) for path in jars)
    build = ROOT / ".build"
    classes = build / "classes"
    if classes.exists():
        shutil.rmtree(classes)
    classes.mkdir(parents=True)
    sources = sorted((ROOT / "src").rglob("*.java"))
    if args.test:
        sources += sorted((ROOT / "tests").rglob("*.java"))
    subprocess.run([str(javac), "--release", "21", "--add-modules", "jdk.httpserver", "-encoding", "UTF-8",
                    "-classpath", classpath, "-d", str(classes), *map(str, sources)], check=True)
    if args.test:
        subprocess.run([str(java), "--add-modules", "jdk.httpserver", "-ea", "-classpath",
                        str(classes) + os.pathsep + classpath, "local.qoder.bridge.LocalBridgeTest"], check=True)
    version = ET.parse(ROOT / "resources/META-INF/plugin.xml").getroot().findtext("version")
    artifact = build / f"qoder-internal-bridge-{version}.zip"
    jar = build / "qoder-internal-bridge.jar"
    with zipfile.ZipFile(jar, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(classes.rglob("*.class")):
            if not path.name.startswith("LocalBridgeTest"):
                archive.write(path, path.relative_to(classes).as_posix())
        archive.write(ROOT / "resources/META-INF/plugin.xml", "META-INF/plugin.xml")
    with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(jar, "qoder-internal-bridge/lib/qoder-internal-bridge.jar")
    print("BUILT", artifact)


if __name__ == "__main__":
    main()
