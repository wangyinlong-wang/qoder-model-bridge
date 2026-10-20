"""Real local HTTP tests for bridge discovery and credential-safe request boundaries."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from idea_bridge import BridgeEndpoint, discover_bridges

TOKEN = "t" * 43
INSTANCE = str(uuid.uuid4())


class BridgeMock(BaseHTTPRequestHandler):
    records = []
    redirect = False

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.records.append({"path": self.path, "auth": self.headers.get("Authorization")})
        if self.redirect:
            self.send_response(302)
            self.send_header("Location", "/must-not-follow")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"service": "qoder-internal-bridge", "protocol": 1, "instance_id": INSTANCE}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.records.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"request_id": body["request_id"], "status": "awaiting_user"}).encode())


class BridgeClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeMock)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        BridgeMock.records.clear()
        BridgeMock.redirect = False
        self.temp = tempfile.TemporaryDirectory(prefix="idea-bridge-client-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "session-test.json"
        self.record = {"service": "qoder-internal-bridge", "protocol": 1, "host": "127.0.0.1", "port": self.server.server_port,
                       "instance_id": INSTANCE, "token": TOKEN, "ide_name": "Test IDEA", "ide_version": "2026.1", "pid": 123}
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.record))
        self.path.chmod(0o600)

    def test_discovery_authenticates_and_does_not_expose_session_secret_in_repr(self):
        endpoints = discover_bridges(self.directory)
        self.assertEqual(len(endpoints), 1)
        self.assertEqual(BridgeMock.records[-1]["auth"], "Bearer " + TOKEN)
        self.assertNotIn(TOKEN, repr(endpoints[0]))

    def test_external_host_or_mismatched_instance_is_not_used(self):
        self.record["host"] = "example.invalid"
        self.save()
        self.assertEqual(discover_bridges(self.directory), [])
        self.assertEqual(BridgeMock.records, [])
        self.record["host"] = "127.0.0.1"
        self.record["instance_id"] = str(uuid.uuid4())
        self.save()
        self.assertEqual(discover_bridges(self.directory), [])

    @unittest.skipIf(os.name == "nt", "Windows discovery ACL is set by the Java bridge")
    def test_shared_discovery_file_is_rejected(self):
        self.path.chmod(0o644)
        self.assertEqual(discover_bridges(self.directory), [])
        self.assertEqual(BridgeMock.records, [])

    def test_redirect_is_not_followed(self):
        endpoint = BridgeEndpoint(self.server.server_port, INSTANCE, "Test", TOKEN)
        BridgeMock.redirect = True
        with self.assertRaises(ValueError):
            endpoint.request("/v1/health")
        self.assertEqual(len(BridgeMock.records), 1)

    def test_import_posts_only_explicit_metadata_once(self):
        endpoint = discover_bridges(self.directory)[0]
        body = {"request_id": str(uuid.uuid4()), "proxy_port": 8899,
                "models": [{"source_id": "my-model", "target_id": "qd/model", "display_name": "Model"}]}
        value = endpoint.request("/v1/imports", body)
        self.assertEqual(value["status"], "awaiting_user")
        self.assertEqual(BridgeMock.records[-1]["body"], body)
        self.assertEqual(sum("body" in row for row in BridgeMock.records), 1)

    def test_port_cannot_be_used_to_change_destination_host(self):
        with self.assertRaises(ValueError):
            BridgeEndpoint("1234@example.invalid", INSTANCE, "Bad", TOKEN)


if __name__ == "__main__":
    unittest.main()
