"""Client isolation and recovery records must survive failures without overwriting user settings."""
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qoder_clients import app_info, attach_ide_proxy, get_client, retain_ide_proxy, restore_ide_proxy
from launcher import main, read_config, write_config


class FakeClient:
    def __init__(self, edition="global"):
        self.info = {"edition": edition, "path": "/fixture/Qoder IDE.app"}
        self.proxy = {"proxyMode": "system", "httpProxy": ""}
        self.fail_after_apply = False
        self.calls = []

    def request(self, action, **data):
        self.calls.append(action)
        if action == "proxy_get":
            return {"proxy": dict(self.proxy)}
        if data.get("expected") and data["expected"] != self.proxy:
            return {"changed_by_user": True}
        self.proxy = dict(data["proxy"])
        if action == "proxy_set" and self.fail_after_apply:
            raise ValueError("fixture interruption")
        return {"proxy": dict(self.proxy)}


class ClientTests(unittest.TestCase):
    def test_retention_archives_only_owned_journal_without_calling_an_offline_client(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            attach_ide_proxy(client, 8898, directory, "owner")
            path = Path(directory) / "qoder-ide-global-proxy-rollback.json"
            before = path.read_bytes()
            proxy = dict(client.proxy)
            with patch.object(client, "request", side_effect=AssertionError("Offline IDE must not receive any request")):
                retain_ide_proxy(client.info, directory, "owner")
                retain_ide_proxy(client.info, directory, "owner")
            archived = list((Path(directory) / "archive").glob("*/*rollback.json"))
            self.assertFalse(path.exists())
            self.assertEqual(len(archived), 1)
            self.assertEqual(hashlib.sha256(archived[0].read_bytes()).digest(), hashlib.sha256(before).digest())
            self.assertEqual(client.proxy, proxy)

    def test_retention_preserves_foreign_owner_and_other_installation_records(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            attach_ide_proxy(client, 8898, directory, "owner")
            path = Path(directory) / "qoder-ide-global-proxy-rollback.json"
            before = path.read_bytes()
            retain_ide_proxy(client.info, directory, "other-owner")
            retain_ide_proxy({**client.info, "path": "/another/Qoder IDE.app"}, directory, "owner")
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((Path(directory) / "archive").exists())
            self.assertEqual(client.proxy["httpProxy"], "http://127.0.0.1:8898")

    def test_start_does_not_overwrite_another_windows_existing_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            attach_ide_proxy(client, 8898, directory, "original-owner")
            path = Path(directory) / "qoder-ide-global-proxy-rollback.json"
            before = path.read_bytes()
            with patch.object(client, "request", side_effect=AssertionError("Foreign journal must be checked first")):
                with self.assertRaises(ValueError):
                    attach_ide_proxy(client, 8897, directory, "another-owner")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(client.proxy["httpProxy"], "http://127.0.0.1:8898")

    def test_old_config_retains_idea_and_wrong_editions_are_rejected(self):
        self.assertEqual(get_client({}), ("idea", "global"))
        self.assertEqual(get_client({"client_type": "qoder_ide", "client_edition": "cn"}), ("qoder_ide", "cn"))
        for item in ({"client_type": "qoder_desktop"}, {"client_type": "idea", "client_edition": "cn"}):
            with self.assertRaises(ValueError):
                get_client(item)

    def test_app_metadata_distinguishes_regions_and_rejects_desktop(self):
        with tempfile.TemporaryDirectory() as directory, patch("qoder_clients.sys.platform", "darwin"):
            bundle = Path(directory) / "应用 with space.app"
            resources = bundle / "Contents/Resources/app"
            workbench = resources / "out/vs/workbench/workbench.desktop.main.js"
            workbench.parent.mkdir(parents=True)
            workbench.write_text("", encoding="utf-8")
            exe = bundle / "Contents/MacOS/Qoder"
            exe.parent.mkdir(parents=True)
            exe.touch()
            for folder, edition in ((".qoder", "global"), (".qodercn", "cn")):
                (resources / "product.json").write_text(json.dumps({"dataFolderName": folder, "productVersion": "1.30.1"}))
                info = app_info(bundle)
                self.assertEqual(info["edition"], edition)
                self.assertEqual(info["executable"], str(exe.resolve()))
            (resources / "product.json").write_text(json.dumps({"nameShort": "QoderCN", "dataFolderName": ".qoder"}))
            with self.assertRaises(ValueError):
                app_info(bundle)
            workbench.unlink()
            with self.assertRaises(ValueError):
                app_info(bundle)

    def test_proxy_round_trip_and_edition_records_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            cn = FakeClient("cn")
            attach_ide_proxy(client, 8897, directory, "one")
            attach_ide_proxy(cn, 8898, directory, "two")
            restore_ide_proxy(client, directory, "one")
            self.assertEqual(client.proxy, {"proxyMode": "system", "httpProxy": ""})
            self.assertEqual(cn.proxy["httpProxy"], "http://127.0.0.1:8898")
            self.assertTrue((Path(directory) / "qoder-ide-cn-proxy-rollback.json").exists())

    def test_interruption_after_apply_rolls_back_before_returning_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            client.fail_after_apply = True
            with self.assertRaisesRegex(ValueError, "fixture interruption"):
                attach_ide_proxy(client, 8897, directory, "owner")
            self.assertEqual(client.proxy["proxyMode"], "system")
            self.assertFalse(list(Path(directory).glob("*rollback.json")))

    def test_restore_preserves_user_changes_and_foreign_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            attach_ide_proxy(client, 8897, directory, "owner")
            restore_ide_proxy(client, directory, "another-owner")
            self.assertEqual(client.proxy["httpProxy"], "http://127.0.0.1:8897")
            client.proxy = {"proxyMode": "manual", "httpProxy": "http://127.0.0.1:8888"}
            restore_ide_proxy(client, directory, "owner")
            self.assertEqual(client.proxy["httpProxy"], "http://127.0.0.1:8888")

    def test_proxy_with_credentials_is_not_written_to_recovery_record(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            client.proxy["httpProxy"] = "http://fixture-user:fixture-password@proxy.invalid"
            with self.assertRaises(ValueError):
                attach_ide_proxy(client, 8897, directory, "owner")
            self.assertFalse(list(Path(directory).iterdir()))


class ConfigFileTests(unittest.TestCase):
    def test_nonsecret_configuration_round_trip_keeps_port_and_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proxy-config.json"
            config = {"client_type": "qoder_ide", "client_edition": "global", "client_path": "/fixture/Qoder IDE.app",
                      "listen_port": "8898", "base_url": "http://127.0.0.1:9/v1", "model_map": {"saved": "upstream/model"}}
            write_config(path, config)
            self.assertEqual(read_config(path), config)
            self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_existing_unknown_sensitive_or_invalid_config_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proxy-config.json"
            for raw in [b'{"api_key":"fixture-private-value","listen_port":8898}',
                        b'{"future_setting":true,"listen_port":8898}', b'{invalid json']:
                with self.subTest(raw=raw):
                    path.write_bytes(raw)
                    with self.assertRaises(ValueError):
                        write_config(path, {"listen_port": 8897})
                    self.assertEqual(path.read_bytes(), raw)
                    self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_gui_loads_default_and_explicit_config_takes_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            default = Path(directory) / "proxy-config.json"
            explicit = Path(directory) / "external.json"
            write_config(default, {"listen_port": 8898, "model_map": {"default": "upstream/a"}})
            write_config(explicit, {"listen_port": 8897, "model_map": {"explicit": "upstream/b"}})
            for arguments, expected, path in [([], 8898, default), (["--config", str(explicit)], 8897, explicit)]:
                with self.subTest(arguments=arguments), patch("sys.argv", ["launcher.py", *arguments]), \
                        patch("launcher.default_config_path", return_value=default), patch("launcher.App") as app, \
                        patch("tkinter.Tk"):
                    self.assertEqual(main(), 0)
                    loaded = app.call_args.args[1]
                    self.assertEqual(loaded["listen_port"], expected)
                    self.assertEqual(loaded["_config_path"], str(path))
                    self.assertEqual(loaded["model_map"], read_config(path)["model_map"])

    def test_invalid_default_shows_error_and_does_not_fall_back_or_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proxy-config.json"
            original = b'{"api_key":"fixture-private-value","listen_port":8898}'
            path.write_bytes(original)
            with patch("sys.argv", ["launcher.py"]), patch("launcher.default_config_path", return_value=path), \
                    patch("launcher.App") as app, patch("tkinter.Tk"), patch("tkinter.messagebox.showerror") as error:
                self.assertEqual(main(), 1)
                app.assert_not_called()
                error.assert_called_once()
                self.assertNotIn("fixture-private-value", str(error.call_args))
            self.assertEqual(path.read_bytes(), original)

    def test_cli_does_not_implicitly_load_the_gui_default_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proxy-config.json"
            original = b'{"future_setting":true}'
            path.write_bytes(original)
            with patch("sys.argv", ["launcher.py", "--list-models"]), \
                    patch("launcher.default_config_path", return_value=path), \
                    patch("os.environ", {"INTERNAL_NO_AUTH": "1", "INTERNAL_BASE_URL": "http://127.0.0.1:9/v1"}), \
                    patch("launcher.fetch_models", return_value={"fixture/model": {"id": "fixture/model"}}) as fetch, \
                    patch("launcher.local_catalog", return_value=({}, [])), patch("builtins.print"):
                self.assertEqual(main(), 0)
                fetch.assert_called_once_with("http://127.0.0.1:9/v1", "", True)
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
