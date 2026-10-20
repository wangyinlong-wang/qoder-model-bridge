"""Prepare the bundled Windows environment without network access, then open the GUI."""
from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
EXPECTED_MITMPROXY = "12.2.3"
RUNTIME_CHECK = (
    "import struct,sys,sysconfig; "
    "assert sys.platform == 'win32' and sys.implementation.name == 'cpython' and sys.version_info[:3] == (3,13,16); "
    "assert struct.calcsize('P') == 8 and not sysconfig.get_config_var('Py_GIL_DISABLED'); "
    "print(sys.executable)"
)
GUI_CHECK = "import sys,tkinter; assert sys.version_info >= (3,10); print(sys.executable)"
CA_CHECK = "import ssl; print(len(ssl.create_default_context().get_ca_certs()))"
CA_BUNDLE_CHECK = "import certifi; print(certifi.where())"


def probe(command, code):
    try:
        result = subprocess.run(command + ["-I", "-X", "utf8", "-c", code],
                                capture_output=True, encoding="utf-8", timeout=12)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def candidates():
    result = []
    if os.environ.get("PROXY_PYTHON"):
        result.append([os.environ["PROXY_PYTHON"]])
    result.append([sys.executable])
    if os.name == "nt" and shutil.which("py"):
        result.extend([["py", "-" + version] for version in ["3.13", "3.12", "3.14"]])
    for name in ["python3.13", "python3.12", "python3.14", "python3", "python"]:
        if shutil.which(name):
            result.append([shutil.which(name)])
    return result


@contextmanager
def setup_lock():
    directory = ROOT / ".runtime"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "setup.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("另一个启动窗口正在准备环境，请等待它完成后再启动。") from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def prepare_environment(arguments):
    requirements = ROOT / "requirements-windows-offline.txt"
    if not requirements.is_file() or not (ROOT / "wheels").is_dir():
        raise RuntimeError("离线组件不完整。请重新解压整个 Windows 离线包，保留 wheels 和 requirements-windows-offline.txt；不会转为联网安装。")
    pip_env = {name: value for name, value in os.environ.items() if not name.startswith("PIP_")}
    pip_env["PIP_CONFIG_FILE"] = os.devnull
    options = candidates()
    runtime = None
    runtime_override = os.environ.get("PROXY_PYTHON")
    if runtime_override:
        runtime = probe([runtime_override], RUNTIME_CHECK)
        if not runtime:
            raise RuntimeError("PROXY_PYTHON 指定的解释器不可用；本离线包需要 Windows x64 标准版 CPython 3.13.16。")
    else:
        for option in options:
            runtime = probe(option, RUNTIME_CHECK)
            if runtime:
                break
    if not runtime:
        raise RuntimeError("本离线包需要 Windows x64 标准版 CPython 3.13.16。请使用 start.cmd 准备随包 Python。")
    environment = ROOT / ".venv"
    venv_python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if (environment.exists() or environment.is_symlink()) and not probe([str(venv_python)], RUNTIME_CHECK):
        if environment.is_symlink():
            raise RuntimeError("当前 .venv 是无效的符号链接，请确认其目标后重新准备环境。")
        backup = ROOT / (".venv.backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        environment.rename(backup)
        print("已保留失效环境：" + backup.name, flush=True)
    if not venv_python.exists():
        print("正在准备本工具独立的 Python 环境…", flush=True)
        subprocess.run([runtime, "-I", "-X", "utf8", "-m", "venv", str(environment)], check=True)
    dependency_check = "import importlib.metadata,mitmproxy.tools.main; print(importlib.metadata.version('mitmproxy'))"
    if probe([str(venv_python)], dependency_check) != EXPECTED_MITMPROXY:
        if environment.is_symlink():
            raise RuntimeError("当前 .venv 指向其他目录且组件不完整，请确认该环境后手动处理。")
        print("正在离线安装 mitmproxy " + EXPECTED_MITMPROXY + " 及全部依赖，不访问网络…", flush=True)
        subprocess.run([str(venv_python), "-I", "-X", "utf8", "-m", "pip", "install", "--no-index", "--no-deps",
                        "--require-hashes", "--disable-pip-version-check", "--no-cache-dir", "--no-input",
                        "--only-binary=:all:", "-r", str(requirements)],
                       cwd=ROOT, env=pip_env, check=True)
    if probe([str(venv_python)], dependency_check) != EXPECTED_MITMPROXY:
        raise RuntimeError("组件安装后仍无法正常加载，请保留上方安装错误；未启动转发。")
    subprocess.run([str(venv_python), "-I", "-X", "utf8", "-m", "pip", "--disable-pip-version-check", "check"],
                   cwd=ROOT, env=pip_env, check=True)
    if "--no-gui" in arguments or "--list-models" in arguments:
        gui_python = str(venv_python)
    else:
        gui_override = os.environ.get("PROXY_GUI_PYTHON")
        tk_options = [[gui_override]] if gui_override else options + [[str(venv_python)]]
        gui_python = None
        for option in tk_options:
            gui_python = probe(option, GUI_CHECK)
            if gui_python:
                break
        if not gui_python:
            if gui_override:
                raise RuntimeError("PROXY_GUI_PYTHON 指定的解释器不可用或缺少 Tcl/Tk。")
            raise RuntimeError("没有找到带 Tcl/Tk 的 Python。请使用 start.command / start.cmd 自动准备，或设置 PROXY_GUI_PYTHON。")
    return runtime, str(venv_python), gui_python


def main():
    arguments = [value for value in sys.argv[1:] if value != "--setup-only"]
    with setup_lock():
        runtime, venv_python, gui_python = prepare_environment(arguments)
    child_env = os.environ.copy()
    if (sys.platform == "darwin" and not child_env.get("SSL_CERT_FILE") and not child_env.get("SSL_CERT_DIR")
            and probe([gui_python], CA_CHECK) == "0"):
        # Some python.org macOS installs omit their default CA file. Keep the fix local to this child.
        bundle = probe([venv_python], CA_BUNDLE_CHECK)
        if not bundle or not Path(bundle).is_file():
            raise RuntimeError("界面解释器缺少 HTTPS 根证书，且工具环境中的 certifi 证书包不可用。")
        child_env["SSL_CERT_FILE"] = bundle
    if "--setup-only" in sys.argv[1:]:
        print("环境已就绪。基础 Python：" + runtime + "；组件环境：" + venv_python + "；界面 Python：" + gui_python
              + "；mitmproxy：" + EXPECTED_MITMPROXY, flush=True)
        return 0
    return subprocess.call([gui_python, "-X", "utf8", str(ROOT / "launcher.py"), *arguments], cwd=ROOT, env=child_env)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)
