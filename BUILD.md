# Development and packaging

## Run from a source checkout

Use standard 64-bit CPython 3.12–3.14 with Tkinter. The proxy dependency is pinned to `mitmproxy==12.2.3`. A normal CPython 3.13 installation with Tkinter is a suitable common environment; free-threaded, 32-bit, and other unverified runtimes are not the offline package baseline.

The application resolves its proxy executable relative to `app/`, so create the virtual environment at **`app/.venv`**. An environment only at the repository root will not satisfy that lookup.

macOS:

```sh
python3.13 -m venv app/.venv
app/.venv/bin/python -m pip install -r app/requirements.txt
app/.venv/bin/python app/launcher.py
```

Windows PowerShell:

```powershell
py -3.13 -m venv app\.venv
app\.venv\Scripts\python.exe -m pip install -r app\requirements.txt
app\.venv\Scripts\python.exe app\launcher.py
```

These are source setup instructions, not evidence that dependency installation was tested on a fresh machine. `http://127.0.0.1:3000/v1` is a configuration example; this project does not provide an upstream service at that address.

`app/launcher.py` is the source GUI/CLI entry. `app/app_entry.py` is the macOS bundle entry and expects the prepared `.app` layout. `app/bootstrap.py` and `app/start.cmd` prepare a complete Windows offline payload and intentionally fail when its bundled installers or wheels are missing. Do not use them as a fresh-clone setup shortcut.

## Focused development checks

From `app/`, using an interpreter with the required modules available:

```sh
python -B launcher.py --help
python -B -m unittest discover -s tests -p test_bootstrap.py -v
python -B -m unittest discover -s tests -p test_qoder_clients.py -k ConfigFileTests -v
```

The bootstrap tests mock runtime preparation and process launching. The configuration tests use temporary files. They do not constitute a live client or model-service acceptance test. GUI, proxy transport, native-client and packaged-architecture tests have separate prerequisites; do not run an undifferentiated full suite against personal client state.

## Optional IDEA bridge

The repository includes the original Java sources, plugin descriptor, build script and Java test. It does not include a precompiled plugin ZIP or vendor JARs.

```sh
python3 app/idea-bridge/build.py --ide-home "<IDE installation/Contents on macOS, installation root on Windows>" --qoder-plugin "<installed Qoder plugin directory>" --test
```

The script uses `jbr/bin/javac` and `jbr/bin/java` from the supplied IDE, searches its libraries and the supplied Qoder plugin's `lib/`, and compiles with `--release 21`. A compatible compiler supporting Java 21 is required. The resulting archive is `app/idea-bridge/.build/qoder-internal-bridge-0.1.2.zip`; install that locally built archive through the IDE's plugin installation UI. Building is not evidence that every IDE/plugin version is compatible.

## Desktop packages

The public prerelease is `v1.1.4-beta.1`; numeric bundle/installer metadata is `1.1.4`. Runtime inputs are intentionally external to Git. The build scripts do not download or install them, and a fresh clone alone cannot produce a complete DMG/EXE.

### Shared optional bridge and licenses

The bridge build above accepts `--build-dir <new isolated directory>`. It packages the project's MIT text and NOTICE with its own classes/metadata and never bundles vendor JARs. The public asset is named `qoder-local-model-bridge-0.1.2.zip`; it is also copied into both desktop packages.

`packaging/collect_licenses.py` reads exact local wheel/runtime inputs without installing them. Supply `--platform macos|windows`, one or more `--wheels` directories, original runtime notice paths, `--supplements` containing any verified upstream notices absent from wheels, `--sources` with the corresponding-source manifest, and a new `--output` directory. Use `--help` for these file arguments. Its output preserves original notice bytes and records package versions/input hashes; it refuses an existing destination and fails if a wheel has no original or supplemented notice. Review its inventory and the actual runtime components separately; it is not a full native-license audit.

The release also supplies `Qoder-Model-Bridge-1.1.4-beta.1-third-party-sources.zip`, with the exact source archives and hashes described in [third-party notices](THIRD_PARTY_NOTICES.md). Do not replace corresponding-source delivery with an unsupported promise to supply it later.

### macOS

The public bundle targets macOS12+, universal2 CPython3.13.16, Tcl/Tk8.6.18 and mitmproxy12.2.3. Preparing the runtime requires the official Python framework, Tcl/Tk frameworks and 42 dependency wheels per architecture. Keep the existing architecture-specific versions; do not silently resolve or upgrade them during packaging.

After independently preparing and auditing the runtime bundle, run from the repository root:

```sh
python3 -B packaging/macos/build.py --runtime-app "<audited runtime .app>" --python-headers "<matching Python framework Headers>" --licenses "<collected macOS license directory>" --bridge "<frozen qoder-local-model-bridge-0.1.2.zip>" --build-dir "<new isolated build directory>" --release-version 1.1.4-beta.1
```

The script reuses only runtime frameworks/Python/dependencies, copies current public application modules, compiles the native launcher, signs the bundle and creates the DMG. It does not copy an old application's configuration, code, CA, logs or runtime state. The [macOS source and helper scripts](https://github.com/wangyinlong-wang/qoder-model-bridge/tree/v1.1.4-beta.1/packaging/macos) also include relocation and native/dependency audits; they do not supply the missing runtime inputs.

The DMG includes `Qoder Model Bridge.app`, an Applications link, `Documentation/` and `IDEA Bridge/`. The app carries its own project license, third-party notices and optional bridge. Signing is ad-hoc; no Developer ID identity or notarization is supplied. Rosetta checks on Apple hardware are not physical Intel acceptance. Preserve the LC_CTYPE fallback in `app/app_entry.py` for GUI startup without an external character locale.

### Windows

The installer targets Windows10/11 x64, standard CPython3.13.16 (its supplied Tcl/Tk is8.6.15), mitmproxy12.2.3 and NSIS3.13. Obtain the original Python ZIP and all 43 wheels matching `packaging/windows/requirements-windows-offline.txt`. The baseline Python input is the [official 3.13.16 x64 ZIP](https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.zip), SHA256 `bbf675bb5e763c1efbb09a3a461b259d81598a63c30c4b0d7ea11b9f063df159`.

Arrange a clean runtime-input directory containing only the required `installers/python-3.13.16-amd64.zip` and `wheels/*.whl` inputs. Do not build from a user's installed directory. From the repository root:

```sh
python3 -B packaging/windows/build.py --runtime-input "<verified runtime inputs>" --licenses "<collected Windows license directory>" --bridge "<frozen qoder-local-model-bridge-0.1.2.zip>" --build-dir "<new isolated Windows build directory>" --output "<new Qoder-Model-Bridge-1.1.4-beta.1-windows-x64-setup.exe>" --makensis "<makensis executable>" --nsisdir "<NSIS share/nsis directory>" --sevenzip "<7z or 7zz executable>"
```

This script copies an explicit application/document allowlist from the public checkout, verifies the official runtime and wheel hashes, adds the license directory/bridge, regenerates install/uninstall file lists in its build directory and runs NSIS with `-NOCONFIG -WX`. It then tests/extracts the EXE and compares every payload file by SHA256 and size. The generated file lists in the repository are a reviewed snapshot; the builder generates fresh lists for its own staged payload.

`bootstrap.py` and `start.cmd` retain the existing offline setup behavior. The installer keeps its established APP_ID, registry/data directories, all three proxy-record guards in both installation and uninstallation, and named-file/reparse-point safeguards. It recognizes both the old Chinese and new English GUI title and removes only known legacy shortcut names after successful setup. User `proxy-config.json` is not a payload or named deletion target; unknown files and preserved state are not recursively removed.

The EXE is not Authenticode-signed. NSIS compilation, archive tests, extraction/hash comparison and static protection review do not prove installation, GUI, upgrade/uninstall or native client behavior on Windows. No Windows-machine/VM acceptance is claimed for this prerelease.

## Release checks and scope

The release assets are the Mac DMG, Windows EXE, optional bridge ZIP, corresponding-source ZIP and `SHA256SUMS`. The sum file covers the four binary/ZIP assets. Verify the actual same-version build results and remaining limitations in the [release notes](https://github.com/wangyinlong-wang/qoder-model-bridge/releases/tag/v1.1.4-beta.1), and see [compatibility](docs/COMPATIBILITY.md). Packaging checks do not make every Qoder workflow or real model API accepted. Historical privately configured installers must not be uploaded as these assets.
