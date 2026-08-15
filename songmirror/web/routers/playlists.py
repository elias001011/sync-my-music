"""Playlist browsing + pairing-link CRUD."""

from dataclasses import asdict

from fastapi import APIRouter, Body, HTTPException, Request

from ...services.playlists import PlaylistLink, PlaylistService

router = APIRouter()


@router.get("/api/playlists")
def playlists(request: Request, provider: str):
    return PlaylistService(request.app.state.settings).browse(provider)


@router.get("/api/playlist-versions")
def playlist_versions(request: Request, provider: str, playlist_id: str):
    try:
        return PlaylistService(request.app.state.settings).versions(provider, playlist_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/playlist-versions/restore")
def restore_playlist_version(request: Request, body: dict = Body(...)):
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
