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

## Desktop package materials

Fresh-clone source execution and self-contained desktop packaging are separate workflows. The files below are packaging materials, not a complete one-command release pipeline. No installers are produced by cloning this repository.

### macOS

`packaging/macos/` retains the native Cocoa/Python launcher, bundle metadata, Python/Tcl/Tk relocation helper, native signing audit and wheel audit. The baseline targets macOS 12+, universal2 CPython 3.13.16, Tcl/Tk 8.6.18 and mitmproxy 12.2.3.

Preparing a new bundle requires externally obtained runtimes and both architectures' dependency wheels. In particular:

- `prepare_framework.py` expects an extracted official Python framework payload under `packaging/macos/python-pkg/Python_Framework.pkg/Payload/Versions/3.13` and creates a new `Qoder 内网模型接入.app` beside the script. It refuses an existing destination.
- `verify_dependencies.py` expects locally prepared `resolved-aarch64.txt`, `resolved-x86_64.txt`, `wheels-arm64/` and `wheels-x86_64/`. Historical resolver reports were deliberately excluded; they must be prepared for the new build.
- The native launcher must be compiled with the appropriate Python framework headers/libraries, the selected application modules copied into `Contents/Resources/app`, and the architecture-specific dependencies staged under `Contents/Resources/dependencies/`.
- `audit_and_sign.py` expects the prepared bundle beside the script. Review its signing actions before running it; build only in an isolated staging directory, never over an installed application.

The repository does not supply the extracted framework, wheel archives, a complete runtime assembly orchestrator, a Developer ID identity, notarization credentials or public-release signing results. Ad-hoc signing and Rosetta checks are not notarization or physical Intel acceptance. Preserve the LC_CTYPE fallback in `app/app_entry.py`; it is needed when macOS launches the Chinese-named bundle without a character locale.

### Windows

`packaging/windows/` retains the NSIS installer template, its generated baseline file lists, offline bootstrap/start templates, dependency lock file and original NSIS license text. The offline baseline is Windows 10/11 x64, standard CPython 3.13.16 and NSIS 3.13.

A builder must assemble a new clean payload containing the public `app/` modules, required instruction/license files, `installers/python-3.13.16-amd64.zip`, the wheels matching `requirements-windows-offline.txt`, and all entries expected by `install-files.nsh`. The repository does not include that payload or its historical hash inventory. Synchronize/regenerate the install/uninstall file lists when changing the payload; do not build from an existing user installation.

Only after the payload is prepared, invoke NSIS from `packaging/windows/`:

```sh
makensis -NOCONFIG -WX -V3 -DPAYLOAD_DIR="<clean payload directory>" -DOUTPUT_FILE="<new installer path>" installer.nsi
```

Supply `INSTALLER-README.txt` and the unmodified `NSIS-COPYING.txt` in that payload. The template keeps the established application identity and proxy-journal protections. Its version metadata is a compatibility baseline, not an announcement of an available GitHub release.

## Public release boundary

Historical macOS 1.1.3 and Windows 1.1.2 installers are not included or offered as public downloads. They were built with private deployment defaults. A public binary needs a separate clean build from this public source, its exact dependency-license inventory, signing/installation checks and actual client acceptance. See [third-party notices](THIRD_PARTY_NOTICES.md) and [compatibility](docs/COMPATIBILITY.md).
