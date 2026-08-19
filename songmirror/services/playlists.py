"""Playlist browsing + explicit cross-service pairing.

Browse reuses each provider's existing list_playlists; pairing lets the user link
differently-named playlists and set a per-pair direction, overriding the default
same-name matching. Services tier — drives the engine (build_one), never the web.
"""

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..engine import spotify
from ..engine import archive
from ..engine.config import parse_args
from ..engine.config import spotify_write_backend
from ..engine.targets import build_account_target, build_one
from ..engine.targets.base import TargetAuthError
from .settings import _open_private


# ponytail: provider playlist dicts store name/id differently (Spotify `name`,
# Apple `attributes.name`, YT `title`/`playlistId`). Read defensively here until
# Phase 3 adds playlist_name/playlist_id accessors to the MirrorTarget protocol.
def _pl_name(pl):
    return pl.get("name") or (pl.get("attributes") or {}).get("name") or pl.get("title") or ""


def _pl_id(pl):
    # The frontend/link-store contract uses string ids, but some providers
    # (notably Qobuz) return JSON numbers. Normalize at this shared boundary so
    # every consumer sees the same stable type.
    for key in ("id", "playlistId"):
        value = pl.get(key)
        if value is not None and value != "":
            return str(value)
    return _pl_name(pl)


def _pl_image(pl):
    """Best-effort cover-art URL across provider shapes (empty string if none)."""
    def entry_url(value):
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ("url", "href", "src"):
                url = value.get(key)
                if isinstance(url, str) and url.strip():
                    return url.strip()
        return ""

    def first_url(values, *, reverse=False):
        if not isinstance(values, (list, tuple)):
            return entry_url(values)
        entries = reversed(values) if reverse else values
        for entry in entries:
            if url := entry_url(entry):
                return url
        return ""

    # Qobuz returns playlist and collage artwork as lists of URL strings.
    for key in ("image_rectangle", "images300", "image_rectangle_mini"):
        if url := first_url(pl.get(key)):
            return url

    # Deezer's Pipe API returns Picture objects, while its REST API returns
    # size-specific scalar fields. Picture.urls is ordered from small to large.
    for key in ("picture_xl", "picture_big", "picture_medium"):
        if url := entry_url(pl.get(key)):
            return url
    for key in ("picture", "defaultPicture"):
        picture = pl.get(key)
        if isinstance(picture, dict) and (url := first_url(picture.get("urls"), reverse=True)):
            return url
        if url := entry_url(picture):
            return url

    # Spotify, TIDAL, and Amazon use image objects; current Qobuz responses use
    # strings. Mixed/empty arrays are tolerated so one malformed card cannot
    # fail the entire provider browse response.
    if url := first_url(pl.get("images")):
        return url
    art = (pl.get("attributes") or {}).get("artwork") or {}  # Apple: {w}x{h} template
    if isinstance(art, dict) and art.get("url"):
        return art["url"].replace("{w}", "300").replace("{h}", "300")
    thumbs = pl.get("thumbnails") or (pl.get("snippet") or {}).get("thumbnails")  # YouTube
    if isinstance(thumbs, list) and thumbs:
        return first_url(thumbs, reverse=True)
    if isinstance(thumbs, dict):
        for size in ("high", "medium", "default"):
            if url := entry_url(thumbs.get(size)):
                return url
    return ""


class PlaylistService:
    def __init__(self, settings, database=None):
        self._settings = settings
        self._database = database

    def browse(self, provider_id):
        """[{id, name, count, image, owned}] for one connected provider (empty if
        unconfigured). Provider-agnostic: every service is listed through its
        MirrorTarget.browse_playlists() + accessors, so adding a provider needs no
        change here. `owned` is False only for a followed (non-owned) playlist — a
        provider surfaces those by overriding browse_playlists (Spotify does today).
        Jellyfin is browse-only and lists via its own API."""
        self._settings.apply_to_env()
        if provider_id == "musify" or provider_id.startswith("musify:"):
            if self._database is None:
                return []
            from .musify import MusifyCanonicalTarget
            account_id = "musify:default" if provider_id == "musify" else provider_id
            target = MusifyCanonicalTarget(self._database, account_id)
            return [{"id": target.playlist_id(pl), "name": target.playlist_name(pl),
                     "count": target.playlist_count(pl), "image": pl.get("image") or "",
                     "owned": True} for pl in target.browse_playlists()]
        if ":" in provider_id:
            # A restored/imported snapshot reads from the canonical database.
            # Live accounts ALSO have service_accounts rows, so the auth_mode
            # distinguishes them — a live profile must never be served stale
            # canonical data instead of the real service.
            from .canonical_target import CanonicalAccountTarget, is_canonical_account
            if is_canonical_account(self._database, provider_id):
                account = next((row for row in self._database.accounts() if row["id"] == provider_id), None)
                target = CanonicalAccountTarget(self._database, provider_id, account["label"])
                return [{"id": target.playlist_id(pl), "name": target.playlist_name(pl),
                         "count": target.playlist_count(pl), "image": pl.get("image") or "",
                         "owned": True} for pl in target.browse_playlists()]
            # A live multi-account profile: browse with its own config snapshot.
            opts = parse_args([])
            opts.accounts = {provider_id: str(provider_id).split(":", 1)[0]}
            opts.account_configs = {provider_id: self._settings.account_config_snapshot(provider_id)}
            try:
                target = build_account_target(provider_id, opts)
            except Exception:
                return []
            if target is None:
                return []
            try:
                playlists = list(target.browse_playlists())
                hydrate_counts = getattr(target, "hydrate_playlist_counts", None)
                if hydrate_counts:
                    playlists = hydrate_counts(playlists) or playlists
            except Exception:
                return []
            return [{"id": target.playlist_id(pl), "name": target.playlist_name(pl),
                     "count": target.playlist_count(pl), "image": _pl_image(pl),
                     "owned": True} for pl in playlists]
        if provider_id == "jellyfin":
            from ..engine import jellyfin
            rows = [{**r, "owned": True} for r in jellyfin.list_playlists()]
            return sorted(rows, key=lambda r: (r["name"] or "").casefold())
        opts = parse_args([])
        try:
            sp = (spotify.client() if provider_id == "spotify" and spotify_write_backend() != "cookie" else None)
            target = build_one(provider_id, opts, sp)
        except TargetAuthError:
            raise
        except Exception:
            return []  # e.g. Spotify not authorized yet -> nothing to browse
        if target is None:
            return []
        try:
            playlists = list(target.browse_playlists())
            hydrate_counts = getattr(target, "hydrate_playlist_counts", None)
            if hydrate_counts:
                playlists = hydrate_counts(playlists) or playlists
        except TargetAuthError:
            raise
        except Exception:
            return []
        rows = [{"id": _pl_id(pl), "name": _pl_name(pl), "count": target.playlist_count(pl),
                 "image": _pl_image(pl), "owned": bool(pl.get("_owned", True))} for pl in playlists]
        return sorted(rows, key=lambda r: (r["name"] or "").casefold())

    def versions(self, provider_id, playlist_id):
        """Recent ordered snapshots already captured by the sync engine."""
        target, opts = self._target(provider_id)
        playlist = target.find_playlist(playlist_id) if target else None
        if playlist is None:
            raise ValueError("playlist not found or connector is paused")
        key = target.playlist_name(playlist).strip().casefold()
        with archive.connect(opts.song_cache_file) as conn:
            rows = archive.get_order_history(conn, key, provider_id)
        return [{"captured_at": captured_at, "item_count": len(items), "items": items}
                for captured_at, items in rows]

    def restore_version(self, provider_id, playlist_id, captured_at, *, execute=False, max_removals=100):
        """Preview or restore membership from a provider snapshot.

        Existing relative order is preserved; missing historical tracks append
        in their recorded order. This avoids a destructive wipe/recreate merely
        to reshuffle tracks.
        """
        target, opts = self._target(provider_id)
        playlist = target.find_playlist(playlist_id) if target else None
        if playlist is None:
            raise ValueError("playlist not found or connector is paused")
        key = target.playlist_name(playlist).strip().casefold()
        current = target.playlist_tracks(playlist)
        current_ids = [target.track_id(item) for item in current]
        with archive.connect(opts.song_cache_file) as conn:
            versions = dict(archive.get_order_history(conn, key, provider_id))
            wanted_rows = versions.get(captured_at)
            if wanted_rows is None:
                raise ValueError("playlist version not found")
            archive.record_order(conn, key, provider_id,
                                 [[target.track_id(item), item.get("name", ""),
                                   item.get("artist") or ", ".join(item.get("artists") or [])] for item in current])
        wanted_ids = [row[0] for row in wanted_rows]
        wanted = set(wanted_ids)
        have = set(current_ids)
        additions = [track_id for track_id in wanted_ids if track_id not in have]
        removals = [item for item in current if target.track_id(item) not in wanted]
        if len(removals) > max(0, max_removals):
            raise ValueError(f"restore needs {len(removals)} removals, above the safety limit {max_removals}")
        if execute:
            if additions:
                target.add(playlist, additions)
            for item in removals:
                target.remove(playlist, item)
        return {"execute": execute, "additions": len(additions), "removals": len(removals),
                "target_count": len(wanted_ids), "order_note": "existing relative order is preserved"}

    def _target(self, provider_id):
        self._settings.apply_to_env()
        opts = parse_args([])
        try:
            if ":" in provider_id:
                opts.accounts = {provider_id: str(provider_id).split(":", 1)[0]}
                opts.account_configs = {provider_id: self._settings.account_config_snapshot(provider_id)}
                return build_account_target(provider_id, opts), opts
            sp = (spotify.client() if provider_id == "spotify" and spotify_write_backend() != "cookie" else None)
            return build_one(provider_id, opts, sp), opts
        except Exception:
            return None, opts


@dataclass
class PlaylistLink:
    name: str
    members: dict = field(default_factory=dict)  # provider_id -> playlist_id | None (None = create by name)
    direction: str = "oneway"                     # oneway | nway
    source: str | None = "spotify"
    enabled: bool = True
    id: str = ""


class LinkStore:
    """Explicit pairings persisted to data/links.json (owner-only, alongside the
    other data-dir state)."""

    def __init__(self, dir="data"):
        self._path = Path(dir) / "links.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def list(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                return [PlaylistLink(**d) for d in json.load(f)]
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def upsert(self, link):
        if not link.id:
            link.id = uuid.uuid4().hex[:8]
        links = [l for l in self.list() if l.id != link.id]
        links.append(link)
        self._save(links)
        return link

    def delete(self, link_id):
        self._save([l for l in self.list() if l.id != link_id])

    def _save(self, links):
        with _open_private(self._path) as f:
            json.dump([asdict(l) for l in links], f, indent=2)
