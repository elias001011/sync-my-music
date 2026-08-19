"""HTTP-level CSV endpoints: list collections, import a CSV playlist, export
one back out. Exercises the FastAPI routes, not just the service functions."""

import io

from fastapi.testclient import TestClient

from songmirror.services.settings import SettingsStore
from songmirror.web import create_app

CSV_BODY = "Title,Artist,Album\nHello,World,Greatest Hits\n"


def _client(tmp_path):
    return TestClient(create_app(settings=SettingsStore(dir=tmp_path)))


def test_csv_import_then_list_then_export_roundtrip(tmp_path):
    with _client(tmp_path) as client:
        assert client.get("/api/library/collections").json() == []

        resp = client.post(
            "/api/library/csv-import",
            data={"name": "My Import", "label": "Test CSV"},
            files={"file": ("mix.csv", io.BytesIO(CSV_BODY.encode()), "text/csv")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["tracks"] == 1
        assert body["account_id"].startswith("csv:")

        collections = client.get("/api/library/collections").json()
        assert len(collections) == 1
        assert collections[0]["title"] == "My Import"
        assert collections[0]["track_count"] == 1

        export = client.get(f"/api/library/collections/{collections[0]['id']}/csv")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        assert "attachment" in export.headers["content-disposition"]
        assert b"Hello" in export.content
        assert b"World" in export.content


def test_csv_import_requires_name(tmp_path):
    with _client(tmp_path) as client:
        resp = client.post(
            "/api/library/csv-import",
            data={"name": "   "},  # present but blank - a genuinely missing field is a 422, not this check
            files={"file": ("mix.csv", io.BytesIO(CSV_BODY.encode()), "text/csv")},
        )
    assert resp.status_code == 400


def test_export_missing_collection_is_404(tmp_path):
    with _client(tmp_path) as client:
        resp = client.get("/api/library/collections/does-not-exist/csv")
    assert resp.status_code == 404
