"""Safe Musify Hive backup import and read-only canonical playlist source.

Musify exports its ``user`` box as a raw Hive 2 binary file.  This module only
decodes Hive's built-in value types (plus its built-in date adapters); it never
loads Dart code or restores application settings.  Imported music is projected
into the canonical database so a Musify playlist can be the source of a normal
one-off transfer to another provider.
"""

from __future__ import annotations

import base64
import json
import math
import sqlite3
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..engine.targets.base import MirrorTarget
from .music_database import MusicDatabase, _now, _stable_id


MAX_BACKUP_BYTES = 32 * 1024 * 1024
MAX_COLLECTION_ITEMS = 100_000
MAX_NESTING_DEPTH = 40
MAX_STRING_BYTES = 4 * 1024 * 1024

USER_KEYS = {
    "likedSongs", "recentlyPlayedSongs", "playlists", "customPlaylists",
    "likedPlaylists", "playlistFolders", "pinnedPlaylistIds",
    "wrappedListeningStats",
}


class HiveDecodeError(ValueError):
    """The uploaded box is corrupt, encrypted or uses an unsupported adapter."""


class _HiveReader:
    def __init__(self, raw: bytes, start: int = 0, end: int | None = None):
        self.raw = raw
        self.offset = start
        self.end = len(raw) if end is None else end

    @property
    def remaining(self) -> int:
        return self.end - self.offset

    def take(self, count: int) -> bytes:
        if count < 0 or self.offset + count > self.end:
            raise HiveDecodeError("truncated Hive value")
        out = self.raw[self.offset:self.offset + count]
        self.offset += count
        return out

    def byte(self) -> int:
        return self.take(1)[0]

    def uint32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def double(self) -> float:
        return struct.unpack("<d", self.take(8))[0]

    def length(self, *, unit: int = 1) -> int:
        value = self.uint32()
        if value > MAX_COLLECTION_ITEMS or value * unit > self.remaining:
            raise HiveDecodeError("Hive collection length exceeds the safety limit")
        return value

    def string(self, byte_count: int | None = None) -> str:
        size = self.uint32() if byte_count is None else byte_count
        if size > MAX_STRING_BYTES:
            raise HiveDecodeError("Hive string exceeds the safety limit")
        try:
            return self.take(size).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HiveDecodeError("invalid UTF-8 in Hive value") from exc

    def key(self) -> int | str:
        kind = self.byte()
        if kind == 0:
            return self.uint32()
        if kind == 1:
            return self.string(self.byte())
        raise HiveDecodeError("unsupported Hive frame key type")

    def value(self, depth: int = 0) -> Any:
        if depth > MAX_NESTING_DEPTH:
            raise HiveDecodeError("Hive value nesting exceeds the safety limit")
        type_id = self.byte()
        if type_id == 0:
            return None
        if type_id in (1, 17):
            if type_id == 17:
                try:
                    return int(self.string(self.byte()))
                except ValueError as exc:
                    raise HiveDecodeError("invalid Hive BigInt") from exc
            number = self.double()
            if not math.isfinite(number):
                raise HiveDecodeError("invalid Hive integer")
            return int(number)
        if type_id == 2:
            number = self.double()
            if not math.isfinite(number):
                raise HiveDecodeError("non-finite Hive number")
            return number
        if type_id == 3:
            return self.byte() > 0
        if type_id == 4:
            return self.string()
        if type_id == 5:
            return self.take(self.length())
        if type_id == 6:
            result = []
            for _ in range(self.length(unit=8)):
                number = self.double()
                if not math.isfinite(number):
                    raise HiveDecodeError("invalid Hive integer list")
                result.append(int(number))
            return result
        if type_id == 7:
            result = [self.double() for _ in range(self.length(unit=8))]
            if any(not math.isfinite(number) for number in result):
                raise HiveDecodeError("non-finite Hive number list")
            return result
        if type_id == 8:
            return [self.byte() > 0 for _ in range(self.length())]
        if type_id == 9:
            return [self.string() for _ in range(self.length())]
        if type_id == 10:
            return [self.value(depth + 1) for _ in range(self.length())]
        if type_id == 11:
            result: dict[Any, Any] = {}
            for _ in range(self.length()):
                key = self.value(depth + 1)
                try:
                    result[key] = self.value(depth + 1)
                except TypeError as exc:
                    raise HiveDecodeError("unsupported compound Hive map key") from exc
            return result
        if type_id == 12:
            length = self.length()
            box_name = self.string(self.byte())
            return {"box": box_name, "keys": [self.key() for _ in range(length)]}
        if type_id in (16, 18):
            millis = int(self.double())
            if type_id == 18:
                self.byte()  # isUtc; epoch milliseconds already identify the instant.
            try:
                return datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError) as exc:
                raise HiveDecodeError("invalid Hive DateTime") from exc
        raise HiveDecodeError(f"unsupported Hive type adapter {type_id}")


def decode_hive_box(raw: bytes) -> dict[int | str, Any]:
    """Decode live values from an unencrypted Hive 2 box, honoring tombstones."""
    if not raw:
        raise HiveDecodeError("empty Hive backup")
    if len(raw) > MAX_BACKUP_BYTES:
        raise HiveDecodeError("Musify backup is larger than 32 MiB")
    values: dict[int | str, Any] = {}
    offset = 0
    while offset < len(raw):
        if len(raw) - offset < 8:
            raise HiveDecodeError("truncated Hive frame")
        frame_length = struct.unpack_from("<I", raw, offset)[0]
        if frame_length < 8 or frame_length > len(raw) - offset:
            raise HiveDecodeError("invalid Hive frame length")
        crc_offset = offset + frame_length - 4
        expected = struct.unpack_from("<I", raw, crc_offset)[0]
        actual = zlib.crc32(raw[offset:crc_offset]) & 0xFFFFFFFF
        if actual != expected:
            raise HiveDecodeError("Hive frame checksum failed")
        reader = _HiveReader(raw, offset + 4, crc_offset)
        key = reader.key()
        if reader.remaining:
            values[key] = reader.value()
            if reader.remaining:
                raise HiveDecodeError("unexpected bytes after Hive value")
        else:
            values.pop(key, None)
        offset += frame_length
    return values


def _items(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _duration_ms(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 86_400:
        return int(value * 1000)
    if isinstance(value, str):
        try:
            parts = [int(part) for part in value.split(":")]
        except ValueError:
            return None
        if 1 < len(parts) <= 3:
            seconds = 0
            for part in parts:
                seconds = seconds * 60 + part
            return seconds * 1000 if seconds > 0 else None
    return None


def _timestamp(value: Any) -> int:
    if isinstance(value, (int, float)):
        # Musify's createdAt is milliseconds; legacy data can contain seconds.
        return int(value / 1000) if value > 10_000_000_000 else int(value)
    if isinstance(value, str) and value:
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    return _now()


def _bounded_int(value: Any, *, maximum: int) -> int:
    try:
        return max(0, min(int(value or 0), maximum))
    except (TypeError, ValueError, OverflowError):
        return 0


def _json_safe(value: Any) -> Any:
    """Keep opaque metadata serializable without discarding a whole import."""
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def listening_entries_from_stats(stats: Any) -> list[dict[str, Any]]:
    if not isinstance(stats, dict):
        return []
    months = dict(stats.get("history")) if isinstance(stats.get("history"), dict) else {}
    current_key = str(stats.get("currentMonthKey") or "")
    if current_key:
        months[current_key] = stats.get("currentMonth") or {}
    entries: list[dict[str, Any]] = []
    for month_key, value in months.items():
        month = value if isinstance(value, dict) else {}
        try:
            year, month_number = (int(part) for part in str(month_key).split("-", 1))
            start = datetime(year, month_number, 1, tzinfo=timezone.utc)
            end = (datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month_number == 12
                   else datetime(year, month_number + 1, 1, tzinfo=timezone.utc))
        except (TypeError, ValueError):
            continue
        song_seconds = 0
        songs = month.get("songs") if isinstance(month.get("songs"), dict) else {}
        for ytid, song_value in songs.items():
            song = dict(song_value) if isinstance(song_value, dict) else {}
            seconds = _bounded_int(song.get("seconds"), maximum=31_536_000)
            plays = _bounded_int(song.get("playCount") or song.get("listeningCount"), maximum=1_000_000)
            song_seconds += seconds
            if seconds == 0 and plays == 0:
                continue
            entries.append({
                "period_start": int(start.timestamp()), "period_end": int(end.timestamp()),
                "play_count": plays,
                "listened_ms": seconds * 1000,
                "track_metadata": {
                    "track_name": song.get("title") or str(ytid),
                    "artist_name": song.get("artist") or "Unknown artist",
                    "duration_ms": _duration_ms(song.get("duration")),
                    "additional_info": {"music_service_name": "musify", "video_id": str(ytid)},
                },
            })
        residual = max(0, _bounded_int(month.get("totalSeconds"), maximum=31_536_000) - song_seconds)
        if residual:
            entries.append({
                "period_start": int(start.timestamp()), "period_end": int(end.timestamp()),
                "play_count": 0, "listened_ms": residual * 1000,
                "track_metadata": {"track_name": "Other Musify listening", "artist_name": "Musify"},
            })
    return entries


def listening_periods_from_stats(stats: Any) -> list[tuple[int, int]]:
    """Periods represented by a snapshot, including an explicitly empty month."""
    if not isinstance(stats, dict):
        return []
    keys = list((stats.get("history") or {}).keys()) if isinstance(stats.get("history"), dict) else []
    current_key = str(stats.get("currentMonthKey") or "")
    if current_key:
        keys.append(current_key)
    periods = []
    for month_key in dict.fromkeys(keys):
        try:
            year, month_number = (int(part) for part in str(month_key).split("-", 1))
            start = datetime(year, month_number, 1, tzinfo=timezone.utc)
            end = (datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month_number == 12
                   else datetime(year, month_number + 1, 1, tzinfo=timezone.utc))
        except (TypeError, ValueError):
            continue
        periods.append((int(start.timestamp()), int(end.timestamp())))
    return periods


class MusifyAdapter:
    account_id = "musify:default"

    def __init__(self, database: MusicDatabase):
        self.db = database

    def import_backup(self, raw: bytes) -> dict[str, Any]:
        data = decode_hive_box(raw)
        found = USER_KEYS.intersection(str(key) for key in data)
        if not found:
            raise HiveDecodeError("this is not a Musify user.hive backup")

        self.db.sync_account("musify", "Musify backup", "connected", "hive-backup")
        liked = _items(data.get("likedSongs"))
        recent = _items(data.get("recentlyPlayedSongs"))
        liked_playlists = _items(data.get("likedPlaylists"))
        custom = _items(data.get("customPlaylists"))
        folders = _items(data.get("playlistFolders"))
        folder_playlists: list[tuple[dict[str, Any], str]] = []
        for folder in folders:
            folder_name = str(folder.get("name") or "Untitled folder")
            folder_playlists.extend((playlist, folder_name) for playlist in _items(folder.get("playlists")))
        playlists_by_id: dict[str, tuple[dict[str, Any], str | None]] = {}
        for playlist, folder_name in [(playlist, None) for playlist in custom] + folder_playlists:
            provider_id = str(playlist.get("ytid") or _stable_id("musify_playlist", playlist.get("title")))
            playlists_by_id[provider_id] = (playlist, folder_name)
        playlists = list(playlists_by_id.values())

        # Preserve every existing Musify mirror before applying the backup
        # snapshot, including mirrors removed from the new file.
        with self.db.connect() as conn:
            existing = [row[0] for row in conn.execute(
                "SELECT collection_id FROM collection_mirrors WHERE account_id=?", (self.account_id,)
            ).fetchall()]
        for collection_id in existing:
            self.db.snapshot_collection(collection_id, "before-musify-backup")

        counts = {"likedSongs": 0, "recentlyPlayedSongs": 0, "likedPlaylists": 0,
                  "followedArtists": 0, "playlists": 0, "playlistTracks": 0,
                  "listeningStats": 0}
        imported_collection_ids: set[str] = set()
        now = _now()
        with self.db.connect() as conn:
            conn.execute("DELETE FROM surface_items WHERE account_id=? AND surface IN "
                         "('liked_songs','recently_played','liked_playlists','followed_artists','owned_playlists')",
                         (self.account_id,))
            for song in liked:
                self._import_song_surface(conn, song, "liked_songs")
                counts["likedSongs"] += 1
            for song in recent:
                self._import_song_surface(conn, song, "recently_played")
                counts["recentlyPlayedSongs"] += 1
            for playlist in liked_playlists:
                is_artist = playlist.get("isArtist") is True or str(playlist.get("source") or "") == "youtube-artist"
                if is_artist:
                    name = str(playlist.get("title") or playlist.get("artist") or "Unknown artist")
                    artist_id = _stable_id("artist", name)
                    conn.execute("INSERT OR IGNORE INTO artists(id, name, sort_name) VALUES (?, ?, ?)",
                                 (artist_id, name, name.casefold()))
                    self._surface(conn, "followed_artists", "artist", artist_id, playlist)
                    counts["followedArtists"] += 1
                else:
                    provider_id = str(playlist.get("ytid") or "")
                    self._surface(conn, "liked_playlists", "playlist",
                                  _stable_id("remote_playlist", "musify", provider_id, playlist.get("title")), playlist)
                    counts["likedPlaylists"] += 1
            owned_playlists = data.get("playlists") if isinstance(data.get("playlists"), list) else []
            for provider_id in owned_playlists:
                metadata = {"ytid": str(provider_id), "title": str(provider_id), "source": "user-youtube"}
                self._surface(conn, "owned_playlists", "playlist",
                              _stable_id("remote_playlist", "musify", provider_id), metadata)

            for playlist, folder_name in playlists:
                provider_id = str(playlist.get("ytid") or _stable_id("musify_playlist", playlist.get("title")))
                collection_id = _stable_id("playlist", "musify", provider_id)
                imported_collection_ids.add(collection_id)
                title = str(playlist.get("title") or "Musify playlist")
                description = f"Imported from Musify folder: {folder_name}" if folder_name else "Imported from Musify"
                conn.execute(
                    """INSERT INTO collections(id, kind, title, description, artwork_url, created_at, updated_at)
                       VALUES (?, 'playlist', ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET title=excluded.title, description=excluded.description,
                         artwork_url=excluded.artwork_url, updated_at=excluded.updated_at""",
                    (collection_id, title, description,
                     playlist.get("image") if isinstance(playlist.get("image"), str) else None,
                     _timestamp(playlist.get("createdAt")), now),
                )
                conn.execute(
                    """INSERT INTO collection_mirrors
                       (collection_id, account_id, provider_collection_id, writable, snapshot, last_pulled_at)
                       VALUES (?, ?, ?, 0, ?, ?)
                       ON CONFLICT(collection_id, account_id) DO UPDATE SET
                         provider_collection_id=excluded.provider_collection_id, writable=0,
                         snapshot=excluded.snapshot, last_pulled_at=excluded.last_pulled_at""",
                    (collection_id, self.account_id, provider_id,
                     json.dumps(_json_safe(playlist), ensure_ascii=False), now),
                )
                conn.execute("DELETE FROM collection_items WHERE collection_id=?", (collection_id,))
                for position, song in enumerate(_items(playlist.get("list")), 1):
                    track_id = self._upsert_song(conn, song)
                    conn.execute("INSERT INTO collection_items VALUES (?, ?, ?, ?)",
                                 (collection_id, track_id, position, now))
                    counts["playlistTracks"] += 1
                counts["playlists"] += 1

            for collection_id in set(existing) - imported_collection_ids:
                conn.execute("DELETE FROM collection_mirrors WHERE collection_id=? AND account_id=?",
                             (collection_id, self.account_id))

        raw_stats = data.get("wrappedListeningStats")
        entries = listening_entries_from_stats(raw_stats)
        periods = listening_periods_from_stats(raw_stats)
        if periods:
            counts["listeningStats"] = self.db.replace_listening_aggregates(
                "musify", entries, periods=periods
            )["replaced"]
        return {**counts, "keysFound": sorted(found)}

    def _upsert_song(self, conn: sqlite3.Connection, song: dict[str, Any]) -> str:
        metadata = {
            "track_name": song.get("title"), "artist_name": song.get("artist"),
            "album": song.get("album"), "duration_ms": _duration_ms(song.get("duration")),
        }
        track_id = self.db._upsert_track(conn, metadata)
        provider_id = str(song.get("ytid") or "").strip()
        if provider_id:
            conn.execute(
                """INSERT INTO service_tracks
                   (account_id, provider_track_id, track_id, metadata, available, last_seen_at)
                   VALUES (?, ?, ?, ?, 1, ?)
                   ON CONFLICT(account_id, provider_track_id) DO UPDATE SET track_id=excluded.track_id,
                     metadata=excluded.metadata, available=1, last_seen_at=excluded.last_seen_at""",
                (self.account_id, provider_id, track_id, json.dumps(_json_safe(song), ensure_ascii=False), _now()),
            )
        return track_id

    def _import_song_surface(self, conn: sqlite3.Connection, song: dict[str, Any], surface: str) -> None:
        track_id = self._upsert_song(conn, song)
        self._surface(conn, surface, "track", track_id, song)

    def _surface(self, conn: sqlite3.Connection, surface: str, entity_type: str,
                 entity_id: str, metadata: dict[str, Any]) -> None:
        provider_id = str(metadata.get("ytid") or "")
        conn.execute(
            """INSERT INTO surface_items
               (id, account_id, surface, entity_type, entity_id, provider_id, added_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET provider_id=excluded.provider_id,
                 added_at=excluded.added_at, metadata=excluded.metadata""",
            (_stable_id("surface", self.account_id, surface, entity_id), self.account_id, surface,
             entity_type, entity_id, provider_id,
             _timestamp(metadata.get("addedAt") or metadata.get("lastPlayed")),
             json.dumps(_json_safe(metadata), ensure_ascii=False)),
        )


class MusifyCanonicalTarget(MirrorTarget):
    """Read-only transfer source backed by imported Musify playlist mirrors."""

    name = "Musify backup"
    tag = source = "musify"

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.cache_file = str(Path(database.path).with_name("musify_transfer_cache.json"))

    def browse_playlists(self):
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT c.id, c.title name, c.description, c.artwork_url image,
                          cm.provider_collection_id, COUNT(ci.position) track_count
                   FROM collections c JOIN collection_mirrors cm ON cm.collection_id=c.id
                   LEFT JOIN collection_items ci ON ci.collection_id=c.id
                   WHERE cm.account_id='musify:default'
                   GROUP BY c.id, cm.provider_collection_id ORDER BY c.title COLLATE NOCASE"""
            ).fetchall()
        return [{**dict(row), "_owned": True} for row in rows]

    def list_playlists(self):
        return {row["name"].casefold(): row for row in self.browse_playlists()}

    def playlist_id(self, playlist):
        return playlist["id"]

    def playlist_name(self, playlist):
        return playlist["name"]

    def playlist_count(self, playlist):
        return playlist["track_count"]

    def playlist_tracks(self, playlist):
        with self.db.connect() as conn:
            mirror = conn.execute(
                "SELECT snapshot FROM collection_mirrors WHERE collection_id=? AND account_id='musify:default'",
                (playlist["id"],),
            ).fetchone()
            rows = conn.execute(
                """SELECT t.id, t.title name, ar.name artist, t.duration_ms, t.isrc,
                          COALESCE((SELECT st.provider_track_id FROM service_tracks st
                                    WHERE st.account_id='musify:default' AND st.track_id=t.id
                                    ORDER BY st.last_seen_at DESC, st.provider_track_id LIMIT 1), '') provider_track_id,
                          ci.added_at, ci.position
                   FROM collection_items ci JOIN tracks t ON t.id=ci.track_id
                   JOIN artists ar ON ar.id=t.artist_id
                   WHERE ci.collection_id=? ORDER BY ci.position""", (playlist["id"],)
            ).fetchall()
        snapshot = json.loads(mirror[0]) if mirror and mirror[0] else {}
        original_items = _items(snapshot.get("list")) if isinstance(snapshot, dict) else []
        result = []
        for row in rows:
            item = dict(row)
            position = int(item.pop("position"))
            if position <= len(original_items):
                item["provider_track_id"] = str(original_items[position - 1].get("ytid") or item["provider_track_id"])
            item["artists"] = [row["artist"]]
            result.append(item)
        return result

    def track_id(self, track):
        return track.get("provider_track_id") or track.get("id")

    def create(self, sp_playlist):
        raise RuntimeError("Musify backups are a read-only transfer source")

    def resolve(self, sp_track, cache):
        raise RuntimeError("Musify backups are a read-only transfer source")

    def add(self, playlist, target_ids):
        raise RuntimeError("Musify backups are a read-only transfer source")

    def remove(self, playlist, track):
        raise RuntimeError("Musify backups are a read-only transfer source")
