"""Manual mode stays independent of IDEA; switching modes retains the current connection state."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import font
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from idea_bridge import BridgeEndpoint
from launcher import App, get_integration_mode
from qoder_clients import CLIENT_CHOICES, retain_ide_proxy


class FakeWorker:
    def __init__(self):
        self.returncode = None
        self.finished = threading.Event()
        self.stdout = self.output()

    def output(self):
        yield '{"event":"ready"}\n'
        self.finished.wait(5)

    def poll(self):
        return self.returncode

    def finish(self, code):
        self.returncode = code
        self.finished.set()

    def terminate(self):
        self.finish(-15)

    def wait(self, timeout=None):
        if not self.finished.wait(timeout if timeout is not None else 5):
            raise subprocess.TimeoutExpired("fake-worker", timeout)
        return self.returncode


class IntegrationModeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state = patch("launcher.proxy_state_directory", return_value=Path(self.directory.name) / ".runtime")
        self.state.start()
        self.workers = []
        self.catalog = patch("launcher.local_catalog", return_value=({}, {}))
        self.read_catalog = self.catalog.start()
        self.openrouter = patch("launcher.openrouter_models", return_value={})
        self.openrouter.start()
        self.discovery = patch("launcher.discover_bridges", return_value=[])
        self.discover = self.discovery.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, {"base_url": "http://127.0.0.1:9/v1", "no_auth": True,
                                  "manage_qoder_proxy": False, "model_map": {"my-model": "remote/model"}})
        self.errors = []
        self.app.error = self.errors.append

    def tearDown(self):
        for worker in self.workers:
            worker.terminate()
        for timer in self.root.tk.call("after", "info"):
            self.root.after_cancel(timer)
        self.root.destroy()
        self.discovery.stop()
        self.openrouter.stop()
        self.catalog.stop()
        self.state.stop()
        self.directory.cleanup()

    def wait_until(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            self.app.poll()
            time.sleep(0.01)
        self.assertTrue(predicate())

    def start_worker(self):
        worker = FakeWorker()
        self.workers.append(worker)
        self.app.auth_base = self.app.base.get()
        self.app.auth_no_auth = True
        self.app.models = {"remote/model": {"id": "remote/model"}}
        with patch("launcher.start_proxy", return_value=(worker, Path(self.directory.name) / "fake-ca.pem")):
            self.app.start()
        return worker

    def owned_ide_journal(self, owner=None):
        self.app.client_path.set("/fixture/Qoder IDE.app")
        self.app.ide_client = Mock(ready=False, info={"edition": "global", "path": "/fixture/Qoder IDE.app"})
        self.app.proxy_state.mkdir(parents=True, exist_ok=True)
        path = self.app.proxy_state / "qoder-ide-global-proxy-rollback.json"
        path.write_text(json.dumps({"owner": owner or self.app.proxy_owner, "path": "/fixture/Qoder IDE.app",
                                    "previous": {"proxyMode": "system", "httpProxy": ""},
                                    "applied": {"proxyMode": "manual", "httpProxy": "http://127.0.0.1:8898"}}))
        self.app.owns_proxy = True
        return path

    def model_snapshot(self, models, catalog=None, sources=None):
        client = self.app.ide_client if self.app.active_client == "qoder_ide" else None
        return {"context": (self.app.active_client, self.app.client_edition, client),
                "base": self.app.base.get(), "key": "", "no_auth": True,
                "models": models, "sources": sources or {}, "catalog": catalog or {},
                "catalog_note": "fixture catalog read; remote update time unknown"}

    def wait_events(self):
        deadline = time.monotonic() + 2
        while self.app.events.empty() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.app.poll()

    def test_manual_model_refresh_does_not_discover_or_import_into_idea(self):
        self.app.events.put(("models", self.model_snapshot({"remote/model": {"id": "remote/model"}})))
        self.app.poll()
        self.app.import_to_idea()
        self.discover.assert_not_called()
        self.assertEqual(self.app.idea_controls.winfo_manager(), "")
        self.assertEqual(self.app.config()["integration_mode"], "manual")
        self.assertEqual(self.app.mapping, {"my-model": "remote/model"})
        self.assertTrue(self.errors)

    def test_bridge_selection_discovers_and_switching_back_preserves_key_and_mapping(self):
        endpoint = BridgeEndpoint(9, "00000000-0000-0000-0000-000000000000", "Test IDEA", "a" * 43)
        self.discover.return_value = [endpoint]
        self.app.key = "test-key-only"
        self.app.checked_models.add("remote/model")
        self.app.mode_buttons[1].invoke()
        self.wait_events()
        self.assertEqual(self.app.idea_controls.winfo_manager(), "pack")
        self.assertEqual(self.app.idea_target.get(), endpoint.title)
        self.assertEqual(self.app.config()["integration_mode"], "bridge")
        self.app.mode_buttons[0].invoke()
        self.assertEqual(self.app.idea_endpoints, {})
        self.assertEqual(self.app.key, "test-key-only")
        self.assertEqual(self.app.mapping, {"my-model": "remote/model"})
        self.assertEqual(self.app.checked_models, {"remote/model"})
        self.assertNotIn("key", self.app.config())

    def test_manual_mode_ignores_an_inflight_bridge_discovery_result(self):
        started, release = threading.Event(), threading.Event()
        endpoint = BridgeEndpoint(9, "00000000-0000-0000-0000-000000000000", "Old IDEA", "a" * 43)
        def delayed_discovery():
            started.set()
            release.wait(2)
            return [endpoint]
        self.discover.side_effect = delayed_discovery
        self.app.mode_buttons[1].invoke()
        self.assertTrue(started.wait(1))
        self.app.mode_buttons[0].invoke()
        release.set()
        self.wait_events()
        self.assertEqual(self.app.active_mode, "manual")
        self.assertEqual(self.app.idea_endpoints, {})
        self.assertEqual(self.app.idea_controls.winfo_manager(), "")

    def test_active_import_cannot_switch_modes(self):
        self.app.mode_buttons[1].invoke()
        self.wait_events()
        self.app.importing = True
        self.app.update_batch_controls()
        self.app.integration_mode.set("manual")
        self.app.change_mode()
        self.assertEqual(self.app.integration_mode.get(), "bridge")
        self.assertTrue(all(str(button.cget("state")) == "disabled" for button in self.app.mode_buttons))

    def test_legacy_configuration_defaults_to_manual_and_invalid_modes_are_rejected(self):
        self.assertEqual(get_integration_mode({"model_map": {}}), "manual")
        for value in (None, "auto", [], True):
            with self.assertRaises(ValueError):
                get_integration_mode({"integration_mode": value})

    def select_ide(self):
        label = next(label for label, value in CLIENT_CHOICES.items() if value == ("qoder_ide", "global"))
        self.app.client_choice.set(label)
        self.app.change_client()

    def test_ide_mode_hides_idea_controls_and_preserves_key_and_mappings(self):
        self.app.key = "fixture-key"
        self.select_ide()
        self.assertEqual(self.app.idea_controls.winfo_manager(), "")
        self.assertEqual(self.app.mode_controls.winfo_manager(), "")
        self.assertEqual(self.app.ide_import_controls.winfo_manager(), "pack")
        self.assertEqual(self.app.ide_connection_controls.winfo_manager(), "pack")
        self.assertEqual(self.app.config()["client_type"], "qoder_ide")
        self.assertEqual(self.app.key, "fixture-key")
        self.assertEqual(self.app.mapping, {"my-model": "remote/model"})
        self.discover.assert_not_called()

    def test_running_proxy_cannot_switch_client_and_lose_restore_owner(self):
        self.app.process = Mock()
        self.app.process.poll.return_value = None
        self.select_ide()
        self.assertEqual(self.app.active_client, "idea")
        self.assertTrue(self.errors)

    def test_pending_model_fetch_cannot_mix_another_clients_catalog(self):
        self.app.fetching = True
        self.select_ide()
        self.assertEqual(self.app.active_client, "idea")
        self.assertTrue(self.errors)

    def test_connecting_then_fetching_cannot_start_an_old_context_or_lock_the_window(self):
        self.select_ide()
        info = {"path": "/fixture/Qoder IDE.app", "executable": "/fixture/Qoder",
                "edition": "global", "version": "1.34.0"}
        self.app.client_path.set(info["path"])
        started, release = threading.Event(), threading.Event()
        client = Mock(ready=False, info=info)
        def connect():
            started.set()
            release.wait(2)
            client.ready = True
            return {"byok_supported": True}
        client.connect.side_effect = connect
        client.request.return_value = {"sources": {"gmodel": "Current IDE reference"}}
        with patch("launcher.app_info", return_value=info), patch("launcher.IdeClient", return_value=client), \
                patch("launcher.fetch_models", return_value={"qd/gmodel": {"id": "qd/gmodel"}}) as fetch:
            try:
                self.app.connect_ide()
                self.assertTrue(started.wait(1))
                self.assertEqual(str(self.app.fetch_button.cget("state")), "disabled")
                self.app.fetch_button.invoke()
                self.app.fetch()
                fetch.assert_not_called()
                self.assertFalse(self.app.fetching)
            finally:
                release.set()
            self.wait_events()
            self.assertIs(self.app.ide_client, client)
            self.assertFalse(self.app.client_connecting)
            self.assertEqual(str(self.app.fetch_button.cget("state")), "normal")
            self.app.fetch_button.invoke()
            self.wait_events()
            fetch.assert_called_once()
        self.assertFalse(self.app.fetching)
        self.assertEqual(str(self.app.fetch_button.cget("state")), "normal")
        self.assertEqual(self.app.table.item("qd/gmodel", "values")[1], "Current IDE reference")

    def test_a_pending_fetch_or_import_cannot_start_another_model_request(self):
        started, release = threading.Event(), threading.Event()
        def delayed_fetch(*_args):
            started.set()
            release.wait(2)
            return {"remote/model": {"id": "remote/model"}}
        with patch("launcher.fetch_models", side_effect=delayed_fetch) as fetch:
            try:
                self.app.fetch()
                self.assertTrue(started.wait(1))
                self.app.fetch_button.invoke()
                self.app.fetch()
                self.assertEqual(fetch.call_count, 1)
            finally:
                release.set()
            self.wait_events()
            self.app.importing = True
            self.app.update_batch_controls()
            self.app.fetch_button.invoke()
            self.app.fetch()
            self.assertEqual(fetch.call_count, 1)
        self.assertFalse(self.app.fetching)
        self.assertEqual(str(self.app.fetch_button.cget("state")), "disabled")
        self.app.importing = False
        self.app.update_batch_controls()
        self.assertEqual(str(self.app.fetch_button.cget("state")), "normal")

    def test_selected_model_shows_full_source_and_version_with_controls_inside_default_window(self):
        self.select_ide()
        self.app.models = {"qd/gmodel": {"id": "qd/gmodel"}}
        self.app.mapping = {"native/model": "qd/gmodel"}
        self.app.catalog = {"native/model": "当前 IDE 的参考名称"}
        self.app.catalog_status.set("当前 Qoder IDE 参考目录读取时间：2026-10-19T10:00:00.000Z；"
                                    "原生目录可能缓存（1.34.0 为 30 秒）或失败沿用旧值，联网更新时间未知。")
        self.app.refresh_model_rows()
        self.app.table.selection_set("qd/gmodel")
        self.app.select_remote()
        # Map only this mock window so macOS settles geometry across sequential Tk roots.
        self.root.title("模型目录模拟布局检查（无凭据）")
        self.root.geometry("1080x880")
        visible = tk.BooleanVar(value=False)
        binding = self.root.bind("<Visibility>", lambda _event: visible.set(True), add="+")
        timer = self.root.after(2000, lambda: visible.set(True))
        try:
            self.root.deiconify()
            if not self.root.winfo_viewable():
                self.root.wait_variable(visible)
        finally:
            self.root.after_cancel(timer)
            self.root.unbind("<Visibility>", binding)
        self.root.update()
        self.root.update_idletasks()
        geometry = {"mapped": bool(self.root.winfo_ismapped()), "viewable": bool(self.root.winfo_viewable()),
                    "root": self.root.winfo_geometry()}
        self.assertTrue(geometry["viewable"], geometry)
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (1080, 880), geometry)
        source = self.app.table.item("qd/gmodel", "values")[4]
        self.assertIn("按已有映射", self.app.model_details.get())
        self.assertIn("上游未声明版本；实际版本未核实", self.app.model_details.get())
        self.assertTrue(self.app.model_details.get().endswith(source))
        label = self.app.model_details_label
        text_font = font.nametofont("TkDefaultFont")
        self.assertGreater(text_font.measure(source), self.app.table.column("source", "width"))
        self.assertLessEqual(text_font.measure(source), label.winfo_width())
        self.assertGreaterEqual(label.winfo_height(), label.winfo_reqheight())
        self.assertGreaterEqual(label.winfo_height(), text_font.metrics("linespace") * 2)
        self.assertLessEqual(int(label.cget("wraplength")), label.winfo_width())
        geometry["detail"] = {"actual": [label.winfo_width(), label.winfo_height()],
                              "requested": [label.winfo_reqwidth(), label.winfo_reqheight()],
                              "wraplength": int(label.cget("wraplength")), "full_source_width": text_font.measure(source)}
        geometry["batch"] = {"actual": [label.master.winfo_width(), label.master.winfo_height()],
                             "requested": [label.master.winfo_reqwidth(), label.master.winfo_reqheight()]}
        geometry["controls"] = []
        for widget in (self.app.fetch_button, self.app.start_button, self.app.ide_import_button,
                       self.app.table, label, self.app.map_button, self.app.logs):
            with self.subTest(widget=str(widget)):
                self.assertGreater(widget.winfo_height(), 0)
                self.assertGreaterEqual(widget.winfo_rooty(), self.root.winfo_rooty())
                self.assertLessEqual(widget.winfo_rooty() - self.root.winfo_rooty() + widget.winfo_height(),
                                     self.root.winfo_height())
                self.assertLessEqual(widget.winfo_rootx() - self.root.winfo_rootx() + widget.winfo_width(),
                                     self.root.winfo_width())
                geometry["controls"].append([widget.winfo_rootx() - self.root.winfo_rootx(),
                                             widget.winfo_rooty() - self.root.winfo_rooty(),
                                             widget.winfo_width(), widget.winfo_height()])
        print("LAYOUT_EVIDENCE " + json.dumps(geometry, ensure_ascii=False))

    def test_idea_refresh_rereads_directory_and_replaces_names_and_removed_models(self):
        models = {"remote/model": {"id": "remote/model"}, "removed": {"id": "removed"}}
        self.read_catalog.side_effect = [({"remote/model": "First IDEA name"}, []),
                                         ({"remote/model": "Updated IDEA name"}, [])]
        with patch("launcher.fetch_models", side_effect=[models, {"remote/model": models["remote/model"]}]) as fetch:
            self.app.fetch()
            self.wait_events()
            self.assertEqual(self.app.table.item("remote/model", "values")[1], "First IDEA name")
            self.app.checked_models = {"remote/model", "removed"}
            self.app.mapping["saved-removed"] = "removed"
            self.app.fetch()
            self.wait_events()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(self.read_catalog.call_count, 2)
        self.assertEqual(self.app.table.get_children(), ("remote/model",))
        row = self.app.table.item("remote/model", "values")
        self.assertEqual(row[1], "Updated IDEA name")
        self.assertIn("本机 IDEA/Qoder 目录参考", row[4])
        self.assertEqual(self.app.checked_models, {"remote/model"})
        self.assertEqual(self.app.mapping["saved-removed"], "removed")

    def test_ide_refresh_uses_each_current_native_result_without_reading_jetbrains(self):
        self.select_ide()
        client = self.app.ide_client = Mock(ready=True)
        client.request.side_effect = [{"sources": {"native/model": "First IDE name"}, "catalog_read_at": "first-read"},
                                      {"sources": {"native/model": "Updated IDE name"}, "catalog_read_at": "second-read"}]
        self.app.mapping = {"native/model": "qd/gmodel"}
        with patch("launcher.fetch_models", return_value={"qd/gmodel": {"id": "qd/gmodel"}}):
            self.app.fetch()
            self.wait_events()
            self.assertEqual(self.app.table.item("qd/gmodel", "values")[1], "First IDE name")
            self.app.fetch()
            self.wait_events()
        self.read_catalog.assert_not_called()
        self.assertEqual([call.args for call in client.request.call_args_list], [("catalog",), ("catalog",)])
        row = self.app.table.item("qd/gmodel", "values")
        self.assertEqual(row[1], "Updated IDE name")
        self.assertIn("按已有映射", row[4])
        self.assertIn("second-read", self.app.catalog_status.get())
        self.assertIn("联网更新时间未知", self.app.catalog_status.get())
        self.assertEqual(self.app.mapping, {"native/model": "qd/gmodel"})

    def test_ide_directory_failure_drops_old_reference_instead_of_using_builtin_name(self):
        self.select_ide()
        client = self.app.ide_client = Mock(ready=True)
        client.request.side_effect = [{"sources": {"gmodel": "Old IDE name"}}, ValueError("fixture directory unavailable")]
        with patch("launcher.fetch_models", return_value={"qd/gmodel": {"id": "qd/gmodel"}}):
            self.app.fetch()
            self.wait_events()
            self.app.fetch()
            self.wait_events()
        row = self.app.table.item("qd/gmodel", "values")
        self.assertEqual(row[1], "qd/gmodel")
        self.assertIn("当前 IDE 未提供参考名称", row[4])
        self.assertEqual(self.app.catalog, {})
        self.assertEqual(self.app.source_models, {})
        self.assertIn("不可读", self.app.catalog_status.get())
        self.read_catalog.assert_not_called()

    def test_upstream_refresh_failure_marks_retained_rows_as_old_results(self):
        models = {"remote/model": {"id": "remote/model", "name": "Upstream declared name"}}
        with patch("launcher.fetch_models", side_effect=[models, ValueError("fixture request failed")]):
            self.app.fetch()
            self.wait_events()
            self.app.fetch()
            self.wait_events()
        self.assertEqual(self.app.table.item("remote/model", "values")[1], "Upstream declared name")
        self.assertIn("上次成功结果", self.app.catalog_status.get())
        self.assertIn("上次成功结果", self.app.status.get())
        self.assertIsNone(self.app.auth_base)

    def test_switching_client_discards_reference_names_and_ignores_old_region_snapshot(self):
        self.select_ide()
        self.app.ide_client = Mock(ready=True)
        self.app.models = {"qd/gmodel": {"id": "qd/gmodel"}}
        self.app.catalog = {"gmodel": "Old region name"}
        self.app.refresh_model_rows()
        old = self.model_snapshot({"stale/model": {"id": "stale/model"}}, {"stale/model": "Stale name"})
        self.app.client_choice.set(next(label for label, value in CLIENT_CHOICES.items() if value == ("qoder_ide", "cn")))
        self.app.change_client()
        self.app.events.put(("models", old))
        self.app.poll()
        self.assertEqual(self.app.client_edition, "cn")
        self.assertEqual(self.app.table.get_children(), ("qd/gmodel",))
        self.assertEqual(self.app.table.item("qd/gmodel", "values")[1], "qd/gmodel")
        self.assertEqual(self.app.catalog, {})
        self.assertIn("已切换客户端", self.app.catalog_status.get())

    def test_a_new_ide_connection_cannot_keep_the_previous_installations_reference(self):
        self.select_ide()
        self.app.ide_client = Mock(ready=True)
        self.app.models = {"qd/gmodel": {"id": "qd/gmodel"}}
        self.app.catalog = {"gmodel": "Previous installation name"}
        self.app.refresh_model_rows()
        current = Mock(ready=True, info={"version": "1.34.0"})
        self.app.events.put(("client_connected", (current, {"byok_supported": True}, {"gmodel": "Current installation name"}, "")))
        self.app.poll()
        self.assertEqual(self.app.table.item("qd/gmodel", "values")[1], "qd/gmodel")
        self.assertEqual(self.app.catalog, {})
        self.assertIn("请重新拉取", self.app.catalog_status.get())

    def test_ide_ignores_late_idea_bridge_results(self):
        self.select_ide()
        self.app.events.put(("idea_bridges", [BridgeEndpoint(9, "00000000-0000-0000-0000-000000000000", "Old IDEA", "a" * 43)]))
        self.app.poll()
        self.assertEqual(self.app.idea_endpoints, {})
        self.discover.assert_not_called()

    def test_ide_import_uses_only_selected_mapped_models_and_never_the_gateway_key(self):
        self.select_ide()
        self.app.key = "fixture-private-gateway-key"
        self.app.models = {"remote/model": {"id": "remote/model"}, "not-selected": {"id": "not-selected"}}
        self.app.checked_models = {"remote/model"}
        self.app.client_supported = True
        self.app.ide_client = Mock(ready=True)
        self.app.ide_client.request.return_value = {"status": "completed", "added": [{"source_id": "my-model"}], "skipped": [], "failed": []}
        self.app.process = Mock()
        self.app.process.poll.return_value = None
        self.app.import_to_client()
        self.wait_events()
        args, data = self.app.ide_client.request.call_args
        self.assertEqual(args, ("import",))
        self.assertEqual([x["source_id"] for x in data["models"]], ["my-model"])
        self.assertNotIn(self.app.key, str(data))
        self.assertFalse(self.app.importing)
        self.discover.assert_not_called()

    def test_offline_ide_can_start_forwarding_without_claiming_native_proxy_setup(self):
        self.select_ide()
        self.app.port.set("8898")
        self.app.manage_qoder.set(True)
        self.app.key = "fixture-private-memory-only"
        with patch("launcher.attach_ide_proxy") as attach:
            worker = self.start_worker()
            self.wait_until(lambda: "未重新核验客户端代理" in self.app.status.get())
            attach.assert_not_called()
        self.assertIsNone(worker.poll())
        self.assertIn("127.0.0.1:8898", self.app.status.get())
        self.assertNotIn("已连接", self.app.status.get())
        self.assertFalse(self.app.proxy_connecting)
        saved = json.loads(self.app.config_path.read_text())
        self.assertEqual(saved["listen_port"], 8898)
        self.assertEqual(saved["model_map"], {"my-model": "remote/model"})
        self.assertNotIn(self.app.key, self.app.config_path.read_text())
        self.assertNotIn("_connect_on_start", saved)
        self.app.import_to_client()
        self.assertFalse(self.app.client_supported)
        self.assertTrue(self.errors)

    def test_live_ide_still_uses_native_setting_and_save_failure_is_not_hidden(self):
        self.select_ide()
        self.app.manage_qoder.set(True)
        self.app.ide_client = Mock(ready=True, info={"edition": "global", "path": "/fixture/Qoder IDE.app"})
        self.app.client_supported = True
        original = b'{"future_setting":true}'
        self.app.config_path.write_bytes(original)
        with patch("launcher.attach_ide_proxy", return_value="http://127.0.0.1:8899") as attach:
            worker = self.start_worker()
            self.wait_until(lambda: "本次配置未记住" in self.app.status.get())
            attach.assert_called_once()
        self.assertIsNone(worker.poll())
        self.assertEqual(self.app.config_path.read_bytes(), original)
        self.assertIn("所选客户端已连接", self.app.status.get())

    def assert_pending_attach_cannot_close(self, fail):
        self.select_ide()
        self.app.manage_qoder.set(True)
        self.app.client_path.set("/fixture/Qoder IDE.app")
        self.app.ide_client = Mock(ready=True, info={"edition": "global", "path": "/fixture/Qoder IDE.app"})
        self.app.client_supported = True
        started, release = threading.Event(), threading.Event()
        path = self.app.proxy_state / "qoder-ide-global-proxy-rollback.json"
        def attach(_client, port, state, owner):
            started.set()
            release.wait(2)
            Path(state).mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"owner": owner, "path": "/fixture/Qoder IDE.app",
                                        "previous": {}, "applied": {"httpProxy": f"http://127.0.0.1:{port}"}}))
            if fail:
                raise ValueError("fixture connection closed after settings attempt")
            return f"http://127.0.0.1:{port}"
        with patch("launcher.attach_ide_proxy", side_effect=attach), \
                patch("launcher.retain_ide_proxy", wraps=retain_ide_proxy) as retain, \
                patch.object(self.root, "destroy") as destroy:
            try:
                worker = self.start_worker()
                self.assertTrue(started.wait(1))
                self.app.close()
                retain.assert_not_called()
                destroy.assert_not_called()
                self.assertIsNone(worker.poll())
                self.assertIn("稍候再停止或关闭", self.errors[-1])
            finally:
                release.set()
            self.wait_until(lambda: not self.app.proxy_connecting)
            self.app.close()
            self.assertEqual(worker.poll(), -15)
            retain.assert_called_once()
            destroy.assert_called_once()
        self.assertFalse(path.exists())
        self.assertEqual(len(list((self.app.proxy_state / "archive").glob("*/*rollback.json"))), 1)
        self.app.ide_client.request.assert_not_called()

    def test_close_waits_for_native_attach_before_archiving_or_destroying(self):
        self.assert_pending_attach_cannot_close(fail=False)

    def test_close_can_stop_after_pending_native_attach_reports_connection_failure(self):
        self.assert_pending_attach_cannot_close(fail=True)

    def test_manual_save_and_autosave_share_the_chosen_nonsecret_file(self):
        self.select_ide()
        default = self.app.config_path
        target = Path(self.directory.name) / "chosen" / "proxy-config.json"
        self.app.key = "fixture-memory-key"
        with patch("tkinter.filedialog.asksaveasfilename", return_value=str(target)):
            self.app.save()
        self.assertEqual(self.app.config_path, target)
        self.app.port.set("8898")
        self.start_worker()
        self.wait_until(lambda: "非秘密配置已保存" in self.app.status.get())
        self.assertEqual(json.loads(target.read_text())["listen_port"], 8898)
        self.assertNotIn("fixture-memory-key", target.read_text())
        self.assertFalse(default.exists())

    def test_saved_account_loading_is_awaited_before_reporting_login_required(self):
        self.select_ide()
        info = {"path": "/fixture/Qoder IDE.app", "executable": "/fixture/Qoder", "edition": "global", "version": "1.34.0"}
        self.app.client_path.set(info["path"])
        client = Mock(ready=False, info=info)
        def connect():
            client.ready = True
            return {"byok_supported": False}
        client.connect.side_effect = connect
        client.request.side_effect = lambda action: {"byok_supported": True} if action == "health" else {"sources": {"one": "One"}}
        with patch("launcher.app_info", return_value=info), patch("launcher.IdeClient", return_value=client), patch("launcher.time.sleep", return_value=None):
            self.app.connect_ide()
            self.wait_events()
        self.assertTrue(self.app.client_supported)
        self.assertFalse(self.app.client_connecting)
        self.assertEqual(self.app.source_models, {"one": "One"})
        self.assertFalse(self.errors)

    def test_failed_idea_restore_keeps_proxy_running(self):
        self.app.owns_proxy = True
        self.app.process = worker = FakeWorker()
        self.workers.append(worker)
        with patch("launcher.restore_proxy", side_effect=ValueError("fixture disconnected")), patch("launcher.stop_proxy") as stop:
            self.assertFalse(self.app.stop())
        stop.assert_not_called()
        self.assertTrue(self.app.owns_proxy)
        self.assertIsNone(worker.poll())

    def test_idea_stop_still_restores_its_proxy_before_stopping_worker(self):
        self.app.owns_proxy = True
        with patch("launcher.restore_proxy", return_value="restored") as restore, patch("launcher.stop_proxy") as stop:
            self.assertTrue(self.app.stop())
        restore.assert_called_once_with(self.app.proxy_state, owner=self.app.proxy_owner)
        stop.assert_called_once()

    def test_ide_stop_and_repeat_stop_archive_own_record_without_native_restore(self):
        self.select_ide()
        path = self.owned_ide_journal()
        self.app.process = worker = FakeWorker()
        self.workers.append(worker)
        self.assertTrue(self.app.stop())
        self.assertEqual(worker.poll(), -15)
        self.assertFalse(path.exists())
        self.app.ide_client.request.assert_not_called()
        self.assertTrue(self.app.stop())
        self.assertEqual(len(list((self.app.proxy_state / "archive").glob("*/*rollback.json"))), 1)

    def test_corrupt_or_unwritable_ide_journal_warns_but_does_not_prevent_stopping_worker(self):
        self.select_ide()
        for broken_json in (True, False):
            with self.subTest(broken_json=broken_json):
                path = self.owned_ide_journal()
                if broken_json:
                    path.write_bytes(b'{invalid json; preserve these bytes')
                before = path.read_bytes()
                self.app.process = worker = FakeWorker()
                self.workers.append(worker)
                if broken_json:
                    self.assertTrue(self.app.stop())
                else:
                    with patch("qoder_clients.Path.rename", side_effect=OSError("fixture archive is not writable")):
                        self.assertTrue(self.app.stop())
                self.assertEqual(worker.poll(), -15)
                self.assertEqual(path.read_bytes(), before)
                self.assertIn("活动记录未归档、仍待处理", self.app.status.get())
                self.assertIn("活动记录未归档、仍待处理", self.errors[-1])
                self.app.ide_client.request.assert_not_called()

    def test_ide_worker_exit_keeps_proxy_and_releases_owned_journal_offline(self):
        self.select_ide()
        path = self.owned_ide_journal()
        self.app.events.put(("exit", 7))
        self.app.poll()
        self.assertFalse(path.exists())
        self.assertFalse(self.app.owns_proxy)
        self.app.ide_client.request.assert_not_called()
        self.assertIn("代理设置已保留", self.app.status.get())

    def test_ide_close_with_dead_relay_stops_worker_and_preserves_foreign_record(self):
        self.select_ide()
        path = self.owned_ide_journal(owner="another-window")
        original = path.read_bytes()
        self.app.process = worker = FakeWorker()
        self.workers.append(worker)
        self.app.key = "fixture-memory-key"
        with patch.object(self.root, "destroy") as destroy:
            self.app.close()
        self.assertEqual(worker.poll(), -15)
        self.assertEqual(path.read_bytes(), original)
        self.app.ide_client.request.assert_not_called()
        destroy.assert_called_once()
        self.assertEqual(self.app.key, "")

    @unittest.skipUnless(sys.platform == "darwin", "macOS native Quit command")
    def test_mac_quit_keeps_window_open_when_proxy_restore_cannot_finish(self):
        with patch.object(self.app, "stop", return_value=False) as stop:
            self.root.tk.call("tk::mac::Quit")
        stop.assert_called_once()
        self.assertTrue(self.root.winfo_exists())

    @unittest.skipUnless(sys.platform == "darwin", "macOS native Quit command")
    def test_mac_quit_stops_proxy_clears_key_and_closes_window(self):
        self.app.key = "test-key-only"
        self.app.password.set("test-key-only")
        with patch.object(self.app, "stop", return_value=True) as stop, \
                patch.object(self.root, "destroy") as destroy:
            self.root.tk.call("tk::mac::Quit")
        stop.assert_called_once()
        destroy.assert_called_once()
        self.assertEqual(self.app.key, "")
        self.assertEqual(self.app.password.get(), "")


if __name__ == "__main__":
    unittest.main()
