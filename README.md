# GitHub Copilot for Hermes Agent

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) model-provider plugin that lets
Hermes use your GitHub Copilot subscription. It runs the
[GitHub Copilot OpenAI bridge](https://github.com/paq85/github-copilot-openai-bridge) for you:
the plugin downloads a pinned bridge release, installs it and starts it in the background, so there
is nothing to clone, build or run by hand.

## Requirements

- Node.js 20.19+ (tested on 24)
- GitHub Copilot access, signed in via the Copilot CLI on this machine

## Install

```bash
hermes plugins install paq85/hermes-plugin-github-copilot --enable
```

That is the whole setup. Then pick the provider like any other, e.g.
`hermes --provider copilot-bridge -m auto` (or `hermes model` / `/model`, where it is listed as
**GitHub Copilot (bridge)**). The install asks no questions and needs no API key or other config:
the plugin supplies the placeholder key Hermes requires.

Plugins are **per Hermes profile**: install and enable the plugin in each profile that should use it.

```bash
# A specific profile
hermes -p <profile> plugins install paq85/hermes-plugin-github-copilot --enable

# From a local checkout (on Windows use two slashes after file:, e.g. file://C:/...)
hermes -p <profile> plugins install "file://C:/path/to/hermes-plugin-github-copilot" --enable
```

`--enable` switches the plugin on right away; without it, run `hermes plugins enable copilot-bridge`.
Or in `config.yaml`:

```yaml
model:
  provider: copilot-bridge
  model: auto            # any id from the model picker
```

## How it works

- **Download and install.** This plugin version pins bridge release **v1.0.1**
  (`BRIDGE_VERSION` in `bridge_process.py`). As soon as Hermes loads the plugin (right after
  `plugins install --enable`, the running gateway reloads it), the plugin downloads that release
  from GitHub, runs `npm ci` and keeps it in a per-user directory:
  `%LOCALAPPDATA%\hermes-plugin-github-copilot\bridge\v1.0.1` on Windows,
  `~/.cache/hermes-plugin-github-copilot/bridge/v1.0.1` elsewhere. It is shared by every Hermes
  profile and process; when a plugin update pins a newer bridge, the old version is removed once
  nothing runs from it. If that first install hasn't finished (or failed), the first message does it.
- **Start on demand.** When you use the provider, the plugin starts the bridge on `127.0.0.1:8788`
  and points Hermes at it. One bridge serves every Hermes process on that port and exits by
  itself after `COPILOT_BRIDGE_IDLE_MINUTES` without requests. The bridge requires the plugin's
  key, so a different server on the port is reported instead of used.
- **Local only, no windows.** The bridge listens on `127.0.0.1` only. On Windows, `npm ci` and
  the bridge start without a console window, so nothing pops up, even when Hermes has no console.
- **Packaged Hermes on Windows.** The Hermes desktop app puts its own bundled tools (node, npm,
  gh) first on its PATH, and those can't be started from a plugin ("Access is denied"). The plugin
  uses your own Node.js install and keeps Hermes' tool directories off the bridge's PATH, so the
  Copilot CLI doesn't pick up Hermes' bundled `gh` and report itself signed out.
- **Logs.** Download, `npm ci` and bridge output go to `$HERMES_HOME/logs/copilot-bridge.log`
  (requests, cache hits, cost, context fill).

| Env var | Default | Purpose |
| ------- | ------- | ------- |
| `COPILOT_BRIDGE_PORT` | `8788` | Port of the plugin-started bridge |
| `COPILOT_BRIDGE_BASE_URL` | unset | Use a bridge you run yourself; the plugin then never downloads or starts one |
| `COPILOT_BRIDGE_API_KEY` | `copilot-bridge` | Optional. Key Hermes sends and the plugin-started bridge requires |
| `COPILOT_BRIDGE_IDLE_MINUTES` | `30` | Bridge exits after this many idle minutes (`0` = never) |
| `COPILOT_BRIDGE_STOP_ON_EXIT` | unset | Stop the bridge when the Hermes process that started it exits |
| `COPILOT_BRIDGE_RUNTIME_DIR` | per-user cache | Where downloaded bridge versions are installed |
| `COPILOT_BRIDGE_SOURCE` | unset | Development: install from a local bridge checkout instead of downloading the pinned release |

What the bridge offers (model catalog, context sizes, Fusion, reasoning, vision, session
continuation, cost reporting) is documented in the
[bridge README](https://github.com/paq85/github-copilot-openai-bridge#readme).

## Choosing a model: auto or a specific model, not a Fusion tier

Fusion tiers (`fusion`, `fusion-high`, `fusion-max`) route **each request** to a different
model, and Copilot enforces that model's context window per request. Hermes learns one context
window when a chat starts and keeps it for the whole session, so it cannot follow that. The
bridge therefore advertises one conservative window (200k) for `auto` and every Fusion tier (see
[the bridge README](https://github.com/paq85/github-copilot-openai-bridge#context-windows-of-auto-and-fusion)).

Why Hermes can't track Fusion dynamically:

- Hermes fixes the window per chat at start. Nothing in the request tells it which model Fusion
  picked before the answer arrives.
- The only runtime adjustment is overflow recovery (`agent/turn_overflow.py`,
  `_adopt_provider_context_limit`): when the provider's error reports a limit, Hermes adopts it
  and saves it permanently (`save_provider_context_length`).
- Advertising 272k therefore only helps until the first Claude turn overflows; after that Hermes
  is pinned to 200k again.
- Hermes plugins get no agent or compressor handle, so a plugin cannot change the window per
  request.

**Recommendation:**

- **`auto`** - a safe general default: one conservative 200k window for the whole chat.
- **A specific model** (`claude-sonnet-5.5`, `gpt-6.1-sol`, ...) - best predictability: the
  catalog window matches the model that answers.
- **Context size.** Hermes has no context-size control, so models with a size choice are also
  listed per size, e.g. `claude-haiku-5.5-100k` and `claude-haiku-5.5-1m`.
- **Fusion tiers** - fine for short chats; avoid them for long ones.

## Troubleshooting

- **`Unknown provider 'copilot-bridge'`**: the plugin isn't installed and enabled in the profile you launched. Plugins don't carry across profiles; check with `hermes -p <profile> plugins list`.
- **GitHub Copilot (bridge) missing from the model picker** (desktop Settings, chat composer, `/model`): the pickers list only providers with a credential, and the desktop ones only credentials you added explicitly. The plugin adds its own key to the profile's credential pool as such an entry (`manual:copilot-bridge-plugin` in `hermes auth list`), since installing and enabling it is that explicit choice. Plugins before 1.0.2 didn't, so update the plugin (`hermes -p <profile> plugins update copilot-bridge`) and then run `hermes gateway restart`: the running gateway keeps the provider it loaded first, and closing the desktop app doesn't stop it. If you remove that credential with `hermes auth remove`, the plugin leaves it removed.
- **The bridge doesn't start**: check `copilot-bridge.log` in your Hermes home (`$HERMES_HOME/logs/`), which holds the download and `npm ci` output, and confirm `node --version` reports 20.19 or newer. The first start needs access to `github.com` (download) and the npm registry.
- **`port 8788 is used by another server`**: something else listens on the port; set `COPILOT_BRIDGE_PORT` to a free port.
- **Bridge answers 500 with `Not authenticated`**: sign in with the Copilot CLI (`copilot` then `/login`) in a normal terminal, then try again.
- **Coming from the `copilot-proxy` plugin** (`paq85/copilot-openai-proxy`): this plugin replaces it under new names: provider `copilot-bridge` (was `copilot-proxy`) and `COPILOT_BRIDGE_*` settings (were `COPILOT_PROXY_*`), with no aliases. Install this plugin, change `model.provider: copilot-proxy` to `copilot-bridge` in your config, and remove the old plugin (the uninstall skill below removes its leftovers too).

## Uninstalling

Agents can do all of this for you: the repo ships a skill, [`skills/copilot-bridge-uninstall`](skills/copilot-bridge-uninstall/SKILL.md), whose script (`python skills/copilot-bridge-uninstall/scripts/uninstall_copilot_bridge.py`, dry run by default, `--apply` to delete) covers every item below across all profiles, and the legacy `copilot-proxy` plugin too. Copy the folder into a Hermes `skills/` directory to make it available as a skill.

`hermes plugins remove copilot-bridge` (or the desktop Plugins page) only deletes the plugin directory of **the profile you ran it in**. Hermes has no uninstall hook for model-provider plugins, so the plugin cannot clean up after itself. If "GitHub Copilot (bridge)" is still in the model picker afterwards, check these:

- **Another profile still has the plugin.** Plugins are per profile; run `hermes -p <profile> plugins list` for each one, and remove it everywhere (`plugins.enabled` in that profile's `config.yaml` should no longer list `copilot-bridge`).
- **The running Hermes process still has the provider registered.** Hermes discovers provider plugins by adding new ones and never un-registers a removed one, so the entry stays until you **restart the gateway** (`hermes gateway restart`; closing the desktop app leaves it running) and reopen the desktop app.
- **The bridge process** exits by itself when idle; to stop it immediately, end the `node` process listening on port 8788. The downloaded bridge (`%LOCALAPPDATA%\hermes-plugin-github-copilot\`) and `copilot-bridge.log` can be deleted.

## Development

```bash
python -m unittest test_plugin -v                       # on Windows: py -3 test_plugin.py -v
COPILOT_BRIDGE_SOURCE=../github-copilot-openai-bridge python -m unittest test_plugin -v
```

The tests that start the bridge need Node.js, a signed-in Copilot CLI and access to GitHub (they
download the pinned release); with `COPILOT_BRIDGE_SOURCE` set, one of them installs from that
local bridge checkout instead. `AGENTS.md` has the versioning and release rules.

## Files

- `plugin.yaml`, `__init__.py` — the Hermes plugin (provider `copilot-bridge`)
- `bridge_process.py` — downloads, installs, starts and stops the bridge (no Hermes imports)
- `test_plugin.py` — tests
- `skills/copilot-bridge-uninstall/` — agent skill that removes the plugin from every profile
- `AGENTS.md` — guidance for coding agents
