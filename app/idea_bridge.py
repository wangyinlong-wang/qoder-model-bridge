"""Client for the authenticated, loopback-only IDEA bridge. Never accepts an upstream API key."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


def discovery_directory():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/QoderInternalBridge"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "QoderInternalBridge"
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "QoderInternalBridge"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class BridgeEndpoint:
    port: int
    instance_id: str
    title: str
    token: str = field(repr=False)

    def __post_init__(self):
        if type(self.port) is not int or not 1 <= self.port <= 65535 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", self.token):
            raise ValueError("无效的本机桥接连接信息。")

    def request(self, path, body=None, timeout=8):
        if path not in ("/v1/health", "/v1/imports") and not re.fullmatch(r"/v1/imports/[0-9a-f-]{36}", path):
            raise ValueError("无效的桥接请求路径。")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode()
        request = Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers)
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
                value = json.loads(response.read(256 * 1024))
        except HTTPError as error:
            if error.code == 409:
                raise ValueError("IDEA 正在处理另一次导入，请先完成或取消该窗口。") from None
            if error.code in (401, 403):
                raise ValueError("桥接会话已失效或被拒绝，请刷新 IDEA 桥接列表。") from None
            raise ValueError(f"IDEA 桥接返回 HTTP {error.code}，未自动重试导入。") from None
        except (OSError, URLError, ValueError):
            raise ValueError("无法读取 IDEA 桥接响应；请检查 IDEA 中的桥接状态。") from None
        if not isinstance(value, dict):
            raise ValueError("IDEA 桥接响应格式无效。")
        return value


def discover_bridges(directory=None):
    directory = Path(directory) if directory is not None else discovery_directory()
    endpoints = []
    for path in sorted(directory.glob("session-*.json")):
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
                continue
            if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
            port, token = value.get("port"), value.get("token", "")
            instance = value.get("instance_id", "")
            if (value.get("service") != "qoder-internal-bridge" or value.get("protocol") != 1
                    or value.get("host") != "127.0.0.1" or type(port) is not int or not 1 <= port <= 65535
                    or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
                    or not isinstance(instance, str) or not re.fullmatch(r"[0-9a-f-]{36}", instance)):
                continue
            title = f"{value.get('ide_name', 'IDEA')} {value.get('ide_version', '')} / PID {value.get('pid', '?')}"
            endpoint = BridgeEndpoint(port, instance, title, token)
            health = endpoint.request("/v1/health", timeout=1)
            if health.get("service") == "qoder-internal-bridge" and health.get("protocol") == 1 and health.get("instance_id") == instance:
                endpoints.append(endpoint)
        except (OSError, ValueError, TypeError):
            continue
    return endpoints
