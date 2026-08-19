"""Options.account_config() must distinguish "no registered account" (None,
falls back to process env) from "a real account with an empty snapshot" ({},
never falls back) - collapsing both to {} broke every CLI-only (.env) pass."""

from songmirror.engine.config import Options
from songmirror.engine.targets import _rest_provider, build_targets
from songmirror.engine.targets.tidal import TidalTarget


def _opts(**over):
    base = dict(execute=False, loop=False, interval_s=60, playlists="", max_removals=0,
                max_adds=200, download_dir="", storefront="us", cache_file="c.json",
                song_cache_file="s.db")
    base.update(over)
    return Options(**base)


def test_account_config_none_when_unregistered():
    # The CLI never populates account_configs; a synthetic ":default" id must
    # not be mistaken for a real (empty) account config.
    opts = _opts()
    assert opts.account_config("spotify:default") is None


def test_account_config_returns_real_snapshot_even_if_empty():
    # A genuinely registered web-managed account keeps its isolation: present
    # (even empty) means "don't fall back to shared process env".
    opts = _opts(account_configs={"spotify:default": {}})
    assert opts.account_config("spotify:default") == {}


def test_cli_legacy_pass_falls_back_to_process_env(monkeypatch, tmp_path):
    # Reproduces the standalone `.env` + `python -m songmirror` path: no
    # `--accounts`, so `_participants()` synthesizes "tidal:default" and the
    # target must still pick up credentials from the environment.
    monkeypatch.setenv("TIDAL_CLIENT_ID", "abc123")
    monkeypatch.setattr("songmirror.engine.targets.tidal.read_token",
                         lambda path: {"access_token": "tok"})
    opts = _opts(providers="tidal", sync_source="spotify")
    targets = build_targets(opts, sp=None)
    assert len(targets) == 1
    assert isinstance(targets[0], TidalTarget)


def test_rest_provider_builder_uses_env_for_synthetic_default_account(monkeypatch):
    monkeypatch.setenv("TIDAL_CLIENT_ID", "abc123")
    monkeypatch.setattr("songmirror.engine.targets.tidal.read_token",
                         lambda path: {"access_token": "tok"})
    opts = _opts()
    target = _rest_provider(TidalTarget, "TIDAL", opts, account="tidal:default")
    assert target is not None
