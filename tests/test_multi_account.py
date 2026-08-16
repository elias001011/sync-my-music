"""Multi-account live profiles: isolation, migration and account_id plumbing.

Covers the roadmap's hard requirement: several live accounts of the SAME
provider must run side by side without sharing credentials, cookie/token
files or caches, while legacy single-account configs keep working through the
migrated `{provider}:default` accounts.
"""

import json
import struct
import zlib

from songmirror.engine.config import account_state_key, parse_args
from songmirror.engine.targets import build_account_target
from songmirror.services.settings import SettingsStore
from songmirror.services.syncs import SyncJob, SyncStore


def _opts(provider="spotify", account="spotify:work"):
    opts = parse_args([])
    opts.accounts = {account: provider}
    opts.account_configs = {account: {}}
    return opts


def test_account_state_key_keeps_legacy_namespace_for_default():
    assert account_state_key("spotify:default") == "spotify"
    assert account_state_key("spotify") == "spotify"
    assert account_state_key("spotify:work") == "spotify:work"
    assert account_state_key("spotify:work") != account_state_key("spotify:personal")


def test_two_accounts_of_same_provider_get_isolated_state_and_caches(monkeypatch):
    # Two live Spotify accounts built for the same pass must never share the
    # archive namespace or the resolution cache file.
    from songmirror.engine import spotify as spotify_engine
    from songmirror.engine import targets

    class _Client:
        pass

    monkeypatch.setattr(targets, "_spotify_cookie_ready", lambda account=None, config=None: False)
    monkeypatch.setattr(spotify_engine, "client", lambda writable=False, config=None: _Client())
    monkeypatch.setenv("SPOTIFY_WRITE_BACKEND", "oauth")

    work = build_account_target("spotify:work", _opts(account="spotify:work"))
    personal = build_account_target("spotify:personal", _opts(account="spotify:personal"))
    assert work is not None and personal is not None
    assert work.state_key == "spotify:work"
    assert personal.state_key == "spotify:personal"
    assert work.state_key != personal.state_key
    assert work.cache_file != personal.cache_file
    assert work.cache_file.endswith("_resolve_cache.json") and personal.cache_file.endswith("_resolve_cache.json")
    assert work.cache_file != personal.cache_file


def test_unconfigured_optional_account_is_skipped(monkeypatch):
    """An optional provider builder may return None when it has no credentials."""
    from songmirror.engine import targets

    monkeypatch.setattr(targets, "_rest_provider", lambda *args, **kwargs: None)
    assert build_account_target("tidal:default", _opts("tidal", "tidal:default")) is None


def test_spotify_cookie_files_isolated_per_account(tmp_path, monkeypatch):
    from songmirror.engine import spotify_cookie as sc

    monkeypatch.setenv("SONGMIRROR_DATA_DIR", str(tmp_path))
    default = sc.sp_dc_path()
    work = sc.sp_dc_path("spotify:work")
    assert default != work
    assert default.endswith("spotify_sp_dc.private")
    assert "spotify_sp_dc.spotify-" in work

    # Writing one account's cookie never touches the other account's file.
    with open(work, "w") as f:
        f.write("cookie-work")
    with open(default, "w") as f:
        f.write("cookie-default")
    assert sc._sp_dc(account_id="spotify:work") == "cookie-work"
    assert sc._sp_dc(account_id="spotify:default") == "cookie-default"
    assert sc._sp_dc() == "cookie-default"
    assert sc.configured("spotify:work") and sc.configured("spotify:default")
    assert not sc.configured("spotify:personal")


def test_syncjob_legacy_providers_migrate_to_default_accounts(tmp_path):
    store = SyncStore(dir=tmp_path)
    store.upsert(SyncJob(name="Legacy", providers="spotify,apple", source="spotify"))
    job = store.list()[0]
    # Old ids like "spotify" keep pointing at the migrated default account.
    assert job.account_list == ["spotify:default", "apple:default"]
    assert job.source == "spotify"  # provider-level source stays compatible
    # Explicit accounts are kept verbatim.
    store.upsert(SyncJob(name="Two Spotifys", providers="spotify",
                         accounts="spotify:work,spotify:personal"))
    assert store.list()[-1].account_list == ["spotify:work", "spotify:personal"]


def test_account_config_snapshot_isolates_files_and_keeps_legacy_fallback(tmp_path):
    store = SettingsStore(dir=tmp_path)
    store.save({"SPOTIFY_CLIENT_ID": "legacy-cid"})
    default = store.account_config_snapshot("spotify:default")
    assert default["SPOTIFY_CLIENT_ID"] == "legacy-cid"  # migration fallback

    store.save_account("spotify:work", config={"SPOTIFY_CLIENT_ID": "work-cid"})
    work = store.account_config_snapshot("spotify:work")
    assert work["SPOTIFY_CLIENT_ID"] == "work-cid"
    # A named account gets its own token/cookie file paths (never the default's).
    assert "spotify-" in work["SPOTIFY_TOKEN_CACHE"]
    # And never inherits the default account's flat credentials.
    assert store.account_config("spotify:work", "SPOTIFY_CLIENT_SECRET") is None


def test_account_engine_config_never_falls_back_to_default_environment(monkeypatch):
    from songmirror.engine.config import from_config, spotify_write_backend
    from songmirror.oauth import token_path

    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "default-cid")
    monkeypatch.setenv("SPOTIFY_WRITE_BACKEND", "cookie")
    assert from_config({}, "SPOTIFY_CLIENT_ID") is None
    assert spotify_write_backend({}) == "oauth"
    assert token_path("SPOTIFY_TOKEN_CACHE", "named.json", {}) == "named.json"


def test_transfer_slot_classification_separates_live_from_restored(tmp_path):
    from songmirror.services.music_database import MusicDatabase
    from songmirror.services.transfers import TransferService

    db = MusicDatabase(tmp_path / "m.db")
    db.sync_account("spotify", "Live", "connected", "oauth_redirect", account_id="spotify:default")
    db.sync_account("spotify", "Restored", "connected", "official-export", account_id="spotify:restored")
    service = TransferService(SettingsStore(dir=tmp_path), object(), object(), db)

    # A restored/imported slot is a read-only canonical source.
    assert service._is_canonical_slot("spotify:restored")
    assert service._is_canonical_slot("musify:default")
    # A live account (even the default) builds a real engine target instead.
    assert not service._is_canonical_slot("spotify:default")
    assert not service._is_canonical_slot("spotify:work")


# -- Musify Hive wire format (same helpers test_musify uses) ---------------------
def _hive_value(value):
    if value is None:
        return b"\x00"
    if isinstance(value, bool):
        return b"\x03" + bytes([value])
    if isinstance(value, int):
        return b"\x01" + struct.pack("<d", value)
    if isinstance(value, str):
        raw = value.encode()
        return b"\x04" + struct.pack("<I", len(raw)) + raw
    if isinstance(value, list):
        return b"\x0a" + struct.pack("<I", len(value)) + b"".join(_hive_value(item) for item in value)
    if isinstance(value, dict):
        body = b"".join(_hive_value(key) + _hive_value(item) for key, item in value.items())
        return b"\x0b" + struct.pack("<I", len(value)) + body
    raise TypeError(type(value))


def _hive_frame(key, value):
    key_raw = key.encode()
    body = b"\x01" + bytes([len(key_raw)]) + key_raw + _hive_value(value)
    length = 4 + len(body) + 4
    prefix = struct.pack("<I", length) + body
    return prefix + struct.pack("<I", zlib.crc32(prefix) & 0xFFFFFFFF)


def hive_box(values):
    return b"".join(_hive_frame(key, value) for key, value in values.items())


def test_surface_toggles_skip_disabled_imports(tmp_path):
    from songmirror.services.musify import MusifyAdapter
    from songmirror.services.music_database import MusicDatabase

    db = MusicDatabase(tmp_path / "m.db")
    adapter = MusifyAdapter(db)
    backup = hive_box({
        "likedSongs": [{"ytid": "a", "title": "Song", "artist": "Artist"}],
        "customPlaylists": [{"ytid": "customId-road", "title": "Road", "source": "user-created",
                              "list": [{"ytid": "a", "title": "Song", "artist": "Artist"}]}],
        "playlists": ["pl1"],
        "wrappedListeningStats": {"schemaVersion": 2, "currentMonthKey": "2026-07",
            "currentMonth": {"songs": {"b": {"ytid": "b", "title": "Song", "artist": "Artist",
                                                    "seconds": 180, "playCount": 3}}},
            "history": {}},
    })
    # History disabled -> stats skipped; liked tracks + playlists still imported.
    stats = adapter.import_backup(backup, surfaces={"history": False})
    assert stats["listeningStats"] == 0
    assert stats["likedSongs"] == 1
    assert stats["playlists"] == 1
    assert db.recap(2026, 7)["plays"] == 0
    # Re-enabling history on a later import fills the recap (snapshot semantics).
    stats = adapter.import_backup(backup, surfaces={"history": True})
    assert stats["listeningStats"] == 1
    assert db.recap(2026, 7)["plays"] == 3


def test_import_spotify_export_respects_surface_toggles(tmp_path):
    from songmirror.services.music_database import MusicDatabase
    from songmirror.services.spotify_export import import_spotify_export

    db = MusicDatabase(tmp_path / "m.db")
    export = {
        "playlists": [{"name": "Road", "uri": "spotify:playlist:p1",
                       "items": [{"track": {"trackName": "Song", "artistName": "A",
                                            "trackUri": "spotify:track:t1"}}]}],
        "tracks": [{"track": "Liked", "artist": "A", "album": "Al", "uri": "spotify:track:t2"}],
    }
    raw = json.dumps(export).encode()
    result = import_spotify_export(db, raw, "MyData.json", "Work",
                                   surfaces={"liked_tracks": False, "history": False})
    assert result["playlists"] == 1
    assert result["liked_tracks"] == 0
    with db.connect() as conn:
        rows = conn.execute("SELECT surface FROM surface_items WHERE account_id=?", (result["account_id"],)).fetchall()
    # Playlist mirrors are not surface_items; liked_tracks were skipped entirely.
    assert [row[0] for row in rows] == []
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM collection_mirrors WHERE account_id=?", (result["account_id"],)).fetchone()[0] == 1
