#!/usr/bin/env python3
"""Fully remove the copilot-bridge Hermes plugin (and the legacy copilot-proxy plugin). Dry run unless --apply."""
import argparse, json, os, re, shutil, stat, subprocess, sys
from pathlib import Path

# copilot-bridge = hermes-plugin-github-copilot; copilot-proxy = the plugin it replaced (copilot-openai-proxy).
NAMES = ("copilot-bridge", "copilot-proxy")
ENV_PREFIXES = ("COPILOT_BRIDGE_", "COPILOT_PROXY_")
LOGS = ("copilot-bridge.log", "copilot-proxy.log")
found = []  # leftovers still present after run


def say(msg):
    print(msg, flush=True)


def rm_readonly(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(p: Path, apply: bool) -> None:
    if not p.exists() and not p.is_symlink():
        return
    say(f"  [dir] {p}")
    if not apply:
        return
    try:
        if p.is_symlink():
            p.unlink()
        else:
            shutil.rmtree(p, onerror=rm_readonly)
    except OSError as e:
        say(f"    !! could not delete ({e}); quit Hermes and retry")
    if p.exists():
        found.append(str(p))


def backup(p: Path):
    shutil.copy2(p, p.with_name(p.name + ".bak-copilot-bridge"))


def clean_config(home: Path, apply: bool) -> None:
    cfg = home / "config.yaml"
    if not cfg.is_file():
        return
    lines = cfg.read_text(encoding="utf-8").splitlines(keepends=True)
    item = re.compile(r"^\s*-\s*['\"]?(" + "|".join(map(re.escape, NAMES)) + r")['\"]?\s*(#.*)?$")
    keep, removed = [], 0
    for ln in lines:
        if item.match(ln.rstrip("\r\n")):
            removed += 1
        else:
            keep.append(ln)
    if removed:
        say(f"  [config.yaml] {removed} plugin list entr{'y' if removed == 1 else 'ies'} for {'/'.join(NAMES)} (plugins.enabled/disabled)")
        if apply:
            backup(cfg)
            cfg.write_text("".join(keep), encoding="utf-8")
    if re.search(r"provider:\s*['\"]?(" + "|".join(map(re.escape, NAMES)) + ")", "".join(keep)):
        say("  !! config.yaml still has a provider: copilot-bridge/copilot-proxy (model/aux/fallback); pick another model manually")
        found.append(f"{cfg}: provider reference")


def clean_env(home: Path, apply: bool) -> None:
    env = home / ".env"
    if not env.is_file():
        return
    lines = env.read_text(encoding="utf-8").splitlines(keepends=True)
    keep, names = [], []
    for ln in lines:
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z0-9_]+)\s*=", ln)
        if m and m.group(1).startswith(ENV_PREFIXES):
            names.append(m.group(1))
        else:
            keep.append(ln)
    if names:
        say(f"  [.env] {', '.join(names)} (values not shown)")
        if apply:
            backup(env)
            env.write_text("".join(keep), encoding="utf-8")


def clean_auth(home: Path, apply: bool) -> None:
    auth = home / "auth.json"
    if not auth.is_file():
        return
    data = json.loads(auth.read_text(encoding="utf-8"))
    pool = data.get("credential_pool")
    names = [n for n in NAMES if isinstance(pool, dict) and n in pool]
    for name in names:
        say(f"  [auth.json] credential_pool['{name}'] ({len(pool[name])} entr{'y' if len(pool[name]) == 1 else 'ies'})")
    if names and apply:
        backup(auth)
        for name in names:
            del pool[name]
        auth.write_text(json.dumps(data, indent=2), encoding="utf-8")


def proxy_pids():
    """(pid, cmdline) of node processes running a plugin-installed bridge (or legacy proxy) server.mjs."""
    if os.name != "nt":
        out = subprocess.run(["pgrep", "-af", "server.mjs"], capture_output=True, text=True).stdout
        return [(int(l.split()[0]), l) for l in out.splitlines() if "copilot" in l.lower()]
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
          "Where-Object { $_.CommandLine -match 'server\\.mjs' } | "
          "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    res = []
    for l in out.splitlines():
        if "\t" in l:
            pid, cmd = l.split("\t", 1)
            if "copilot" in cmd.lower():
                res.append((int(pid.strip()), cmd))
    return res


def stop_proxy(apply: bool) -> None:
    procs = proxy_pids()
    for pid, cmd in procs:
        say(f"  [process] node pid {pid}: {cmd[:140]}")
        if apply:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"] if os.name == "nt" else ["kill", str(pid)],
                           capture_output=True)
    if not procs:
        say("  [process] no bridge node process running")


def homes(root: Path, only):
    hs = [("default", root)]
    pd = root / "profiles"
    if pd.is_dir():
        hs += [(p.name, p) for p in sorted(pd.iterdir()) if p.is_dir()]
    return [h for h in hs if only in (None, h[0])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--root", default=os.path.join(os.environ.get("LOCALAPPDATA", str(Path.home())), "hermes"))
    ap.add_argument("--only", help="profile name, or 'default' for the root home")
    ap.add_argument("--keep-runtime", action="store_true")
    a = ap.parse_args()
    root = Path(a.root)
    say(("APPLYING" if a.apply else "DRY RUN (no changes)") + f" - root {root}")

    say("Bridge process:")
    stop_proxy(a.apply)

    for name, home in homes(root, a.only):
        say(f"Home '{name}': {home}")
        for name in NAMES:
            rmtree(home / "plugins" / name, a.apply)
            rmtree(home / "plugins" / "model-providers" / name, a.apply)
        clean_config(home, a.apply)
        clean_env(home, a.apply)
        clean_auth(home, a.apply)
        if not a.keep_runtime:
            for log in (home / "logs" / n for n in LOGS):
                if log.is_file():
                    say(f"  [file] {log}")
                    if a.apply:
                        log.unlink(missing_ok=True)

    if not a.keep_runtime:
        cache = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        runtimes = [Path(os.environ["COPILOT_BRIDGE_RUNTIME_DIR"]) if os.environ.get("COPILOT_BRIDGE_RUNTIME_DIR")
                    else cache / "hermes-plugin-github-copilot",
                    Path(os.environ["COPILOT_PROXY_RUNTIME_DIR"]).parent if os.environ.get("COPILOT_PROXY_RUNTIME_DIR")
                    else cache / "copilot-openai-proxy"]  # legacy copilot-proxy plugin
        say("Shared runtime (downloaded bridge):")
        for rt in runtimes:
            rmtree(rt, a.apply)

    if a.apply:
        say("Leftovers after apply: " + ("; ".join(found) if found else "none"))
        say("NEXT: run `hermes gateway restart` and reopen the desktop app; the running gateway still has the provider registered.")
    else:
        say("Re-run with --apply after the user confirms.")
    return 1 if (a.apply and found) else 0


if __name__ == "__main__":
    sys.exit(main())
