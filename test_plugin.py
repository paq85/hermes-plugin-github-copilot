"""Tests for the Hermes plugin: bridge download/install/start (real Node + Copilot) and provider registration (stubbed Hermes).

Tests that start the bridge need Node.js 20.19+, network access to GitHub and a signed-in Copilot CLI.
Set COPILOT_BRIDGE_SOURCE=<bridge checkout> to install from a local bridge instead of the pinned release.
"""

import http.server
import importlib.util
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NEEDS_BRIDGE = not os.environ.get("CI")  # CI has no Copilot login


def load(name, file="bridge_process.py"):
    return load_from(name, ROOT / file)


def load_from(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Isolated(unittest.TestCase):
    """Each test gets its own runtime directory and port, and leaves the environment as it found it."""

    def setUp(self):
        self._env = dict(os.environ)
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["COPILOT_BRIDGE_RUNTIME_DIR"] = str(self.tmp / "runtime")
        os.environ["HERMES_HOME"] = str(self.tmp / "home")
        for k in ("COPILOT_BRIDGE_BASE_URL", "COPILOT_BRIDGE_API_KEY", "COPILOT_BRIDGE_PORT"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        time.sleep(1)  # let a stopped bridge release its files before cleanup
        shutil.rmtree(self.tmp, ignore_errors=True)


@unittest.skipUnless(NEEDS_BRIDGE, "needs Node.js and a signed-in Copilot CLI")
class Bridge(Isolated):
    def test_downloads_pinned_release_installs_and_serves(self):
        os.environ.pop("COPILOT_BRIDGE_SOURCE", None)  # the real download of the pinned tag
        os.environ["COPILOT_BRIDGE_PORT"] = "18781"
        bp = load("bp_download")
        self.assertFalse(bp.is_healthy())
        try:
            url = bp.ensure_running()
            self.assertEqual(url, "http://127.0.0.1:18781/v1")
            runtime = bp.runtime_dir()
            self.assertEqual(runtime.name, f"v{bp.BRIDGE_VERSION}")
            self.assertTrue((runtime / "node_modules" / "@github" / "copilot-sdk").is_dir())
            self.assertEqual(bp.ensure_running(), url, "second call reuses the running bridge")
            models = bp.list_models()
            self.assertIn("auto", models)
            self.assertIn("fusion", models)
            self.assert_loopback_only(18781)
            self.assertFalse((ROOT / "node_modules").exists(), "nothing is installed into the plugin directory")
        finally:
            bp.stop()
        self.assertFalse(bp.is_healthy())

    def test_snapshots_share_one_install_and_prune_old_versions(self):
        # Hermes may import the plugin from per-process copies of its directory.
        source = os.environ.get("COPILOT_BRIDGE_SOURCE")
        if not source:
            self.skipTest("set COPILOT_BRIDGE_SOURCE to a bridge checkout (avoids a second download)")
        os.environ["COPILOT_BRIDGE_PORT"] = "18782"
        old = self.tmp / "runtime" / "v0.0.1"  # a bridge from an older plugin version
        old.mkdir(parents=True)
        for snap in ("snap1", "snap2"):
            (self.tmp / snap).mkdir()
            shutil.copy2(ROOT / "bridge_process.py", self.tmp / snap / "bridge_process.py")
        bp1 = load_from("bp_snap1", self.tmp / "snap1" / "bridge_process.py")
        try:
            bp1.ensure_running()
            runtime = bp1.runtime_dir()
            self.assertTrue(runtime.name.startswith("local-"))
            self.assertFalse(old.exists(), "older bridge versions are pruned")
            self.assertIn("auto", bp1.list_models())
        finally:
            bp1.stop()
        bp2 = load_from("bp_snap2", self.tmp / "snap2" / "bridge_process.py")
        started = time.monotonic()
        self.assertEqual(bp2.runtime_dir(), runtime, "a fresh snapshot reuses the installed bridge")
        self.assertLess(time.monotonic() - started, 5, "reuse must not reinstall")
        self.assertEqual([p.name for p in (self.tmp / "runtime").iterdir()], [runtime.name], "no staging leftovers")

    def assert_loopback_only(self, port):
        # The machine's own non-loopback address (no packet is sent for a UDP connect).
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect(("192.0.2.1", 9))
                lan = s.getsockname()[0]
            except OSError:
                lan = None
        if not lan or lan.startswith("127."):
            return
        with self.assertRaises(OSError, msg=f"bridge must not accept connections on {lan}"):
            socket.create_connection((lan, port), timeout=2).close()


class Process(Isolated):
    def test_port_used_by_a_server_rejecting_our_key_is_reported(self):
        class Reject(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Reject)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        os.environ["COPILOT_BRIDGE_PORT"] = str(srv.server_address[1])
        try:
            bp = load("bp_foreign")
            self.assertEqual(bp._probe(), "foreign")
            with self.assertRaisesRegex(RuntimeError, "COPILOT_BRIDGE_PORT"):
                bp.ensure_running(wait=2)
        finally:
            srv.shutdown()

    def test_unmanaged_url_is_never_spawned(self):
        os.environ["COPILOT_BRIDGE_BASE_URL"] = "http://127.0.0.1:1/v1"
        bp = load("bp_unmanaged")
        self.assertIsNone(bp.prepare_in_background(), "no install for a bridge the user runs")
        with self.assertRaises(RuntimeError):
            bp.ensure_running(wait=1)

    def test_pinned_release_url(self):
        bp = load("bp_url")
        self.assertEqual(bp.bridge_url(),
                         f"https://github.com/paq85/github-copilot-openai-bridge/archive/refs/tags/v{bp.BRIDGE_VERSION}.tar.gz")
        self.assertEqual(bp.DEFAULT_PORT, 8788)

    def test_children_get_no_console_window(self):
        bp = load("bp_hidden")
        kw = bp._hidden(detach=True)
        if sys.platform == "win32":
            import subprocess
            self.assertTrue(kw["creationflags"] & subprocess.CREATE_NO_WINDOW)
            self.assertEqual(kw["startupinfo"].wShowWindow, 0)
            self.assertTrue(bp._hidden()["creationflags"] & subprocess.CREATE_NO_WINDOW)
        else:
            self.assertTrue(kw["start_new_session"])


class SpawnEnvironment(unittest.TestCase):
    """The bridge and npm must not come from, or inherit, Hermes' packaged tool directories."""

    @unittest.skipUnless(sys.platform == "win32", "packaged Hermes (WindowsApps) is Windows-only")
    def test_clean_path_drops_hermes_packaged_tool_dirs(self):
        bp = load("bp_env")
        parts = [
            r"C:\Program Files\WindowsApps\NousResearchInc.HermesAgent_2026.1_arm64__x\app\resources\agent-payload\tools\gh-2.97.0-win32-arm64\bin",
            r"C:\Program Files\nodejs",
            r"C:\Users\me\bin",
            r"C:\Users\me\AppData\Local\Temp\hermes-provider-plugins-x\app\resources\agent-payload",
        ]
        cleaned = bp._clean_path(os.pathsep.join(parts)).split(os.pathsep)
        self.assertEqual(cleaned, [r"C:\Program Files\nodejs", r"C:\Users\me\bin"])

    def test_clean_path_keeps_unrelated_dirs(self):
        bp = load("bp_clean")
        value = os.pathsep.join([str(Path("/opt/python3")), str(Path("/usr/local/bin"))])
        self.assertEqual(bp._clean_path(value), value)

    def test_which_skips_hermes_packaged_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            packaged = Path(tmp) / "WindowsApps" / "NousResearchInc.HermesAgent_1" / "agent-payload" / "node"
            user = Path(tmp) / "nodejs"
            exe = "npm.cmd" if sys.platform == "win32" else "npm"
            for d in (packaged, user):
                d.mkdir(parents=True)
                (d / exe).write_text("")
                (d / exe).chmod(0o755)
            old = os.environ["PATH"]
            os.environ["PATH"] = os.pathsep.join([str(packaged), str(user)])
            try:
                bp = load("bp_which")
                self.assertEqual(Path(bp._which("npm")).parent, user)
                self.assertNotIn("agent-payload", bp._child_env()["PATH"])
                os.environ["PATH"] = str(packaged)
                self.assertEqual(Path(bp._which("npm")).parent, packaged, "packaged node is the last resort")
            finally:
                os.environ["PATH"] = old


class Registration(Isolated):
    def test_registers_provider_profile(self):
        registered = []
        providers = types.ModuleType("providers")
        providers.register_provider = registered.append
        base = types.ModuleType("providers.base")

        class ProviderProfile:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        base.ProviderProfile = ProviderProfile
        sys.modules.update({"providers": providers, "providers.base": base})
        os.environ["COPILOT_BRIDGE_BASE_URL"] = "http://127.0.0.1:1/v1"  # keep the import from installing
        try:
            load("copilot_bridge_plugin", "__init__.py")
        finally:
            for m in ("providers", "providers.base"):
                sys.modules.pop(m, None)
        (p,) = registered
        self.assertEqual(p.name, "copilot-bridge")
        self.assertFalse(getattr(p, "aliases", ()), "no legacy copilot-proxy alias")
        self.assertTrue(p.base_url.endswith("/v1"))
        self.assertIn("auto", p.fallback_models)
        key, _source = p.resolve_api_key_secret()
        self.assertEqual(key, "copilot-bridge", "the plugin supplies its own key: nothing to configure after install")
        # Hermes' model pickers list only providers with a pool credential: seed the plugin's key.
        (row,) = p.seed_pool_credentials(suppressed_sources=frozenset(), explicitly_configured=False)
        self.assertEqual(row["access_token"], "copilot-bridge")
        self.assertEqual(row["base_url"], p.base_url)
        self.assertTrue(row["source"].startswith("manual:"), "desktop pickers list only explicit (manual:) pool entries")
        suppress, _hints = p.remove_credential_source(row["source"])
        self.assertIn(row["source"], suppress, "`hermes auth remove` suppresses the seeded entry")
        self.assertIsNone(p.remove_credential_source("manual:other"), "other entries use Hermes' generic removal")
        self.assertEqual(p.seed_pool_credentials(suppressed_sources=frozenset(suppress),
                                                 explicitly_configured=True), [], "`hermes auth remove` sticks")
        os.environ["COPILOT_BRIDGE_API_KEY"] = "custom"
        self.assertEqual(p.seed_pool_credentials(suppressed_sources=frozenset(), explicitly_configured=False)[0]["access_token"], "custom")

    def test_install_asks_nothing(self):
        # `hermes plugins install` asks to npm-install a package.json at the plugin root.
        self.assertFalse((ROOT / "package.json").exists())
        self.assertFalse((ROOT / "requirements.txt").exists())

    def test_versions_and_docs_agree(self):
        import re
        bp = load("bp_versions")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"v{bp.BRIDGE_VERSION}", readme, "README names the pinned bridge release")
        version = re.search(r"^version:\s*(\S+)", (ROOT / "plugin.yaml").read_text(), re.M).group(1)
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
