"""Environment preparation must be repeatable, recoverable and stop before launch on failure."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "requirements-windows-offline.txt").write_text("# Mock offline dependencies\n", encoding="utf-8")
        (self.root / "wheels").mkdir()
        self.venv_python = self.root / ".venv" / ("Scripts/python.exe" if bootstrap.os.name == "nt" else "bin/python")
        self.state = {"dependencies": False, "invalid": False}
        self.patches = [patch.object(bootstrap, "ROOT", self.root),
                        patch.object(bootstrap, "candidates", return_value=[["test-runtime"]]),
                        patch.dict(bootstrap.os.environ, {"PROXY_PYTHON": "", "PROXY_GUI_PYTHON": ""}),
                        patch.object(bootstrap, "probe", side_effect=self.probe)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def probe(self, command, code):
        if code == bootstrap.RUNTIME_CHECK:
            if command == ["test-runtime"]:
                return "test-runtime"
            return str(self.venv_python) if self.venv_python.exists() and not self.state["invalid"] else None
        if code == bootstrap.GUI_CHECK:
            return "test-gui"
        if code == bootstrap.CA_CHECK:
            return "1"
        return bootstrap.EXPECTED_MITMPROXY if self.state["dependencies"] else None

    def prepare_command(self, command, **_):
        if "venv" in command:
            self.venv_python.parent.mkdir(parents=True, exist_ok=True)
            self.venv_python.touch()
            self.state["invalid"] = False
        elif "pip" in command:
            self.state["dependencies"] = True
        return subprocess.CompletedProcess(command, 0)

    def test_ready_environment_skips_install_and_preserves_launcher_arguments_and_exit_code(self):
        self.venv_python.parent.mkdir(parents=True)
        self.venv_python.touch()
        self.state["dependencies"] = True
        args = ["bootstrap.py", "--mode", "bridge", "--config", "目录 with spaces.json", "--no-gui"]
        with patch.object(sys, "argv", args), patch.object(bootstrap.subprocess, "run") as run, \
                patch.object(bootstrap.subprocess, "call", return_value=7) as launch:
            self.assertEqual(bootstrap.main(), 7)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], [str(self.venv_python), "-I", "-X", "utf8", "-m", "pip",
                                               "--disable-pip-version-check", "check"])
        self.assertEqual(run.call_args.kwargs["cwd"], self.root)
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertEqual(launch.call_args.args[0], [str(self.venv_python), "-X", "utf8",
                                                  str(self.root / "launcher.py"), *args[1:]])

    def test_missing_offline_inputs_refuses_preparation_and_launch(self):
        for missing in ("lock", "wheels"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if missing != "lock":
                    (root / "requirements-windows-offline.txt").write_text("# Mock offline dependencies\n", encoding="utf-8")
                if missing != "wheels":
                    (root / "wheels").mkdir()
                with patch.object(bootstrap, "ROOT", root), patch.object(sys, "argv", ["bootstrap.py"]), \
                        patch.object(bootstrap.subprocess, "run") as prepare, \
                        patch.object(bootstrap.subprocess, "call") as launch:
                    with self.assertRaisesRegex(RuntimeError, "离线组件不完整"):
                        bootstrap.main()
                prepare.assert_not_called()
                launch.assert_not_called()

    def test_install_failure_does_not_launch_the_gui(self):
        def fail_pip(command, **kwargs):
            if "pip" in command:
                raise subprocess.CalledProcessError(2, command)
            return self.prepare_command(command, **kwargs)
        with patch.object(sys, "argv", ["bootstrap.py"]), \
                patch.object(bootstrap.subprocess, "run", side_effect=fail_pip), \
                patch.object(bootstrap.subprocess, "call") as launch:
            with self.assertRaises(subprocess.CalledProcessError):
                bootstrap.main()
        launch.assert_not_called()

    def test_invalid_environment_is_preserved_before_rebuild_and_setup_only_does_not_open_gui(self):
        marker = self.root / ".venv" / "keep-this.txt"
        marker.parent.mkdir()
        marker.write_text("old environment data")
        self.state["invalid"] = True
        with patch.object(sys, "argv", ["bootstrap.py", "--setup-only"]), \
                patch.object(bootstrap.subprocess, "run", side_effect=self.prepare_command), \
                patch.object(bootstrap.subprocess, "call") as launch:
            self.assertEqual(bootstrap.main(), 0)
        saved = list(self.root.glob(".venv.backup-*/keep-this.txt"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_text(), "old environment data")
        self.assertTrue(self.venv_python.exists())
        launch.assert_not_called()

    def test_setup_lock_rejects_another_preparation_and_is_released_after_failure(self):
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            with bootstrap.setup_lock():
                with self.assertRaises(RuntimeError):
                    with bootstrap.setup_lock():
                        self.fail("A second preparation acquired the lock")
                raise RuntimeError("test failure")
        with bootstrap.setup_lock():
            pass

    def test_incomplete_linked_environment_is_not_modified(self):
        target = self.root / "separate-environment"
        executable = target / self.venv_python.relative_to(self.root / ".venv")
        executable.parent.mkdir(parents=True)
        executable.touch()
        (self.root / ".venv").symlink_to(target, target_is_directory=True)
        with patch.object(bootstrap.subprocess, "run") as install:
            with self.assertRaises(RuntimeError):
                bootstrap.prepare_environment(["--no-gui"])
        install.assert_not_called()
        self.assertTrue((self.root / ".venv").is_symlink())
        self.assertTrue(executable.exists())

    def test_invalid_explicit_runtime_is_not_replaced_by_another_interpreter(self):
        with patch.dict(bootstrap.os.environ, {"PROXY_PYTHON": "missing-runtime"}), \
                patch.object(bootstrap.subprocess, "run") as install:
            with self.assertRaisesRegex(RuntimeError, "PROXY_PYTHON"):
                bootstrap.prepare_environment(["--no-gui"])
        install.assert_not_called()

    def test_macos_missing_ca_uses_private_bundle_without_changing_explicit_trust_or_parent_environment(self):
        self.venv_python.parent.mkdir(parents=True)
        self.venv_python.touch()
        self.state["dependencies"] = True
        bundle = self.root / "cacert.pem"
        bundle.write_text("test fixture")
        def certificate_probe(command, code):
            if code == bootstrap.CA_CHECK:
                return "0"
            if code == bootstrap.CA_BUNDLE_CHECK:
                return str(bundle)
            return self.probe(command, code)
        for explicit in ("", "user-provided.pem"):
            with self.subTest(explicit=explicit), patch.object(bootstrap.sys, "platform", "darwin"), \
                    patch.dict(bootstrap.os.environ, {"SSL_CERT_FILE": explicit, "SSL_CERT_DIR": ""}), \
                    patch.object(bootstrap, "probe", side_effect=certificate_probe), \
                    patch.object(sys, "argv", ["bootstrap.py"]), \
                    patch.object(bootstrap.subprocess, "run", side_effect=self.prepare_command), \
                    patch.object(bootstrap.subprocess, "call", return_value=0) as launch:
                self.assertEqual(bootstrap.main(), 0)
                self.assertEqual(launch.call_args.kwargs["env"]["SSL_CERT_FILE"], explicit or str(bundle))
                self.assertEqual(bootstrap.os.environ.get("SSL_CERT_FILE"), explicit)


if __name__ == "__main__":
    unittest.main()
