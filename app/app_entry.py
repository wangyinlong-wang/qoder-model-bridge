"""Start the self-contained macOS app or its native-architecture proxy worker."""
import json
import os
from pathlib import Path
import platform
import ssl
import sys

APP = Path(__file__).resolve().parent
CONTENTS = APP.parents[1]
ARCHITECTURE = platform.machine()
if ARCHITECTURE not in {"arm64", "x86_64"}:
    raise RuntimeError("此应用仅支持 Apple 芯片和 Intel x86_64 Mac。")
DEPENDENCIES = CONTENTS / "Resources" / "dependencies" / ARCHITECTURE
PYTHON_HOME = CONTENTS / "Resources/python"
EXECUTABLE = CONTENTS / "MacOS" / "QoderInternalProxy"
sys.path[:0] = [str(APP), str(DEPENDENCIES)]

os.environ["QODER_BUNDLE_EXECUTABLE"] = str(EXECUTABLE)
os.environ["PYTHONHOME"] = str(PYTHON_HOME)
os.environ["PYTHONNOUSERSITE"] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["PYTHONUTF8"] = "1"
# Apps may omit LC_CTYPE; Tcl needs UTF-8 for the Chinese bundle path.
if not os.environ.get("LC_CTYPE"):
    os.environ["LC_CTYPE"] = "UTF-8"
os.environ["TCL_LIBRARY"] = str(CONTENTS / "Frameworks/Tcl.framework/Versions/8.6/Resources/Scripts")
os.environ["TK_LIBRARY"] = str(CONTENTS / "Frameworks/Tk.framework/Versions/8.6/Resources/Scripts")
if not os.environ.get("SSL_CERT_FILE") and not os.environ.get("SSL_CERT_DIR") and not ssl.create_default_context().get_ca_certs():
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["QODER_MANAGED_CA_FILE"] = os.environ["SSL_CERT_FILE"]

if len(sys.argv) > 1 and sys.argv[1] == "--proxy-worker":
    del sys.argv[1]
    sys.argv[0] = "mitmdump"
    from mitmproxy.tools.main import mitmdump
    mitmdump()
elif len(sys.argv) > 1 and sys.argv[1] == "--self-check":
    import importlib.metadata
    import mitmproxy.tools.main
    import mitmproxy_rs
    import tkinter
    root = tkinter.Tk()
    root.withdraw()
    root.update()
    tk_version = root.tk.call("info", "patchlevel")
    root.destroy()
    print(json.dumps({"architecture": ARCHITECTURE, "python": platform.python_version(),
                      "python_home": sys.prefix, "tk": tk_version,
                      "mitmproxy": importlib.metadata.version("mitmproxy"),
                      "native_dependency": str(mitmproxy_rs.__file__),
                      "ca_count": len(ssl.create_default_context().get_ca_certs()),
                      "check_scope": "imports and Tk; no model request"}, ensure_ascii=False))
else:
    data = Path(os.environ.get("QODER_APP_DATA_DIR", str(Path.home() / "Library/Application Support/QoderInternalProxy")))
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["QODER_PROXY_STATE_DIR"] = str(data / ".runtime")
    os.chdir(data)
    import launcher
    raise SystemExit(launcher.main())
