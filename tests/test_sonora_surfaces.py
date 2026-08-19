"""Every Sonora backup/sync endpoint must apply the account's surface toggles,
not just the desktop-UI-initiated "Sync selected" path. Peer-initiated sync
(POST /api/sync/merge, called by the Sonora app on its own schedule) and the
manual export download previously ignored a disabled surface entirely."""

from fastapi.testclient import TestClient

from songmirror.services.settings import SettingsStore
from songmirror.services.sonora import SonoraAdapter
from songmirror.web import create_app


def _app(tmp_path):
    store = SettingsStore(dir=tmp_path)
    app = create_app(settings=store)
    return app, store


def test_manual_export_respects_disabled_surface(tmp_path, monkeypatch):
    app, store = _app(tmp_path)
    store.save_account("sonora:default", surfaces={"history": False})
    captured = {}

    def fake_export(self, surfaces=None):
        captured["surfaces"] = surfaces
        return {"version": 2}

    monkeypatch.setattr(SonoraAdapter, "export_backup", fake_export)
    with TestClient(app) as client:
        resp = client.get("/api/sonora/backup")
    assert resp.status_code == 200
    assert "history" not in captured["surfaces"]
    assert "likedSongs" in captured["surfaces"]


def test_peer_merge_respects_disabled_surface(tmp_path, monkeypatch):
    app, store = _app(tmp_path)
    store.save_account("sonora:default", surfaces={"history": False})
    app.state.sonora.save_device("phone-1", "Phone", "10.0.0.5", 8080)
    captured = {}

    def fake_import(self, data, surfaces=None):
        captured["import_surfaces"] = surfaces
        return {}

    def fake_export(self, surfaces=None):
        captured["export_surfaces"] = surfaces
        return {"version": 2}

    monkeypatch.setattr(SonoraAdapter, "import_backup", fake_import)
    monkeypatch.setattr(SonoraAdapter, "export_backup", fake_export)
    with TestClient(app) as client:
        resp = client.post("/api/sync/merge", json={"clientId": "phone-1", "library": {}})
    assert resp.status_code == 200
    assert "history" not in captured["import_surfaces"]
    assert "history" not in captured["export_surfaces"]
    assert "likedSongs" in captured["import_surfaces"]
    assert "likedSongs" in captured["export_surfaces"]


def test_peer_merge_rejects_unpaired_device(tmp_path):
    app, _store = _app(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/sync/merge", json={"clientId": "unknown", "library": {}})
    assert resp.status_code == 403
