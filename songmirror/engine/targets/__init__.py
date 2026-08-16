"""Mirror targets: the services a playlist is mirrored across.

Adding a provider (Deezer, Tidal, …) is deliberately local:
  1. Write `targets/<svc>.py` with a `MirrorTarget` subclass implementing the
     ~8 methods (see base.py). Carry ISRC in `playlist_tracks` if the API has
     it — that's what makes cross-provider matching reliable and free.
  2. Add one line to `_REGISTRY` below: `source -> builder(opts, sp) -> target|None`.
Everything else — one-way mirroring, N-way reconcile, canonical identity,
caching, safety rails — is provider-agnostic and needs no change.
"""

import os

from .apple import AppleMusicTarget
from .amazon_music import AmazonMusicTarget
from .base import MirrorTarget, TargetAuthError, mirror_pair, reconcile
from .deezer import DeezerTarget
from .qobuz import QobuzTarget
from .spotify_target import SpotifyTarget
from .tidal import TidalTarget
from . import ytmusic

__all__ = ["AppleMusicTarget", "AmazonMusicTarget", "DeezerTarget", "QobuzTarget",
           "SpotifyTarget", "TidalTarget", "MirrorTarget", "TargetAuthError",
           "mirror_pair", "reconcile", "build_targets", "build_peers", "build_one",
           "build_account_target", "is_peer"]


def _apple(opts, account=None):
    from ..config import required_env_from
    from ..logs import log_note
    config = opts.account_config(account) if account else None
    try:
        required_env_from(config, "APPLE_BEARER_TOKEN")
        required_env_from(config, "APPLE_USER_TOKEN")
        return AppleMusicTarget(opts.storefront, opts.cache_file, config=config)
    except RuntimeError as e:
        log_note(f"Apple Music skipped: {e}", tag="apple")
        return None


def _rest_provider(target_cls, label, opts, account=None):
    """Build a REST peer from the account's own config (or env for the CLI),
    logging a clean skip when absent."""
    from ..logs import log_note
    config = opts.account_config(account) if account else None
    try:
        return target_cls(config=config)
    except RuntimeError as e:
        log_note(f"{label} skipped: {e}", tag=target_cls.tag)
        return None


def _apply_account(target, account_id):
    """Give a built target its per-account identity: state namespace and, for a
    named account, its own resolve cache file (so two accounts of the same
    provider never share cache state). Legacy (no account) targets stay exactly
    as before."""
    from ..config import account_state_key

    if target is None or not account_id:
        return target
    target.state_key = account_state_key(account_id)
    if account_state_key(account_id) != target.source:
        slug = account_id.replace(":", "-")
        target.cache_file = f"{slug}_resolve_cache.json"
    return target


# source -> builder(opts, sp, sync_peer, songs, account) -> a ready MirrorTarget,
# or None when unconfigured. Order matters: ISRC-rich providers first so they
# seed cross-provider identity. `sp` (the Spotify client) is only needed by
# peers that read/write Spotify on the legacy path. `account` is an account_id;
# its config snapshot comes from opts.account_configs (never os.environ).
def _spotify(opts, sp, sync_peer=False, songs=None, account=None):
    """Build one Spotify account's target. Account-scoped passes get a client
    minted from the account's own config (own token cache); the legacy path uses
    the shared client the runner built. Cookie mode needs no OAuth client at all."""
    from .. import spotify
    from ..config import spotify_write_backend
    from ..targets.base import TargetAuthError

    config = opts.account_config(account) if account else None
    if spotify_write_backend(config) == "cookie":
        if not _spotify_cookie_ready(account, config):
            return None
        client = None
    elif account is not None:
        try:
            client = spotify.client(writable=sync_peer, config=config)
        except (RuntimeError, TargetAuthError):
            return None
    else:
        client = sp
        if client is None:
            return None
    return _apply_account(
        SpotifyTarget(client, opts.spotify_cache_file, sync_peer=sync_peer, songs=songs,
                      account=account, config=config),
        account)


_REGISTRY = {
    "spotify": _spotify,
    "tidal": lambda opts, sp, sync_peer=False, songs=None, account=None: _apply_account(
        _rest_provider(TidalTarget, "TIDAL", opts, account), account),
    "qobuz": lambda opts, sp, sync_peer=False, songs=None, account=None: _apply_account(
        _rest_provider(QobuzTarget, "Qobuz", opts, account), account),
    "deezer": lambda opts, sp, sync_peer=False, songs=None, account=None: _apply_account(
        _rest_provider(DeezerTarget, "Deezer", opts, account), account),
    "amazon": lambda opts, sp, sync_peer=False, songs=None, account=None: _apply_account(
        _rest_provider(AmazonMusicTarget, "Amazon Music", opts, account), account),
    "apple": lambda opts, sp, sync_peer=False, songs=None, account=None: _apply_account(
        _apple(opts, account), account),
    "ytmusic": lambda opts, sp, sync_peer=False, songs=None, account=None: _apply_account(
        ytmusic.build(opts.account_config(account) if account else None), account),
}
_SOURCE_ORDER = ["spotify", "tidal", "qobuz", "deezer", "amazon", "apple", "ytmusic"]


def _spotify_cookie_ready(account=None, config=None):
    from ..config import spotify_write_backend
    from ..spotify_cookie import configured
    return spotify_write_backend(config) == "cookie" and configured(account_id=account)


def _disabled():
    return {item.strip() for item in os.getenv("DISABLED_PROVIDERS", "").split(",") if item.strip()}


def _participants(opts):
    """(account_id, provider) pairs for this pass: explicit accounts when the job
    is account-scoped, else one `{provider}:default` per opted-in provider."""
    if opts.accounts:
        return list(opts.accounts.items())
    wanted = {s.strip() for s in (opts.providers or "").split(",") if s.strip()}
    return [(f"{src}:default", src) for src in _SOURCE_ORDER if not wanted or src in wanted]


def build_targets(opts, sp=None):
    """One-way mirror targets this run: every participating account except the
    source. Legacy mode (no opts.accounts) keeps the provider-only behaviour.
    `sp` (the Spotify client) is only needed when the source is a non-Spotify
    provider, so Spotify itself becomes a writable target."""
    source = getattr(opts, "sync_source", None) or "spotify"
    source_provider = str(source).split(":", 1)[0]
    disabled = _disabled()
    out = []
    for account_id, src in _participants(opts):
        if src in disabled or account_id == source:
            continue
        # Legacy bare source (`spotify`) names the PROVIDER, not one account: it
        # skips only that provider's `:default` account (the migrated single
        # connection). A named account of the same provider (`spotify:work`) is
        # a different live profile and stays a valid target — the whole point
        # of the multi-account model. Normalized jobs store `spotify:default`
        # and are covered by the `account_id == source` clause above.
        if ":" not in str(source) and src == source and str(account_id).endswith(":default"):
            continue
        builder = _REGISTRY.get(src)
        if builder is None:
            continue
        target = builder(opts, sp, sync_peer=True, account=account_id)
        if target:
            out.append(target)
    return out


def build_account_target(account_id, opts, sp=None):
    """One target for a specific account (live profile), or None when unknown/
    unconfigured. Used by the web layer to browse/transfer a named account."""
    provider = str(account_id).split(":", 1)[0]
    builder = _REGISTRY.get(provider)
    if not builder or provider in _disabled():
        return None
    return builder(opts, sp, sync_peer=False, account=account_id)


def build_one(provider_id, opts, sp=None):
    """Construct a single provider by id (None if unknown/unconfigured). An id
    containing ':' is treated as an account id (multi-account path). Used by the
    web layer to browse or transfer one specific service."""
    if ":" in provider_id:
        return build_account_target(provider_id, opts, sp)
    builder = _REGISTRY.get(provider_id)
    return builder(opts, sp) if builder and provider_id not in _disabled() else None


def is_peer(provider_id):
    """Whether a provider is a sync/transfer peer — i.e. has a MirrorTarget that
    can read and write tracks. False for browse/output-only services like
    Jellyfin, which the download mirror feeds instead of track-level writes."""
    return provider_id in _REGISTRY


def build_peers(opts, sp, songs=None):
    """N-way peer nodes for the participating accounts, in ISRC-rich-first
    order. An empty opts.providers means every configured provider (matching the
    UI, which shows every connected peer when none are explicitly chosen). Needs
    the Spotify client for the Spotify peer. `songs` (the archive conn) backs the
    Spotify peer's persistent ISRC cache."""
    disabled = _disabled()
    out = []
    for account_id, src in _participants(opts):
        if src in disabled:
            continue
        builder = _REGISTRY.get(src)
        if builder is None:
            continue
        peer = builder(opts, sp, sync_peer=True, songs=songs, account=account_id)
        if peer:
            out.append(peer)
    return out
