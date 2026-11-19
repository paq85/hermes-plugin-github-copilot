# AGENTS.md

Guidance for coding agents working in this repository. Read `README.md` for what the plugin does
and how it is used; this file covers how to change it.

## What lives here

`hermes-plugin-github-copilot`: a Hermes Agent model-provider plugin (provider `copilot-bridge`)
that runs the GitHub Copilot OpenAI bridge,
[`paq85/github-copilot-openai-bridge`](https://github.com/paq85/github-copilot-openai-bridge).

- `plugin.yaml`, `__init__.py` — plugin manifest and provider registration.
- `bridge_process.py` — downloads the pinned bridge release, installs it, starts/stops it. Must stay
  free of Hermes imports so it can be tested alone.
- `test_plugin.py` — tests (see below).
- `skills/copilot-bridge-uninstall/` — agent skill (SKILL.md + script) that fully removes the plugin
  (and the legacy `copilot-proxy` plugin) from every Hermes profile. Update it when the plugin gains
  new files, env vars or runtime locations.

The bridge code is **not** in this repository. Bridge changes are made in the bridge repository,
released there as a `vX.Y.Z` tag, and then picked up here by bumping `BRIDGE_VERSION`.

## Versioning: Semantic Versioning

The project follows [Semantic Versioning 2.0.0](https://semver.org/) (`MAJOR.MINOR.PATCH`).

- **Every change that ships behavior bumps `version:` in `plugin.yaml`** in the same commit or PR.
  Hermes only offers a plugin update when that version changes, so an unbumped change never
  reaches users.
- **`BRIDGE_VERSION`** in `bridge_process.py` pins the bridge release tag the plugin downloads. It
  must name a tag that exists in the bridge repository; never point it at a branch. Changing it is a
  plugin release: bump `plugin.yaml` at the level of the bridge change (a bridge MAJOR that changes
  what Hermes users see or configure is a plugin MAJOR; otherwise MINOR or PATCH), and update the
  version named in `README.md` (a test checks it).
- Which part to bump:
  - **MAJOR** — anything that breaks an existing user: a renamed provider ID (`copilot-bridge`),
    removed or renamed `COPILOT_BRIDGE_*` env var, changed default port, a raised minimum Node.js
    or Python version, anything that needs users to change their Hermes config.
  - **MINOR** — backwards-compatible features: new env vars, plugin capabilities, a bridge release
    with new features.
  - **PATCH** — backwards-compatible fixes, and bridge releases with fixes only.
  - Docs-, test- or CI-only changes don't need a bump.
- Several changes going out together get a single bump, at the highest level among them.
- Don't create git tags unless asked.

## Testing

- `py -3 test_plugin.py -v` (Windows) / `python3 -m unittest test_plugin -v`. Tests that start the
  bridge need Node.js, a signed-in Copilot CLI and GitHub access (they download the pinned
  release); `COPILOT_BRIDGE_SOURCE=<bridge checkout>` also runs the local-install test. CI sets
  `CI` and runs only the tests that need neither.
- For install or startup changes, also check the real flow: a fresh profile
  (`hermes profile create <name> --no-alias --no-credentials --no-skills`), the install command
  from a local copy of the tree (`hermes -p <name> plugins install "file://<path>" --enable`), one
  message (`hermes -p <name> --provider copilot-bridge -m claude-haiku-5.5 -z "Reply with just: ok"`),
  then delete the profile. `-z` runs in the already-running Hermes gateway, so it uses the
  gateway's environment, not your shell's.
- Run the tests relevant to a change before committing, and report failures plainly.

## Conventions and constraints

- **One command is the whole Hermes setup**: `hermes plugins install paq85/hermes-plugin-github-copilot --enable`,
  then choosing the provider. Never add a step after it:
  - no `package.json` or Python dependency declaration at the repository root — `plugins install`
    would ask to install it; the plugin installs the bridge itself (`runtime_dir()`);
  - no required config or `.env` keys — `resolve_api_key_secret()` supplies the key Hermes insists on
    at request time, and `seed_pool_credentials()` puts it in the credential pool so the model
    pickers, which list only providers with a credential, show the provider. The desktop pickers
    (Settings, chat composer) accept only "explicitly configured" providers, which for a pool entry
    means a `manual:` source, hence `manual:copilot-bridge-plugin`; `remove_credential_source()`
    makes `hermes auth remove` stick, which Hermes doesn't do for `manual:` entries. Never set the
    key via `os.environ` instead: that leaks the provider into every profile of the Hermes process;
  - prerequisites stay limited to Node.js 20.19+ and a signed-in Copilot CLI.
- The bridge binds `127.0.0.1` only; never pass anything that would widen that.
- The plugin-started bridge always gets `BRIDGE_API_KEY`, and a port only counts as ours when
  that key gets a 200 (`_probe()`); a server that rejects it is reported, never used.
- Windows: every child process goes through `_hidden()`, or a console/Windows Terminal window pops
  up when Hermes runs without a console (desktop app, `pythonw`).
- Windows packaged Hermes: its bundled tools (node, npm, gh) come first on its PATH and fail with
  "Access is denied" when the plugin spawns them. Resolve executables with `_which()` and give
  children `_child_env()` (PATH filtered by `_clean_path()`).
- Hermes may import the plugin from a per-process snapshot of its directory. Never write into the
  plugin directory or assume files there persist; the bridge lives in the durable runtime
  directory (`runtime_dir()`), one subdirectory per bridge version, installed through a staging
  directory and an atomic rename so concurrent Hermes processes are safe.
- The bridge interface the plugin relies on (env vars `PORT`, `BRIDGE_API_KEY`, `IDLE_EXIT_MS`,
  `NO_COLOR`; entry `server.mjs`; exit code 3 when the port is taken) is documented in the bridge's
  AGENTS.md. A bridge release that changes it needs matching changes here.
- User-facing behavior changes update `README.md` in the same change.

## Commits

- Imperative, descriptive subject line; body explaining why when it isn't obvious.
- Commit or push only when asked.
