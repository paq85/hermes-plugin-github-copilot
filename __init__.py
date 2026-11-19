"""Hermes model-provider plugin: GitHub Copilot through the GitHub Copilot OpenAI bridge.

Selecting provider `copilot-bridge` (config `model.provider: copilot-bridge`, `--provider copilot-bridge`
or `/model`) runs the bridge (https://github.com/paq85/github-copilot-openai-bridge) in the
background. The plugin downloads and installs the pinned bridge release as soon as Hermes loads it;
the bridge starts on first use and exits by itself when idle.
"""

import importlib.util
from pathlib import Path

from providers import register_provider
from providers.base import ProviderProfile

# Load the sibling module by path so this works however Hermes imports the plugin package.
_spec = importlib.util.spec_from_file_location("copilot_bridge_process", Path(__file__).with_name("bridge_process.py"))
_bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bp)

# `manual:` makes Hermes treat the entry as explicit configuration (see seed_pool_credentials).
POOL_SOURCE = "manual:copilot-bridge-plugin"
POOL_SOURCES = (POOL_SOURCE, "copilot-bridge-plugin")  # 1.0.1 seeded the second one
FALLBACK_MODELS = ("auto", "fusion", "fusion-high", "claude-sonnet-5.5", "claude-opus-5.5", "claude-haiku-5.5", "gpt-6.1-sol", "gpt-6-luna")


class CopilotBridgeProfile(ProviderProfile):
    """Starts the bridge lazily: only when the provider is actually used or listed."""

    def create_client(self, **client_kwargs):
        _bp.ensure_running()
        return None  # use the standard OpenAI client against the bridge

    def fetch_models(self, *, api_key=None, base_url=None, timeout=8.0):
        try:
            _bp.ensure_running()
        except Exception:
            return None
        return _bp.list_models(timeout) or None

    def resolve_api_key_secret(self):
        # The local bridge needs no real credential (Copilot auth is the Copilot CLI's login), so the
        # plugin supplies the key itself: nothing to configure after install. It is the same value
        # the plugin-started bridge requires (COPILOT_BRIDGE_API_KEY overrides both sides).
        return _bp.api_key(), "copilot-bridge plugin"

    def seed_pool_credentials(self, *, suppressed_sources=frozenset(), explicitly_configured=False):
        # Hermes' model pickers only list providers with a credential: /model accepts any pool
        # entry, but the desktop pickers (Settings, chat composer) only "explicitly configured"
        # ones, which for a pool entry means a `manual:` source. resolve_api_key_secret() is only
        # used at request time. Installing and enabling this plugin is the user's explicit choice,
        # so the plugin seeds its own key as a manual entry and the provider is listed right after
        # install. Overriding this also replaces Hermes' generic env seeding; api_key() already
        # honours COPILOT_BRIDGE_API_KEY.
        if suppressed_sources & set(POOL_SOURCES):  # `hermes auth remove copilot-bridge ...`
            return []
        return [{"source": POOL_SOURCE, "access_token": _bp.api_key(), "base_url": _bp.base_url(),
                 "label": "copilot-bridge plugin (local bridge)"}]

    def remove_credential_source(self, source):
        # Hermes doesn't suppress a removed `manual:` entry, so without this the seeding above
        # would bring it back on the next load.
        if source in POOL_SOURCES:
            return POOL_SOURCES, ("Set COPILOT_BRIDGE_API_KEY, or reinstall the plugin, to list GitHub Copilot (bridge) again.",)
        return None


register_provider(
    CopilotBridgeProfile(
        name="copilot-bridge",
        display_name="GitHub Copilot (bridge)",
        description="GitHub Copilot via the local GitHub Copilot OpenAI bridge: session continuation, prompt cache, Fusion, reasoning",
        env_vars=("COPILOT_BRIDGE_API_KEY", "COPILOT_BRIDGE_BASE_URL"),
        base_url=_bp.base_url(),
        auth_type="api_key",
        default_aux_model="claude-haiku-5.5",
        fallback_models=FALLBACK_MODELS,
        supports_vision=True,
    )
)

# Get the bridge ready right away (download + npm ci), so the first message doesn't wait for it.
_bp.prepare_in_background()
