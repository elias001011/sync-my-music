"""SettingsStore — the UI's single source of truth for engine config.

`settings.json` holds everything the wizard and settings page manage (provider
app credentials, sync options, download/Jellyfin config). On save it also
regenerates a managed env file (`app.env`) and updates `os.environ`.

Why a managed env file: the engine reads `os.getenv(...)` and reloads its env
each pass via `load_dotenv(..., override=True)`. `override=True` makes the file
win over the process environment, so a stale hand-edited `.env` would clobber a
freshly wizard-saved token. Pointing the engine at THIS file (via SONGMIRROR_ENV_FILE,
wired in the app factory) makes wizard saves authoritative instead.
"""

import hashlib
import json
import os
import re
import shlex
from pathlib import Path


def _scalar(v):
    return v is not None and not isinstance(v, (dict, list))


def _open_private(path):
    """Open for writing with owner-only perms (0o600) from creation — these files
    hold OAuth secrets and service tokens. POSIX modes are ignored on Windows,
    but the deployment where at-rest exposure matters (Linux/Docker with a
    bind-mounted data dir) honors them."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.chmod(path, 0o600)  # enforce on a pre-existing file too (best-effort)
    except OSError:
        pass
    return os.fdopen(fd, "w", encoding="utf-8")


# Surface keys every account can toggle. The engine's surface model (see
# docs/architecture.md) keeps them independent so one broken surface can be
# disabled without pausing the whole provider.
SURFACES = ("playlists", "liked_tracks", "saved_albums", "followed_artists", "history")

DEFAULT_SURFACES = {surface: True for surface in SURFACES}

# The settings keys each provider's live connector actually consumes. Used for
# (a) migrating legacy single-account settings into `{provider}:default`
# registry entries and (b) building per-account config snapshots that the
# engine passes DIRECTLY to target instances (no os.environ swapping).
PROVIDER_KEYS: dict[str, tuple[str, ...]] = {
    "spotify": ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_TOKEN_CACHE",
                "SPOTIFY_REDIRECT_URI", "SPOTIFY_WRITE_BACKEND", "SPOTIFY_SP_DC",
                "SPOTIFY_SP_DC_FILE", "SPOTIFY_ISRC_CLIENTS", "SPOTIFY_CACHE_FILE"),
    "ytmusic": ("YTMUSIC_BROWSER_AUTH", "YTMUSIC_PREFER_BROWSER", "YTMUSIC_AUTH_FILE",
                 "YTMUSIC_OAUTH_CLIENT_ID", "YTMUSIC_OAUTH_CLIENT_SECRET", "YTMUSIC_CACHE_FILE"),
    "tidal": ("TIDAL_CACHE_FILE", "TIDAL_WEB_HEADERS", "TIDAL_TOKEN_FILE", "TIDAL_COUNTRY_CODE",
              "TIDAL_CLIENT_ID", "TIDAL_CLIENT_SECRET", "TIDAL_BEARER_TOKEN"),
    "qobuz": ("QOBUZ_CACHE_FILE", "QOBUZ_WEB_REQUEST", "QOBUZ_APP_ID", "QOBUZ_USER_AUTH_TOKEN",
              "QOBUZ_USER_ID"),
    "deezer": ("DEEZER_CACHE_FILE", "DEEZER_WEB_HEADERS", "DEEZER_REFRESH_TOKEN",
                "DEEZER_WEB_SESSION_FILE", "DEEZER_WEB_ENDPOINT", "DEEZER_TOKEN_FILE"),
    "amazon": ("AMAZON_MUSIC_CACHE_FILE", "AMAZON_MUSIC_WEB_HEADERS", "AMAZON_MUSIC_RENEWAL_REQUEST",
               "AMAZON_MUSIC_WEB_SESSION_FILE", "AMAZON_MUSIC_WEB_ENDPOINT", "AMAZON_MUSIC_CLIENT_ID",
               "AMAZON_MUSIC_CLIENT_SECRET", "AMAZON_MUSIC_API_KEY", "AMAZON_MUSIC_TOKEN_FILE"),
    "apple": ("APPLE_CACHE_FILE", "APPLE_BEARER_TOKEN", "APPLE_USER_TOKEN", "APPLE_STOREFRONT"),
    "jellyfin": ("JELLYFIN_URL", "JELLYFIN_API_KEY"),
}

# Per-account session/token FILES: a named account must never reuse (or
# overwrite) the default account's file, so the snapshot points it at its own.
_PER_ACCOUNT_FILE_KEYS: dict[str, tuple[str, ...]] = {
    "spotify": ("SPOTIFY_TOKEN_CACHE",),  # SPOTIFY_SP_DC_FILE is derived in account_config_snapshot (cookie file naming)
    "ytmusic": ("YTMUSIC_AUTH_FILE", "YTMUSIC_BROWSER_AUTH"),
    "tidal": ("TIDAL_TOKEN_FILE",),
    "deezer": ("DEEZER_WEB_SESSION_FILE", "DEEZER_TOKEN_FILE"),
    "amazon": ("AMAZON_MUSIC_WEB_SESSION_FILE", "AMAZON_MUSIC_TOKEN_FILE"),
    "apple": (),
    "qobuz": (),
    "jellyfin": (),
}


class SettingsStore:
    def __init__(self, dir=None):
        # Default to $SONGMIRROR_DATA_DIR (Docker points it at the /data bind mount) so
        # wizard-saved config + OAuth secrets land on the persistent volume, not
        # the container's ephemeral filesystem. Falls back to a local ./data.
        self._dir = Path(dir or os.getenv("SONGMIRROR_DATA_DIR") or "data")
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._dir, 0o700)  # keep the secrets dir owner-only (best-effort; POSIX)
        except OSError:
            pass
        self._json = self._dir / "settings.json"
        self.env_path = str(self._dir / "app.env")
        self._data = self._read()

    def _read(self):
        try:
            with open(self._json, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def load(self):
        return dict(self._data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    # -- account registry -----------------------------------------------------
    # Live credential profiles live under the ACCOUNTS settings key, one entry
    # per account_id, each with its own config namespace — so two accounts of
    # the same provider never share a token/cookie/cache, and concurrent passes
    # never swap os.environ between accounts (targets receive their config
    # directly instead).

    @staticmethod
    def account_provider(account_id: str) -> str:
        return str(account_id).split(":", 1)[0]

    def create_account_id(self, provider: str, label: str) -> str:
        """A stable, unique account id for a new named profile: `{provider}:{slug}`
        where slug is derived from the chosen label. Collisions get a numeric
        suffix (`spotify:work-2`), and `default` is never reused as a name."""
        base = re.sub(r"[^a-z0-9]+", "-", (label or "").strip().lower()).strip("-")
        if not base or base == "default":
            base = "account"
        base = base[:24].rstrip("-")
        slug, n = base, 2
        existing = set(self.accounts())
        while f"{provider}:{slug}" in existing:
            slug = f"{base}-{n}"
            n += 1
        return f"{provider}:{slug}"

    @staticmethod
    def account_slug(account_id: str) -> str:
        """Filesystem-safe account suffix: the default account is the bare
        provider (legacy paths), a named account gets a short hash suffix."""
        provider, _, rest = str(account_id).partition(":")
        if not rest or rest == "default":
            return provider
        return f"{provider}-{hashlib.sha256(account_id.encode()).hexdigest()[:8]}"

    def accounts(self) -> dict[str, dict]:
        """account_id -> registry entry (label, enabled, config, surfaces)."""
        raw = self.get("ACCOUNTS") or {}
        return raw if isinstance(raw, dict) else {}

    def account(self, account_id: str) -> dict | None:
        entry = self.accounts().get(account_id)
        if entry is None:
            return None
        surfaces = dict(DEFAULT_SURFACES)
        surfaces.update(entry.get("surfaces") or {})
        return {"label": entry.get("label") or account_id, "enabled": bool(entry.get("enabled", True)),
                "config": dict(entry.get("config") or {}), "surfaces": surfaces}

    def save_account(self, account_id: str, *, label: str | None = None, enabled: bool | None = None,
                     config: dict | None = None, surfaces: dict | None = None) -> dict:
        """Upsert one registry entry. `config` merges (never replaces) so one
        field update cannot wipe the account's other credentials."""
        accounts = self.accounts()
        entry = accounts.setdefault(account_id, {})
        if label is not None:
            entry["label"] = str(label).strip() or account_id
        if enabled is not None:
            entry["enabled"] = bool(enabled)
        if config:
            entry["config"] = {**(entry.get("config") or {}), **config}
        if surfaces is not None:
            merged = dict(DEFAULT_SURFACES)
            merged.update(entry.get("surfaces") or {})
            merged.update(surfaces)
            entry["surfaces"] = merged
        self.save({"ACCOUNTS": accounts})
        return self.account(account_id)

    def delete_account_registry(self, account_id: str) -> bool:
        accounts = self.accounts()
        if account_id not in accounts:
            return False
        accounts.pop(account_id)
        self.save({"ACCOUNTS": accounts})
        return True

    def account_config(self, account_id: str, key: str, default=None):
        """One credential for one account. Named accounts read only their own
        config; the `:default` account falls back to the legacy flat key so
        pre-migration settings keep working untouched."""
        entry = self.account(account_id)
        if entry:
            value = entry["config"].get(key)
            if value is not None:
                return value
        if account_id.endswith(":default"):
            return self.get(key, default)
        return default

    def account_enabled(self, account_id: str) -> bool:
        entry = self.account(account_id)
        return entry["enabled"] if entry else True

    def account_surface(self, account_id: str, surface: str) -> bool:
        entry = self.account(account_id)
        return bool(entry["surfaces"].get(surface, True)) if entry else True

    def account_config_snapshot(self, account_id: str) -> dict:
        """The full settings snapshot one account's engine target should read.

        The `:default` account inherits the legacy flat keys (migration path, so
        pre-registry settings keep working untouched); a named account reads
        only its own config and gets its own session/token file paths — two
        accounts of the same provider can never collide on a file."""
        provider = self.account_provider(account_id)
        config: dict = {}
        if account_id.endswith(":default"):
            for key in PROVIDER_KEYS.get(provider, ()):
                value = self.get(key)
                if value not in (None, ""):
                    config[key] = value
        entry = self.account(account_id)
        if entry:
            config.update({k: v for k, v in entry["config"].items() if v not in (None, "")})
        if not account_id.endswith(":default"):
            slug = self.account_slug(account_id)
            for key in _PER_ACCOUNT_FILE_KEYS.get(provider, ()):
                config.setdefault(key, f"data/{slug}_{key.casefold()}.json")
            # Spotify's cookie file lives at the path engine/spotify_cookie.py
            # derives per account (spotify_sp_dc.<slug>.private) — mirror it here
            # so a named account's snapshot and its connector agree on one file.
            if provider == "spotify":
                config.setdefault("SPOTIFY_SP_DC_FILE", f"data/spotify_sp_dc.{slug}.private")
        return config

    def migrate_accounts(self) -> int:
        """Create `{provider}:default` registry entries for providers that have
        legacy single-account config, without touching anything already there.
        Returns how many entries were created."""
        created = 0
        for provider, keys in PROVIDER_KEYS.items():
            account_id = f"{provider}:default"
            if account_id in self.accounts():
                continue
            if not any(self.get(key) for key in keys):
                continue
            self.save_account(account_id, label=provider.capitalize(), enabled=True)
            created += 1
        return created

    def save(self, values):
        """Merge non-None `values`, persist json + env file, apply to os.environ."""
        self._data.update({k: v for k, v in values.items() if v is not None})
        with _open_private(self._json) as f:
            json.dump(self._data, f, indent=2)
        self._render_env()
        self.apply_to_env()

    def reload(self):
        """Reload restored settings from disk and refresh the managed env."""
        previous_scalar_keys = {key for key, value in self._data.items() if _scalar(value)}
        self._data = self._read()
        for key in previous_scalar_keys - self._data.keys():
            os.environ.pop(key, None)
        self._render_env()
        self.apply_to_env()
        return self.load()

    def _render_env(self):
        lines = [f"{k}={shlex.quote(str(v))}" for k, v in self._data.items() if _scalar(v)]
        with _open_private(self.env_path) as f:
            f.write("\n".join(lines) + "\n")

    def apply_to_env(self):
        """Project scalar settings into the process env (engine reads os.getenv)."""
        for k, v in self._data.items():
            if _scalar(v):
                os.environ[k] = str(v)
