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
    "musify": {"library_read": False, "playlist_read": False, "playlist_create": True, "playlist_write": False},
    "sonora": {"library_read": True, "playlist_read": True, "playlist_create": True, "playlist_write": True},
}


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
            conn.execute("PRAGMA optimize")

    def sync_account(self, provider: str, label: str, status: str, auth_mode: str | None = None) -> dict[str, Any]:
        now = _now()
        account_id = f"{provider}:default"
        capabilities = PROVIDER_CAPABILITIES.get(provider, {"library_read": False, "playlist_read": False,
                                                               "playlist_create": False, "playlist_write": False})
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO service_accounts
                   (id, provider, label, status, auth_mode, capabilities, is_default, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET label=excluded.label, status=excluded.status,
                     auth_mode=excluded.auth_mode, capabilities=excluded.capabilities, updated_at=excluded.updated_at""",
                (account_id, provider, label, status, auth_mode, json.dumps(capabilities), now, now),
            )
        return {"id": account_id, "provider": provider, "label": label, "status": status,
                "capabilities": capabilities, "is_default": True}

    def accounts(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM service_accounts ORDER BY provider, label").fetchall()
        return [{**dict(row), "capabilities": json.loads(row["capabilities"]),
                 "is_default": bool(row["is_default"])} for row in rows]

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

    def import_listens(self, payloads: Iterable[dict[str, Any]], source: str = "listenbrainz") -> dict[str, int]:
        inserted = duplicate = 0
        with self.connect() as conn:
            for payload in payloads:
                metadata = payload.get("track_metadata") or payload
                listened_at = int(payload.get("listened_at") or _now())
                extra = metadata.get("additional_info") or {}
                event_id = str(extra.get("recording_msid") or extra.get("submission_client") or "").strip() or None
                fingerprint = event_id or _stable_id(
                    "listen", source, listened_at, metadata.get("artist_name"), metadata.get("track_name"),
                )
                listen_id = _stable_id("listen", source, fingerprint)
                track_id = self._upsert_track(conn, metadata)
                duration = extra.get("duration_ms") or metadata.get("duration_ms")
                if duration:
                    duration = int(float(duration) * 1000) if float(duration) < 10_000 else int(float(duration))
                account_id = f"{source}:default"
                if not conn.execute("SELECT 1 FROM service_accounts WHERE id=?", (account_id,)).fetchone():
                    capabilities = PROVIDER_CAPABILITIES.get(source, {})
                    now = _now()
                    conn.execute(
                        "INSERT INTO service_accounts VALUES (?, ?, ?, NULL, 'connected', 'scrobble', ?, 1, ?, ?)",
                        (account_id, source, source.replace("ytmusic", "YouTube Music").title(), json.dumps(capabilities), now, now),
                    )
                cur = conn.execute(
                    """INSERT OR IGNORE INTO listens
                       (id, track_id, account_id, listened_at, listened_ms, source, source_event_id,
                        skipped, metadata, imported_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (listen_id, track_id, account_id, listened_at, duration, source, fingerprint,
                     extra.get("skipped"), json.dumps(payload, ensure_ascii=False), _now()),
                )
                if cur.rowcount:
                    inserted += 1
                else:
                    duplicate += 1
        return {"inserted": inserted, "duplicates": duplicate}

    def replace_listening_aggregates(self, source: str, entries: Iterable[dict[str, Any]]) -> dict[str, int]:
        """Upsert provider totals for a period; repeated imports replace counts.

        This is deliberately separate from ``listens``.  A monthly Wrapped
        export saying 40 minutes after an earlier 20-minute export means 40,
        never 60.
        """
        account_id = f"{source}:default"
        self.sync_account(source, source.replace("ytmusic", "YouTube Music").title(), "connected", "aggregate-import")
        entries = list(entries)
        replaced = 0
        with self.connect() as conn:
            periods = {(int(entry["period_start"]), int(entry["period_end"])) for entry in entries}
            for period_start, period_end in periods:
                conn.execute(
                    "DELETE FROM listening_aggregates WHERE account_id=? AND period_start=? AND period_end=?",
                    (account_id, period_start, period_end),
                )
            for entry in entries:
                metadata = entry.get("track_metadata") or entry
                track_id = self._upsert_track(conn, metadata)
                period_start = int(entry["period_start"])
                period_end = int(entry["period_end"])
                if period_end <= period_start:
                    raise ValueError("period_end must be after period_start")
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
        return {"replaced": replaced}

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

    def recap(self, year: int | None = None, month: int | None = None) -> dict[str, Any]:
        year = year or datetime.now().year
        start = datetime(year, month or 1, 1, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        elif month:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        args = (int(start.timestamp()), int(end.timestamp()))
        with self.connect() as conn:
            event_headline = conn.execute(
                """SELECT COUNT(*) plays, COUNT(DISTINCT l.track_id) tracks,
                          COUNT(DISTINCT t.artist_id) artists,
                          COALESCE(SUM(COALESCE(l.listened_ms, t.duration_ms, 0)), 0) listened_ms
                   FROM listens l JOIN tracks t ON t.id=l.track_id
                   WHERE l.listened_at >= ? AND l.listened_at < ?""", args).fetchone()
            aggregate_headline = conn.execute(
                """SELECT COALESCE(SUM(play_count), 0) plays, COUNT(DISTINCT track_id) tracks,
                          COALESCE(SUM(listened_ms), 0) listened_ms
                   FROM listening_aggregates WHERE period_start < ? AND period_end > ?""", (args[1], args[0])).fetchone()
            top_tracks = conn.execute(
                """WITH combined AS (
                     SELECT track_id, COUNT(*) plays, SUM(COALESCE(l.listened_ms, t.duration_ms, 0)) listened_ms
                     FROM listens l JOIN tracks t ON t.id=l.track_id WHERE listened_at >= ? AND listened_at < ? GROUP BY track_id
                     UNION ALL
                     SELECT track_id, SUM(play_count), SUM(listened_ms) FROM listening_aggregates
                     WHERE period_start < ? AND period_end > ? GROUP BY track_id
                   ) SELECT t.id, t.title, ar.name artist, SUM(c.plays) plays, SUM(c.listened_ms) listened_ms
                   FROM combined c JOIN tracks t ON t.id=c.track_id JOIN artists ar ON ar.id=t.artist_id
                   GROUP BY t.id ORDER BY plays DESC, listened_ms DESC LIMIT 10""", (*args, args[1], args[0])).fetchall()
            top_artists = conn.execute(
                """WITH combined AS (
                     SELECT track_id, COUNT(*) plays FROM listens WHERE listened_at >= ? AND listened_at < ? GROUP BY track_id
                     UNION ALL
                     SELECT track_id, SUM(play_count) FROM listening_aggregates
                     WHERE period_start < ? AND period_end > ? GROUP BY track_id
                   ) SELECT ar.id, ar.name, SUM(c.plays) plays, COUNT(DISTINCT t.id) tracks
                   FROM combined c JOIN tracks t ON t.id=c.track_id JOIN artists ar ON ar.id=t.artist_id
                   GROUP BY ar.id ORDER BY plays DESC LIMIT 10""", (*args, args[1], args[0])).fetchall()
            event_services = conn.execute(
                """SELECT source, COUNT(*) plays, SUM(COALESCE(l.listened_ms, t.duration_ms, 0)) listened_ms
                   FROM listens l JOIN tracks t ON t.id=l.track_id
                   WHERE l.listened_at >= ? AND l.listened_at < ? GROUP BY source""", args).fetchall()
            aggregate_services = conn.execute(
                """SELECT source, SUM(play_count) plays, SUM(listened_ms) listened_ms FROM listening_aggregates
                   WHERE period_start < ? AND period_end > ? GROUP BY source""", (args[1], args[0])).fetchall()
            service_totals: dict[str, dict[str, Any]] = {}
            for row in [*event_services, *aggregate_services]:
                item = service_totals.setdefault(row["source"], {"source": row["source"], "plays": 0, "listened_ms": 0})
                item["plays"] += row["plays"] or 0
                item["listened_ms"] += row["listened_ms"] or 0
            event_months = conn.execute(
                """SELECT CAST(strftime('%m', datetime(listened_at, 'unixepoch')) AS INTEGER) month, COUNT(*) plays
                   FROM listens WHERE listened_at >= ? AND listened_at < ? GROUP BY month""", args).fetchall()
            aggregate_months = conn.execute(
                """SELECT CAST(strftime('%m', datetime(period_start, 'unixepoch')) AS INTEGER) month, SUM(play_count) plays
                   FROM listening_aggregates WHERE period_start < ? AND period_end > ? GROUP BY month""",
                (args[1], args[0])).fetchall()
            month_totals: dict[int, int] = {}
            for row in [*event_months, *aggregate_months]:
                month_totals[row["month"]] = month_totals.get(row["month"], 0) + int(row["plays"] or 0)
            artist_count = conn.execute(
                """SELECT COUNT(DISTINCT t.artist_id) FROM tracks t WHERE t.id IN (
                     SELECT track_id FROM listens WHERE listened_at >= ? AND listened_at < ?
                     UNION SELECT track_id FROM listening_aggregates WHERE period_start < ? AND period_end > ?)""",
                (*args, args[1], args[0])).fetchone()[0]
            track_count = conn.execute(
                """SELECT COUNT(*) FROM tracks t WHERE t.id IN (
                     SELECT track_id FROM listens WHERE listened_at >= ? AND listened_at < ?
                     UNION SELECT track_id FROM listening_aggregates WHERE period_start < ? AND period_end > ?)""",
                (*args, args[1], args[0])).fetchone()[0]
        headline = {"plays": event_headline["plays"] + aggregate_headline["plays"],
                    "tracks": track_count, "artists": artist_count,
                    "listened_ms": event_headline["listened_ms"] + aggregate_headline["listened_ms"]}
        return {"year": year, "month": month, **headline,
                "top_tracks": [dict(r) for r in top_tracks], "top_artists": [dict(r) for r in top_artists],
                "services": sorted(service_totals.values(), key=lambda row: row["plays"], reverse=True),
                "by_month": [{"month": key, "plays": month_totals[key]} for key in sorted(month_totals)]}
