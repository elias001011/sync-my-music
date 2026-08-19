"""Cookie-mode Spotify browsing carries no playlist cover art (confirmed live:
the pathfinder document used for reads has no image field for the playlist
itself, only per-track album art). When the account also has an OAuth app
configured, its read access (unaffected by the dev-mode write gate) backfills
the thumbnail; with no OAuth app it must stay a silent no-op, never a crash."""

import songmirror.engine.targets.spotify_target as st
from songmirror.engine.targets.base import TargetAuthError
from songmirror.engine.targets.spotify_target import SpotifyTarget


def setup_function(_):
    st._image_cache.clear()


def test_backfill_noop_when_no_oauth_app_configured(monkeypatch):
    monkeypatch.setenv("SPOTIFY_WRITE_BACKEND", "cookie")
    monkeypatch.setattr(st.spotify_cookie, "all_playlists",
                        lambda account_id=None: [{"id": "p1", "name": "Mix"}])

    def boom(**_kwargs):
        raise RuntimeError("Missing required environment variable: SPOTIFY_CLIENT_ID")

    monkeypatch.setattr(st.spotify, "client", boom)
    t = SpotifyTarget(None, "cache.json")

    playlists = t.browse_playlists()

    assert playlists == [{"id": "p1", "name": "Mix"}]  # unchanged, no crash


def test_backfill_fills_and_caches_image(monkeypatch):
    monkeypatch.setenv("SPOTIFY_WRITE_BACKEND", "cookie")
    monkeypatch.setattr(st.spotify_cookie, "all_playlists",
                        lambda account_id=None: [{"id": "p1", "name": "Mix"}])

    calls = []

    class FakeSp:
        def playlist(self, pid, fields=None):
            calls.append(pid)
            return {"images": [{"url": f"https://img/{pid}.jpg"}]}

    monkeypatch.setattr(st.spotify, "client", lambda config=None: FakeSp())
    monkeypatch.setattr(st.spotify, "_retry", lambda fn, what: fn())
    t = SpotifyTarget(None, "cache.json")

    first = t.browse_playlists()
    assert first[0]["images"] == [{"url": "https://img/p1.jpg"}]

    # Second browse must not re-fetch: the in-process cache already has it.
    second = t.browse_playlists()
    assert second[0]["images"] == [{"url": "https://img/p1.jpg"}]
    assert calls == ["p1"]


def test_backfill_survives_per_playlist_lookup_failure(monkeypatch):
    monkeypatch.setenv("SPOTIFY_WRITE_BACKEND", "cookie")
    monkeypatch.setattr(st.spotify_cookie, "all_playlists",
                        lambda account_id=None: [{"id": "p1", "name": "Mix"}])

    class FakeSp:
        def playlist(self, pid, fields=None):
            raise TargetAuthError("expired")

    monkeypatch.setattr(st.spotify, "client", lambda config=None: FakeSp())
    monkeypatch.setattr(st.spotify, "_retry", lambda fn, what: fn())
    t = SpotifyTarget(None, "cache.json")

    playlists = t.browse_playlists()
    assert playlists == [{"id": "p1", "name": "Mix"}]  # no "images" key added
