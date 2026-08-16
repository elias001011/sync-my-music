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
    assert recap["services"] == [{"source": "musify", "account_id": "musify:default",
                                   "account_label": "Musify", "plays": 4,
                                   "listened_ms": 40 * 60_000}]


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


def test_month_history_and_configurable_calendar_year_retention(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    track = db.upsert_track({"track_name": "A Song", "artist_name": "An Artist", "duration_ms": 180_000})
    account = db.sync_account("spotify", "Spotify", "connected")
    with db.connect() as conn:
        for year, month, plays in [(2023, 12, 1), (2024, 2, 2), (2026, 7, 3)]:
            for day in range(1, plays + 1):
                listened_at = int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())
                conn.execute(
                    """INSERT INTO listens(id, track_id, account_id, listened_at, listened_ms, source,
                       source_event_id, metadata, imported_at) VALUES (?, ?, ?, ?, ?, 'spotify', ?, '{}', ?)""",
                    (f"{year}-{month}-{day}", track, account["id"], listened_at, 180_000,
                     f"event-{year}-{month}-{day}", listened_at),
                )

    result = db.prune_listening_history(3, datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert result["cutoff_year"] == 2024
    assert result["deleted_listens"] == 1
    assert db.library()["total"] == 1  # retention never removes canonical tracks

    history = db.recap_history(3, datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert history["cutoff_year"] == 2024
    assert [(item["year"], item["month"], item["plays"]) for item in history["months"]] == [
        (2026, 7, 3), (2024, 2, 2),
    ]


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


def _listen(db, account_id, source, year, month, day, track_name="A Song"):
    """Insert one direct listen row for an existing account/track."""
    track = db.upsert_track({"track_name": track_name, "artist_name": "An Artist", "duration_ms": 180_000})
    listened_at = int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO listens(id, track_id, account_id, listened_at, listened_ms, source,
               source_event_id, metadata, imported_at) VALUES (?, ?, ?, ?, 180_000, ?, ?, '{}', ?)""",
            (f"{account_id}-{year}-{month}-{day}-{track_name}", track, account_id, listened_at,
             source, f"event-{account_id}-{year}-{month}-{day}-{track_name}", listened_at),
        )
    return track


def test_recap_filtered_by_account(tmp_path):
    """Selecting one account's recap excludes the other account's totals and
    events/snapshots are never double counted."""
    db = MusicDatabase(tmp_path / "music.db")
    spotify = db.sync_account("spotify", "Spotify Work", "connected")["id"]
    musify = db.sync_account("musify", "Musify", "connected", account_id="musify:default")["id"]
    _listen(db, spotify, "spotify", 2026, 7, 3, "Work Track")
    _listen(db, musify, "musify", 2026, 7, 4, "Musify Track")
    start, end = _month(2026, 7)
    db.replace_listening_aggregates("musify", [{
        "period_start": start, "period_end": end, "play_count": 2, "listened_ms": 20 * 60_000,
        "track_metadata": {"track_name": "Musify Track", "artist_name": "An Artist"},
    }], account_id=musify)

    unified = db.recap(2026, 7)
    assert unified["plays"] == 4  # 1 event + 1 event + 2 snapshot — no double counting
    only_spotify = db.recap(2026, 7, account_ids=[spotify])
    assert only_spotify["plays"] == 1
    only_musify = db.recap(2026, 7, account_ids=[musify])
    assert only_musify["plays"] == 3  # 1 event + 2 snapshot
    both = db.recap(2026, 7, account_ids=[spotify, musify])
    assert both["plays"] == unified["plays"]
    # The services breakdown names the contributing account, not just the source.
    assert {(row["account_id"], row["source"], row["plays"]) for row in unified["services"]} == {
        (spotify, "spotify", 1), (musify, "musify", 3),
    }
    labels = {row["account_id"]: row["account_label"] for row in unified["services"]}
    assert labels[spotify] == "Spotify Work"


def test_recap_history_filtered_by_account(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    spotify = db.sync_account("spotify", "Spotify Work", "connected")["id"]
    musify = db.sync_account("musify", "Musify", "connected", account_id="musify:default")["id"]
    _listen(db, spotify, "spotify", 2026, 7, 3, "Work Track")
    _listen(db, musify, "musify", 2026, 7, 4, "Musify Track")

    history = db.recap_history(3, datetime(2026, 8, 1, tzinfo=timezone.utc))
    july = next(item for item in history["months"] if (item["year"], item["month"]) == (2026, 7))
    assert july["plays"] == 2
    filtered = db.recap_history(3, datetime(2026, 8, 1, tzinfo=timezone.utc), account_ids=[spotify])
    july_filtered = next(item for item in filtered["months"] if (item["year"], item["month"]) == (2026, 7))
    assert july_filtered["plays"] == 1


def test_rename_and_delete_account_keeps_entities_shared_with_other_accounts(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    first = db.sync_account("spotify", "Work", "connected")["id"]
    second = db.sync_account("spotify", "Personal", "connected", account_id="spotify:personal")["id"]
    # Both accounts reference the same canonical track (shared entity).
    db.import_provider_library("spotify", first, "Work", liked_tracks=[{
        "track_name": "Shared Song", "artist_name": "Artist", "provider_track_id": "t1"}])
    db.import_provider_library("spotify", second, "Personal", liked_tracks=[{
        "track_name": "Shared Song", "artist_name": "Artist", "provider_track_id": "t2"},
        {"track_name": "Only Personal", "artist_name": "Artist", "provider_track_id": "t3"}])

    # Rename keeps the stable id.
    db.rename_account(first, "Work Renamed")
    assert next(row for row in db.accounts() if row["id"] == first)["label"] == "Work Renamed"

    before = db.library()
    assert before["total"] == 2
    db.delete_account(first)
    after = db.library()
    # 'Shared Song' survives because the personal account still references it.
    assert after["total"] == 2
    assert any(item["title"] == "Shared Song" for item in after["items"])
    db.delete_account(second)
    # With both gone, every orphan (shared or not) is garbage collected.
    assert db.library()["total"] == 0
    assert db.accounts() == []


def test_delete_account_removes_only_its_own_listens_and_collections(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    spotify = db.sync_account("spotify", "Spotify", "connected")["id"]
    musify = db.sync_account("musify", "Musify", "connected", account_id="musify:default")["id"]
    _listen(db, spotify, "spotify", 2026, 7, 3, "Spotify Song")
    _listen(db, musify, "musify", 2026, 7, 4, "Musify Song")
    db.import_provider_library("spotify", spotify, "Spotify",
                               playlists=[{"name": "Road", "provider_id": "pl1",
                                           "tracks": [{"track_name": "Spotify Song", "artist_name": "An Artist"}]}])

    db.delete_account(spotify)
    remaining = db.accounts()
    assert [row["id"] for row in remaining] == [musify]
    # The Spotify listen and its playlist are gone; Musify's listen survives.
    assert db.recap(2026, 7)["plays"] == 1
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM collections").fetchone()[0] == 0


def test_delete_account_requires_explicit_confirmation_at_api_layer():
    from fastapi import HTTPException
    import pytest

    from songmirror.web.routers import library

    class _App:
        state = type("State", (), {"music_db": None})()

    request = type("Request", (), {"app": _App})()
    with pytest.raises(HTTPException) as exc:
        library.delete_library_account("spotify:default", request, {})
    assert exc.value.status_code == 400


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


def test_sonora_remove_device_forgets_stale_pairing(tmp_path):
    """A restored/reinstalled Sonora app gets a fresh device id — the old
    pairing row would fail every sync forever. The DELETE endpoint must forget
    the record (canonical library untouched)."""
    from fastapi.testclient import TestClient
    from songmirror.services.settings import SettingsStore
    from songmirror.web import create_app

    db = MusicDatabase(tmp_path / "music.db")
    with TestClient(create_app(settings=SettingsStore(dir=tmp_path), music_db=db)) as client:
        sonora = client.app.state.sonora
        sonora.save_device("dev-stale", "Sonora (Android)", "192.168.2.31", 46473, paired=True)
        assert sonora.paired("dev-stale")
        assert client.delete("/api/sonora/devices/dev-stale").json()["ok"] is True
        assert not sonora.paired("dev-stale")
        assert client.delete("/api/sonora/devices/dev-stale").status_code == 404


def test_sonora_export_aggregates_the_whole_canonical_library(tmp_path):
    """The push to a Sonora device carries the HUB's library — playlists and
    tracks imported under OTHER accounts (Musify hive, live YT import) — not
    just the sonora:default slot. Before the fix the export was scoped to the
    adapter's own account, so a fresh sync pushed an EMPTY library and the
    Sonora app stayed empty."""
    db = MusicDatabase(tmp_path / "music.db")
    # Another account's library (e.g. a Musify hive or a live YT import).
    db.import_provider_library(
        "musify", "musify:default", "Musify",
        playlists=[{"provider_id": "p1", "name": "Road",
                    "tracks": [{"provider_track_id": "vid1234567890a", "track_name": "Song",
                                 "artist_name": "Artist", "duration_ms": 180000}]}])
    adapter = SonoraAdapter(db)  # sonora:default owns nothing itself
    exported = adapter.export_backup()
    assert [p["name"] for p in exported["playlists"]] == ["Road"]
    assert exported["playlistEntries"]["1"][0]["videoId"] == "vid1234567890a"
