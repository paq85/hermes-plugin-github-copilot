---
name: copilot-bridge-uninstall
description: Use when fully removing the copilot-bridge Hermes plugin (hermes-plugin-github-copilot), or the legacy copilot-proxy plugin it replaced.
version: 1.0.0
author: paq85
---

# Fully uninstall the copilot-bridge plugin (GitHub Copilot (bridge) provider)

`hermes plugins remove copilot-bridge` only deletes the plugin dir of ONE profile. Hermes has no
uninstall hook for model-provider plugins and never un-registers a provider in a running process,
so the provider keeps showing in the model picker. A full removal needs all steps below. The same
script also removes the legacy `copilot-proxy` plugin (from `copilot-openai-proxy`), which leaves the
same kinds of leftovers under its own names.

## What is left behind (checked per Hermes home: root `%LOCALAPPDATA%\hermes` AND each `profiles\<name>`)
1. `plugins\copilot-bridge\` (legacy: `plugins\copilot-proxy\`) — plugins are per profile; the default profile is separate.
2. `copilot-bridge` / `copilot-proxy` in `plugins.enabled` / `plugins.disabled` of that home's `config.yaml`.
3. `COPILOT_BRIDGE_*` / `COPILOT_PROXY_*` lines in that home's `.env` (the plugin needs none; older
   `copilot-proxy` versions asked for `COPILOT_PROXY_API_KEY`).
4. `credential_pool["copilot-bridge"]` / `["copilot-proxy"]` in that home's `auth.json`.
5. `provider: copilot-bridge` / `copilot-proxy` in config.yaml (only warned about: the user must pick another model).
6. Running bridge: a `node` process running `server.mjs` from the runtime dir (default port 8788,
   legacy 8787; idle-exits by itself).
7. `logs\copilot-bridge.log` / `logs\copilot-proxy.log` per home, and the shared runtime dirs holding the
   downloaded bridge: `%LOCALAPPDATA%\hermes-plugin-github-copilot\` (legacy `%LOCALAPPDATA%\copilot-openai-proxy\`).
8. The running Hermes process (desktop app + gateway) still has the provider registered -> must be
   restarted with `hermes gateway restart` (Hermes core only adds providers on rescan, never removes;
   closing the desktop app leaves the gateway running).

Source checkouts of the plugin or the bridge are NOT touched.

## Procedure
1. Dry run (default, changes nothing, prints findings):
   `python <plugin repo>/skills/copilot-bridge-uninstall/scripts/uninstall_copilot_bridge.py`
   (optional `--root <hermes root>`).
2. Show the findings to the user; other profiles than the active one are not ours to modify, so
   get explicit OK first. Stop any agent session using the provider first.
3. Apply: `python skills/copilot-bridge-uninstall/scripts/uninstall_copilot_bridge.py --apply`
   (`--keep-runtime` keeps the shared runtime dirs and logs; `--only <profile>` limits to one home,
   `--only default` for the root).
4. Run `hermes -p <profile> plugins list` for each profile to confirm it is gone (the script also
   prints leftovers it could not delete).
5. Run `hermes gateway restart` (stop agent sessions first; closing the desktop app does not stop
   the gateway), have the user reopen the desktop app, then verify
   the picker no longer lists GitHub Copilot (bridge) or GitHub Copilot (proxy).

## Pitfalls
- On Windows the plugin dir may be locked by a running Hermes/node process; the script stops the
  bridge first and reports (never hides) any directory it cannot delete. Retry after quitting Hermes.
- Hermes-bundled git/gh under WindowsApps are access-denied; use system ones in `C:\Program Files\`.
- `config.yaml`, `.env`, `auth.json` get a `.bak-copilot-bridge` backup before edit. `.env` values are
  never printed (they may hold keys). config.yaml is edited by line, no YAML round-trip.
