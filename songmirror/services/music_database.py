"""Canonical local music database.

The sync engine's ``song_cache.db`` is an implementation cache.  This module is
the user-facing source of truth: accounts, canonical tracks, provider
identities, playlists and listening history all live here and survive connector
changes.  Every public method opens a short-lived SQLite connection so FastAPI
workers and background sync threads can safely share the database.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS service_accounts (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    label TEXT NOT NULL,
    external_id TEXT,
    status TEXT NOT NULL DEFAULT 'unconfigured',
    auth_mode TEXT,
    capabilities TEXT NOT NULL DEFAULT '{}',
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_service_accounts_provider_label
    ON service_accounts(provider, label);

CREATE TABLE IF NOT EXISTS artists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sort_name TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_artists_sort_name ON artists(sort_name);

CREATE TABLE IF NOT EXISTS albums (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist_id TEXT REFERENCES artists(id),
    release_year INTEGER,
    artwork_url TEXT
);
CREATE INDEX IF NOT EXISTS idx_albums_artist ON albums(artist_id);

CREATE TABLE IF NOT EXISTS tracks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist_id TEXT REFERENCES artists(id),
    album_id TEXT REFERENCES albums(id),
    duration_ms INTEGER,
    isrc TEXT,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist_id);
CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_id);
CREATE INDEX IF NOT EXISTS idx_tracks_isrc ON tracks(isrc) WHERE isrc IS NOT NULL;

CREATE TABLE IF NOT EXISTS service_tracks (
    account_id TEXT NOT NULL REFERENCES service_accounts(id) ON DELETE CASCADE,
    provider_track_id TEXT NOT NULL,
    track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    provider_url TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    available INTEGER NOT NULL DEFAULT 1,
    last_seen_at INTEGER NOT NULL,
    PRIMARY KEY(account_id, provider_track_id)
);
CREATE INDEX IF NOT EXISTS idx_service_tracks_track ON service_tracks(track_id);

CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'playlist',
    title TEXT NOT NULL,
    description TEXT,
    artwork_url TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_mirrors (
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES service_accounts(id) ON DELETE CASCADE,
    provider_collection_id TEXT,
    provider_url TEXT,
    writable INTEGER NOT NULL DEFAULT 0,
    snapshot TEXT,
    last_pulled_at INTEGER,
    last_pushed_at INTEGER,
    PRIMARY KEY(collection_id, account_id)
);

CREATE TABLE IF NOT EXISTS collection_items (
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    added_at INTEGER,
    PRIMARY KEY(collection_id, position)
);
CREATE INDEX IF NOT EXISTS idx_collection_items_track ON collection_items(track_id);

CREATE TABLE IF NOT EXISTS collection_versions (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    item_count INTEGER NOT NULL,
    items TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_collection_versions_collection_time
    ON collection_versions(collection_id, created_at DESC);

CREATE TABLE IF NOT EXISTS surface_items (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES service_accounts(id) ON DELETE CASCADE,
    surface TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    provider_id TEXT,
    added_at INTEGER NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_surface_items_identity
    ON surface_items(account_id, surface, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_surface_items_surface ON surface_items(surface, added_at DESC);

CREATE TABLE IF NOT EXISTS listens (
    id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    account_id TEXT REFERENCES service_accounts(id) ON DELETE SET NULL,
    listened_at INTEGER NOT NULL,
    listened_ms INTEGER,
    source TEXT NOT NULL,
    source_event_id TEXT,
    skipped INTEGER,
    metadata TEXT NOT NULL DEFAULT '{}',
    imported_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_listens_source_event
    ON listens(source, source_event_id) WHERE source_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_listens_time ON listens(listened_at);
CREATE INDEX IF NOT EXISTS idx_listens_track_time ON listens(track_id, listened_at);

-- Snapshot imports (Musify/Sonora Wrapped) are replacement values, not append-
-- only events. Re-importing July replaces July for the same track/source.
CREATE TABLE IF NOT EXISTS listening_aggregates (
    account_id TEXT NOT NULL REFERENCES service_accounts(id) ON DELETE CASCADE,
    period_start INTEGER NOT NULL,
    period_end INTEGER NOT NULL,
    track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    play_count INTEGER NOT NULL DEFAULT 0,
    listened_ms INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    imported_at INTEGER NOT NULL,
    PRIMARY KEY(account_id, period_start, period_end, track_id)
);
CREATE INDEX IF NOT EXISTS idx_listening_aggregates_period
    ON listening_aggregates(period_start, period_end, source);

CREATE TABLE IF NOT EXISTS sync_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    source_account_id TEXT REFERENCES service_accounts(id),
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    summary TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sync_runs_started ON sync_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS sync_operations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
    destination_account_id TEXT REFERENCES service_accounts(id),
    collection_id TEXT REFERENCES collections(id),
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    track_id TEXT REFERENCES tracks(id),
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_operations_run ON sync_operations(run_id);

CREATE TABLE IF NOT EXISTS app_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    tag TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_app_logs_ts ON app_logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_app_logs_kind_tag ON app_logs(kind, tag, ts DESC);

CREATE TABLE IF NOT EXISTS sync_policies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_account_id TEXT REFERENCES service_accounts(id),
    destination_account_id TEXT REFERENCES service_accounts(id),
    surface TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'oneway',
    conflict_strategy TEXT NOT NULL DEFAULT 'merge',
    automatic INTEGER NOT NULL DEFAULT 0,
    interval_seconds INTEGER NOT NULL DEFAULT 3600,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sync_policies_enabled ON sync_policies(enabled, automatic);

CREATE TABLE IF NOT EXISTS sonora_devices (
    device_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    ip TEXT,
    port INTEGER,
    paired INTEGER NOT NULL DEFAULT 0,
    auto_sync INTEGER NOT NULL DEFAULT 0,
    surfaces TEXT NOT NULL DEFAULT '["likedSongs","followedArtists","likedAlbums","likedPlaylists","playlists","history"]',
    last_seen_at INTEGER,
    last_synced_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


PROVIDER_CAPABILITIES: dict[str, dict[str, bool]] = {
    "spotify": {"library_read": True, "playlist_read": True, "playlist_create": True, "playlist_write": True},
    "ytmusic": {"library_read": True, "playlist_read": True, "playlist_create": True, "playlist_write": True},
    "amazon": {"library_read": True, "playlist_read": True, "playlist_create": True, "playlist_write": True},
    "apple": {"library_read": True, "playlist_read": True, "playlist_create": True, "playlist_write": True},
    "tidal": {"library_read": True, "playlist_read": True, "playlist_create": True, "playlist_write": True},
    "qobuz": {"library_read": True, "playlist_read": True, "playlist_create": True, "playlist_write": True},
    "deezer": {"library_read": True, "playlist_read": True, "playlist_create": True, "playlist_write": True},
    "jellyfin": {"library_read": True, "playlist_read": True, "playlist_create": False, "playlist_write": False},
    # Reads come from an uploaded user.hive snapshot. Writes remain the
    # explicit deep-link export path, never an in-place backup mutation.
    "musify": {"library_read": True, "playlist_read": True, "playlist_create": True, "playlist_write": False},
    "sonora": {"library_read": True, "playlist_read": True, "playlist_create": True, "playlist_write": True},
}

# What each provider can actually do per surface, as read/write pairs. This is
# the honest capability matrix the UI and the sync engine consult: a surface
# the connector cannot write is offered as a backup/source only, never as a
# destination. `history` is inbound-only everywhere (recaps never write back).
SURFACE_CAPABILITIES: dict[str, dict[str, str]] = {
    "spotify": {"playlists": "rw", "liked_tracks": "r", "saved_albums": "r",
                 "followed_artists": "r", "history": "r"},
    "ytmusic": {"playlists": "rw", "liked_tracks": "r", "saved_albums": "-",
                 "followed_artists": "-", "history": "r"},
    "tidal": {"playlists": "rw", "liked_tracks": "-", "saved_albums": "-",
               "followed_artists": "-", "history": "-"},
    "qobuz": {"playlists": "rw", "liked_tracks": "-", "saved_albums": "-",
               "followed_artists": "-", "history": "-"},
    "deezer": {"playlists": "rw", "liked_tracks": "-", "saved_albums": "-",
                "followed_artists": "-", "history": "-"},
    "amazon": {"playlists": "rw", "liked_tracks": "-", "saved_albums": "-",
                "followed_artists": "-", "history": "-"},
    "apple": {"playlists": "rw", "liked_tracks": "-", "saved_albums": "-",
               "followed_artists": "-", "history": "-"},
    "jellyfin": {"playlists": "r", "liked_tracks": "-", "saved_albums": "-",
                  "followed_artists": "-", "history": "-"},
    "musify": {"playlists": "r", "liked_tracks": "r", "saved_albums": "r",
                "followed_artists": "r", "history": "r"},
    "sonora": {"playlists": "rw", "liked_tracks": "r", "saved_albums": "r",
                "followed_artists": "r", "history": "r"},
}

SURFACES = ("playlists", "liked_tracks", "saved_albums", "followed_artists", "history")

MIN_LISTENING_RETENTION_YEARS = 1
MAX_LISTENING_RETENTION_YEARS = 10
DEFAULT_LISTENING_RETENTION_YEARS = 3


def listening_retention_years(value: object | None = None) -> int:
    """Return the configured retention, safely clamped to the supported range."""
    try:
        years = int(value if value is not None else os.getenv(
            "LISTENING_RETENTION_YEARS", str(DEFAULT_LISTENING_RETENTION_YEARS)
        ))
    except (TypeError, ValueError):
        years = DEFAULT_LISTENING_RETENTION_YEARS
    return max(MIN_LISTENING_RETENTION_YEARS, min(years, MAX_LISTENING_RETENTION_YEARS))


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "\x1f".join(str(p or "").strip().casefold() for p in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


class MusicDatabase:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("SYNC_DATABASE") or "data/sync_music.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self._migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _migrate(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            # Per-account pause switch: an account can be disabled without
            # deleting its imported data. Existing rows default to enabled.
            cols = [row[1] for row in conn.execute("PRAGMA table_info(service_accounts)").fetchall()]
            if "enabled" not in cols:
                conn.execute("ALTER TABLE service_accounts ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
            conn.execute("PRAGMA optimize")

    def sync_account(self, provider: str, label: str, status: str, auth_mode: str | None = None,
                     account_id: str | None = None, is_default: bool | None = None,
                     enabled: bool | None = None) -> dict[str, Any]:
        now = _now()
        account_id = account_id or f"{provider}:default"
        if not account_id.startswith(f"{provider}:"):
            raise ValueError("account_id must be namespaced by provider")
        default = account_id == f"{provider}:default" if is_default is None else bool(is_default)
        capabilities = PROVIDER_CAPABILITIES.get(provider, {"library_read": False, "playlist_read": False,
                                                               "playlist_create": False, "playlist_write": False})
        with self.connect() as conn:
            if enabled is None:
                existing = conn.execute("SELECT enabled FROM service_accounts WHERE id=?", (account_id,)).fetchone()
                enabled = bool(existing["enabled"]) if existing else True
            conn.execute(
                """INSERT INTO service_accounts
                   (id, provider, label, status, auth_mode, capabilities, is_default, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET label=excluded.label, status=excluded.status,
                     auth_mode=excluded.auth_mode, capabilities=excluded.capabilities, updated_at=excluded.updated_at""",
                (account_id, provider, label, status, auth_mode, json.dumps(capabilities),
                 int(default), int(enabled), now, now),
            )
        return {"id": account_id, "provider": provider, "label": label, "status": status,
                "capabilities": capabilities, "is_default": default, "enabled": bool(enabled)}

    def accounts(self) -> list[dict[str, Any]]:
        """Every known account with its imported-data counts and last update."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT sa.*,
                          (SELECT COUNT(*) FROM service_tracks st WHERE st.account_id=sa.id) tracks,
                          (SELECT COUNT(*) FROM collection_mirrors cm WHERE cm.account_id=sa.id) playlists,
                          (SELECT COUNT(*) FROM surface_items si WHERE si.account_id=sa.id) surfaces,
                          (SELECT COUNT(*) FROM listens le WHERE le.account_id=sa.id) +
                          (SELECT COUNT(*) FROM listening_aggregates la WHERE la.account_id=sa.id) listens
                   FROM service_accounts sa ORDER BY sa.provider, sa.label""").fetchall()
        return [{**dict(row), "capabilities": json.loads(row["capabilities"]),
                 "is_default": bool(row["is_default"])} for row in rows]

    def set_account_enabled(self, account_id: str, enabled: bool) -> dict[str, Any]:
        """Pause/resume one account without deleting its imported data."""
        with self.connect() as conn:
            cur = conn.execute("UPDATE service_accounts SET enabled=?, updated_at=? WHERE id=?",
                               (int(bool(enabled)), _now(), account_id))
            if not cur.rowcount:
                raise KeyError("account not found")
        return {"id": account_id, "enabled": bool(enabled)}

    def rename_account(self, account_id: str, label: str) -> dict[str, Any]:
        """Rename an account slot. The id stays stable so jobs, links and recap
        rows keep pointing at the same account."""
        label = str(label or "").strip()
        if not label:
            raise ValueError("label cannot be empty")
        with self.connect() as conn:
            cur = conn.execute("UPDATE service_accounts SET label=?, updated_at=? WHERE id=?",
                               (label, _now(), account_id))
            if not cur.rowcount:
                raise KeyError("account not found")
        return {"id": account_id, "label": label}

    def delete_account(self, account_id: str) -> dict[str, Any]:
        """Remove exactly one account slot and the data that only it owns.

        Canonical entities (artists/albums/tracks/collections) still referenced
        by another account are deliberately kept: only orphans are garbage
        collected after the account-scoped rows go."""
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM service_accounts WHERE id=?", (account_id,)).fetchone():
                raise KeyError("account not found")
            # Account-scoped rows first (listens keep canonical tracks alive
            # through FK, so they must go before the track GC).
            for table in ("surface_items", "service_tracks", "listening_aggregates",
                          "listens", "collection_mirrors"):
                conn.execute(f"DELETE FROM {table} WHERE account_id=?", (account_id,))
            conn.execute("DELETE FROM service_accounts WHERE id=?", (account_id,))
            # Orphan GC — never touches rows another account still references.
            conn.execute(
                """DELETE FROM collections WHERE id NOT IN (SELECT DISTINCT collection_id FROM collection_mirrors)""")
            conn.execute(
                """DELETE FROM tracks WHERE id NOT IN (
                     SELECT track_id FROM service_tracks
                     UNION SELECT track_id FROM collection_items
                     UNION SELECT track_id FROM listens
                     UNION SELECT track_id FROM listening_aggregates)""")
            conn.execute("DELETE FROM albums WHERE id NOT IN (SELECT album_id FROM tracks WHERE album_id IS NOT NULL)")
            conn.execute("DELETE FROM artists WHERE id NOT IN (SELECT artist_id FROM tracks WHERE artist_id IS NOT NULL)")
        return {"account_id": account_id, "deleted": True}

    def export_account_backup(self, account_id: str) -> dict[str, Any]:
        """Portable canonical snapshot for one account, excluding credentials."""
        with self.connect() as conn:
            account = conn.execute("SELECT * FROM service_accounts WHERE id=?", (account_id,)).fetchone()
            if not account:
                raise KeyError("account not found")
            service_tracks = conn.execute("SELECT * FROM service_tracks WHERE account_id=?", (account_id,)).fetchall()
            mirrors = conn.execute("SELECT * FROM collection_mirrors WHERE account_id=?", (account_id,)).fetchall()
            collection_ids = [row["collection_id"] for row in mirrors]
            surfaces = conn.execute("SELECT * FROM surface_items WHERE account_id=?", (account_id,)).fetchall()
            listens = conn.execute("SELECT * FROM listens WHERE account_id=?", (account_id,)).fetchall()
            aggregates = conn.execute("SELECT * FROM listening_aggregates WHERE account_id=?", (account_id,)).fetchall()
            track_ids = {row["track_id"] for row in service_tracks} | {row["track_id"] for row in listens} | {
                row["track_id"] for row in aggregates}
            collections, items = [], []
            for collection_id in collection_ids:
                row = conn.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()
                if row:
                    collections.append(row)
                rows = conn.execute("SELECT * FROM collection_items WHERE collection_id=? ORDER BY position",
                                    (collection_id,)).fetchall()
                items.extend(rows)
                track_ids.update(item["track_id"] for item in rows)
            tracks = [row for track_id in track_ids
                      for row in [conn.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()] if row]
            artist_ids = {row["artist_id"] for row in tracks if row["artist_id"]}
            album_ids = {row["album_id"] for row in tracks if row["album_id"]}
            artists = [row for row_id in artist_ids
                       for row in [conn.execute("SELECT * FROM artists WHERE id=?", (row_id,)).fetchone()] if row]
            albums = [row for row_id in album_ids
                      for row in [conn.execute("SELECT * FROM albums WHERE id=?", (row_id,)).fetchone()] if row]
        pack = lambda rows: [dict(row) for row in rows]  # noqa: E731
        return {"format": "sync-account-backup", "version": 1, "created_at": _now(),
                "account": dict(account), "artists": pack(artists), "albums": pack(albums),
                "tracks": pack(tracks), "service_tracks": pack(service_tracks),
                "collections": pack(collections), "collection_mirrors": pack(mirrors),
                "collection_items": pack(items), "surface_items": pack(surfaces),
                "listens": pack(listens), "listening_aggregates": pack(aggregates)}

    def restore_account_backup(self, payload: dict[str, Any], account_id: str | None = None,
                               label: str | None = None) -> dict[str, Any]:
        """Replace exactly one account from a credential-free portable snapshot."""
        if payload.get("format") != "sync-account-backup" or payload.get("version") != 1:
            raise ValueError("unsupported account backup format")
        source_account = payload.get("account") or {}
        provider = str(source_account.get("provider") or "").strip()
        source_id = str(source_account.get("id") or "")
        target_id = account_id or source_id
        target_label = (label or source_account.get("label") or provider).strip()
        if not provider or not target_id.startswith(f"{provider}:"):
            raise ValueError("backup account/provider namespace is invalid")
        self.sync_account(provider, target_label, "connected", "sync-account-restore", account_id=target_id)
        with self.connect() as conn:
            # UPSERT rather than SQLite REPLACE: REPLACE deletes the old parent
            # row first and would cascade into other provider accounts that share
            # the same canonical artist/track/collection.
            for row in payload.get("artists") or []:
                conn.execute("""INSERT INTO artists(id, name, sort_name) VALUES (?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name, sort_name=excluded.sort_name""",
                    (row.get("id"), row.get("name"), row.get("sort_name")))
            for row in payload.get("albums") or []:
                conn.execute("""INSERT INTO albums(id, title, artist_id, release_year, artwork_url) VALUES (?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET title=excluded.title, artist_id=excluded.artist_id,
                    release_year=excluded.release_year, artwork_url=excluded.artwork_url""",
                    (row.get("id"), row.get("title"), row.get("artist_id"), row.get("release_year"), row.get("artwork_url")))
            for row in payload.get("tracks") or []:
                conn.execute("""INSERT INTO tracks(id, title, artist_id, album_id, duration_ms, isrc, first_seen_at, last_seen_at)
                    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                    artist_id=excluded.artist_id, album_id=excluded.album_id, duration_ms=excluded.duration_ms,
                    isrc=excluded.isrc, last_seen_at=excluded.last_seen_at""",
                    (row.get("id"), row.get("title"), row.get("artist_id"), row.get("album_id"),
                     row.get("duration_ms"), row.get("isrc"), row.get("first_seen_at"), row.get("last_seen_at")))
            conn.execute("DELETE FROM surface_items WHERE account_id=?", (target_id,))
            conn.execute("DELETE FROM service_tracks WHERE account_id=?", (target_id,))
            conn.execute("DELETE FROM listens WHERE account_id=?", (target_id,))
            conn.execute("DELETE FROM listening_aggregates WHERE account_id=?", (target_id,))
            conn.execute("DELETE FROM collection_mirrors WHERE account_id=?", (target_id,))
            for row in payload.get("collections") or []:
                conn.execute("""INSERT INTO collections(id, kind, title, description, artwork_url, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, title=excluded.title,
                    description=excluded.description, artwork_url=excluded.artwork_url, updated_at=excluded.updated_at""",
                    (row.get("id"), row.get("kind"), row.get("title"), row.get("description"),
                     row.get("artwork_url"), row.get("created_at"), row.get("updated_at")))
            collection_ids = {row.get("id") for row in payload.get("collections") or []}
            for collection_id in collection_ids:
                conn.execute("DELETE FROM collection_items WHERE collection_id=?", (collection_id,))
            for row in payload.get("collection_items") or []:
                conn.execute("INSERT OR REPLACE INTO collection_items VALUES (?,?,?,?)",
                             (row.get("collection_id"), row.get("track_id"), row.get("position"), row.get("added_at")))
            for row in payload.get("collection_mirrors") or []:
                conn.execute("""INSERT OR REPLACE INTO collection_mirrors
                    (collection_id, account_id, provider_collection_id, provider_url, writable, snapshot, last_pulled_at, last_pushed_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (row.get("collection_id"), target_id, row.get("provider_collection_id"), row.get("provider_url"),
                     row.get("writable", 0), row.get("snapshot"), row.get("last_pulled_at"), row.get("last_pushed_at")))
            for row in payload.get("service_tracks") or []:
                conn.execute("INSERT OR REPLACE INTO service_tracks VALUES (?,?,?,?,?,?,?)",
                             (target_id, row.get("provider_track_id"), row.get("track_id"), row.get("provider_url"),
                              row.get("metadata", "{}"), row.get("available", 1), row.get("last_seen_at", _now())))
            for row in payload.get("surface_items") or []:
                entity_id, surface = row.get("entity_id"), row.get("surface")
                conn.execute("INSERT OR REPLACE INTO surface_items VALUES (?,?,?,?,?,?,?,?)",
                             (_stable_id("surface", target_id, surface, entity_id), target_id, surface,
                              row.get("entity_type"), entity_id, row.get("provider_id"), row.get("added_at"),
                              row.get("metadata", "{}")))
            for row in payload.get("listens") or []:
                event = _stable_id("event", target_id, row.get("source_event_id") or row.get("id"))
                conn.execute("INSERT OR IGNORE INTO listens VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (_stable_id("listen", target_id, event), row.get("track_id"), target_id,
                              row.get("listened_at"), row.get("listened_ms"), provider, event,
                              row.get("skipped"), row.get("metadata", "{}"), _now()))
            for row in payload.get("listening_aggregates") or []:
                conn.execute("INSERT OR REPLACE INTO listening_aggregates VALUES (?,?,?,?,?,?,?,?,?)",
                             (target_id, row.get("period_start"), row.get("period_end"), row.get("track_id"),
                              row.get("play_count", 0), row.get("listened_ms", 0), provider,
                              row.get("metadata", "{}"), _now()))
        self.prune_listening_history()
        return {"account_id": target_id, "provider": provider, "label": target_label,
                "tracks": len(payload.get("service_tracks") or []),
                "playlists": len(payload.get("collection_mirrors") or []),
                "listens": len(payload.get("listens") or []),
                "aggregates": len(payload.get("listening_aggregates") or [])}

    def import_provider_library(self, provider: str, account_id: str, label: str,
                                playlists: Iterable[dict[str, Any]] = (),
                                liked_tracks: Iterable[dict[str, Any]] = (),
                                albums: Iterable[dict[str, Any]] = (),
                                artists: Iterable[dict[str, Any]] = (),
                                auth_mode: str = "official-export") -> dict[str, int]:
        """Replace one account's exported library surfaces without touching peers.

        This is the shared restore contract used by provider exports.  The
        account id is explicit, so importing a second account of the same
        provider cannot overwrite the first one's playlists or likes.
        `auth_mode` marks how the data was sourced: `official-export` for
        provider exports (read-only snapshot), `live-import` for a pull straight
        from a live connected account (stays a live profile, not a snapshot).
        """
        playlists, liked_tracks, albums, artists = map(list, (playlists, liked_tracks, albums, artists))
        self.sync_account(provider, label, "connected", auth_mode, account_id=account_id)
        now = _now()
        counts = {"playlists": 0, "playlist_tracks": 0, "liked_tracks": 0,
                  "albums": 0, "artists": 0}
        with self.connect() as conn:
            # A provider export is a snapshot for these surfaces. Only this
            # account is replaced; canonical tracks shared by other accounts stay.
            conn.execute("DELETE FROM surface_items WHERE account_id=? AND surface IN ('liked_tracks','saved_albums','followed_artists')",
                         (account_id,))
            for metadata in liked_tracks:
                track_id = self._upsert_track(conn, metadata)
                provider_id = str(metadata.get("provider_track_id") or metadata.get("uri") or track_id)
                conn.execute(
                    """INSERT INTO service_tracks(account_id, provider_track_id, track_id, metadata, available, last_seen_at)
                       VALUES (?, ?, ?, ?, 1, ?) ON CONFLICT(account_id, provider_track_id) DO UPDATE SET
                       track_id=excluded.track_id, metadata=excluded.metadata, available=1, last_seen_at=excluded.last_seen_at""",
                    (account_id, provider_id, track_id, json.dumps(metadata, ensure_ascii=False), now))
                conn.execute(
                    """INSERT OR REPLACE INTO surface_items
                       (id, account_id, surface, entity_type, entity_id, provider_id, added_at, metadata)
                       VALUES (?, ?, 'liked_tracks', 'track', ?, ?, ?, ?)""",
                    (_stable_id("surface", account_id, "liked_tracks", track_id), account_id, track_id,
                     provider_id, now, json.dumps(metadata, ensure_ascii=False)))
                counts["liked_tracks"] += 1
            for surface, entity_type, values in (("saved_albums", "album", albums),
                                                  ("followed_artists", "artist", artists)):
                for metadata in values:
                    name = str(metadata.get("name") or metadata.get("album") or metadata.get("artist") or "Unknown")
                    provider_id = str(metadata.get("provider_id") or metadata.get("uri") or name)
                    entity_id = _stable_id(entity_type, provider, provider_id, name)
                    conn.execute(
                        """INSERT OR REPLACE INTO surface_items
                           (id, account_id, surface, entity_type, entity_id, provider_id, added_at, metadata)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (_stable_id("surface", account_id, surface, entity_id), account_id, surface,
                         entity_type, entity_id, provider_id, now, json.dumps(metadata, ensure_ascii=False)))
                    counts["albums" if entity_type == "album" else "artists"] += 1

            existing = conn.execute("SELECT collection_id FROM collection_mirrors WHERE account_id=?", (account_id,)).fetchall()
            imported_ids = set()
            for playlist in playlists:
                provider_id = str(playlist.get("provider_id") or playlist.get("id") or playlist.get("name") or uuid.uuid4())
                collection_id = _stable_id("collection", account_id, provider_id)
                imported_ids.add(collection_id)
                old = conn.execute("SELECT track_id, position, added_at FROM collection_items WHERE collection_id=? ORDER BY position",
                                   (collection_id,)).fetchall()
                if old:
                    version_id = _stable_id("version", collection_id, now, len(old))
                    conn.execute(
                        "INSERT OR IGNORE INTO collection_versions(id, collection_id, reason, created_at, item_count, items) VALUES (?, ?, 'before-provider-restore', ?, ?, ?)",
                        (version_id, collection_id, now, len(old), json.dumps([dict(row) for row in old])))
                conn.execute(
                    """INSERT INTO collections(id, kind, title, description, created_at, updated_at)
                       VALUES (?, 'playlist', ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET
                       title=excluded.title, description=excluded.description, updated_at=excluded.updated_at""",
                    (collection_id, str(playlist.get("name") or "Untitled playlist"),
                     str(playlist.get("description") or ""), now, now))
                conn.execute(
                    """INSERT INTO collection_mirrors(collection_id, account_id, provider_collection_id, writable, snapshot, last_pulled_at)
                       VALUES (?, ?, ?, 0, ?, ?) ON CONFLICT(collection_id, account_id) DO UPDATE SET
                       provider_collection_id=excluded.provider_collection_id, snapshot=excluded.snapshot,
                       last_pulled_at=excluded.last_pulled_at""",
                    (collection_id, account_id, provider_id, str(playlist.get("snapshot") or ""), now))
                conn.execute("DELETE FROM collection_items WHERE collection_id=?", (collection_id,))
                for position, metadata in enumerate(playlist.get("tracks") or []):
                    track_id = self._upsert_track(conn, metadata)
                    provider_track_id = str(metadata.get("provider_track_id") or metadata.get("uri") or track_id)
                    conn.execute(
                        """INSERT INTO service_tracks(account_id, provider_track_id, track_id, metadata, available, last_seen_at)
                           VALUES (?, ?, ?, ?, 1, ?) ON CONFLICT(account_id, provider_track_id) DO UPDATE SET
                           track_id=excluded.track_id, metadata=excluded.metadata, available=1, last_seen_at=excluded.last_seen_at""",
                        (account_id, provider_track_id, track_id, json.dumps(metadata, ensure_ascii=False), now))
                    conn.execute("INSERT INTO collection_items(collection_id, track_id, position, added_at) VALUES (?, ?, ?, ?)",
                                 (collection_id, track_id, position, metadata.get("added_at")))
                    counts["playlist_tracks"] += 1
                counts["playlists"] += 1
            for row in existing:
                if row["collection_id"] not in imported_ids:
                    conn.execute("DELETE FROM collections WHERE id=?", (row["collection_id"],))
        return counts

    def _upsert_track(self, conn: sqlite3.Connection, metadata: dict[str, Any]) -> str:
        title = str(metadata.get("track_name") or metadata.get("title") or "Unknown track").strip()
        artist_name = str(metadata.get("artist_name") or metadata.get("artist") or "Unknown artist").strip()
        album_name = str(metadata.get("release_name") or metadata.get("album") or "").strip()
        extra = metadata.get("additional_info") or {}
        isrc = metadata.get("isrc") or extra.get("isrc")
        duration = metadata.get("duration_ms") or extra.get("duration_ms") or extra.get("duration")
        if duration and float(duration) < 10_000:  # ListenBrainz duration is commonly seconds.
            duration = int(float(duration) * 1000)
        elif duration:
            duration = int(float(duration))
        artist_id = _stable_id("artist", artist_name)
        album_id = _stable_id("album", artist_name, album_name) if album_name else None
        track_id = _stable_id("track", isrc or "", artist_name, title, album_name, duration or "")
        now = _now()
        conn.execute("INSERT OR IGNORE INTO artists(id, name, sort_name) VALUES (?, ?, ?)",
                     (artist_id, artist_name, artist_name.casefold()))
        if album_id:
            conn.execute("INSERT OR IGNORE INTO albums(id, title, artist_id) VALUES (?, ?, ?)",
                         (album_id, album_name, artist_id))
        conn.execute(
            """INSERT INTO tracks(id, title, artist_id, album_id, duration_ms, isrc, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET title=excluded.title, artist_id=excluded.artist_id,
                 album_id=COALESCE(excluded.album_id, tracks.album_id),
                 duration_ms=COALESCE(excluded.duration_ms, tracks.duration_ms),
                 isrc=COALESCE(excluded.isrc, tracks.isrc), last_seen_at=excluded.last_seen_at""",
            (track_id, title, artist_id, album_id, duration, isrc, now, now),
        )
        return track_id

    def upsert_track(self, metadata: dict[str, Any]) -> str:
        with self.connect() as conn:
            return self._upsert_track(conn, metadata)

    def snapshot_collection(self, collection_id: str, reason: str = "before-change", limit: int | None = None) -> str | None:
        """Save an ordered playlist snapshot and prune only this playlist's oldest versions."""
        limit = limit if limit is not None else int(os.getenv("PLAYLIST_VERSION_LIMIT", "10"))
        if limit < 1:
            return None
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM collections WHERE id=?", (collection_id,)).fetchone():
                return None
            rows = conn.execute(
                """SELECT ci.track_id, ci.position, ci.added_at, t.title, ar.name artist
                   FROM collection_items ci JOIN tracks t ON t.id=ci.track_id
                   JOIN artists ar ON ar.id=t.artist_id WHERE ci.collection_id=? ORDER BY ci.position""",
                (collection_id,),
            ).fetchall()
            payload = [dict(row) for row in rows]
            version_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO collection_versions VALUES (?, ?, ?, ?, ?, ?)",
                (version_id, collection_id, reason, _now(), len(payload), json.dumps(payload, ensure_ascii=False)),
            )
            conn.execute(
                """DELETE FROM collection_versions WHERE collection_id=? AND id NOT IN
                   (SELECT id FROM collection_versions WHERE collection_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?)""",
                (collection_id, collection_id, limit),
            )
        return version_id

    def collection_versions(self, collection_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, collection_id, reason, created_at, item_count FROM collection_versions WHERE collection_id=? ORDER BY created_at DESC",
                (collection_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_event(self, event: Any) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO app_logs(ts, kind, tag, message, data) VALUES (?, ?, ?, ?, ?)",
                         (event.ts, event.kind, event.tag or "", event.message,
                          json.dumps(event.data, ensure_ascii=False) if event.data is not None else None))
            # A bounded diagnostic history: enough for debugging without an
            # ever-growing database on a small Termux server.
            conn.execute("DELETE FROM app_logs WHERE id NOT IN (SELECT id FROM app_logs ORDER BY id DESC LIMIT 5000)")

    def logs(self, kind: str = "", tag: str = "", query: str = "", limit: int = 500) -> list[dict[str, Any]]:
        clauses, params = [], []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if tag:
            clauses.append("tag = ?")
            params.append(tag)
        if query:
            clauses.append("message LIKE ?")
            params.append(f"%{query}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT id, ts, kind, tag, message, data FROM app_logs {where} ORDER BY id DESC LIMIT ?",
                [*params, max(1, min(limit, 2000))],
            ).fetchall()
        return [{**dict(row), "data": json.loads(row["data"]) if row["data"] else None} for row in rows]

    def restore_collection_version(self, version_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM collection_versions WHERE id=?", (version_id,)).fetchone()
            if not row:
                raise KeyError("playlist version not found")
            collection_id = row["collection_id"]
        self.snapshot_collection(collection_id, "before-restore")
        items = json.loads(row["items"])
        with self.connect() as conn:
            conn.execute("DELETE FROM collection_items WHERE collection_id=?", (collection_id,))
            conn.executemany(
                "INSERT INTO collection_items(collection_id, track_id, position, added_at) VALUES (?, ?, ?, ?)",
                [(collection_id, item["track_id"], item["position"], item.get("added_at")) for item in items],
            )
            conn.execute("UPDATE collections SET updated_at=? WHERE id=?", (_now(), collection_id))
        return {"collection_id": collection_id, "restored_items": len(items)}

    def import_listens(self, payloads: Iterable[dict[str, Any]], source: str = "listenbrainz",
                       account_id: str | None = None, account_label: str | None = None) -> dict[str, int]:
        inserted = duplicate = 0
        account_id = account_id or f"{source}:default"
        self.sync_account(source, account_label or source.replace("ytmusic", "YouTube Music").title(),
                          "connected", "history-import", account_id=account_id)
        with self.connect() as conn:
            for payload in payloads:
                metadata = payload.get("track_metadata") or payload
                listened_at = int(payload.get("listened_at") or _now())
                extra = metadata.get("additional_info") or {}
                event_id = str(payload.get("source_event_id") or extra.get("recording_msid") or "").strip() or None
                fingerprint = _stable_id(
                    "event", account_id, event_id or "", listened_at,
                    metadata.get("artist_name"), metadata.get("track_name"), payload.get("listened_ms") or "",
                )
                listen_id = _stable_id("listen", source, fingerprint)
                track_id = self._upsert_track(conn, metadata)
                explicit_listened_ms = payload.get("listened_ms")
                duration = explicit_listened_ms or extra.get("duration_ms") or metadata.get("duration_ms")
                if duration:
                    duration = (int(float(duration)) if explicit_listened_ms is not None else
                                int(float(duration) * 1000) if float(duration) < 10_000 else int(float(duration)))
                cur = conn.execute(
                    """INSERT OR IGNORE INTO listens
                       (id, track_id, account_id, listened_at, listened_ms, source, source_event_id,
                        skipped, metadata, imported_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (listen_id, track_id, account_id, listened_at, duration, source, fingerprint,
                     payload.get("skipped", extra.get("skipped")), json.dumps(payload, ensure_ascii=False), _now()),
                )
                if cur.rowcount:
                    inserted += 1
                else:
                    duplicate += 1
        self.prune_listening_history()
        return {"inserted": inserted, "duplicates": duplicate}

    def replace_listening_aggregates(self, source: str, entries: Iterable[dict[str, Any]],
                                     periods: Iterable[tuple[int, int]] = (), account_id: str | None = None,
                                     account_label: str | None = None) -> dict[str, int]:
        """Upsert provider totals for a period; repeated imports replace counts.

        This is deliberately separate from ``listens``.  A monthly Wrapped
        export saying 40 minutes after an earlier 20-minute export means 40,
        never 60.
        """
        account_id = account_id or f"{source}:default"
        self.sync_account(source, account_label or source.replace("ytmusic", "YouTube Music").title(),
                          "connected", "aggregate-import", account_id=account_id)
        entries = list(entries)
        replaced = 0
        with self.connect() as conn:
            snapshot_periods = {(int(entry["period_start"]), int(entry["period_end"])) for entry in entries}
            snapshot_periods.update((int(start), int(end)) for start, end in periods)
            for period_start, period_end in snapshot_periods:
                if period_end <= period_start:
                    raise ValueError("period_end must be after period_start")
                conn.execute(
                    "DELETE FROM listening_aggregates WHERE account_id=? AND period_start=? AND period_end=?",
                    (account_id, period_start, period_end),
                )
            for entry in entries:
                metadata = entry.get("track_metadata") or entry
                track_id = self._upsert_track(conn, metadata)
                period_start = int(entry["period_start"])
                period_end = int(entry["period_end"])
                plays = max(0, int(entry.get("play_count") or 0))
                listened_ms = max(0, int(entry.get("listened_ms") or 0))
                conn.execute(
                    """INSERT INTO listening_aggregates
                       (account_id, period_start, period_end, track_id, play_count, listened_ms,
                        source, metadata, imported_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(account_id, period_start, period_end, track_id) DO UPDATE SET
                         play_count=excluded.play_count, listened_ms=excluded.listened_ms,
                         metadata=excluded.metadata, imported_at=excluded.imported_at""",
                    (account_id, period_start, period_end, track_id, plays, listened_ms,
                     source, json.dumps(entry, ensure_ascii=False), _now()),
                )
                replaced += 1
        self.prune_listening_history()
        return {"replaced": replaced}

    def prune_listening_history(self, years: int | None = None,
                                now: datetime | None = None) -> dict[str, Any]:
        """Drop recap rows before the retained calendar-year window.

        Canonical tracks, playlists and surfaces are intentionally untouched.
        With three years in 2026, only listening rows before 2024-01-01 go.
        """
        years = listening_retention_years(years)
        now = now or datetime.now(timezone.utc)
        cutoff = datetime(now.year - years + 1, 1, 1, tzinfo=timezone.utc)
        cutoff_ts = int(cutoff.timestamp())
        with self.connect() as conn:
            events = conn.execute("DELETE FROM listens WHERE listened_at < ?", (cutoff_ts,)).rowcount
            aggregates = conn.execute(
                "DELETE FROM listening_aggregates WHERE period_end <= ?", (cutoff_ts,)
            ).rowcount
        return {
            "years": years,
            "cutoff_year": cutoff.year,
            "deleted_listens": events,
            "deleted_aggregates": aggregates,
        }

    @staticmethod
    def _account_clause(account_ids: Iterable[str] | None) -> tuple[str, list[str]]:
        """(sql fragment, params) restricting a listens/aggregates query to the
        given account ids. Empty/None means every account — the default recap."""
        ids = [a for a in (account_ids or []) if a]
        if not ids:
            return "", []
        marks = ",".join("?" * len(ids))
        return f" AND account_id IN ({marks})", list(ids)

    def recap_history(self, years: int | None = None, now: datetime | None = None,
                      account_ids: Iterable[str] | None = None) -> dict[str, Any]:
        """Compact month summaries for the configured recap history window.

        `account_ids` filters the combined month totals to those accounts; empty
        means the unified recap across every account."""
        years = listening_retention_years(years)
        now = now or datetime.now(timezone.utc)
        cutoff_year = now.year - years + 1
        cutoff = int(datetime(cutoff_year, 1, 1, tzinfo=timezone.utc).timestamp())
        end = int(datetime(now.year + 1, 1, 1, tzinfo=timezone.utc).timestamp())
        account_sql, account_params = self._account_clause(account_ids)
        with self.connect() as conn:
            rows = conn.execute(
                """WITH combined AS (
                     SELECT CAST(strftime('%Y', listened_at, 'unixepoch') AS INTEGER) year,
                            CAST(strftime('%m', listened_at, 'unixepoch') AS INTEGER) month,
                            l.track_id track_id, t.artist_id artist_id,
                            COUNT(*) plays,
                            SUM(COALESCE(l.listened_ms, t.duration_ms, 0)) listened_ms
                     FROM listens l JOIN tracks t ON t.id=l.track_id
                     WHERE listened_at >= ? AND listened_at < ?""" + account_sql +
                """
                     GROUP BY year, month, l.track_id
                     UNION ALL
                     SELECT CAST(strftime('%Y', period_start, 'unixepoch') AS INTEGER) year,
                            CAST(strftime('%m', period_start, 'unixepoch') AS INTEGER) month,
                            la.track_id track_id, t.artist_id artist_id,
                            SUM(play_count) plays, SUM(listened_ms) listened_ms
                     FROM listening_aggregates la JOIN tracks t ON t.id=la.track_id
                     WHERE period_start >= ? AND period_start < ?""" + account_sql +
                """
                     GROUP BY year, month, la.track_id
                   )
                   SELECT year, month, SUM(plays) plays, SUM(listened_ms) listened_ms,
                          COUNT(DISTINCT track_id) tracks, COUNT(DISTINCT artist_id) artists
                   FROM combined GROUP BY year, month ORDER BY year DESC, month DESC""",
                [cutoff, end, *account_params, cutoff, end, *account_params],
            ).fetchall()
        return {
            "retention_years": years,
            "cutoff_year": cutoff_year,
            "current_year": now.year,
            "months": [dict(row) for row in rows],
        }

    def library(self, query: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        params: list[Any] = []
        where = ""
        if query.strip():
            where = "WHERE t.title LIKE ? OR ar.name LIKE ? OR al.title LIKE ?"
            needle = f"%{query.strip()}%"
            params.extend([needle, needle, needle])
        with self.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM tracks t JOIN artists ar ON ar.id=t.artist_id LEFT JOIN albums al ON al.id=t.album_id {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"""SELECT t.id, t.title, ar.name artist, COALESCE(al.title, '') album,
                           t.duration_ms, t.isrc,
                           (SELECT COUNT(*) FROM listens le WHERE le.track_id=t.id) +
                           COALESCE((SELECT SUM(la.play_count) FROM listening_aggregates la WHERE la.track_id=t.id), 0) play_count,
                           NULLIF(MAX(COALESCE((SELECT MAX(le.listened_at) FROM listens le WHERE le.track_id=t.id), 0),
                                      COALESCE((SELECT MAX(la.period_end) FROM listening_aggregates la WHERE la.track_id=t.id), 0)), 0) last_listened_at
                    FROM tracks t JOIN artists ar ON ar.id=t.artist_id
                    LEFT JOIN albums al ON al.id=t.album_id
                    {where} ORDER BY play_count DESC, t.title LIMIT ? OFFSET ?""",
                [*params, max(1, min(limit, 500)), max(0, offset)],
            ).fetchall()
        return {"total": total, "items": [dict(row) for row in rows]}

    def summary(self) -> dict[str, Any]:
        with self.connect() as conn:
            counts = {name: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for name, table in {
                "tracks": "tracks", "artists": "artists", "albums": "albums", "playlists": "collections",
                "listens": "listens", "accounts": "service_accounts",
            }.items()}
            event_ms = conn.execute(
                "SELECT COALESCE(SUM(COALESCE(listened_ms, t.duration_ms, 0)), 0) FROM listens l JOIN tracks t ON t.id=l.track_id"
            ).fetchone()[0]
            aggregate_ms = conn.execute("SELECT COALESCE(SUM(listened_ms), 0) FROM listening_aggregates").fetchone()[0]
            counts["listens"] += conn.execute("SELECT COALESCE(SUM(play_count), 0) FROM listening_aggregates").fetchone()[0]
            counts["listened_ms"] = event_ms + aggregate_ms
        return counts

    def recap(self, year: int | None = None, month: int | None = None,
              account_ids: Iterable[str] | None = None) -> dict[str, Any]:
        """Recap for a year/month. `account_ids` restricts every headline, ranking
        and breakdown to those accounts (empty means the unified recap); events
        and snapshots are never double counted — each side is filtered by its own
        account column and summed once."""
        year = year or datetime.now(timezone.utc).year
        start = datetime(year, month or 1, 1, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        elif month:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        start_ts, end_ts = int(start.timestamp()), int(end.timestamp())
        account_sql, account_params = self._account_clause(account_ids)
        args = (start_ts, end_ts)
        with self.connect() as conn:
            event_headline = conn.execute(
                """SELECT COUNT(*) plays, COUNT(DISTINCT l.track_id) tracks,
                          COUNT(DISTINCT t.artist_id) artists,
                          COALESCE(SUM(COALESCE(l.listened_ms, t.duration_ms, 0)), 0) listened_ms
                   FROM listens l JOIN tracks t ON t.id=l.track_id
                   WHERE l.listened_at >= ? AND l.listened_at < ?""" + account_sql,
                [*args, *account_params]).fetchone()
            aggregate_headline = conn.execute(
                """SELECT COALESCE(SUM(play_count), 0) plays, COUNT(DISTINCT track_id) tracks,
                          COALESCE(SUM(listened_ms), 0) listened_ms
                   FROM listening_aggregates WHERE period_start < ? AND period_end > ?""" + account_sql,
                [end_ts, start_ts, *account_params]).fetchone()
            top_tracks = conn.execute(
                """WITH combined AS (
                     SELECT track_id, COUNT(*) plays, SUM(COALESCE(l.listened_ms, t.duration_ms, 0)) listened_ms
                     FROM listens l JOIN tracks t ON t.id=l.track_id
                     WHERE listened_at >= ? AND listened_at < ?""" + account_sql +
                """ GROUP BY track_id
                     UNION ALL
                     SELECT track_id, SUM(play_count), SUM(listened_ms) FROM listening_aggregates
                     WHERE period_start < ? AND period_end > ?""" + account_sql +
                """ GROUP BY track_id
                   ) SELECT t.id, t.title, ar.name artist, SUM(c.plays) plays, SUM(c.listened_ms) listened_ms
                   FROM combined c JOIN tracks t ON t.id=c.track_id JOIN artists ar ON ar.id=t.artist_id
                   GROUP BY t.id ORDER BY plays DESC, listened_ms DESC LIMIT 10""",
                [*args, *account_params, end_ts, start_ts, *account_params]).fetchall()
            top_artists = conn.execute(
                """WITH combined AS (
                     SELECT track_id, COUNT(*) plays FROM listens
                     WHERE listened_at >= ? AND listened_at < ?""" + account_sql +
                """ GROUP BY track_id
                     UNION ALL
                     SELECT track_id, SUM(play_count) FROM listening_aggregates
                     WHERE period_start < ? AND period_end > ?""" + account_sql +
                """ GROUP BY track_id
                   ) SELECT ar.id, ar.name, SUM(c.plays) plays, COUNT(DISTINCT t.id) tracks
                   FROM combined c JOIN tracks t ON t.id=c.track_id JOIN artists ar ON ar.id=t.artist_id
                   GROUP BY ar.id ORDER BY plays DESC LIMIT 10""",
                [*args, *account_params, end_ts, start_ts, *account_params]).fetchall()
            event_services = conn.execute(
                """SELECT l.account_id, sa.label account_label, l.source,
                          COUNT(*) plays, SUM(COALESCE(l.listened_ms, t.duration_ms, 0)) listened_ms
                   FROM listens l JOIN tracks t ON t.id=l.track_id
                   LEFT JOIN service_accounts sa ON sa.id=l.account_id
                   WHERE l.listened_at >= ? AND l.listened_at < ?""" + account_sql +
                """ GROUP BY l.account_id, l.source""",
                [*args, *account_params]).fetchall()
            aggregate_services = conn.execute(
                """SELECT la.account_id, sa.label account_label, la.source,
                          SUM(play_count) plays, SUM(listened_ms) listened_ms
                   FROM listening_aggregates la
                   LEFT JOIN service_accounts sa ON sa.id=la.account_id
                   WHERE la.period_start < ? AND la.period_end > ?""" + account_sql +
                """ GROUP BY la.account_id, la.source""",
                [end_ts, start_ts, *account_params]).fetchall()
            service_totals: dict[str, dict[str, Any]] = {}
            for row in [*event_services, *aggregate_services]:
                key = f"{row['account_id'] or ''}|{row['source']}"
                item = service_totals.setdefault(key, {
                    "source": row["source"], "account_id": row["account_id"],
                    "account_label": row["account_label"] or row["account_id"] or row["source"],
                    "plays": 0, "listened_ms": 0,
                })
                item["plays"] += row["plays"] or 0
                item["listened_ms"] += row["listened_ms"] or 0
            event_months = conn.execute(
                """SELECT CAST(strftime('%m', datetime(listened_at, 'unixepoch')) AS INTEGER) month, COUNT(*) plays
                   FROM listens WHERE listened_at >= ? AND listened_at < ?""" + account_sql + " GROUP BY month",
                [*args, *account_params]).fetchall()
            aggregate_months = conn.execute(
                """SELECT CAST(strftime('%m', datetime(period_start, 'unixepoch')) AS INTEGER) month, SUM(play_count) plays
                   FROM listening_aggregates WHERE period_start < ? AND period_end > ?""" + account_sql + " GROUP BY month",
                [end_ts, start_ts, *account_params]).fetchall()
            month_totals: dict[int, int] = {}
            for row in [*event_months, *aggregate_months]:
                month_totals[row["month"]] = month_totals.get(row["month"], 0) + int(row["plays"] or 0)
            artist_count = conn.execute(
                """SELECT COUNT(DISTINCT t.artist_id) FROM tracks t WHERE t.id IN (
                     SELECT track_id FROM listens WHERE listened_at >= ? AND listened_at < ?""" + account_sql +
                """ UNION SELECT track_id FROM listening_aggregates WHERE period_start < ? AND period_end > ?""" +
                account_sql + ")",
                [*args, *account_params, end_ts, start_ts, *account_params]).fetchone()[0]
            track_count = conn.execute(
                """SELECT COUNT(*) FROM tracks t WHERE t.id IN (
                     SELECT track_id FROM listens WHERE listened_at >= ? AND listened_at < ?""" + account_sql +
                """ UNION SELECT track_id FROM listening_aggregates WHERE period_start < ? AND period_end > ?""" +
                account_sql + ")",
                [*args, *account_params, end_ts, start_ts, *account_params]).fetchone()[0]
        headline = {"plays": event_headline["plays"] + aggregate_headline["plays"],
                    "tracks": track_count, "artists": artist_count,
                    "listened_ms": event_headline["listened_ms"] + aggregate_headline["listened_ms"]}
        return {"year": year, "month": month, **headline,
                "top_tracks": [dict(r) for r in top_tracks], "top_artists": [dict(r) for r in top_artists],
                "services": sorted(service_totals.values(), key=lambda row: row["plays"], reverse=True),
                "by_month": [{"month": key, "plays": month_totals[key]} for key in sorted(month_totals)]}
