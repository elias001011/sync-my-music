from datetime import datetime, timezone

from songmirror.services.music_database import MusicDatabase
from songmirror.services.sonora import SonoraAdapter


def _month(year, month):
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12 else datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp()), int(end.timestamp())


def test_aggregate_import_replaces_same_month_instead_of_adding(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    start, end = _month(2026, 7)
    row = {
        "period_start": start, "period_end": end, "play_count": 2, "listened_ms": 20 * 60_000,
        "track_metadata": {"track_name": "A Song", "artist_name": "An Artist"},
    }
    db.replace_listening_aggregates("musify", [row])
    row.update(play_count=4, listened_ms=40 * 60_000)
    db.replace_listening_aggregates("musify", [row])

    recap = db.recap(2026, 7)
    assert recap["plays"] == 4
    assert recap["listened_ms"] == 40 * 60_000
    assert recap["services"] == [{"source": "musify", "plays": 4, "listened_ms": 40 * 60_000}]


def test_listenbrainz_events_are_idempotent(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    event = {
        "listened_at": int(datetime(2026, 7, 5, tzinfo=timezone.utc).timestamp()),
        "track_metadata": {
            "track_name": "A Song", "artist_name": "An Artist",
            "additional_info": {"recording_msid": "stable-event-id", "duration_ms": 180_000},
        },
    }
    assert db.import_listens([event], "spotify") == {"inserted": 1, "duplicates": 0}
    assert db.import_listens([event], "spotify") == {"inserted": 0, "duplicates": 1}


def test_playlist_versions_are_bounded_and_restorable(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    track_a = db.upsert_track({"track_name": "A", "artist_name": "Artist"})
    track_b = db.upsert_track({"track_name": "B", "artist_name": "Artist"})
    now = int(datetime.now(timezone.utc).timestamp())
    with db.connect() as conn:
        conn.execute("INSERT INTO collections(id, kind, title, created_at, updated_at) VALUES ('p', 'playlist', 'Test', ?, ?)", (now, now))
        conn.execute("INSERT INTO collection_items VALUES ('p', ?, 1, ?)", (track_a, now))
    db.snapshot_collection("p", limit=2)
    with db.connect() as conn:
        conn.execute("DELETE FROM collection_items WHERE collection_id='p'")
        conn.execute("INSERT INTO collection_items VALUES ('p', ?, 1, ?)", (track_b, now))
    second = db.snapshot_collection("p", limit=2)
    with db.connect() as conn:
        conn.execute("DELETE FROM collection_items WHERE collection_id='p'")
        conn.execute("INSERT INTO collection_items VALUES ('p', ?, 1, ?)", (track_a, now))
    db.snapshot_collection("p", limit=2)
    assert len(db.collection_versions("p")) == 2

    result = db.restore_collection_version(second)
    assert result == {"collection_id": "p", "restored_items": 1}
    with db.connect() as conn:
        assert conn.execute("SELECT track_id FROM collection_items WHERE collection_id='p'").fetchone()[0] == track_b


def test_sonora_backup_v2_import_export(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    adapter = SonoraAdapter(db)
    backup = {
        "version": 2,
        "likedSongs": [{"videoId": "abcdefghijk", "title": "Song", "artist": "Artist",
                         "addedAt": "2026-07-01T00:00:00Z", "duration": 180}],
        "followedArtists": [], "likedAlbums": [], "likedPlaylists": [],
        "playlists": [{"id": 7, "name": "Road", "createdAt": "2026-07-01T00:00:00Z"}],
        "playlistEntries": {"7": [{"videoId": "abcdefghijk", "position": 1,
                                      "title": "Song", "artist": "Artist", "duration": 180}]},
        "history": [{"videoId": "abcdefghijk", "title": "Song", "artist": "Artist",
                     "playedAt": "2026-07-12T12:00:00Z", "playCount": 3, "duration": 180}],
    }
    stats = adapter.import_backup(backup)
    assert stats["likedSongs"] == 1
    assert stats["playlists"] == 1
    assert db.recap(2026, 7)["plays"] == 3
    exported = adapter.export_backup()
    assert exported["version"] == 2
    assert exported["playlists"][0]["name"] == "Road"
    assert exported["playlistEntries"]["1"][0]["videoId"] == "abcdefghijk"
