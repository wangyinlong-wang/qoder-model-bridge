"""Settings ownership and restoration; no installed Qoder configuration is changed."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qoder_control


class ProxyOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.backend = {"proxyMode": "system", "httpProxy": "", "terminalRunMode": "ask", "unrelated": "preserve"}
        self.writes = []
        def fake(method, params=None, **_):
            if method == "config/getGlobal":
                return dict(self.backend)
            if method == "config/updateBatch":
                update = dict(params["updates"])
                self.assertEqual(set(update), {"proxyMode", "httpProxy"})
                self.writes.append(update)
                self.backend.update(update)
                return {"success": True}
            raise AssertionError("Unexpected RPC")
        self.mock = patch.object(qoder_control, "rpc", side_effect=fake)
        self.mock.start()

    def tearDown(self):
        self.mock.stop()
        self.directory.cleanup()

    def test_start_and_stop_restore_only_the_owned_proxy_fields(self):
        qoder_control.attach_proxy(8899, self.directory.name)
        self.assertEqual(self.backend["httpProxy"], "http://127.0.0.1:8899")
        qoder_control.restore_proxy(self.directory.name)
        self.assertEqual(self.backend, {"proxyMode": "system", "httpProxy": "", "terminalRunMode": "ask", "unrelated": "preserve"})
        self.assertFalse((Path(self.directory.name) / "qoder-proxy-rollback.json").exists())

    def test_user_changes_are_not_overwritten_on_stop(self):
        qoder_control.attach_proxy(8899, self.directory.name)
        self.backend["httpProxy"] = "http://127.0.0.1:9000"
        count = len(self.writes)
        qoder_control.restore_proxy(self.directory.name)
        self.assertEqual(len(self.writes), count)
        self.assertEqual(self.backend["httpProxy"], "http://127.0.0.1:9000")

    def test_credential_bearing_existing_proxy_is_never_rewritten_or_saved(self):
        self.backend["httpProxy"] = "http://mock-user:mock-secret@127.0.0.1:9000"
        with self.assertRaises(ValueError):
            qoder_control.attach_proxy(8899, self.directory.name)
        self.assertFalse(self.writes)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_restart_preserves_original_recovery_settings(self):
        qoder_control.attach_proxy(8897, self.directory.name)
        qoder_control.attach_proxy(8899, self.directory.name)
        record = json.loads((Path(self.directory.name) / "qoder-proxy-rollback.json").read_text())
        self.assertEqual(record["previous"], {"proxyMode": "system", "httpProxy": ""})
        qoder_control.restore_proxy(self.directory.name)
        self.assertEqual(self.backend["proxyMode"], "system")

    def test_older_window_cannot_restore_over_the_newer_owner(self):
        qoder_control.attach_proxy(8897, self.directory.name, owner="old-window")
        qoder_control.attach_proxy(8899, self.directory.name, owner="new-window")
        count = len(self.writes)
        qoder_control.restore_proxy(self.directory.name, owner="old-window")
        self.assertEqual(len(self.writes), count)
        self.assertEqual(self.backend["httpProxy"], "http://127.0.0.1:8899")
        qoder_control.restore_proxy(self.directory.name, owner="new-window")
        self.assertEqual(self.backend["proxyMode"], "system")


if __name__ == "__main__":
    unittest.main()
