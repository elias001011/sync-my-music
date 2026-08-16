"""SettingsStore: json + managed env file; wizard saves survive engine reload."""

import os
import stat

import pytest
from dotenv import load_dotenv

from songmirror.services.settings import SettingsStore


def test_saved_credential_survives_dotenv_reload(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLE_BEARER_TOKEN", raising=False)
    store = SettingsStore(dir=tmp_path)
    store.save({"APPLE_BEARER_TOKEN": "NEW"})
    # The engine reloads the managed file each pass; it must win — this is the
    # regression guard for load_dotenv(override=True) clobbering wizard saves.
    load_dotenv(store.env_path, override=True)
    assert os.environ["APPLE_BEARER_TOKEN"] == "NEW"


def test_roundtrip_persists(tmp_path):
    SettingsStore(dir=tmp_path).save({"SYNC_INTERVAL": "30m", "SPOTIFY_CLIENT_ID": "abc"})
    reopened = SettingsStore(dir=tmp_path)
    assert reopened.get("SYNC_INTERVAL") == "30m"
    assert reopened.get("SPOTIFY_CLIENT_ID") == "abc"


def test_none_values_ignored(tmp_path):
    store = SettingsStore(dir=tmp_path)
    store.save({"A": "1", "B": None})
    assert store.get("A") == "1"
    assert "B" not in store.load()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes are ignored on Windows")
def test_credential_files_owner_only(tmp_path):
    store = SettingsStore(dir=tmp_path)
    store.save({"APPLE_BEARER_TOKEN": "secret"})
    for p in (store._json, store.env_path):
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_env_file_quotes_spaces(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLE_STOREFRONT", raising=False)
    store = SettingsStore(dir=tmp_path)
    store.save({"NOTE": "two words"})
    load_dotenv(store.env_path, override=True)
    assert os.environ["NOTE"] == "two words"


def test_account_config_is_isolated_per_account(tmp_path):
    store = SettingsStore(dir=tmp_path)
    store.save_account("spotify:work", label="Work", config={"SPOTIFY_CLIENT_ID": "work-id"})
    store.save_account("spotify:personal", label="Personal", config={"SPOTIFY_CLIENT_ID": "personal-id"})
    # Named accounts never see each other's (or legacy) credentials.
    assert store.account_config("spotify:work", "SPOTIFY_CLIENT_ID") == "work-id"
    assert store.account_config("spotify:personal", "SPOTIFY_CLIENT_ID") == "personal-id"
    assert store.account_config("spotify:work", "SPOTIFY_CLIENT_SECRET") is None
    # The default account falls back to the legacy flat key (migration path).
    store.save({"SPOTIFY_CLIENT_ID": "legacy-id"})
    assert store.account_config("spotify:default", "SPOTIFY_CLIENT_ID") == "legacy-id"


def test_legacy_config_migrates_to_default_account(tmp_path):
    store = SettingsStore(dir=tmp_path)
    store.save({"SPOTIFY_CLIENT_ID": "abc", "TIDAL_BEARER_TOKEN": "t"})
    created = store.migrate_accounts()
    assert created == 2
    assert store.account("spotify:default")["label"] == "Spotify"
    assert store.account("tidal:default")["enabled"] is True
    # Idempotent: a second run creates nothing.
    assert store.migrate_accounts() == 0


def test_surface_toggles_default_on_and_persist(tmp_path):
    store = SettingsStore(dir=tmp_path)
    assert store.account_surface("spotify:default", "liked_tracks") is True
    store.save_account("spotify:default", surfaces={"liked_tracks": False, "history": False})
    assert store.account_surface("spotify:default", "liked_tracks") is False
    assert store.account_surface("spotify:default", "history") is False
    # Other surfaces are untouched.
    assert store.account_surface("spotify:default", "playlists") is True
    # A disabled surface never deletes data — it only stops new imports.
    assert store.get("ACCOUNTS")


def test_account_slug_is_stable_and_filesystem_safe(tmp_path):
    store = SettingsStore(dir=tmp_path)
    assert store.account_slug("spotify:default") == "spotify"
    first = store.account_slug("spotify:work")
    assert first.startswith("spotify-") and len(first) == len("spotify-") + 8
    assert store.account_slug("spotify:work") == first
