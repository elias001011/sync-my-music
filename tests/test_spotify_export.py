import io
import json
import zipfile

from songmirror.services.music_database import MusicDatabase
from songmirror.services.spotify_export import import_spotify_export


def _official_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Spotify Account Data/StreamingHistory_music_0.json", json.dumps([
            {"endTime": "2026-07-01 12:00", "artistName": "Artist", "trackName": "Song", "msPlayed": 3000},
        ]))
        archive.writestr("Spotify Extended Streaming History/endsong_0.json", json.dumps([
            {"ts": "2026-07-02T12:00:00Z", "master_metadata_album_artist_name": "Artist",
             "master_metadata_track_name": "Other", "master_metadata_album_album_name": "Album",
             "spotify_track_uri": "spotify:track:t2", "ms_played": 180000},
        ]))
        archive.writestr("Playlist1.json", json.dumps({"playlists": [{
            "name": "Road", "uri": "spotify:playlist:p1", "items": [
                {"track": {"trackName": "Song", "artistName": "Artist", "trackUri": "spotify:track:t1"}},
            ],
        }]}))
        archive.writestr("YourLibrary.json", json.dumps({
            "tracks": [{"track": "Liked", "artist": "Singer", "album": "Record", "uri": "spotify:track:t3"}],
            "albums": ["Record"], "artists": ["Singer"],
        }))
    return buffer.getvalue()


def test_spotify_export_is_idempotent_and_account_scoped(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    raw = _official_zip()
    first = import_spotify_export(db, raw, "my-data.zip", "Personal")
    second = import_spotify_export(db, raw, "my-data.zip", "Personal")
    other = import_spotify_export(db, raw, "my-data.zip", "Work")

    assert first["listens_inserted"] == 2
    assert second["listens_inserted"] == 0
    assert second["listens_duplicates"] == 2
    assert other["account_id"] != first["account_id"]
    # A 3-second play remains 3,000ms; it must never be interpreted as seconds.
    with db.connect() as conn:
        durations = [row[0] for row in conn.execute(
            "SELECT listened_ms FROM listens WHERE account_id=? ORDER BY listened_ms", (first["account_id"],))]
    assert durations == [3000, 180000]
    assert db.summary()["accounts"] == 2


def test_account_backup_roundtrip_preserves_one_provider_slot(tmp_path):
    db = MusicDatabase(tmp_path / "source.db")
    raw = _official_zip()
    imported = import_spotify_export(db, raw, "my-data.zip", "Personal")
    backup = db.export_account_backup(imported["account_id"])

    restored = MusicDatabase(tmp_path / "restored.db")
    result = restored.restore_account_backup(backup)
    assert result["account_id"] == imported["account_id"]
    assert result["playlists"] == 1
    assert result["listens"] == 2
    assert restored.recap(2026, 7)["plays"] == 2
