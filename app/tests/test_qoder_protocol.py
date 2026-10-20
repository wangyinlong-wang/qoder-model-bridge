"""Qoder-native conversion checks with a real local TLS proxy and mock upstream."""
import base64
import contextlib
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import ssl
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import launcher
from qoder_protocol import DECODE_TABLE, QODER, STANDARD, QoderSSE, chat_request, complete_response, decode_body, mapped_model
from test_proxy import MockGateway, INTERNAL_KEY, CLIENT_KEY, BODY_MARKER, available_port


def encoded(value):
    data = base64.b64encode(json.dumps(value, ensure_ascii=False).encode()).decode()
    n = len(data) // 3
    data = data[-n:] + data[n:-n] + data[:n] if n else data
    return data.translate(str.maketrans(STANDARD, QODER)).encode()


class NativeProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = tempfile.TemporaryDirectory(prefix="qoder-native-")
        cls.gateway = ThreadingHTTPServer(("127.0.0.1", 0), MockGateway)
        threading.Thread(target=cls.gateway.serve_forever, daemon=True).start()
        cls.port = available_port()
        config = {"base_url": f"http://127.0.0.1:{cls.gateway.server_port}/v1", "listen_port": cls.port,
                  "qoder_enabled": True, "model_map": {"glm-5.3-flash": "internal-model"}}
        with patch.dict(os.environ, {"QODER_PROXY_STATE_DIR": cls.state.name}):
            cls.process, ca = launcher.start_proxy(config, INTERNAL_KEY)
        cls.lines, cls.ready = [], threading.Event()
        def consume():
            for line in cls.process.stdout:
                cls.lines.append(line)
                try:
                    if json.loads(line).get("event") == "ready":
                        cls.ready.set()
                except ValueError:
                    pass
        cls.reader = threading.Thread(target=consume, daemon=True)
        cls.reader.start()
        if not cls.ready.wait(12):
            raise RuntimeError("Native proxy did not start: " + "".join(cls.lines))
        cls.context = ssl.create_default_context(cafile=str(ca))

    @classmethod
    def tearDownClass(cls):
        launcher.stop_proxy(cls.process)
        cls.reader.join(timeout=2)
        cls.process.stdout.close()
        cls.gateway.shutdown()
        cls.gateway.server_close()
        cls.state.cleanup()

    def setUp(self):
        MockGateway.records.clear()
        MockGateway.response_status = 200

    def connection(self, host="center.qoder.sh"):
        c = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.context, timeout=5)
        c.set_tunnel(host, 443)
        return c

    def check(self, model="glm-5.3-flash", encode=False):
        value = {"provider": "openrouter", "type": "pg", "model": model, "parameters": {"api_key": CLIENT_KEY}}
        body = encoded(value) if encode else json.dumps(value).encode()
        with contextlib.closing(self.connection()) as c:
            c.request("POST", "/algo/api/v2/byok/check" + ("?Encode=1" if encode else ""), body,
                      {"Content-Type": "application/json", "Cosy-Auth": "mock-account-signature", "Cookie": "mock-account-cookie"})
            r = c.getresponse()
            return r.status, json.loads(r.read())

    def test_validation_requires_actual_upstream_auth_and_model(self):
        status, value = self.check()
        self.assertEqual(status, 200)
        self.assertIs(value["success"], True)
        sent = MockGateway.records[-1]
        self.assertEqual((sent["method"], sent["path"], sent["body"]), ("GET", "/v1/models", b""))
        self.assertEqual(sent["headers"]["Authorization"], "Bearer " + INTERNAL_KEY)
        self.assertNotIn("Cosy-Auth", sent["headers"])
        self.assertNotIn("Cookie", sent["headers"])
        self.assertNotIn(CLIENT_KEY, str(sent))
        MockGateway.response_status = 401
        _, value = self.check()
        self.assertIs(value["success"], False)
        self.assertEqual(value["errorCode"], "401")

    def test_unknown_model_cannot_validate_or_leave_proxy(self):
        _, value = self.check("unconfigured")
        self.assertIs(value["success"], False)
        self.assertEqual(MockGateway.records, [])

    def test_encoded_check_body(self):
        self.assertTrue(self.check(encode=True)[1]["success"])

    def test_chat_rewrites_native_schema_and_streams_envelopes(self):
        value = {
            "model_config": {"key": "custom_model", "provider": "openrouter", "model": "glm-5.3-flash", "parameters": {"api_key": CLIENT_KEY}},
            "system": "mock-system-instruction", "messages": [{"role": "user", "content": BODY_MARKER}],
            "tools": [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}],
            "parameters": {"max_tokens": 16}, "business": {"private_metadata": "must-not-forward"},
        }
        with contextlib.closing(self.connection("api3.qoder.sh")) as c:
            before = time.monotonic()
            c.request("POST", "/algo/api/v2/service/pro/sse/agent_chat_generation?FetchKeys=llm_model_result&Encode=1",
                      encoded(value), {"Content-Type": "application/json", "Cosy-Sign": "mock-signature"})
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            first = r.readline()
            self.assertLess(time.monotonic() - before, 0.6)
            first_value = json.loads(first.decode()[5:])
            self.assertEqual(first_value["statusCodeValue"], 200)
            inner = json.loads(first_value["body"])
            self.assertEqual(inner["choices"][0]["delta"]["tool_calls"][0]["id"], "call_mock")
            self.assertEqual((first + r.read()).count(b"[DONE]"), 1)
        sent = MockGateway.records[-1]
        request = json.loads(sent["body"])
        self.assertEqual(request["model"], "internal-model")
        self.assertEqual(request["messages"][0], {"role": "system", "content": "mock-system-instruction"})
        self.assertEqual(request["tools"], value["tools"])
        self.assertEqual(request["max_tokens"], 16)
        self.assertNotIn("model_config", request)
        self.assertNotIn("business", request)
        self.assertNotIn(CLIENT_KEY, sent["body"].decode())
        self.assertNotIn("Cosy-Sign", sent["headers"])

    def test_upstream_generation_error_remains_error(self):
        MockGateway.response_status = 429
        body = {"model_config": {"model": "glm-5.3-flash"}, "messages": [{"role": "user", "content": "local fixture"}]}
        with contextlib.closing(self.connection("api3.qoder.sh")) as c:
            c.request("POST", "/algo/api/v2/service/pro/sse/agent_chat_generation", json.dumps(body), {"Content-Type": "application/json"})
            r = c.getresponse()
            event = json.loads(r.read().split(b"\n\n")[0][5:])
            self.assertEqual(event["statusCodeValue"], 429)

    def test_prompt_enhancement_preserves_nonstream_request_and_complete_output(self):
        value = {"request_id": "fixture-enhance-request", "stream": False,
                 "model_config": {"model": "glm-5.3-flash"}, "parameters": None,
                 "messages": [{"role": "system", "content": "improve the input"},
                              {"role": "user", "content": "", "contents": [{"type": "text", "text": "fixture input"}]}]}
        with contextlib.closing(self.connection("api3.qoder.sh")) as c:
            c.request("POST", "/algo/api/v2/service/pro/sse/agent_chat_generation?AgentId=agent_enhance&FetchKeys=llm_model_result&Encode=1",
                      encoded(value), {"Content-Type": "application/json"})
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            self.assertIn("text/event-stream", r.getheader("Content-Type"))
            wire = r.read()
        first = json.loads(wire.split(b"\n\n")[0][5:])
        result = json.loads(first["body"])
        self.assertEqual(result["choices"][0]["delta"]["content"], "MOCK_OK")
        self.assertEqual(result["id"], "fixture-enhance-request")
        self.assertTrue(wire.endswith(b"event: finish\ndata: [DONE]\n\n"))
        request = json.loads(MockGateway.records[-1]["body"])
        self.assertIs(request["stream"], False)
        self.assertNotIn("stream_options", request)
        self.assertEqual(request["messages"][1]["content"], "fixture input")

    def test_logs_do_not_include_credentials_or_prompt(self):
        log = "".join(self.lines)
        for value in (CLIENT_KEY, INTERNAL_KEY, BODY_MARKER, "mock-account-signature", "mock-signature", "mock-account-cookie"):
            self.assertNotIn(value, log)

    def test_diagnostic_mode_never_sends_a_generation_upstream(self):
        port = available_port()
        config = {"base_url": f"http://127.0.0.1:{self.gateway.server_port}/v1", "listen_port": port,
                  "qoder_enabled": True, "allow_generation": False,
                  "model_map": {"glm-5.3-flash": "internal-model"}}
        with tempfile.TemporaryDirectory(prefix="qoder-disabled-") as state:
            with patch.dict(os.environ, {"QODER_PROXY_STATE_DIR": state}):
                process, ca = launcher.start_proxy(config, INTERNAL_KEY)
            ready = threading.Event()
            def consume():
                for line in process.stdout:
                    try:
                        if json.loads(line).get("event") == "ready": ready.set()
                    except ValueError:
                        pass
            reader = threading.Thread(target=consume, daemon=True)
            reader.start()
            try:
                self.assertTrue(ready.wait(10))
                tls = ssl.create_default_context(cafile=str(ca))
                payload = {"model": "glm-5.3-flash", "model_config": {"model": "glm-5.3-flash"},
                           "messages": [{"role": "user", "content": "must not reach upstream"}]}
                for host, path in [("api3.qoder.sh", "/algo/api/v2/service/pro/sse/agent_chat_generation"),
                                   ("openrouter.ai", "/api/v1/chat/completions")]:
                    with contextlib.closing(http.client.HTTPSConnection("127.0.0.1", port, context=tls, timeout=4)) as c:
                        c.set_tunnel(host, 443)
                        c.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
                        r = c.getresponse()
                        result = r.read()
                        self.assertIn(b"409" if host != "openrouter.ai" else b"generation_disabled", result)
                self.assertEqual(MockGateway.records, [])
            finally:
                launcher.stop_proxy(process)
                reader.join(timeout=2)
                process.stdout.close()


class FramingTests(unittest.TestCase):
    def test_complete_output_preserves_usage_and_rejects_error_or_delta_only(self):
        value = {"choices": [{"message": {"role": "assistant", "content": "优化后的提示"}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
        first = json.loads(complete_response(json.dumps(value))[5:].split(b"\n\n")[0])
        body = json.loads(first["body"])
        self.assertEqual(body["choices"][0]["delta"], value["choices"][0]["message"])
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        self.assertEqual(body["usage"], value["usage"])
        for invalid in ({"error": {"message": "failure"}}, {"choices": []}, {"choices": [{"delta": {"content": "partial"}}]}):
            with self.assertRaises(ValueError):
                complete_response(json.dumps(invalid))

    def test_native_contents_preserves_text_and_tool_history(self):
        call = {"id": "call_fixture", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
        value = {"messages": [
            {"role": "system", "content": "", "contents": [{"type": "text", "text": "system one"}, {"type": "text", "text": "system two"}], "response_meta": {"private": "drop"}},
            {"role": "user", "content": "", "contents": [{"type": "text", "text": "user instruction", "extras": {"special_role": "system"}}]},
            {"role": "assistant", "content": "", "contents": [], "tool_calls": [call], "reasoning_content": "fixture reasoning", "reasoning_content_signature": "drop"},
            {"role": "tool", "content": "", "contents": [{"type": "text", "text": "tool result"}], "tool_call_id": "call_fixture"},
        ]}
        messages = chat_request(value, "internal-model")["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "system one\nsystem two"})
        self.assertEqual(messages[1], {"role": "user", "content": "user instruction"})
        self.assertEqual(messages[2], {"role": "assistant", "content": "", "tool_calls": [call], "reasoning_content": "fixture reasoning"})
        self.assertEqual(messages[3], {"role": "tool", "content": "tool result", "tool_call_id": "call_fixture"})
        self.assertEqual(value["messages"][0]["content"], "")

    def test_native_text_and_image_blocks_keep_order(self):
        value = {"messages": [{"role": "user", "contents": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}},
        ]}]}
        self.assertEqual(chat_request(value, "internal-model")["messages"][0]["content"], value["messages"][0]["contents"])

    def test_empty_or_unknown_native_content_is_rejected(self):
        for content in ([], [{"type": "unrecognized", "text": "must not disappear"}]):
            with self.assertRaises(ValueError):
                chat_request({"messages": [{"role": "user", "content": "", "contents": content}]}, "internal-model")

    def test_legacy_system_role_content_is_preserved(self):
        value = {"system_role_content": "mock legacy instruction", "messages": [{"role": "user", "content": "test"}]}
        result = chat_request(value, "internal-model")
        self.assertEqual(result["messages"][0], {"role": "system", "content": "mock legacy instruction"})

    def test_custom_model_descriptor_and_composite_keys_use_explicit_mapping(self):
        mapping = {"glm-5.3-flash": "internal-model"}
        packed = {"model_config": {"key": "custom_model", "model": json.dumps({"provider": "openrouter", "model": "glm-5.3-flash", "parameters": {"api_key": CLIENT_KEY}})}}
        self.assertEqual(mapped_model(packed, mapping)[1], "internal-model")
        composite = {"model_config": {"key": "openrouter/pg/glm-5.3-flash", "model": "upstream-private-label"}}
        self.assertEqual(mapped_model(composite, mapping)[1], "internal-model")
    def test_body_decoder_handles_chinese_and_all_lengths(self):
        for n in range(6):
            value = {"model": "测试" + "a" * n}
            self.assertEqual(decode_body(encoded(value), True), value)

    def test_utf8_tool_deltas_and_usage_across_fragment_boundaries(self):
        first = {"choices": [{"delta": {"content": "你好", "tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}}]}
        finish = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
        usage = {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}
        wire = b"".join(("data: " + json.dumps(x, ensure_ascii=False) + "\r\n\r\n").encode() for x in [first, finish, usage]) + b"data: [DONE]\n\n"
        stream = QoderSSE()
        output = b"".join(stream(wire[i:i+1]) for i in range(len(wire))) + stream(b"")
        frames = [json.loads(part[5:]) for part in output.split(b"\n\n") if part]
        self.assertEqual(json.loads(frames[0]["body"]), first)
        self.assertEqual(json.loads(frames[1]["body"])["choices"][0]["delta"]["finish_reason"], "tool_calls")
        self.assertEqual(json.loads(frames[2]["body"])["usage"], usage["usage"])
        self.assertEqual(frames[-1]["body"], "[DONE]")

    def test_broken_stream_does_not_claim_success(self):
        stream = QoderSSE()
        stream(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
        final = stream(b"")
        self.assertIn(b'"statusCodeValue":502', final)


if __name__ == "__main__":
    unittest.main()
