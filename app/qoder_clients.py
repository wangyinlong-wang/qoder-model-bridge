"""Connect a selected Qoder IDE through its own Electron runtime and anonymous pipes."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent
CLIENT_CHOICES = {
    "IDEA / JetBrains（现有国际版模式）": ("idea", "global"),
    "Qoder IDE（国际版）": ("qoder_ide", "global"),
    "Qoder CN IDE（国内版，连接后检查能力）": ("qoder_ide", "cn"),
}
ERRORS = {
    "CLIENT_START_FAILED": "无法启动所选 Qoder IDE，请检查安装路径。",
    "CLIENT_CLOSED": "客户端连接已关闭。如 Qoder IDE 原来已打开，请先正常退出，再点击连接客户端。",
    "CLIENT_INTERFACE_NOT_READY": "未找到 Qoder IDE 原生模型接口，请完成客户端启动并打开 Qoder 聊天面板。",
    "CLIENT_INTERFACE_UNSUPPORTED": "这个 Qoder IDE 版本的原生接口尚未适配，未修改客户端。",
    "CLIENT_MULTIPLE_WINDOWS": "当前有多个 Qoder IDE 窗口，请保留要接入的一个窗口后重新连接。",
    "CLIENT_BYOK_NOT_AVAILABLE": "请先在所选 Qoder IDE 登录，并确认当前账号允许使用自定义模型。",
    "CLIENT_OPENROUTER_UNAVAILABLE": "当前未取得可用的 OpenRouter 按量入口。请检查 Qoder 网络、登录和账号支持情况；目录确实没有该入口时不能复用此方案，未修改模型。",
    "CLIENT_PROXY_CONTAINS_CREDENTIALS": "客户端原代理含凭据，工具不会改写，请在 Qoder IDE 手动配置代理。",
    "CLIENT_PROXY_MISMATCH": "Qoder IDE 当前代理与本工具不一致，请先连接本窗口的转发代理。",
    "CLIENT_PROXY_NOT_APPLIED": "Qoder IDE 尚未确认代理设置，请检查客户端后重试。",
    "CLIENT_REQUEST_TIMEOUT": "客户端操作超时；请先检查模型列表，工具不会自动重复添加。",
    "CLIENT_BUSY_OR_NOT_READY": "客户端正在处理操作，请稍后重试。",
}


def get_client(config):
    kind, edition = config.get("client_type", "idea"), config.get("client_edition", "global")
    if (kind, edition) not in CLIENT_CHOICES.values():
        raise ValueError("客户端必须是已有 IDEA、Qoder IDE 国际版或 Qoder CN IDE 国内版。")
    return kind, edition


def app_info(selected):
    """Use installed product metadata, not UI language or a guessed region."""
    path = Path(selected).expanduser().resolve()
    if sys.platform == "darwin":
        bundle = next((p for p in (path, *path.parents) if p.suffix == ".app"), None)
        if bundle is None:
            raise ValueError("请选择 Qoder IDE 的 .app 应用。")
        resources = bundle / "Contents/Resources/app"
    else:
        directory = path.parent if path.is_file() else path
        resources = directory / "resources/app"
    try:
        product = json.loads((resources / "product.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("所选位置不是可识别的 Qoder IDE 安装目录。") from None
    if not (resources / "out/vs/workbench/workbench.desktop.main.js").is_file():
        raise ValueError("请选择 Qoder IDE；Qoder 桌面端可直接配置自定义 Base URL，无需本适配。")
    folder = product.get("dataFolderName")
    if folder == ".qoder":
        edition = "global"
    elif folder in (".qodercn", ".qoder-cn"):
        edition = "cn"
    else:
        raise ValueError("无法从安装清单确认该客户端的国际版/国内版标识，未启动。")
    product_edition = {"Qoder": "global", "QoderCN": "cn", "Qoder CN": "cn"}.get(product.get("nameShort"))
    if product_edition and product_edition != edition:
        raise ValueError("安装清单的产品标识与数据目录区域不一致，未启动；请核对该客户端版本。")
    if sys.platform == "darwin":
        executable = bundle / "Contents/MacOS" / product.get("darwinExecutableName", "Qoder")
        display_path = bundle
    else:
        names = [product.get("win32ExecutableName", "") + ".exe",
                 "Qoder IDE.exe", "Qoder CN IDE.exe", "Qoder.exe", "QoderCN.exe"]
        executable = path if path.is_file() else next((directory / n for n in names if (directory / n).is_file()), directory / names[0])
        display_path = executable
    if not executable.is_file():
        raise ValueError("未找到所选 Qoder IDE 的启动程序。")
    return {"path": str(display_path), "executable": str(executable), "edition": edition,
            "version": str(product.get("productVersion", product.get("version", "未知")))}


def installed_clients(edition):
    roots = [Path("/Applications"), Path.home() / "Applications"] if sys.platform == "darwin" else [
        Path(value) / suffix for value, suffix in ((os.environ.get("LOCALAPPDATA"), "Programs"),
                                                  (os.environ.get("ProgramFiles"), ""),
                                                  (os.environ.get("ProgramFiles(x86)"), "")) if value]
    result = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if "qoder" not in path.name.lower() and "lingma" not in path.name.lower():
                continue
            try:
                info = app_info(path)
                if info["edition"] == edition and info not in result:
                    result.append(info)
            except ValueError:
                continue
    return result


class IdeClient:
    def __init__(self, info):
        self.info, self.process = info, None
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.ready = False

    def connect(self):
        env = os.environ.copy()
        env["ELECTRON_RUN_AS_NODE"] = "1"
        managed_ca = env.pop("QODER_MANAGED_CA_FILE", None)
        if managed_ca and env.get("SSL_CERT_FILE") == managed_ca:
            env.pop("SSL_CERT_FILE", None)
        # Do not give the client or the Node relay the gateway key from an inherited shell.
        for key in ("INTERNAL_API_KEY", "QODER_PROXY_SETTINGS", "NODE_OPTIONS",
                    "QODER_BUNDLE_EXECUTABLE", "PYTHONHOME", "PYTHONPATH"):
            env.pop(key, None)
        self.process = subprocess.Popen([self.info["executable"], str(ROOT / "client_relay.cjs"), "qoder_ide"],
            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            start_new_session=os.name != "nt",
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0)
        def read():
            for line in self.process.stdout:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        self.events.put(value)
                except ValueError:
                    continue
            self.ready = False
            self.events.put({"event": "closed"})
        threading.Thread(target=read, daemon=True).start()
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline:
            event = self._next(deadline)
            if event.get("event") == "ready":
                self.ready = True
                return event
            if event.get("event") in ("error", "closed"):
                raise ValueError(ERRORS.get(event.get("code", "CLIENT_CLOSED"), "客户端连接失败，未修改设置。"))
        raise ValueError(ERRORS["CLIENT_REQUEST_TIMEOUT"])

    def _next(self, deadline):
        try:
            return self.events.get(timeout=max(0.01, deadline - time.monotonic()))
        except queue.Empty:
            raise ValueError(ERRORS["CLIENT_REQUEST_TIMEOUT"]) from None

    def request(self, action, **params):
        with self.lock:
            if not self.ready or self.process is None or self.process.poll() is not None:
                raise ValueError(ERRORS["CLIENT_CLOSED"])
            request_id = uuid.uuid4().hex
            try:
                self.process.stdin.write(json.dumps({"id": request_id, "action": action, **params}, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.ready = False
                raise ValueError(ERRORS["CLIENT_CLOSED"]) from None
            deadline = time.monotonic() + 135
            while time.monotonic() < deadline:
                value = self._next(deadline)
                if value.get("event") == "closed":
                    self.ready = False
                    raise ValueError(ERRORS["CLIENT_CLOSED"])
                if value.get("id") != request_id:
                    continue
                if not value.get("ok"):
                    raise ValueError(ERRORS.get(value.get("code"), "客户端原生接口拒绝操作；未展示可能含敏感信息的原始错误。"))
                return value
            raise ValueError(ERRORS["CLIENT_REQUEST_TIMEOUT"])

    def detach(self):
        self.ready = False
        if self.process and self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()


def attach_ide_proxy(client, port, state_directory, owner):
    state = Path(state_directory)
    state.mkdir(parents=True, exist_ok=True)
    path = state / ("qoder-ide-" + client.info["edition"] + "-proxy-rollback.json")
    if path.exists():
        recorded = json.loads(path.read_text(encoding="utf-8"))
        if recorded.get("path") != client.info["path"] or recorded.get("owner") != owner:
            raise ValueError("这个客户端仍有其他窗口的代理记录，未覆盖；请先处理原窗口或遗留记录。")
    active = client.request("proxy_get")["proxy"]
    previous = dict(active)
    if "@" in previous.get("httpProxy", ""):
        raise ValueError(ERRORS["CLIENT_PROXY_CONTAINS_CREDENTIALS"])
    address = f"http://127.0.0.1:{int(port)}"
    applied = {"proxyMode": "manual", "httpProxy": address, "userProxyPresent": True, "userProxyValue": address}
    if path.exists():
        recorded = json.loads(path.read_text(encoding="utf-8"))
        if recorded.get("path") != client.info["path"]:
            raise ValueError("同区域另一安装目录仍有代理恢复记录，请先恢复那个客户端。")
        if previous == recorded.get("applied"):
            previous = recorded["previous"]
    if "@" in previous.get("httpProxy", "") or "@" in (previous.get("userProxyValue") or ""):
        raise ValueError(ERRORS["CLIENT_PROXY_CONTAINS_CREDENTIALS"])
    recorded = {"previous": previous, "applied": applied, "owner": owner, "path": client.info["path"]}
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(recorded, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    try:
        result = client.request("proxy_set", proxy=applied, expected=active)
        if result.get("proxy") != applied:
            raise ValueError(ERRORS["CLIENT_PROXY_NOT_APPLIED"])
    except Exception:
        restore_ide_proxy(client, state, owner)
        raise
    return applied["httpProxy"]


def retain_ide_proxy(info, state_directory, owner):
    """Release only this window's journal without requiring a running IDE or changing its proxy."""
    path = Path(state_directory) / ("qoder-ide-" + info["edition"] + "-proxy-rollback.json")
    if not path.exists():
        return "Qoder IDE 代理设置保持现状，无活动恢复记录。"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    if recorded.get("owner") != owner or recorded.get("path") != info["path"]:
        return "Qoder IDE 代理设置保持现状；其他窗口的记录已保留。"
    archive = path.parent / "archive" / ("kept-proxy-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    archive.mkdir(parents=True, mode=0o700)
    path.rename(archive / path.name)
    return "Qoder IDE 代理设置已保留；本窗口恢复记录已归档。"


def restore_ide_proxy(client, state_directory, owner):
    path = Path(state_directory) / ("qoder-ide-" + client.info["edition"] + "-proxy-rollback.json")
    if not path.exists():
        return "无需恢复"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    if recorded.get("owner") != owner or recorded.get("path") != client.info["path"]:
        return "其他窗口正在管理客户端，保留其设置"
    previous = recorded["previous"]
    if "@" in previous.get("httpProxy", "") or "@" in (previous.get("userProxyValue") or ""):
        raise ValueError(ERRORS["CLIENT_PROXY_CONTAINS_CREDENTIALS"])
    result = client.request("proxy_restore", proxy=previous, expected=recorded["applied"])
    if not result.get("changed_by_user") and not result.get("restored") and result.get("proxy") != previous:
        raise ValueError("Qoder IDE 原代理尚未恢复，请保持转发运行并重试。")
    path.unlink()
    return "已保留用户后来设置的代理" if result.get("changed_by_user") else "Qoder IDE 原代理已恢复"
