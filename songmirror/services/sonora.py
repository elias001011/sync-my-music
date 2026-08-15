"""Sonora backup v2 adapter and opt-in LAN peer protocol.

Sonora's library exchange format is intentionally simple JSON.  Keeping the
translation here lets the canonical database remain independent from Drift and
also makes file backup/restore use exactly the same path as P2P synchronization.
"""

from __future__ import annotations

import json
import random
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import requests

from .music_database import PROVIDER_CAPABILITIES, MusicDatabase, _now, _stable_id


DEFAULT_SURFACES = [
    "likedSongs", "followedArtists", "likedAlbums", "likedPlaylists",
    "playlists", "history",
]


class SonoraAdapter:
    def __init__(self, database: MusicDatabase):
        self.db = database
        self.account_id = "sonora:default"
        self.db.sync_account("sonora", "Sonora", "connected", "backup+p2p")

    @staticmethod
    def _iso(ts: int | None = None) -> str:
        return datetime.fromtimestamp(ts or _now(), timezone.utc).isoformat()

    def import_backup(self, data: dict[str, Any], surfaces: list[str] | None = None) -> dict[str, int]:
        selected = set(surfaces or DEFAULT_SURFACES)
        stats = {key: 0 for key in DEFAULT_SURFACES}
        with self.db.connect() as conn:
            if "likedSongs" in selected:
                for song in data.get("likedSongs") or []:
                    track_id = self.db._upsert_track(conn, {
                        "track_name": song.get("title"), "artist_name": song.get("artist"),
                        "duration_ms": song.get("duration"),
                    })
                    video_id = str(song.get("videoId") or "")
                    conn.execute(
                        """INSERT OR REPLACE INTO service_tracks
                           (account_id, provider_track_id, track_id, metadata, available, last_seen_at)
                           VALUES (?, ?, ?, ?, 1, ?)""",
                        (self.account_id, video_id, track_id, json.dumps(song, ensure_ascii=False), _now()),
                    )
                    conn.execute(
                        """INSERT OR IGNORE INTO surface_items
                           (id, account_id, surface, entity_type, entity_id, provider_id, added_at, metadata)
                           VALUES (?, ?, 'liked_songs', 'track', ?, ?, ?, ?)""",
                        (_stable_id("surface", self.account_id, "liked_songs", track_id), self.account_id,
                         track_id, video_id, self._parse_time(song.get("addedAt")), json.dumps(song, ensure_ascii=False)),
                    )
                    stats["likedSongs"] += 1

            if "followedArtists" in selected:
                for artist in data.get("followedArtists") or []:
                    name = str(artist.get("name") or "Unknown artist")
                    artist_id = _stable_id("artist", name)
                    provider_id = str(artist.get("artistId") or "")
                    conn.execute("INSERT OR IGNORE INTO artists VALUES (?, ?, ?)", (artist_id, name, name.casefold()))
                    self._surface(conn, "followed_artists", "artist", artist_id, provider_id, artist)
                    stats["followedArtists"] += 1

            if "likedAlbums" in selected:
                for album in data.get("likedAlbums") or []:
                    artist_name = str(album.get("artistName") or "Unknown artist")
                    artist_id = _stable_id("artist", artist_name)
                    title = str(album.get("name") or "Unknown album")
                    album_id = _stable_id("album", artist_name, title)
                    conn.execute("INSERT OR IGNORE INTO artists VALUES (?, ?, ?)",
                                 (artist_id, artist_name, artist_name.casefold()))
                    conn.execute("INSERT OR IGNORE INTO albums VALUES (?, ?, ?, ?, ?)",
                                 (album_id, title, artist_id, album.get("year"), album.get("thumbnailUrl")))
                    self._surface(conn, "liked_albums", "album", album_id, str(album.get("albumId") or ""), album)
                    stats["likedAlbums"] += 1

            if "likedPlaylists" in selected:
                for playlist in data.get("likedPlaylists") or []:
                    provider_id = str(playlist.get("playlistId") or "")
                    entity_id = _stable_id("remote_playlist", "sonora", provider_id)
                    self._surface(conn, "liked_playlists", "playlist", entity_id, provider_id, playlist)
                    stats["likedPlaylists"] += 1

        if "playlists" in selected:
            stats["playlists"] = self._import_playlists(data)

        if "history" in selected:
            aggregates = []
            for item in data.get("history") or []:
                listened_at = self._parse_time(item.get("playedAt"))
                dt = datetime.fromtimestamp(listened_at, timezone.utc)
                period_start = int(datetime(dt.year, dt.month, 1, tzinfo=timezone.utc).timestamp())
                period_end = int((datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
                                  if dt.month == 12 else datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)).timestamp())
                plays = max(0, min(int(item.get("playCount") or 0), 10_000))
                duration_s = int(item.get("duration") or 210)
                aggregates.append({
                    "period_start": period_start, "period_end": period_end,
                    "play_count": plays, "listened_ms": plays * duration_s * 1000,
                    "track_metadata": {"track_name": item.get("title"), "artist_name": item.get("artist"),
                                       "duration_ms": duration_s * 1000},
                    "videoId": item.get("videoId"),
                })
            result = self.db.replace_listening_aggregates("sonora", aggregates)
            stats["history"] = result["replaced"]
        return stats

    def _surface(self, conn, surface: str, entity_type: str, entity_id: str,
                 provider_id: str, metadata: dict[str, Any]) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO surface_items
               (id, account_id, surface, entity_type, entity_id, provider_id, added_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (_stable_id("surface", self.account_id, surface, entity_id), self.account_id, surface,
             entity_type, entity_id, provider_id, self._parse_time(metadata.get("addedAt")),
             json.dumps(metadata, ensure_ascii=False)),
        )

    def _import_playlists(self, data: dict[str, Any]) -> int:
        entries_by_id = data.get("playlistEntries") or {}
        imported = 0
        for playlist in data.get("playlists") or []:
            provider_id = str(playlist.get("id"))
            with self.db.connect() as conn:
                mirror = conn.execute(
                    "SELECT collection_id FROM collection_mirrors WHERE account_id=? AND provider_collection_id=?",
                    (self.account_id, provider_id),
                ).fetchone()
                collection_id = mirror[0] if mirror else _stable_id("playlist", "sonora", provider_id, playlist.get("name"))
                exists = conn.execute("SELECT 1 FROM collections WHERE id=?", (collection_id,)).fetchone()
            if exists:
                self.db.snapshot_collection(collection_id, "before-sonora-merge")
            now = _now()
            with self.db.connect() as conn:
                conn.execute(
                    """INSERT INTO collections(id, kind, title, description, created_at, updated_at)
                       VALUES (?, 'playlist', ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET title=excluded.title, description=excluded.description,
                         updated_at=excluded.updated_at""",
                    (collection_id, playlist.get("name") or "Sonora playlist", playlist.get("description"),
                     self._parse_time(playlist.get("createdAt")), now),
                )
                conn.execute(
                    """INSERT OR REPLACE INTO collection_mirrors
                       (collection_id, account_id, provider_collection_id, writable, last_pulled_at)
                       VALUES (?, ?, ?, 1, ?)""", (collection_id, self.account_id, provider_id, now),
                )
                conn.execute("DELETE FROM collection_items WHERE collection_id=?", (collection_id,))
                for position, entry in enumerate(entries_by_id.get(provider_id) or [], 1):
                    track_id = self.db._upsert_track(conn, {
                        "track_name": entry.get("title"), "artist_name": entry.get("artist"),
                        "duration_ms": entry.get("duration"),
                    })
                    video_id = str(entry.get("videoId") or "")
                    conn.execute(
                        """INSERT OR REPLACE INTO service_tracks
                           (account_id, provider_track_id, track_id, metadata, available, last_seen_at)
                           VALUES (?, ?, ?, ?, 1, ?)""",
                        (self.account_id, video_id, track_id, json.dumps(entry, ensure_ascii=False), now),
                    )
                    conn.execute(
                        "INSERT INTO collection_items VALUES (?, ?, ?, ?)",
                        (collection_id, track_id, int(entry.get("position") or position), now),
                    )
            imported += 1
        return imported

    def export_backup(self, surfaces: list[str] | None = None) -> dict[str, Any]:
        selected = set(surfaces or DEFAULT_SURFACES)
        out: dict[str, Any] = {"version": 2, "exportedAt": self._iso()}
        with self.db.connect() as conn:
            out["likedSongs"] = self._export_surface(conn, "liked_songs") if "likedSongs" in selected else []
            out["followedArtists"] = self._export_surface(conn, "followed_artists") if "followedArtists" in selected else []
            out["likedAlbums"] = self._export_surface(conn, "liked_albums") if "likedAlbums" in selected else []
            out["likedPlaylists"] = self._export_surface(conn, "liked_playlists") if "likedPlaylists" in selected else []
            playlists = conn.execute("SELECT * FROM collections WHERE kind='playlist' ORDER BY created_at").fetchall()
            out["playlists"] = []
            out["playlistEntries"] = {}
            if "playlists" in selected:
                for index, playlist in enumerate(playlists, 1):
                    out["playlists"].append({"id": index, "name": playlist["title"],
                                             "description": playlist["description"],
                                             "createdAt": self._iso(playlist["created_at"])})
                    rows = conn.execute(
                        """SELECT ci.position, t.title, ar.name artist, t.duration_ms,
                                  COALESCE(st.provider_track_id, '') video_id
                           FROM collection_items ci JOIN tracks t ON t.id=ci.track_id
                           JOIN artists ar ON ar.id=t.artist_id
                           LEFT JOIN service_tracks st ON st.track_id=t.id AND st.account_id=?
                           WHERE ci.collection_id=? ORDER BY ci.position""", (self.account_id, playlist["id"])).fetchall()
                    out["playlistEntries"][str(index)] = [
                        {"playlistId": index, "videoId": row["video_id"], "position": row["position"],
                         "title": row["title"], "artist": row["artist"], "thumbnailUrl": None,
                         "duration": int((row["duration_ms"] or 0) / 1000) or None,
                         "isVideo": False, "isExplicit": False}
                        for row in rows if row["video_id"]
                    ]
            out["history"] = []
            if "history" in selected:
                rows = conn.execute(
                    """SELECT t.title, ar.name artist, t.duration_ms, MAX(l.listened_at) played_at,
                              COUNT(*) play_count, COALESCE(st.provider_track_id, '') video_id
                       FROM listens l JOIN tracks t ON t.id=l.track_id JOIN artists ar ON ar.id=t.artist_id
                       LEFT JOIN service_tracks st ON st.track_id=t.id AND st.account_id=?
                       GROUP BY t.id ORDER BY played_at DESC LIMIT 500""", (self.account_id,)).fetchall()
                out["history"] = [
                    {"videoId": r["video_id"], "title": r["title"], "artist": r["artist"],
                     "thumbnailUrl": None, "playedAt": self._iso(r["played_at"]), "playCount": r["play_count"],
                     "duration": int((r["duration_ms"] or 0) / 1000) or None, "isVideo": False,
                     "isExplicit": False} for r in rows if r["video_id"]
                ]
        out["searchHistory"] = []
        out["settings"] = None
        return out

    @staticmethod
    def _export_surface(conn, surface: str) -> list[dict[str, Any]]:
        rows = conn.execute("SELECT metadata FROM surface_items WHERE surface=? ORDER BY added_at", (surface,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    @staticmethod
    def _parse_time(value: Any) -> int:
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value:
            try:
                return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
            except ValueError:
                pass
        return _now()


class SonoraLanService:
    """Small protocol coordinator; HTTP endpoints live in the FastAPI router."""

    def __init__(self, settings, adapter: SonoraAdapter):
        self.settings = settings
        self.adapter = adapter
        self.device_id = settings.get("SONORA_DEVICE_ID") or uuid.uuid4().hex
        if not settings.get("SONORA_DEVICE_ID"):
            settings.save({"SONORA_DEVICE_ID": self.device_id})
        self.pending: dict[str, dict[str, Any]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def name(self) -> str:
        return self.settings.get("SONORA_DEVICE_NAME") or "Sync My Music"

    @property
    def port(self) -> int:
        return int(self.settings.get("SONORA_SYNC_HTTP_PORT") or 8080)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._discovery_loop, name="sonora-discovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _discovery_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", 53530))
            sock.settimeout(1)
            while not self._stop.is_set():
                try:
                    data, address = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                if data == b"SONORA_DISCOVERY_REQUEST":
                    message = f"SONORA_DISCOVERY_RESPONSE;{self.name};{self.port};{self.device_id}"
                    sock.sendto(message.encode(), address)
        except OSError:
            # Another Sonora instance can legitimately own the discovery port.
            return
        finally:
            sock.close()

    def discover(self, timeout: float = 3.0) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", 0))
        sock.settimeout(0.25)
        sock.sendto(b"SONORA_DISCOVERY_REQUEST", ("255.255.255.255", 53530))
        deadline = time.monotonic() + min(max(timeout, 0.5), 8)
        while time.monotonic() < deadline:
            try:
                data, address = sock.recvfrom(4096)
            except socket.timeout:
                continue
            parts = data.decode(errors="ignore").split(";")
            if len(parts) >= 4 and parts[0] == "SONORA_DISCOVERY_RESPONSE" and parts[3] != self.device_id:
                found[parts[3]] = {"device_id": parts[3], "name": parts[1], "ip": address[0], "port": int(parts[2])}
        sock.close()
        return list(found.values())

    def save_device(self, device_id: str, name: str, ip: str, port: int, paired: bool = True) -> None:
        now = _now()
        with self.adapter.db.connect() as conn:
            conn.execute(
                """INSERT INTO sonora_devices(device_id, name, ip, port, paired, last_seen_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(device_id) DO UPDATE SET name=excluded.name, ip=excluded.ip, port=excluded.port,
                     paired=MAX(sonora_devices.paired, excluded.paired),
                     last_seen_at=excluded.last_seen_at, updated_at=excluded.updated_at""",
                (device_id, name, ip, port, int(paired), now, now, now),
            )

    def devices(self) -> list[dict[str, Any]]:
        with self.adapter.db.connect() as conn:
            rows = conn.execute("SELECT * FROM sonora_devices ORDER BY name").fetchall()
        return [{**dict(row), "paired": bool(row["paired"]), "auto_sync": bool(row["auto_sync"]),
                 "surfaces": json.loads(row["surfaces"])} for row in rows]

    def paired(self, device_id: str) -> bool:
        with self.adapter.db.connect() as conn:
            row = conn.execute("SELECT paired FROM sonora_devices WHERE device_id=?", (device_id,)).fetchone()
        return bool(row and row[0])

    def request_pair(self, ip: str, port: int) -> dict[str, Any]:
        response = requests.post(f"http://{ip}:{port}/api/sync/pair-request", json={
            "clientId": self.device_id, "clientName": self.name, "clientPort": self.port,
        }, timeout=15)
        response.raise_for_status()
        return response.json()

    def verify_pair(self, ip: str, port: int, pin: str) -> dict[str, Any]:
        response = requests.post(f"http://{ip}:{port}/api/sync/pair-verify",
                                 json={"clientId": self.device_id, "pin": pin}, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "paired":
            self.save_device(data["deviceId"], data.get("deviceName") or "Sonora", ip, port)
        return data

    def sync(self, device_id: str, surfaces: list[str] | None = None) -> dict[str, Any]:
        with self.adapter.db.connect() as conn:
            row = conn.execute("SELECT * FROM sonora_devices WHERE device_id=? AND paired=1", (device_id,)).fetchone()
        if not row:
            raise ValueError("Sonora device is not paired")
        selected = surfaces or json.loads(row["surfaces"])
        response = requests.post(f"http://{row['ip']}:{row['port']}/api/sync/merge", json={
            "clientId": self.device_id, "clientName": self.name,
            "library": self.adapter.export_backup(selected),
        }, timeout=45)
        response.raise_for_status()
        remote = response.json()
        local_stats = self.adapter.import_backup(remote["library"], selected)
        with self.adapter.db.connect() as conn:
            conn.execute("UPDATE sonora_devices SET last_synced_at=?, updated_at=? WHERE device_id=?",
                         (_now(), _now(), device_id))
        return {"local": local_stats, "remote": remote.get("stats") or {}}
