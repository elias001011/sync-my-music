"""CSV import/export for canonical playlists. Spotify has no native CSV
export, so import must tolerate the header shapes third-party tools (e.g.
Exportify) and hand-made spreadsheets actually use; export must produce a
file that round-trips through both a spreadsheet and back through import."""

import csv
import io

import pytest

from songmirror.services.csv_transfer import (
    account_id_for,
    export_collection_csv,
    import_csv_playlist,
    parse_csv_playlist,
)
from songmirror.services.music_database import MusicDatabase


EXPORTIFY_STYLE = (
    "Spotify URI,Track Name,Artist Name(s),Album Name,Album Artist Name(s),"
    "Duration (ms),Added At,ISRC\n"
    "spotify:track:abc123,Song One,Artist One,Album One,Artist One,210000,"
    "2024-01-01T00:00:00Z,US1234567890\n"
    ",Song Two,Artist Two,,,,\n"  # sparse row: only name/artist present
)


def test_parse_recognizes_exportify_style_headers():
    playlist = parse_csv_playlist(EXPORTIFY_STYLE.encode(), "My Mix")
    assert playlist["name"] == "My Mix"
    assert len(playlist["tracks"]) == 2
    first = playlist["tracks"][0]
    assert first["track_name"] == "Song One"
    assert first["artist_name"] == "Artist One"
    assert first["release_name"] == "Album One"
    assert first["duration_ms"] == 210000
    assert first["isrc"] == "US1234567890"
    assert first["provider_track_id"] == "abc123"
    assert first["uri"] == "spotify:track:abc123"


def test_parse_tolerates_generic_headers():
    raw = "Title,Artist,Album\nHello,World,Greatest Hits\n"
    playlist = parse_csv_playlist(raw.encode(), "Generic")
    assert playlist["tracks"] == [{
        "track_name": "Hello", "artist_name": "World", "release_name": "Greatest Hits",
        "isrc": None, "provider_track_id": "", "uri": "", "duration_ms": None, "added_at": None,
    }]


def test_parse_skips_rows_with_no_name_and_no_artist():
    raw = "Title,Artist\n,\nReal Song,Real Artist\n"
    playlist = parse_csv_playlist(raw.encode(), "Mix")
    assert len(playlist["tracks"]) == 1
    assert playlist["tracks"][0]["track_name"] == "Real Song"


def test_parse_rejects_oversized_file():
    huge = b"a" * (9 * 1024 * 1024)
    with pytest.raises(ValueError):
        parse_csv_playlist(huge, "Mix")


def test_account_id_for_is_stable_and_namespaced():
    a = account_id_for("My Playlists")
    b = account_id_for("My Playlists")
    c = account_id_for("Other")
    assert a == b
    assert a != c
    assert a.startswith("csv:")


def test_import_csv_playlist_roundtrips_into_canonical_db(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    result = import_csv_playlist(db, EXPORTIFY_STYLE.encode(), "Imported Mix", "My CSV")
    assert result["tracks"] == 2
    assert result["playlists"] == 1
    assert result["account_id"].startswith("csv:")

    collections = db.collections()
    assert len(collections) == 1
    assert collections[0]["title"] == "Imported Mix"
    assert collections[0]["track_count"] == 2


def test_export_collection_csv_produces_readable_file(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    import_csv_playlist(db, EXPORTIFY_STYLE.encode(), "Exported Mix", "My CSV")
    collection_id = db.collections()[0]["id"]

    filename, payload = export_collection_csv(db, collection_id)
    assert filename.endswith(".csv")
    text = payload.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["Track Name", "Artist Name", "Album Name", "Duration (ms)", "ISRC", "Track URI", "Added At"]
    assert rows[1][0] == "Song One"
    assert rows[1][1] == "Artist One"
    assert len(rows) == 3  # header + 2 tracks


def test_export_unknown_collection_raises(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    with pytest.raises(ValueError):
        export_collection_csv(db, "does-not-exist")


def test_import_then_export_round_trip_preserves_track_names(tmp_path):
    db = MusicDatabase(tmp_path / "music.db")
    import_csv_playlist(db, EXPORTIFY_STYLE.encode(), "Round Trip", "My CSV")
    collection_id = db.collections()[0]["id"]
    _filename, payload = export_collection_csv(db, collection_id)

    reimported = parse_csv_playlist(payload, "Round Trip Again")
    names = {t["track_name"] for t in reimported["tracks"]}
    assert names == {"Song One", "Song Two"}
