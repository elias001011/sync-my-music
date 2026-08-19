"""Playlist browsing + pairing-link CRUD."""

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json

from fastapi import APIRouter, Body, HTTPException, Request

from ...services.playlists import PlaylistLink, PlaylistService
from ...engine.targets.base import TargetAuthError

router = APIRouter()


@router.get("/api/playlists")
def playlists(request: Request, provider: str):
    try:
        return PlaylistService(request.app.state.settings, request.app.state.music_db).browse(provider)
    except TargetAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/api/playlist-versions")
def playlist_versions(request: Request, provider: str, playlist_id: str):
    if provider == "musify" or ":" in provider:
        rows = request.app.state.music_db.collection_versions(playlist_id)
        with request.app.state.music_db.connect() as conn:
            payloads = {row["id"]: json.loads(conn.execute(
                "SELECT items FROM collection_versions WHERE id=?", (row["id"],)
            ).fetchone()[0]) for row in rows}
        return [{"version_id": row["id"],
                 "captured_at": datetime.fromtimestamp(row["created_at"], timezone.utc).isoformat(),
                 "item_count": row["item_count"],
                 "items": [[item["track_id"], item.get("title", ""), item.get("artist", "")]
                           for item in payloads[row["id"]]]} for row in rows]
    try:
        return PlaylistService(request.app.state.settings).versions(provider, playlist_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/playlist-versions/restore")
def restore_playlist_version(request: Request, body: dict = Body(...)):
    provider = str(body.get("provider"))
    if provider == "musify" or ":" in provider:
        version_id = str(body.get("captured_at") or "")
        with request.app.state.music_db.connect() as conn:
            version = conn.execute("SELECT * FROM collection_versions WHERE id=?", (version_id,)).fetchone()
            if not version or version["collection_id"] != str(body.get("playlist_id")):
                raise HTTPException(status_code=400, detail="playlist version not found")
            historical = json.loads(version["items"])
            current = conn.execute(
                "SELECT track_id FROM collection_items WHERE collection_id=? ORDER BY position",
                (version["collection_id"],),
            ).fetchall()
        historical_ids = [item["track_id"] for item in historical]
        current_ids = [row[0] for row in current]
        historical_counts = Counter(historical_ids)
        current_counts = Counter(current_ids)
        additions = sum((historical_counts - current_counts).values())
        removals = sum((current_counts - historical_counts).values())
        limit = max(0, int(body.get("max_removals", 100)))
        if removals > limit:
            raise HTTPException(status_code=400, detail=f"restore needs {removals} removals, above the safety limit {limit}")
        execute = bool(body.get("execute"))
        if execute:
            request.app.state.music_db.restore_collection_version(version_id)
        return {"execute": execute, "additions": additions, "removals": removals,
                "target_count": len(historical_ids),
                "order_note": "the canonical Musify snapshot order is restored exactly"}
    try:
        return PlaylistService(request.app.state.settings).restore_version(
            str(body["provider"]), str(body["playlist_id"]), str(body["captured_at"]),
            execute=bool(body.get("execute")), max_removals=int(body.get("max_removals", 100)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/links")
def list_links(request: Request):
    return [asdict(link) for link in request.app.state.links.list()]


@router.put("/api/links")
def upsert_link(request: Request, body: dict = Body(...)):
    if not body.get("name"):
        raise HTTPException(status_code=400, detail="missing required field: name")
    link = PlaylistLink(
        name=body["name"],
        members=body.get("members", {}),
        direction=body.get("direction", "oneway"),
        source=body.get("source", "spotify"),
        enabled=body.get("enabled", True),
        id=body.get("id", ""),
    )
    return asdict(request.app.state.links.upsert(link))


@router.delete("/api/links/{link_id}")
def delete_link(request: Request, link_id: str):
    request.app.state.links.delete(link_id)
    return {"ok": True}
