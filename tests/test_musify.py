import io
import struct
import zlib
import zipfile
import pytest
from fastapi.testclient import TestClient

from songmirror.services.musify import (
    HiveDecodeError,
    MusifyAdapter,
    MusifyCanonicalTarget,
    decode_hive_box,
)
from songmirror.services.music_database import MusicDatabase
from songmirror.services.settings import SettingsStore
from songmirror.services.transfers import transfer
from songmirror.web import create_app


def _value(value):
    if value is None:
        return b"\x00"
    if isinstance(value, bool):
        return b"\x03" + bytes([value])
    if isinstance(value, int):
        return b"\x01" + struct.pack("<d", value)
    if isinstance(value, float):
        return b"\x02" + struct.pack("<d", value)
    if isinstance(value, str):
        raw = value.encode()
        return b"\x04" + struct.pack("<I", len(raw)) + raw
    if isinstance(value, list):
        return b"\x0a" + struct.pack("<I", len(value)) + b"".join(_value(item) for item in value)
    if isinstance(value, dict):
        body = b"".join(_value(key) + _value(item) for key, item in value.items())
        return b"\x0b" + struct.pack("<I", len(value)) + body
    raise TypeError(type(value))


def _frame(key, value=...):
    key_raw = key.encode()
    body = b"\x01" + bytes([len(key_raw)]) + key_raw
    if value is not ...:
        body += _value(value)
    length = 4 + len(body) + 4
    prefix = struct.pack("<I", length) + body
    return prefix + struct.pack("<I", zlib.crc32(prefix) & 0xFFFFFFFF)


def hive_box(values):
    return b"".join(_frame(key, value) for key, value in values.items())


def musify_backup(minutes=20, playlist_songs=None):
    playlist_songs = playlist_songs or [
        {"ytid": "video000001", "title": "Road Song", "artist": "Driver", "duration": 180},
    ]
    return hive_box({
        "likedSongs": [
            {"ytid": "video000001", "title": "Road Song", "artist": "Driver", "duration": 180},
        ],
        "recentlyPlayedSongs": [
            {"ytid": "video000002", "title": "Night Song", "artist": "Driver", "duration": 200,
             "listeningCount": 3, "lastPlayed": "2026-07-20T12:00:00Z"},
        ],
        "customPlaylists": [{
            "ytid": "customId-road", "title": "Road", "source": "user-created",
            "createdAt": 1_751_328_000_000, "list": playlist_songs,
        }],
        "playlistFolders": [{
            "id": "folder-1", "name": "Trips", "playlists": [{
                "ytid": "customId-flight", "title": "Flight", "source": "user-created", "list": [],
            }],
        }],
        "likedPlaylists": [
            {"ytid": "artist-channel", "title": "An Artist", "source": "youtube-artist", "isArtist": True},
            {"ytid": "PL-liked", "title": "A saved playlist", "source": "youtube"},
        ],
        "playlists": ["PL-owned"],
        "pinnedPlaylistIds": ["customId-road"],
        "wrappedListeningStats": {
            "schemaVersion": 2,
            "currentMonthKey": "2026-07",
            "currentMonth": {
                "totalSeconds": minutes * 60,
                "songs": {"video000001": {
                    "ytid": "video000001", "title": "Road Song", "artist": "Driver",
                    "duration": 180, "seconds": minutes * 60, "playCount": minutes // 10,
                }},
            },
            "history": {},
        },
    })


def test_hive_decoder_honors_latest_frames_and_tombstones():
    raw = _frame("answer", 1) + _frame("answer", 2) + _frame("answer")
    assert decode_hive_box(raw) == {}
    assert decode_hive_box(_frame("nested", {"items": [1, True, "ok"]})) == {
        "nested": {"items": [1, True, "ok"]}
    }


def test_hive_decoder_rejects_corruption_and_unknown_adapters():
    corrupted = bytearray(_frame("answer", 42))
    corrupted[-1] ^= 0xFF
    with pytest.raises(HiveDecodeError, match="checksum"):
        decode_hive_box(bytes(corrupted))

    key = b"answer"
    body = b"\x01" + bytes([len(key)]) + key + b"\x20"
    length = 4 + len(body) + 4
    prefix = struct.pack("<I", length) + body
    raw = prefix + struct.pack("<I", zlib.crc32(prefix) & 0xFFFFFFFF)
    with pytest.raises(HiveDecodeError, match="adapter 32"):
        decode_hive_box(raw)


def test_hive_decoder_reads_the_datetime_adapter_used_by_recent_history():
    key = b"lastPlayed"
    body = b"\x01" + bytes([len(key)]) + key + b"\x12" + struct.pack("<d", 1_751_371_200_000) + b"\x01"
    length = 4 + len(body) + 4
    prefix = struct.pack("<I", length) + body
    raw = prefix + struct.pack("<I", zlib.crc32(prefix) & 0xFFFFFFFF)
    assert decode_hive_box(raw)["lastPlayed"].startswith("2025-07-01T")


def test_real_musify_backup_populates_canonical_surfaces_playlists_and_recap(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    adapter = MusifyAdapter(db)
    first = adapter.import_backup(musify_backup(20))

    assert first["likedSongs"] == 1
    assert first["recentlyPlayedSongs"] == 1
    assert first["playlists"] == 2
    assert first["playlistTracks"] == 1
    assert first["followedArtists"] == 1
    assert first["likedPlaylists"] == 1
    assert first["listeningStats"] == 1

    recap = db.recap(2026, 7)
    assert recap["listened_ms"] == 20 * 60_000
    assert recap["plays"] == 2

    target = MusifyCanonicalTarget(db)
    playlists = target.browse_playlists()
    road = next(item for item in playlists if item["name"] == "Road")
    assert target.playlist_count(road) == 1
    assert target.playlist_tracks(road)[0]["provider_track_id"] == "video000001"

    changed_song = {"ytid": "video000003", "title": "New Road", "artist": "Driver", "duration": 210}
    second = adapter.import_backup(musify_backup(40, [changed_song]))
    assert second["listeningStats"] == 1
    assert db.recap(2026, 7)["listened_ms"] == 40 * 60_000
    assert db.recap(2026, 7)["plays"] == 4
    assert target.playlist_tracks(road)[0]["name"] == "New Road"
    assert len(db.collection_versions(road["id"])) == 1

    adapter.import_backup(musify_backup(0, [changed_song]))
    cleared = db.recap(2026, 7)
    assert cleared["listened_ms"] == 0
    assert cleared["plays"] == 0

    with db.connect() as conn:
        surfaces = dict(conn.execute(
            "SELECT surface, COUNT(*) FROM surface_items WHERE account_id='musify:default' GROUP BY surface"
        ).fetchall())
    assert surfaces == {
        "followed_artists": 1, "liked_playlists": 1, "liked_songs": 1,
        "owned_playlists": 1, "recently_played": 1,
    }


def test_imported_musify_playlist_can_feed_a_cross_service_transfer(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    MusifyAdapter(db).import_backup(musify_backup())
    source = MusifyCanonicalTarget(db)
    playlist = next(item for item in source.browse_playlists() if item["name"] == "Road")

    class Destination:
        source = "ytmusic"
        added = []

        @staticmethod
        def playlist_tracks(_playlist):
            return []

        @staticmethod
        def track_id(track):
            return track.get("videoId")

        @staticmethod
        def resolve(track, _cache):
            return "yt-resolved-1", "search"

        def add(self, _playlist, target_ids):
            self.added.extend(target_ids)

    destination = Destination()
    result = transfer(source, destination, playlist, {"id": "destination"}, {}, execute=True, max_adds=100)
    assert result["added"] == 1
    assert destination.added == ["yt-resolved-1"]


def test_playlist_positions_keep_the_exact_youtube_ids_when_metadata_matches(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    twins = [
        {"ytid": "video-copy-a", "title": "Same", "artist": "Artist", "duration": 180},
        {"ytid": "video-copy-b", "title": "Same", "artist": "Artist", "duration": 180},
    ]
    MusifyAdapter(db).import_backup(musify_backup(20, twins))
    target = MusifyCanonicalTarget(db)
    road = next(item for item in target.browse_playlists() if item["name"] == "Road")
    assert [item["provider_track_id"] for item in target.playlist_tracks(road)] == ["video-copy-a", "video-copy-b"]


def test_musify_upload_accepts_user_hive_and_zip_and_exposes_transfer_source(tmp_path):
    app = create_app(settings=SettingsStore(dir=tmp_path))
    with TestClient(app) as client:
        response = client.post("/api/musify/backup", files={
            "backup": ("user.hive", musify_backup(), "application/octet-stream"),
        })
        assert response.status_code == 200
        assert response.json()["likedSongs"] == 1
        assert [item["name"] for item in client.get("/api/playlists?provider=musify").json()] == ["Flight", "Road"]
        musify = next(item for item in client.get("/api/accounts").json() if item["id"] == "musify")
        assert musify["state"] == "connected"
        assert musify["capabilities"]["playlist_read"] is True

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("Musify/user.hive", musify_backup(40))
            output.writestr("Musify/settings.hive", b"private settings are not imported")
        zipped = client.post("/api/musify/backup", files={
            "backup": ("musify.zip", archive.getvalue(), "application/zip"),
        })
        assert zipped.status_code == 200
        assert client.get("/api/recaps?year=2026&month=7").json()["listened_ms"] == 40 * 60_000

        road = next(item for item in client.get("/api/playlists?provider=musify").json() if item["name"] == "Road")
        versions = client.get(f"/api/playlist-versions?provider=musify&playlist_id={road['id']}").json()
        assert len(versions) == 1
        preview = client.post("/api/playlist-versions/restore", json={
            "provider": "musify", "playlist_id": road["id"],
            "captured_at": versions[0]["version_id"], "execute": False, "max_removals": 100,
        })
        assert preview.status_code == 200
        assert preview.json()["target_count"] == 1


def test_settings_hive_is_rejected_with_actionable_message(tmp_path):
    with TestClient(create_app(settings=SettingsStore(dir=tmp_path))) as client:
        response = client.post("/api/musify/backup", files={
            "backup": ("settings.hive", _frame("themeMode", "dark"), "application/octet-stream"),
        })
    assert response.status_code == 400
    assert "user.hive" in response.json()["detail"]
