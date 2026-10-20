"""Use Qoder's existing loopback JSON-RPC configuration interface."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import time
import uuid


def qoder_port():
    info = Path.home() / ".qoder/shared_client/.info.json"
    value = json.loads(info.read_text(encoding="utf-8"))
    port = value.get("websocketPort")
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("Qoder 没有提供有效的本机配置端口，请先打开 IDEA 中的 Qoder。")
    return port


def rpc(method, params=None, *, port=None, timeout=8):
    """No initialize, workspace, login, or indexing calls are made."""
    request_id = "local-gateway-" + uuid.uuid4().hex
    port = port or qoder_port()
    deadline = time.monotonic() + timeout
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
        buffer = bytearray()

        def read(count):
            while len(buffer) < count:
                connection.settimeout(max(0.05, deadline - time.monotonic()))
                part = connection.recv(max(4096, count - len(buffer)))
                if not part:
                    raise ConnectionError("Qoder 本机配置连接已关闭。")
                buffer.extend(part)
            result = bytes(buffer[:count])
            del buffer[:count]
            return result

        def send_frame(payload, opcode=1):
            mask = os.urandom(4)
            length = len(payload)
            prefix = bytes([0x80 | opcode])
            if length < 126:
                prefix += bytes([0x80 | length])
            elif length <= 65535:
                prefix += b"\xfe" + struct.pack("!H", length)
            else:
                prefix += b"\xff" + struct.pack("!Q", length)
            connection.sendall(prefix + mask + bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload)))

        def send_json(value):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            send_frame(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)

        nonce = base64.b64encode(os.urandom(16)).decode()
        handshake = (f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                     "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Key: {nonce}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        connection.sendall(handshake.encode("ascii"))
        header = bytearray()
        while not header.endswith(b"\r\n\r\n"):
            header += read(1)
            if len(header) > 16384:
                raise ConnectionError("Qoder 本机握手无效。")
        lines = header.decode("latin-1").split("\r\n")
        headers = {k.lower(): v.strip() for line in lines[1:] if ":" in line for k, v in [line.split(":", 1)]}
        expected = base64.b64encode(hashlib.sha1((nonce + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if " 101 " not in lines[0] or headers.get("sec-websocket-accept") != expected:
            raise ConnectionError("Qoder 本机配置接口没有接受连接。")
        send_json({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        message = bytearray()
        while time.monotonic() < deadline:
            first, second = read(2)
            opcode = first & 15
            length = second & 127
            if length == 126:
                length = struct.unpack("!H", read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", read(8))[0]
            if length > 8 * 1024 * 1024:
                raise ConnectionError("Qoder 配置响应过大。")
            mask = read(4) if second & 128 else None
            payload = read(length)
            if mask:
                payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
            if opcode == 9:
                send_frame(payload, 10)
                continue
            if opcode == 8:
                raise ConnectionError("Qoder 本机配置接口关闭了连接。")
            if opcode not in (0, 1, 2):
                continue
            message.extend(payload)
            if not first & 0x80:
                continue
            raw = bytes(message)
            message.clear()
            if raw.startswith(b"Content-Length:"):
                raw = raw.split(b"\r\n\r\n", 1)[1]
            response = json.loads(raw)
            if response.get("id") == request_id and "method" not in response:
                send_frame(struct.pack("!H", 1000), 8)
                if response.get("error"):
                    raise RuntimeError(f"Qoder 配置接口返回错误 {response['error'].get('code')}。")
                return response.get("result")
            if "id" in response and "method" in response:
                if response["method"] == "config/changeEndpoint":
                    send_json({"jsonrpc": "2.0", "id": response["id"], "result": None})
                else:
                    send_json({"jsonrpc": "2.0", "id": response["id"], "error": {"code": -32601, "message": "Not supported by the local configuration client"}})
        raise TimeoutError("Qoder 本机配置接口响应超时。")


def openrouter_models():
    value = rpc("model/getByokConfig")
    result = {}
    for provider in value.get("providers", []):
        if not isinstance(provider, dict) or provider.get("key") != "openrouter":
            continue
        models = list(provider.get("models") or [])
        for kind in provider.get("types") or []:
            if isinstance(kind, dict) and kind.get("key") == "pg":
                models.extend(kind.get("models") or [])
        for model in models:
            if isinstance(model, dict) and isinstance(model.get("key"), str):
                label = model.get("display_name")
                result[model["key"]] = label if isinstance(label, str) else model["key"]
    return result


def attach_proxy(port, state_directory, owner=None):
    """Change only the two non-secret Qoder proxy fields; retain a recovery record."""
    state = Path(state_directory)
    state.mkdir(parents=True, exist_ok=True)
    path = state / "qoder-proxy-rollback.json"
    current = rpc("config/getGlobal")
    previous = {key: current[key] for key in ("proxyMode", "httpProxy")}
    if "@" in previous["httpProxy"]:
        raise ValueError("Qoder 原代理地址包含凭据，请自行在 Qoder 设置本机代理，本工具不会改写该配置。")
    applied = {"proxyMode": "manual", "httpProxy": f"http://127.0.0.1:{int(port)}"}
    if path.exists():
        recorded = json.loads(path.read_text(encoding="utf-8"))
        if previous == recorded.get("applied"):
            previous = recorded["previous"]
    record = {"previous": previous, "applied": applied, "owner": owner}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = rpc("config/updateBatch", {"updates": applied})
    after = rpc("config/getGlobal")
    if not result.get("success") or any(after.get(k) != v for k, v in applied.items()):
        restore_proxy(state, owner=owner)
        raise RuntimeError("Qoder 没有确认代理配置，已尝试恢复原设置。")
    return applied["httpProxy"]


def restore_proxy(state_directory, owner=None):
    path = Path(state_directory) / "qoder-proxy-rollback.json"
    if not path.exists():
        return "无需恢复"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    if owner is not None and recorded.get("owner") != owner:
        return "其他启动窗口正在管理 Qoder，未覆盖其设置"
    if "@" in recorded.get("previous", {}).get("httpProxy", ""):
        raise ValueError("恢复记录含凭据，必须由用户自行恢复。")
    current = rpc("config/getGlobal")
    active = {key: current[key] for key in ("proxyMode", "httpProxy")}
    if active != recorded["applied"]:
        path.unlink()
        return "Qoder 代理已被其他操作修改，保留该设置"
    result = rpc("config/updateBatch", {"updates": recorded["previous"]})
    current = rpc("config/getGlobal")
    if not result.get("success") or any(current.get(k) != v for k, v in recorded["previous"].items()):
        raise RuntimeError("Qoder 原代理尚未恢复，请保持本地代理运行并重试。")
    path.unlink()
    return "Qoder 原代理已恢复"
