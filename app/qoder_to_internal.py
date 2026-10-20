"""Route selected Qoder BYOK/chat requests to the user's internal gateway."""
import json
import os
import re
from urllib.parse import parse_qs, urlsplit

from mitmproxy import http
from qoder_protocol import QoderSSE, chat_request, check_result, complete_response, decode_body, mapped_model, stream_error

QODER_HOST = re.compile(r"^(?:center|api\d+)\.qoder\.sh$|^gateway\.qoder\.com\.cn$")


def report(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


class InternalProxy:
    def __init__(self):
        config = json.loads(os.environ.get("QODER_PROXY_SETTINGS", "{}"))
        self.base = urlsplit(config.get("base_url", "http://127.0.0.1:3000/v1"))
        if (self.base.scheme not in ("http", "https") or not self.base.hostname
                or self.base.username or self.base.password or self.base.query
                or self.base.fragment or self.base.hostname.lower().rstrip(".") == "openrouter.ai"):
            raise ValueError("Invalid internal base URL")
        self.port = self.base.port or (443 if self.base.scheme == "https" else 80)
        self.prefix = self.base.path.rstrip("/")
        self.key = os.environ.pop("INTERNAL_API_KEY", "").strip()
        self.no_auth = config.get("no_auth", False) is True
        if self.no_auth:
            self.key = ""
        if not self.key and not self.no_auth:
            raise ValueError("Internal API key is required")
        self.mapping = config.get("model_map", {})
        self.qoder_enabled = config.get("qoder_enabled", False) is True
        self.allow_generation = config.get("allow_generation", True) is True
        if not isinstance(self.mapping, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and k and v
            for k, v in self.mapping.items()
        ):
            raise ValueError("Invalid explicit model mapping")

    def running(self):
        report("ready", chat_enabled=bool(self.mapping), qoder_enabled=self.qoder_enabled)

    def safe_model(self, model):
        if not isinstance(model, str):
            return "<invalid>"
        if model == self.key or model.startswith("sk-"):
            return "<redacted>"
        return "".join(c for c in model[:120] if c.isprintable())

    def reject(self, flow, status, code):
        flow.response = http.Response.make(
            status,
            json.dumps({"error": {"type": "local_proxy_error", "code": code}}).encode(),
            {"Content-Type": "application/json", "X-Qoder-Proxy": "local-reject"},
        )
        report("reject", method=flow.request.method,
               path=flow.request.path.split("?", 1)[0][:160], status=status, code=code,
               model=self.safe_model(flow.metadata.get("source_model", "")))

    def request(self, flow):
        request = flow.request
        if self.qoder_enabled and QODER_HOST.fullmatch(request.host.lower()):
            self.qoder_request(flow)
            return
        if request.host.lower().rstrip(".") != "openrouter.ai":
            return
        flow.metadata["qoder_internal"] = True
        path = request.path.split("?", 1)[0]
        flow.metadata["source_path"] = path
        incoming_auth = "authorization" in request.headers
        if request.scheme != "https" or request.port != 443 or "?" in request.path:
            self.reject(flow, 400, "unsupported_origin_or_query")
            return
        if request.method == "GET" and path == "/api/v1/models":
            target_path, body = "/models", b""
        elif request.method == "POST" and path == "/api/v1/chat/completions":
            if not self.allow_generation:
                self.reject(flow, 409, "generation_disabled_for_diagnosis")
                return
            try:
                payload = json.loads(request.content or b"")
            except (ValueError, UnicodeError):
                self.reject(flow, 400, "invalid_json")
                return
            if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
                self.reject(flow, 400, "model_required")
                return
            source = payload["model"]
            flow.metadata["source_model"] = source
            if source not in self.mapping:
                self.reject(flow, 409, "explicit_model_mapping_required")
                return
            payload["model"] = self.mapping[source]
            flow.metadata["target_model"] = payload["model"]
            target_path = "/chat/completions"
            body = json.dumps(payload, ensure_ascii=False).encode()
        else:
            self.reject(flow, 501, "unsupported_openrouter_path")
            return

        self.forward(flow, target_path, body, incoming_auth)

    def forward(self, flow, target_path, body, incoming_auth=False):
        request = flow.request
        path = flow.metadata["source_path"]
        # Neither Qoder account signatures, the source key nor cookies cross domains.
        request.headers = http.Headers()
        request.scheme = self.base.scheme
        request.host = self.base.hostname
        request.port = self.port
        request.path = self.prefix + target_path
        request.host_header = self.base.netloc
        request.headers["Accept"] = "application/json, text/event-stream"
        if self.key:
            request.headers["Authorization"] = "Bearer " + self.key
        if request.method == "POST":
            request.headers["Content-Type"] = "application/json"
        request.content = body
        report("request", method=request.method, path=path,
               target=f"{self.base.scheme}://{self.base.netloc}{request.path}",
               incoming_auth_present=incoming_auth, internal_auth_present=bool(self.key),
               model=self.safe_model(flow.metadata.get("target_model", "")))

    def native_error(self, flow, code, message):
        flow.metadata["local_native_response"] = True
        is_check = flow.metadata.get("native_kind") == "check"
        content = check_result(False, code, message) if is_check else stream_error(code, message)
        flow.response = http.Response.make(200, content, {
            "Content-Type": "application/json" if is_check else "text/event-stream",
            "X-Qoder-Proxy": "local-reject", "Cache-Control": "no-cache",
        })
        report("native_reject", path=flow.metadata.get("source_path"), status=code)

    def qoder_request(self, flow):
        request = flow.request
        parsed = urlsplit(request.url)
        path = parsed.path
        is_check = path == "/algo/api/v2/byok/check"
        is_chat = path.endswith(("/sse/agent_chat_generation", "/sse/agent_chat_ask"))
        if not is_check and not is_chat:
            return
        flow.metadata.update(qoder_internal=True, source_path=path,
                             native_kind="check" if is_check else "chat")
        if request.scheme != "https" or request.port != 443 or request.method != "POST":
            self.native_error(flow, 400, "未支持的 Qoder 请求形式，未转发。")
            return
        try:
            encoded = parse_qs(parsed.query).get("Encode", ["0"])[0] == "1"
            payload = decode_body(request.content or b"", encoded)
            if is_check and payload.get("provider") != "openrouter":
                # Other configured providers keep using Qoder's original validation.
                flow.metadata.pop("qoder_internal", None)
                flow.metadata.pop("native_kind", None)
                return
            source, target = mapped_model(payload, self.mapping)
            flow.metadata.update(source_model=source, target_model=target)
            if is_check:
                request.method = "GET"
                self.forward(flow, "/models", b"")
            else:
                if not self.allow_generation:
                    report("native_request_shape", path=path, model=self.safe_model(source),
                           fields=sorted(payload), model_fields=sorted(payload.get("model_config", {})))
                    self.native_error(flow, 409, "本机已收到 Qoder 聊天请求；当前诊断模式尚未启用真实生成。")
                    return
                converted = chat_request(payload, target)
                flow.metadata["native_stream"] = converted["stream"]
                flow.metadata["native_request_id"] = payload.get("request_id", "")
                self.forward(flow, "/chat/completions", json.dumps(converted, ensure_ascii=False).encode())
        except (ValueError, UnicodeError, TypeError) as error:
            # These are protocol-only validation messages, never upstream bodies or secrets.
            self.native_error(flow, 400, str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError)
                              else "无法解析 Qoder 请求，未向上游发送。")

    def responseheaders(self, flow):
        if not flow.metadata.get("qoder_internal") or not flow.response:
            return
        if flow.response.headers.get("X-Qoder-Proxy") == "local-reject":
            return
        kind = flow.metadata.get("native_kind")
        if kind == "check":
            flow.response.stream = False
            return
        flow.response.headers["X-Qoder-Proxy"] = "internal"
        flow.response.headers.pop("set-cookie", None)
        if kind == "chat":
            flow.metadata["internal_status"] = flow.response.status_code
            if flow.metadata.get("native_stream", True) and flow.response.status_code == 200 and "text/event-stream" in flow.response.headers.get("content-type", "").lower():
                flow.response.stream = QoderSSE()
                flow.response.headers.pop("content-length", None)
            else:
                flow.response.stream = False
        elif "text/event-stream" in flow.response.headers.get("content-type", "").lower():
            flow.response.stream = True
        report("response", method=flow.request.method,
               path=flow.metadata.get("source_path", ""), status=flow.response.status_code)

    def response(self, flow):
        kind = flow.metadata.get("native_kind")
        if not kind or flow.metadata.get("local_native_response") or not flow.response:
            return
        if kind == "check":
            status = flow.response.status_code
            success = False
            code = str(status)
            message = f"内网网关模型列表返回 HTTP {status}。"
            if status == 200:
                try:
                    data = json.loads(flow.response.content or b"").get("data", [])
                    success = any(isinstance(item, dict) and item.get("id") == flow.metadata["target_model"] for item in data)
                    code = "" if success else "model_not_found"
                    message = "" if success else "映射目标不在内网网关当前模型列表中。"
                except (ValueError, TypeError, AttributeError):
                    code, message = "invalid_models", "内网网关未返回有效模型列表。"
            flow.response = http.Response.make(200, check_result(success, code, message), {
                "Content-Type": "application/json", "X-Qoder-Proxy": "internal-validation",
                "Cache-Control": "no-store",
            })
            report("native_validation", model=self.safe_model(flow.metadata.get("target_model", "")),
                   success=success, checked="gateway_authentication_and_model_exists", upstream_status=status)
        elif not flow.response.stream:
            status = flow.metadata.get("internal_status", flow.response.status_code)
            effective = status if status != 200 else 502
            content = stream_error(effective, f"内网生成接口返回 HTTP {status} 或不符合要求的响应。")
            if status == 200 and not flow.metadata.get("native_stream", True):
                try:
                    content = complete_response(flow.response.content or b"", flow.metadata.get("native_request_id", ""))
                except (ValueError, TypeError, UnicodeError):
                    content = stream_error(502, "内网模型没有返回有效的完整聊天结果。")
            flow.response = http.Response.make(200, content, {
                "Content-Type": "text/event-stream", "X-Qoder-Proxy": "internal",
                "Cache-Control": "no-cache",
            })

    def error(self, flow):
        if flow.metadata.get("qoder_internal"):
            report("transport_error", method=flow.request.method,
                   path=flow.metadata.get("source_path", ""), code="upstream_connection_failed")


addons = [InternalProxy()]
