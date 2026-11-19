"""Downloads, installs, starts, health-checks and stops the GitHub Copilot OpenAI bridge for the plugin.

The bridge is a separate project (https://github.com/paq85/github-copilot-openai-bridge). The plugin
pins one release of it (BRIDGE_VERSION), downloads that tag from GitHub on first use, installs its
Node.js dependencies once into a durable per-user directory and runs it from there.

Kept free of Hermes imports so it can be tested on its own.
"""

from __future__ import annotations

import atexit
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

BRIDGE_REPO = "paq85/github-copilot-openai-bridge"
BRIDGE_VERSION = "1.0.1"  # a release tag (v1.0.1) of BRIDGE_REPO; bump together with plugin.yaml
BRIDGE_FILES = ("server.mjs", "package.json", "package-lock.json")
DEFAULT_PORT = 8788  # not the bridge's own 8787, so a bridge started by hand is never mistaken for ours

ROOT = Path(__file__).resolve().parent
_lock = threading.Lock()  # one start at a time in this process
_install_lock = threading.Lock()  # one install at a time in this process (background prepare + first use)
_proc: subprocess.Popen | None = None
_log_handle = None


def port() -> int:
    return int(os.environ.get("COPILOT_BRIDGE_PORT", str(DEFAULT_PORT)))


def base_url() -> str:
    return os.environ.get("COPILOT_BRIDGE_BASE_URL") or f"http://127.0.0.1:{port()}/v1"


def api_key() -> str:
    # Hermes needs a non-empty key; the plugin-started bridge requires exactly this one.
    return os.environ.get("COPILOT_BRIDGE_API_KEY") or "copilot-bridge"


def _managed() -> bool:
    """False when the user points the plugin at a bridge they run themselves."""
    return not os.environ.get("COPILOT_BRIDGE_BASE_URL")


def _request(path: str, timeout: float = 2.0):
    req = urllib.request.Request(base_url() + path, headers={"Authorization": f"Bearer {api_key()}"})
    return urllib.request.urlopen(req, timeout=timeout)


def _probe() -> str:
    """'ok' = a bridge accepting our key answers; 'down' = nothing listens; 'foreign' = something else does."""
    try:
        with _request("/sessions") as r:
            return "ok" if r.status == 200 else "foreign"
    except urllib.error.HTTPError:
        return "foreign"  # e.g. 401: a bridge or other server that doesn't take our key
    except Exception:
        return "down"


def is_healthy() -> bool:
    return _probe() == "ok"


def list_models(timeout: float = 15.0) -> list[str] | None:
    try:
        with _request("/models", timeout) as r:
            return [m["id"] for m in json.load(r).get("data", [])]
    except Exception:
        return None


def log_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    d = home / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        d = Path(tempfile.gettempdir())
    return d / "copilot-bridge.log"


def _log(msg: str) -> None:
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(f"[copilot-bridge plugin] {msg}\n")
    except OSError:
        pass


def _clean_path(value: str) -> str:
    """Remove Hermes' packaged (MSIX) tool directories from a PATH value.

    Hermes may run from a packaged layout whose bundled tools (node, npm, gh, git shims) live under
    WindowsApps\\...\\agent-payload. Spawning those from the plugin fails with "Access is denied"
    (package identity), and the Copilot CLI prefers a `gh` found on PATH for authentication, so a
    bridge that inherits them reports itself unauthenticated.
    """
    kept = []
    for entry in value.split(os.pathsep):
        if not entry:
            continue
        lowered = entry.lower().replace("/", "\\")
        if "agent-payload" in lowered or "windowsapps\\nousresearchinc" in lowered:
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


def _which(name: str) -> str | None:
    """Resolve node/npm outside Hermes' packaged tool directories (a user-installed Node.js first)."""
    return shutil.which(name, path=_clean_path(os.environ.get("PATH", ""))) or shutil.which(name)


def _child_env(**extra: str) -> dict:
    env = {**os.environ, **extra}
    env["PATH"] = _clean_path(env.get("PATH", ""))
    return env


def _hidden(detach: bool = False) -> dict:
    """Popen kwargs so children never open a console window on Windows.

    Hermes may run without a console (desktop app, pythonw); a console child (node, npm.cmd)
    then gets a new visible console - a Windows Terminal window on Windows 11 - unless told not to.
    """
    if sys.platform != "win32":
        return {"start_new_session": True} if detach else {}
    flags = subprocess.CREATE_NO_WINDOW
    if detach:
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP  # survives the parent and ignores its Ctrl+C
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE, also for a GUI or .cmd-shim launched through cmd.exe
    return {"creationflags": flags, "startupinfo": si}


def _has_deps(d: Path) -> bool:
    return (d / "server.mjs").is_file() and (d / "node_modules" / "@github" / "copilot-sdk" / "package.json").is_file()


def _runtime_base() -> Path:
    """Durable, per-user home for installed bridges, shared by every Hermes profile and process."""
    if os.environ.get("COPILOT_BRIDGE_RUNTIME_DIR"):
        return Path(os.environ["COPILOT_BRIDGE_RUNTIME_DIR"])
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "hermes-plugin-github-copilot" / "bridge"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "hermes-plugin-github-copilot" / "bridge"


def _source_dir() -> Path | None:
    """A local bridge checkout to install from instead of downloading (development and tests)."""
    src = os.environ.get("COPILOT_BRIDGE_SOURCE")
    return Path(src) if src else None


def bridge_url() -> str:
    return f"https://github.com/{BRIDGE_REPO}/archive/refs/tags/v{BRIDGE_VERSION}.tar.gz"


def _runtime_key() -> str:
    src = _source_dir()
    if src is None:
        return f"v{BRIDGE_VERSION}"
    digest = hashlib.sha256()
    for name in BRIDGE_FILES:
        digest.update(name.encode() + b"\0" + (src / name).read_bytes() + b"\0")
    return "local-" + digest.hexdigest()[:16]


def _fetch_bridge(dest: Path) -> None:
    """Put the pinned bridge release's files into dest (download), or copy them from COPILOT_BRIDGE_SOURCE."""
    src = _source_dir()
    if src is not None:
        for name in BRIDGE_FILES:
            shutil.copy2(src / name, dest / name)
        return
    url = bridge_url()
    _log(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = member.name.split("/")
            # GitHub archives hold one top-level folder; only the bridge's own top-level files are used.
            if len(parts) == 2 and parts[1] in BRIDGE_FILES and member.isfile():
                (dest / parts[1]).write_bytes(tar.extractfile(member).read())
    missing = [n for n in BRIDGE_FILES if not (dest / n).is_file()]
    if missing:
        raise RuntimeError(f"bridge release v{BRIDGE_VERSION} from {url} lacks {', '.join(missing)}")
    version = json.loads((dest / "package.json").read_text(encoding="utf-8")).get("version")
    if version != BRIDGE_VERSION:
        raise RuntimeError(f"{url} contains bridge version {version}, expected {BRIDGE_VERSION}")


def runtime_dir() -> Path:
    """Directory with the pinned bridge and its node_modules, ready to run; installs it on first use.

    Hermes may import the plugin from a per-process snapshot of its directory, so nothing is kept in
    the plugin directory: the bridge lives in a durable per-user directory, one subdirectory per
    bridge version, shared by every Hermes profile and process.
    """
    base = _runtime_base()
    target = base / _runtime_key()
    if _has_deps(target):
        return target
    with _install_lock:
        if _has_deps(target):
            return target
        npm = _which("npm")
        if not npm:
            raise RuntimeError("npm not found; install Node.js 20.19+ (https://nodejs.org) to use the GitHub Copilot plugin")
        base.mkdir(parents=True, exist_ok=True)
        _remove_stale_staging(base)
        # Install into a private staging directory, then rename it into place: concurrent Hermes
        # processes never see (or run from) a half-installed bridge.
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=base))
        try:
            _fetch_bridge(staging)
            _log(f"installing bridge {target.name} dependencies into {target}")
            with open(log_path(), "ab") as log:
                code = subprocess.run([npm, "ci", "--omit=dev", "--no-audit", "--no-fund"], cwd=staging,
                                      env=_child_env(), stdin=subprocess.DEVNULL, stdout=log,
                                      stderr=subprocess.STDOUT, **_hidden()).returncode
            if code != 0:
                raise RuntimeError(f"npm ci failed with exit code {code}; see {log_path()}")
            try:
                os.replace(staging, target)
            except OSError:
                if not _has_deps(target):  # not a concurrent install that got there first
                    raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    _prune_runtimes(base, keep=target)
    return target


def _remove_stale_staging(base: Path, max_age_s: float = 3600) -> None:
    """Staging dirs left by a process that exited mid-install (e.g. a short `hermes` command)."""
    for d in base.glob(".staging-*"):
        try:
            if time.time() - d.stat().st_mtime > max_age_s:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _prune_runtimes(base: Path, keep: Path) -> None:
    """Best-effort removal of other bridge versions.

    Each is renamed away before deletion: on Windows that rename fails while a bridge still runs
    from it, so a live runtime is never half-deleted under its running bridge.
    """
    for d in base.iterdir():
        if d == keep or not d.is_dir() or d.name.startswith(".staging-"):
            continue
        trash = base / f".staging-old-{d.name}-{os.getpid()}"
        try:
            os.rename(d, trash)
        except OSError:
            continue
        shutil.rmtree(trash, ignore_errors=True)


def prepare_in_background() -> threading.Thread | None:
    """Download and install the bridge without starting it, so the first message doesn't wait.

    Called when Hermes loads the plugin (right after `hermes plugins install --enable` the running
    gateway reloads it). Failures are only logged: first use retries and reports them.
    """
    if not _managed():
        return None

    def run():
        try:
            runtime_dir()
        except Exception:
            _log("background install failed (retried on first use):\n" + traceback.format_exc())

    t = threading.Thread(target=run, name="copilot-bridge-install", daemon=True)
    t.start()
    return t


def _idle_exit_ms() -> str:
    # A plugin-started bridge is shared by every Hermes process on this port (desktop gateway,
    # `hermes chat`, cron), so it must not die with the process that happened to start it.
    # It exits on its own after this many idle minutes instead (0 = never).
    return str(int(float(os.environ.get("COPILOT_BRIDGE_IDLE_MINUTES", "30")) * 60_000))


def _foreign_error() -> RuntimeError:
    return RuntimeError(
        f"port {port()} is used by another server that doesn't accept this plugin's key; "
        "set COPILOT_BRIDGE_PORT to a free port")


def ensure_running(wait: float = 40.0) -> str:
    """Make sure the bridge answers on base_url(); install and start it if needed. Returns the base URL."""
    global _proc, _log_handle
    state = _probe()
    if state == "ok":
        return base_url()
    if not _managed():
        raise RuntimeError(f"GitHub Copilot bridge at {base_url()} is not reachable with the configured key")
    if state == "foreign":
        raise _foreign_error()
    with _lock:
        if _probe() != "ok" and (_proc is None or _proc.poll() is not None):
            node = _which("node")
            if not node:
                raise RuntimeError("Node.js not found; install Node.js 20.19+ to use the GitHub Copilot plugin")
            cwd = runtime_dir()
            env = _child_env(PORT=str(port()), NO_COLOR="1", IDLE_EXIT_MS=_idle_exit_ms(), BRIDGE_API_KEY=api_key())
            _log_handle = open(log_path(), "ab")
            # server.mjs always binds 127.0.0.1 only; nothing in the environment can widen that.
            _proc = subprocess.Popen([node, "server.mjs"], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                     stdout=_log_handle, stderr=subprocess.STDOUT, **_hidden(detach=True))
            if os.environ.get("COPILOT_BRIDGE_STOP_ON_EXIT"):
                atexit.register(stop)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        state = _probe()
        if state == "ok":
            return base_url()
        code = _proc.poll() if _proc is not None else None
        if code == 3 and state == "foreign":
            raise _foreign_error()
        # Exit code 3 = port taken: a bridge started by another Hermes process won the race; wait for it.
        if code is not None and code != 3:
            raise RuntimeError(f"GitHub Copilot bridge exited with code {code}; see {log_path()}")
        time.sleep(0.25)
    raise RuntimeError(f"GitHub Copilot bridge did not become ready within {wait:.0f}s; see {log_path()}")


def stop() -> None:
    global _proc
    p, _proc = _proc, None
    if p is not None and p.poll() is None:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
