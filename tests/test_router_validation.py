"""Malformed request bodies at these endpoints used to raise a bare KeyError/
TypeError/ValueError with no handler registered, surfacing as an opaque 500.
A missing/malformed field is a normal client mistake and should be a 400."""

from fastapi.testclient import TestClient

from songmirror.services.settings import SettingsStore
from songmirror.web import create_app


def _client(tmp_path):
    return TestClient(create_app(settings=SettingsStore(dir=tmp_path)))


def test_start_transfer_missing_fields_is_400(tmp_path):
    with _client(tmp_path) as client:
        resp = client.post("/api/transfers", json={"source_provider": "spotify"})
    assert resp.status_code == 400
    assert "source_playlist_id" in resp.json()["detail"]


def test_start_transfer_complete_body_is_not_400(tmp_path):
    with _client(tmp_path) as client:
        resp = client.post("/api/transfers", json={
            "source_provider": "spotify", "source_playlist_id": "p1", "dest_provider": "tidal",
        })
    assert resp.status_code == 202


def test_resolve_conflict_missing_fields_is_400(tmp_path):
    with _client(tmp_path) as client:
        resp = client.post("/api/transfers/some-job/resolve", json={"key": "only-key"})
    assert resp.status_code == 400


def test_create_sync_bad_max_adds_is_400(tmp_path):
    with _client(tmp_path) as client:
        resp = client.post("/api/syncs", json={"name": "job", "max_adds": "not-a-number"})
    assert resp.status_code == 400
    assert "max_adds" in resp.json()["detail"]


def test_create_sync_valid_body_is_not_400(tmp_path):
    with _client(tmp_path) as client:
        resp = client.post("/api/syncs", json={"name": "job", "max_adds": "5"})
    assert resp.status_code == 200
    assert resp.json()["max_adds"] == 5


def test_upsert_link_missing_name_is_400(tmp_path):
    with _client(tmp_path) as client:
        resp = client.put("/api/links", json={"members": {}})
    assert resp.status_code == 400
