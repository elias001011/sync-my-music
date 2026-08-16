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


def test_create_account_id_generates_unique_named_profiles(tmp_path):
    store = SettingsStore(dir=tmp_path)
    assert store.create_account_id("spotify", "Work") == "spotify:work"
    store.save_account("spotify:work", label="Work")
    # Collisions get a numeric suffix; `default` is never reused as a name.
    assert store.create_account_id("spotify", "Work") == "spotify:work-2"
    assert store.create_account_id("spotify", "default") == "spotify:account"
    assert store.create_account_id("tidal", "Conta Pessoal") == "tidal:conta-pessoal"


def test_connector_named_account_never_reads_default_credentials(tmp_path, monkeypatch):
    """A named profile's connector reads/writes ONLY its own registry namespace:
    it can't inherit the default's flat keys or env, and saving never touches
    the default's config."""
    from songmirror.services.accounts.spotify import SpotifyConnector

    store = SettingsStore(dir=tmp_path)
    store.save({"SPOTIFY_CLIENT_ID": "default-cid", "SPOTIFY_CLIENT_SECRET": "default-secret"})
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "env-cid")

    named = SpotifyConnector(store, account_id="spotify:work")
    assert named._get("SPOTIFY_CLIENT_ID") is None  # never inherits default/env
    named._save({"SPOTIFY_CLIENT_ID": "work-cid"})
    # Wrote only the named account's namespace.
    assert store.account_config("spotify:work", "SPOTIFY_CLIENT_ID") == "work-cid"
    assert store.get("SPOTIFY_CLIENT_ID") == "default-cid"  # flat keys untouched
    assert store.account_config("spotify:default", "SPOTIFY_CLIENT_ID") == "default-cid"

    default = SpotifyConnector(store, account_id="spotify:default")
    assert default._get("SPOTIFY_CLIENT_ID") == "default-cid"  # migration fallback
    default._save({"SPOTIFY_CLIENT_SECRET": "new-secret"})
    # The default account also mirrors flat keys so legacy env consumers work.
    assert store.get("SPOTIFY_CLIENT_SECRET") == "new-secret"


def test_connector_named_account_gets_own_token_file(tmp_path):
    from songmirror.services.accounts.spotify import SpotifyConnector

    store = SettingsStore(dir=tmp_path)
    named = SpotifyConnector(store, account_id="spotify:work")
    default = SpotifyConnector(store, account_id="spotify:default")
    assert named._token_cache() != default._token_cache()
    assert "spotify-" in named._token_cache()


def test_normalize_accounts_handles_provider_and_account_ids():
    from songmirror.web.routers.syncs import _normalize_accounts

    # Legacy providers -> their :default accounts.
    assert _normalize_accounts({"providers": "spotify,apple"})["accounts"] == \
        "spotify:default,apple:default"
    # Already-account ids (multi-account UI) pass through verbatim.
    assert _normalize_accounts({"providers": "spotify:pessoal,ytmusic:default"})["accounts"] == \
        "spotify:pessoal,ytmusic:default"
    # Mixed forms normalize each entry independently.
    assert _normalize_accounts({"providers": "spotify:pessoal,apple"})["accounts"] == \
        "spotify:pessoal,apple:default"
    # Explicit accounts always win.
    assert _normalize_accounts({"providers": "spotify", "accounts": "spotify:work"})["accounts"] == \
        "spotify:work"


def test_playlist_service_browse_live_account_is_never_canonical(tmp_path):
    """A live named account that ALSO has a service_accounts row must browse the
    real provider target — not stale canonical rows. Only restored/imported
    snapshots (auth_mode marks them) read from the canonical database."""
    from songmirror.services.music_database import MusicDatabase
    from songmirror.services.playlists import PlaylistService

    db = MusicDatabase(tmp_path / "m.db")
    # A live account synced into service_accounts (as list_accounts does).
    db.sync_account("spotify", "Live Work", "connected", "oauth_redirect", account_id="spotify:work")
    # A restored snapshot with canonical rows.
    db.import_provider_library(
        "spotify", "spotify:restored", "Restored",
        playlists=[{"provider_id": "pl1", "name": "Road",
                    "tracks": [{"provider_track_id": "t1", "track_name": "Song",
                                 "artist_name": "A", "duration_ms": 1000}]}])
    service = PlaylistService(SettingsStore(dir=tmp_path), db)

    # Live account with no credentials: builds a real (unconfigured) target and
    # returns [] — never the canonical rows.
    assert service.browse("spotify:work") == []
    # The restored snapshot still reads its canonical playlists.
    rows = service.browse("spotify:restored")
    assert [r["name"] for r in rows] == ["Road"]


def test_nway_reconcile_keeps_two_accounts_of_one_provider_separate(tmp_path):
    """Reconcile keys playlists by state_key: two Spotify accounts participating
    in one N-way pass each get their OWN playlist + baseline, never colliding on
    the shared `source` string."""
    from songmirror.engine import archive
    from songmirror.engine.targets.base import reconcile

    class _Peer:
        def __init__(self, state_key, isrcs):
            self.state_key = state_key
            self.source = "spotify"  # same provider for both accounts
            self.tag = self.name = state_key
            self._isrcs = list(isrcs)

        def playlist_tracks(self, pl):
            return [{"id": f"{self.state_key}-{i}", "name": f"Song {i}", "artists": ["A"],
                     "artist": "A", "duration_ms": 1000, "isrc": i, "added_at": "2020"}
                    for i in self._isrcs]

        def track_id(self, t):
            return t.get("id")

        def prefetch(self, norms, cache):
            pass

        def native_isrc_map(self, cache):
            return {}

        def resolve(self, norm, cache):
            return f"{self.state_key}-{norm['isrc']}", "search"

        def add(self, pl, ids):
            for tid in ids:
                isrc = tid.rsplit("-", 1)[1]
                if isrc not in self._isrcs:
                    self._isrcs.append(isrc)

        def remove(self, pl, raw):
            if raw["isrc"] in self._isrcs:
                self._isrcs.remove(raw["isrc"])

    conn = archive.connect(str(tmp_path / "s.db"))
    work = _Peer("spotify:work", ["A", "B"])
    personal = _Peer("spotify:personal", ["A"])
    # Each account's playlist lives under ITS state_key — the same source string
    # must never cause a collision or a KeyError.
    playlists = {"spotify:work": {"id": "s1"}, "spotify:personal": {"id": "s2"}}
    caches = {p.state_key: {"isrc": {}, "search": {}, "dirty": False} for p in (work, personal)}
    stats = reconcile([work, personal], "Mix", playlists, caches, conn,
                      execute=True, max_removals=25, max_adds=200)
    # Work's B propagated to personal (its own playlist, its own baseline).
    assert stats["added"] >= 1
    assert "B" in personal._isrcs
    # Baselines are recorded under EACH account's own state key — no
    # cross-account contamination on the shared source string.
    assert archive.get_playlist_state(conn, "mix", "spotify:work") == {"i:A", "i:B"}
    assert archive.get_playlist_state(conn, "mix", "spotify:personal") == {"i:A"}
    assert archive.get_playlist_state(conn, "mix", "spotify") == set()
    conn.close()


def test_web_account_crud_named_profiles(tmp_path):
    """The accounts API: create a named profile, list it as a live account,
    rename it, and remove it only with explicit confirmation."""
    from fastapi.testclient import TestClient
    from songmirror.web import create_app

    with TestClient(create_app(settings=SettingsStore(dir=tmp_path))) as client:
        created = client.post("/api/accounts/spotify/accounts", json={"label": "Work"}).json()
        assert created["account_id"] == "spotify:work"

        accounts = client.get("/api/accounts").json()
        work = next(a for a in accounts if a["id"] == "spotify:work")
        assert work["live"] is True and work["name"] == "Work"
        assert work["state"] == "unconfigured"
        assert not work.get("local_snapshot")

        # Per-account config save lands in the named namespace only.
        client.post("/api/accounts/spotify:work/config", json={"SPOTIFY_CLIENT_ID": "work-cid"})
        store = client.app.state.settings
        assert store.account_config("spotify:work", "SPOTIFY_CLIENT_ID") == "work-cid"
        assert store.get("SPOTIFY_CLIENT_ID") is None  # default untouched

        # Rename via prefs.
        renamed = client.put("/api/accounts/spotify:work/prefs", json={"label": "Work 2"}).json()
        assert renamed["name"] == "Work 2"

        # Remove requires explicit confirmation.
        assert client.delete("/api/accounts/spotify:work/remove").status_code == 400
        removed = client.delete("/api/accounts/spotify:work/remove", params={"confirm": True}).json()
        assert removed["removed"] is True
        assert all(a["id"] != "spotify:work" for a in client.get("/api/accounts").json())
        # The default account can't be removed.
        assert client.delete("/api/accounts/spotify/remove", params={"confirm": True}).status_code == 400


def test_oauth_named_account_connect_keeps_full_account_id_in_callback(tmp_path):
    """connect() for a named profile builds the callback path with the FULL
    account id (`/oauth/spotify:work/callback`) so the browser handshake
    resolves back to that account; the default keeps the legacy bare-provider
    path (`/oauth/spotify/callback`) and its credentials are never touched."""
    from fastapi.testclient import TestClient
    from songmirror.web import create_app

    store = SettingsStore(dir=tmp_path)
    store.save({"SPOTIFY_CLIENT_ID": "default-cid", "SPOTIFY_CLIENT_SECRET": "default-secret"})
    with TestClient(create_app(settings=store)) as client:
        client.post("/api/accounts/spotify/accounts", json={"label": "Work"})
        client.post("/api/accounts/spotify:work/config",
                    json={"SPOTIFY_CLIENT_ID": "work-cid", "SPOTIFY_CLIENT_SECRET": "work-secret"})

        r = client.post("/api/accounts/spotify:work/connect")
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "redirect"
        assert body["redirect_uri"].endswith("/oauth/spotify:work/callback")
        # The authorize URL embeds the same account-scoped callback (URL-encoded).
        assert "spotify%3Awork%2Fcallback" in body["url"]

        # The default account keeps the legacy callback path.
        r2 = client.post("/api/accounts/spotify/connect")
        assert r2.status_code == 200
        assert r2.json()["redirect_uri"].endswith("/oauth/spotify/callback")
        assert "spotify%2Fcallback" in r2.json()["url"]

        # Nothing about the named flow leaked into the default account.
        assert store.account_config("spotify:default", "SPOTIFY_CLIENT_ID") == "default-cid"
        assert store.get("SPOTIFY_CLIENT_ID") == "default-cid"
        assert store.account_config("spotify:work", "SPOTIFY_CLIENT_ID") == "work-cid"


def test_oauth_callback_instantiates_named_account_connector(tmp_path, monkeypatch):
    """The callback route must construct the connector for the exact account
    named in the path (`spotify:work`), never the default."""
    from fastapi.testclient import TestClient
    from songmirror.services.accounts import ConnStatus
    from songmirror.web import create_app
    import songmirror.web.routers.accounts as accounts_router

    captured = {}

    class FakeSpotify:
        name = "Spotify"
        auth_kind = "oauth_redirect"

        def __init__(self, settings, account_id=None):
            captured["account_id"] = account_id

        def complete_redirect(self, params):
            captured["params"] = params
            return ConnStatus("connected", "authorized")

    monkeypatch.setitem(accounts_router.CONNECTORS, "spotify", FakeSpotify)

    with TestClient(create_app(settings=SettingsStore(dir=tmp_path))) as client:
        r = client.get("/oauth/spotify:work/callback?code=abc123")
        assert r.status_code == 200
        assert captured["account_id"] == "spotify:work"
        assert "spotify:work" in captured["params"]["url"]
        # The legacy bare path still resolves to the default account.
        r2 = client.get("/oauth/spotify/callback?code=abc123")
        assert r2.status_code == 200
        assert captured["account_id"] == "spotify:default"


def test_sync_job_normalizes_bare_ids_and_source(tmp_path):
    """A payload mixing bare providers and account ids persists fully
    normalized: source becomes `spotify:default`, accounts become
    `spotify:default,spotify:work`. And `_opts_for()` loads the real
    `spotify:default` snapshot (legacy flat keys), never an empty config."""
    from fastapi.testclient import TestClient
    from songmirror.services.sync_service import SyncService
    from songmirror.web import create_app

    store = SettingsStore(dir=tmp_path)
    store.save({"SPOTIFY_CLIENT_ID": "default-cid"})
    store.save_account("spotify:work", label="Work", config={"SPOTIFY_CLIENT_ID": "work-cid"})

    class _Bus:
        def publish(self, *a, **k):
            pass

    with TestClient(create_app(settings=store)) as client:
        r = client.post("/api/syncs", json={
            "name": "Two Spotify",
            "source": "spotify",
            "providers": "spotify,spotify:work",
            "accounts": "spotify,spotify:work",
        })
        assert r.status_code == 200
        job = r.json()
        assert job["source"] == "spotify:default"
        assert job["accounts"] == "spotify:default,spotify:work"

        svc = SyncService(store, _Bus(), client.app.state.syncs)
        opts = svc._opts_for(SyncJob(**{k: v for k, v in job.items() if k != "id"}), execute=False)
        assert opts.account_configs["spotify:default"]["SPOTIFY_CLIENT_ID"] == "default-cid"
        assert opts.account_configs["spotify:work"]["SPOTIFY_CLIENT_ID"] == "work-cid"
        assert opts.accounts == {"spotify:default": "spotify", "spotify:work": "spotify"}


def test_build_targets_source_default_allows_named_same_provider_target(monkeypatch):
    """With the normalized source `spotify:default`, a named `spotify:work`
    account is a valid DESTINATION (the whole point of multi-account); a
    legacy bare source skips only that provider's `:default` account."""
    from songmirror.engine import targets

    class Fake:
        def __init__(self, account, src):
            self.state_key = account
            self.tag = self.name = src.title()
            self.source = src

    def _builder(src):
        def build(opts, sp, sync_peer=False, songs=None, account=None):
            return Fake(account or f"{src}:default", src)
        return build

    monkeypatch.setitem(targets._REGISTRY, "spotify", _builder("spotify"))
    monkeypatch.setitem(targets._REGISTRY, "ytmusic", _builder("ytmusic"))

    opts = parse_args([])
    opts.accounts = {"spotify:default": "spotify", "spotify:work": "spotify",
                     "ytmusic:default": "ytmusic"}

    # Normalized source: only spotify:default excluded.
    opts.sync_source = "spotify:default"
    out = targets.build_targets(opts)
    assert sorted(t.state_key for t in out) == ["spotify:work", "ytmusic:default"]

    # Legacy bare source: also only the provider's :default account excluded —
    # spotify:work stays a target (defensive compat).
    opts.sync_source = "spotify"
    out = targets.build_targets(opts)
    assert sorted(t.state_key for t in out) == ["spotify:work", "ytmusic:default"]


def test_oauth_callback_unknown_provider_answers_404_not_500(tmp_path):
    """The OAuth callback page is reachable without auth — an unknown provider
    id in the URL must never crash the app with a 500."""
    from fastapi.testclient import TestClient
    from songmirror.web import create_app

    with TestClient(create_app(settings=SettingsStore(dir=tmp_path))) as client:
        assert client.get("/oauth/evil/callback?code=x").status_code == 404
        assert client.get("/oauth/spotify/callback?code=x").status_code == 200  # known provider still renders


def test_prefs_rejects_empty_label(tmp_path):
    """Renaming via prefs with a blank label must 400, not silently reset the
    label to the account id (matching the library rename contract)."""
    from fastapi.testclient import TestClient
    from songmirror.web import create_app

    with TestClient(create_app(settings=SettingsStore(dir=tmp_path))) as client:
        client.post("/api/accounts/spotify/accounts", json={"label": "Work"})
        r = client.put("/api/accounts/spotify:work/prefs", json={"label": "   "})
        assert r.status_code == 400
        assert client.get("/api/accounts").json()  # list still works after the refusal


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
