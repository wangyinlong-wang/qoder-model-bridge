"""Real local TLS proxy transport. No external model calls or account credentials."""
import contextlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import launcher

INTERNAL_KEY = "mock-internal-key-not-a-real-credential"
CLIENT_KEY = "mock-client-key-must-not-cross-domains"
BODY_MARKER = "mock-private-prompt-not-for-logs"


class MockGateway(BaseHTTPRequestHandler):
    records = []
    response_status = 200

    def log_message(self, *_):
        pass

    def handle_request(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.records.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})
        if self.response_status != 200:
            self.send_response(self.response_status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"mock-upstream-error"}')
            return
        self.send_response(200)
        payload = json.loads(body) if body else None
        if payload and payload.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"tool_calls":[{"id":"call_mock","function":{"name":"read_file","arguments":"{}"}}]}}]}\n\n')
            self.wfile.flush()
            time.sleep(0.8)
            self.wfile.write(b'data: [DONE]\n\n')
        else:
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"object": "list", "data": [{"id": "internal-model"}]} if self.command == "GET" else {"choices": [{"message": {"role": "assistant", "content": "MOCK_OK"}}]}
            self.wfile.write(json.dumps(response).encode())

    do_GET = handle_request
    do_POST = handle_request


def available_port():
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ProxyTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="qoder-proxy-test-")
        cls.gateway = ThreadingHTTPServer(("127.0.0.1", 20128), MockGateway)
        threading.Thread(target=cls.gateway.serve_forever, daemon=True).start()
        cls.port = available_port()
        config = {"base_url": "http://127.0.0.1:20128/v1", "listen_port": cls.port, "no_auth": False, "model_map": {"source-id": "internal-model"}}
        previous = os.environ.get("QODER_PROXY_STATE_DIR")
        os.environ["QODER_PROXY_STATE_DIR"] = cls.temp.name
        try:
            cls.process, cls.ca = launcher.start_proxy(config, INTERNAL_KEY)
        finally:
            if previous is None:
                os.environ.pop("QODER_PROXY_STATE_DIR", None)
            else:
                os.environ["QODER_PROXY_STATE_DIR"] = previous
        cls.lines, cls.events = [], queue.Queue()
        def read():
            for line in cls.process.stdout:
                cls.lines.append(line)
                cls.events.put(line)
        cls.reader = threading.Thread(target=read, daemon=True)
        cls.reader.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                line = cls.events.get(timeout=0.2)
                if json.loads(line).get("event") == "ready":
                    break
            except queue.Empty:
                if cls.process.poll() is not None:
                    raise RuntimeError("Proxy exited: " + "".join(cls.lines))
            except ValueError:
                continue
        else:
            raise RuntimeError("Proxy did not become ready")
        cls.tls = ssl.create_default_context(cafile=str(cls.ca))

    @classmethod
    def tearDownClass(cls):
        launcher.stop_proxy(cls.process)
        cls.reader.join(timeout=2)
        cls.process.stdout.close()
        cls.gateway.shutdown()
        cls.gateway.server_close()
        cls.temp.cleanup()

    def setUp(self):
        MockGateway.response_status = 200
        MockGateway.records.clear()

    def connection(self):
        connection = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.tls, timeout=10)
        connection.set_tunnel("openrouter.ai", 443)
        return connection

    def request(self, method, path, body=None, headers=None):
        with contextlib.closing(self.connection()) as c:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, dict(r.headers), r.read()

    def test_01_https_to_http_port_and_path_and_key_isolation(self):
        status, headers, body = self.request("GET", "/api/v1/models", headers={"Authorization": "Bearer " + CLIENT_KEY, "Cookie": "private-cookie", "X-Api-Key": "client-side-secret"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Qoder-Proxy"], "internal")
        self.assertEqual(json.loads(body)["data"][0]["id"], "internal-model")
        observed = MockGateway.records[-1]
        self.assertEqual(observed["path"], "/v1/models")
        self.assertEqual(observed["headers"]["Host"], "127.0.0.1:20128")
        self.assertEqual(observed["headers"]["Authorization"], "Bearer " + INTERNAL_KEY)
        self.assertNotIn("Cookie", observed["headers"])
        self.assertNotIn("X-Api-Key", observed["headers"])
        self.assertNotIn(CLIENT_KEY, str(observed))

    def test_02_model_mapping_and_tool_history_preserved(self):
        payload = {"model": "source-id", "messages": [{"role": "user", "content": BODY_MARKER}, {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]}, {"role": "tool", "tool_call_id": "call_1", "content": "fixture"}], "tools": [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}], "tool_choice": "auto", "stream": False}
        status, _, _ = self.request("POST", "/api/v1/chat/completions", json.dumps(payload), {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        observed = MockGateway.records[-1]
        self.assertEqual(observed["path"], "/v1/chat/completions")
        forwarded = json.loads(observed["body"])
        self.assertEqual(forwarded["model"], "internal-model")
        del forwarded["model"]
        del payload["model"]
        self.assertEqual(forwarded, payload)

    def test_03_sse_is_incremental_and_tool_delta_is_unchanged(self):
        payload = json.dumps({"model": "source-id", "messages": [{"role": "user", "content": BODY_MARKER}], "stream": True})
        with contextlib.closing(self.connection()) as c:
            start = time.monotonic()
            c.request("POST", "/api/v1/chat/completions", payload, {"Content-Type": "application/json"})
            r = c.getresponse()
            first = r.readline()
            first_time = time.monotonic() - start
            self.assertIn(b'"tool_calls"', first)
            self.assertLess(first_time, 0.6, "First SSE event was buffered until completion")
            remainder = r.read()
            self.assertEqual((first + remainder).count(b"[DONE]"), 1)
            self.assertGreater(time.monotonic() - start, 0.7)

    def test_04_unknown_path_and_model_fail_locally(self):
        for path in ["/api/v1/key", "/api/v1/auth/key", "/api/v2/byok/check"]:
            status, headers, _ = self.request("GET", path)
            self.assertEqual(status, 501)
            self.assertEqual(headers["X-Qoder-Proxy"], "local-reject")
        status, _, body = self.request("POST", "/api/v1/chat/completions", b'{"model":"unconfirmed-source","messages":[]}', {"Content-Type": "application/json"})
        self.assertEqual(status, 409)
        self.assertIn(b"explicit_model_mapping_required", body)
        self.assertEqual(MockGateway.records, [])

    def test_05_source_authorization_is_optional_but_internal_key_is_used(self):
        status, _, _ = self.request("GET", "/api/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(MockGateway.records[-1]["headers"]["Authorization"], "Bearer " + INTERNAL_KEY)

    def test_06_upstream_error_is_not_replaced_with_success(self):
        MockGateway.response_status = 401
        status, headers, body = self.request("GET", "/api/v1/models")
        self.assertEqual(status, 401)
        self.assertEqual(body, b'{"error":"mock-upstream-error"}')
        self.assertEqual(headers["X-Qoder-Proxy"], "internal")

    def test_07_invalid_json_rejected_without_upstream(self):
        status, _, _ = self.request("POST", "/api/v1/chat/completions", b"not-json", {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertEqual(MockGateway.records, [])

    def test_08_logs_have_no_key_cookie_prompt_or_response_body(self):
        logs = "".join(self.lines)
        for forbidden in [INTERNAL_KEY, CLIENT_KEY, BODY_MARKER, "private-cookie", "MOCK_OK", "mock-upstream-error"]:
            self.assertNotIn(forbidden, logs)

    def test_09_no_auth_proxy_drops_even_a_supplied_environment_key(self):
        port = available_port()
        config = {"base_url": "http://127.0.0.1:20128/v1", "listen_port": port, "no_auth": True, "model_map": {}}
        with tempfile.TemporaryDirectory(prefix="qoder-no-auth-") as state:
            with patch.dict(os.environ, {"QODER_PROXY_STATE_DIR": state}):
                process, ca = launcher.start_proxy(config, INTERNAL_KEY)
            events = queue.Queue()
            def read():
                for line in process.stdout:
                    events.put(line)
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    line = events.get(timeout=3)
                    try:
                        if json.loads(line).get("event") == "ready":
                            break
                    except ValueError:
                        continue
                else:
                    self.fail("No-auth proxy did not become ready")
                context = ssl.create_default_context(cafile=str(ca))
                with contextlib.closing(http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=10)) as connection:
                    connection.set_tunnel("openrouter.ai", 443)
                    connection.request("GET", "/api/v1/models", headers={"Authorization": "Bearer " + CLIENT_KEY})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                self.assertNotIn("Authorization", MockGateway.records[-1]["headers"])
            finally:
                launcher.stop_proxy(process)
                reader.join(timeout=2)
                process.stdout.close()


class ModelDiscoveryTests(unittest.TestCase):
    def test_redirect_does_not_forward_credentials_to_another_target(self):
        class Target(BaseHTTPRequestHandler):
            received = []
            def do_GET(self):
                self.received.append(dict(self.headers))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"should-not-be-reached"}]}')
            def log_message(self, *_):
                pass
        target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_port}/v1/models")
                self.end_headers()
            def log_message(self, *_):
                pass
        redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=target.serve_forever, daemon=True).start()
        threading.Thread(target=redirect.serve_forever, daemon=True).start()
        try:
            with self.assertRaisesRegex(ValueError, "HTTP 302"):
                launcher.fetch_models(f"http://127.0.0.1:{redirect.server_port}/v1", INTERNAL_KEY)
            self.assertEqual(Target.received, [])
        finally:
            redirect.shutdown()
            target.shutdown()
            redirect.server_close()
            target.server_close()

    def test_no_auth_never_sends_even_an_existing_key(self):
        MockGateway.records.clear()
        MockGateway.response_status = 200
        gateway = ThreadingHTTPServer(("127.0.0.1", 0), MockGateway)
        threading.Thread(target=gateway.serve_forever, daemon=True).start()
        try:
            launcher.fetch_models(f"http://127.0.0.1:{gateway.server_port}/v1", INTERNAL_KEY, no_auth=True)
            self.assertNotIn("Authorization", MockGateway.records[-1]["headers"])
        finally:
            gateway.shutdown()
            gateway.server_close()

    def test_gui_changed_base_requires_new_key_before_network(self):
        app = object.__new__(launcher.App)
        app.process = None
        app.base = SimpleNamespace(get=lambda: "http://127.0.0.1:20129/v1")
        app.password = SimpleNamespace(get=lambda: "")
        app.no_auth = SimpleNamespace(get=lambda: False)
        app.key, app.auth_base, app.auth_no_auth = INTERNAL_KEY, "http://127.0.0.1:20128/v1", False
        errors = []
        app.error = errors.append
        with patch.object(launcher, "fetch_models") as network:
            app.fetch()
            network.assert_not_called()
        self.assertEqual(len(errors), 1)
        self.assertIn("不会复用", errors[0])

    def test_reference_name_is_available_without_local_qoder(self):
        name, source = launcher.display_name("qd/kmodel", {}, {})
        self.assertEqual(name, "Kimi-K2.8-Preview")
        self.assertIn("2026-10-16", source)

    def test_cn_auto_display_does_not_change_request_id(self):
        name, description = launcher.display_name("cn-auto", {}, {})
        self.assertEqual(name, "auto")
        self.assertIn("智能路由", description)
        with self.assertRaises(ValueError):
            launcher.validate_mapping({"some-source": "cn-auto"}, {"qd/gfmodel": {}})

    def test_catalog_name_is_only_a_reference(self):
        name, source = launcher.display_name("qd/gfmodel", {"capabilities": {"vision": False}}, {"gfmodel": "GLM-5.3-Flash"})
        self.assertEqual(name, "GLM-5.3-Flash")
        self.assertIn("未核实", source)
        self.assertEqual(launcher.display_name("unknown", {}, {})[0], "unknown")

    def test_url_and_mapping_validation(self):
        self.assertEqual(launcher.normalize_base("http://127.0.0.1:20128/v1/"), "http://127.0.0.1:20128/v1")
        for url in ["https://user:password@host/v1", "https://openrouter.ai/api/v1", "file:///etc/hosts"]:
            with self.assertRaises(ValueError):
                launcher.normalize_base(url)
        with self.assertRaises(ValueError):
            launcher.validate_mapping({"x": "missing-model"}, {"actual-model": {}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
