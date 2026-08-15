"""Canonical library, recap, scrobble and Musify interoperability routes."""

from __future__ import annotations

import base64
import hmac
import io
import json
import os
import random
import time
import zipfile
from collections import defaultdict
from datetime import datetime
from datetime import timezone

import requests
from fastapi import APIRouter, Body, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

router = APIRouter()


def _source_name(payload: dict) -> str:
    metadata = payload.get("track_metadata") or payload
    info = metadata.get("additional_info") or {}
    raw = str(info.get("music_service_name") or info.get("submission_client") or "listenbrainz").casefold()
    if "youtube" in raw:
        return "ytmusic"
    if "spotify" in raw:
        return "spotify"
    if "amazon" in raw:
        return "amazon"
    if "musify" in raw:
        return "musify"
    return "listenbrainz"


def _check_scrobble_token(request: Request) -> None:
    expected = os.getenv("SCROBBLE_TOKEN", "").strip()
    if not expected:
        return
    supplied = request.headers.get("Authorization", "")
    if supplied.lower().startswith("token "):
        supplied = supplied[6:]
    elif supplied.lower().startswith("bearer "):
        supplied = supplied[7:]
    if not hmac.compare_digest(supplied.strip(), expected):
        raise HTTPException(status_code=401, detail="invalid scrobble token")


@router.get("/api/library/summary")
def library_summary(request: Request):
    return request.app.state.music_db.summary()


@router.get("/api/library/tracks")
def library_tracks(request: Request, q: str = "", limit: int = Query(100, ge=1, le=500),
                   offset: int = Query(0, ge=0)):
    return request.app.state.music_db.library(q, limit, offset)


@router.get("/api/library/accounts")
def library_accounts(request: Request):
    return request.app.state.music_db.accounts()


@router.get("/api/logs")
def app_logs(request: Request, kind: str = "", tag: str = "", q: str = "",
             limit: int = Query(500, ge=1, le=2000)):
    return request.app.state.music_db.logs(kind, tag, q, limit)


@router.get("/api/recaps")
def recap(request: Request, year: int | None = None, month: int | None = Query(None, ge=1, le=12)):
    return request.app.state.music_db.recap(year, month)


@router.post("/1/submit-listens")
async def listenbrainz_submit(request: Request):
    """ListenBrainz-compatible ingestion endpoint for Pano/Web Scrobbler.

    ``playing_now`` is deliberately acknowledged but not persisted: the final
    listen contains the useful timestamp and avoids counting a play twice.
    """
    _check_scrobble_token(request)
    body = await request.json()
    listen_type = body.get("listen_type")
    payloads = body.get("payload") or []
    if listen_type not in {"single", "import", "playing_now"} or not isinstance(payloads, list):
        raise HTTPException(status_code=400, detail="invalid ListenBrainz payload")
    if listen_type == "playing_now":
        return {"status": "ok", "inserted": 0, "duplicates": 0}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for payload in payloads:
        if not isinstance(payload, dict) or not (payload.get("track_metadata") or payload.get("track_name")):
            raise HTTPException(status_code=400, detail="each listen needs track_metadata")
        grouped[_source_name(payload)].append(payload)
    result = {"inserted": 0, "duplicates": 0}
    for source, group in grouped.items():
        part = request.app.state.music_db.import_listens(group, source)
        result["inserted"] += part["inserted"]
        result["duplicates"] += part["duplicates"]
    return {"status": "ok", **result}


@router.post("/api/listens/import")
async def import_listens(request: Request, body: dict = Body(...)):
    source = str(body.get("source") or "manual").strip().casefold()
    payloads = body.get("listens") or []
    if not isinstance(payloads, list):
        raise HTTPException(status_code=400, detail="listens must be an array")
    return request.app.state.music_db.import_listens(payloads, source)


@router.post("/api/musify/listening-stats")
def import_musify_listening_stats(request: Request, body: dict = Body(...)):
    """Import Musify's ``wrappedListeningStats`` as replaceable month snapshots."""
    months = dict(body.get("history") or {})
    current_key = str(body.get("currentMonthKey") or "")
    if current_key:
        months[current_key] = body.get("currentMonth") or {}
    entries = []
    for month_key, month in months.items():
        try:
            year, month_number = (int(part) for part in month_key.split("-", 1))
            start = datetime(year, month_number, 1, tzinfo=timezone.utc)
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month_number == 12 else datetime(year, month_number + 1, 1, tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        song_seconds = 0
        for ytid, song_value in dict((month or {}).get("songs") or {}).items():
            song = dict(song_value or {})
            seconds = max(0, int(song.get("seconds") or 0))
            song_seconds += seconds
            entries.append({
                "period_start": int(start.timestamp()), "period_end": int(end.timestamp()),
                "play_count": max(0, int(song.get("playCount") or song.get("listeningCount") or 0)),
                "listened_ms": seconds * 1000,
                "track_metadata": {"track_name": song.get("title") or ytid,
                                   "artist_name": song.get("artist") or "Unknown artist",
                                   "additional_info": {"music_service_name": "musify", "video_id": ytid}},
            })
        residual = max(0, int((month or {}).get("totalSeconds") or 0) - song_seconds)
        if residual:
            entries.append({
                "period_start": int(start.timestamp()), "period_end": int(end.timestamp()),
                "play_count": 0, "listened_ms": residual * 1000,
                "track_metadata": {"track_name": "Other Musify listening", "artist_name": "Musify"},
            })
    if not entries:
        raise HTTPException(status_code=400, detail="no valid Musify month data found")
    return request.app.state.music_db.replace_listening_aggregates("musify", entries)


@router.post("/api/musify/export")
async def musify_export(request: Request, body: dict = Body(...)):
    """Create the compact deep link understood by Musify's custom importer.

    Musify resolves each YouTube video id itself, so no private Hive backup is
    generated or modified here.
    """
    title = str(body.get("title") or "Imported from Sync My Music").strip()
    image = str(body.get("image") or "").strip()
    raw_ids = body.get("youtube_ids") or []
    unmatched = []
    if body.get("source_provider") and body.get("playlist_id"):
        try:
            resolved = await request.app.state.transfers.export_musify(
                str(body["source_provider"]), str(body["playlist_id"])
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        title = str(body.get("title") or resolved["title"]).strip()
        image = str(body.get("image") or resolved["image"]).strip()
        raw_ids = resolved["youtube_ids"]
        unmatched = resolved["unmatched"]
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="youtube_ids must be an array")
    youtube_ids = list(dict.fromkeys(str(item).strip() for item in raw_ids if str(item).strip()))
    if not youtube_ids:
        raise HTTPException(status_code=400, detail="at least one YouTube id is required")
    compact = {"title": title, "source": "user-created", "list": youtube_ids}
    if image:
        compact["image"] = image
    encoded = base64.urlsafe_b64encode(
        json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return {
        "format": "musify-custom-playlist-v1",
        "track_count": len(youtube_ids),
        "unmatched": unmatched,
        "compact": compact,
        "deep_link": f"musify://playlist/custom/{encoded}",
    }


@router.get("/api/sonora/backup")
def sonora_backup(request: Request):
    payload = json.dumps(request.app.state.sonora.adapter.export_backup(), ensure_ascii=False).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("backup.json", payload)
    buffer.seek(0)
    name = datetime.now().strftime("sync_sonora_backup_%Y%m%d_%H%M%S.zip")
    return StreamingResponse(buffer, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/api/sonora/backup")
async def sonora_restore(request: Request, backup: UploadFile):
    try:
        raw = await backup.read()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            data = json.loads(archive.read("backup.json"))
    except (KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid Sonora backup") from exc
    return request.app.state.sonora.adapter.import_backup(data)


@router.get("/api/sonora/status")
def sonora_status(request: Request):
    service = request.app.state.sonora
    return {"enabled": bool(service._thread and service._thread.is_alive()),
            "device_id": service.device_id, "name": service.name, "port": service.port,
            "devices": service.devices(), "pending": list(service.pending.values())}


@router.put("/api/sonora/status")
def set_sonora_status(request: Request, body: dict = Body(...)):
    enabled = bool(body.get("enabled"))
    request.app.state.settings.save({"SONORA_LAN_SYNC": "1" if enabled else "0"})
    request.app.state.sonora.start() if enabled else request.app.state.sonora.stop()
    return sonora_status(request)


@router.post("/api/sonora/discover")
def sonora_discover(request: Request):
    devices = request.app.state.sonora.discover()
    for device in devices:
        request.app.state.sonora.save_device(**device, paired=False)
    return devices


@router.post("/api/sonora/pair-request")
def sonora_pair_request(request: Request, body: dict = Body(...)):
    return request.app.state.sonora.request_pair(str(body["ip"]), int(body["port"]))


@router.post("/api/sonora/pair-verify")
def sonora_pair_verify(request: Request, body: dict = Body(...)):
    return request.app.state.sonora.verify_pair(str(body["ip"]), int(body["port"]), str(body["pin"]))


@router.post("/api/sonora/devices/{device_id}/sync")
def sonora_sync_device(device_id: str, request: Request, body: dict = Body(default={})):
    try:
        return request.app.state.sonora.sync(device_id, body.get("surfaces"))
    except (ValueError, requests.RequestException) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# Sonora-compatible peer endpoints. UDP discovery advertises this app's HTTP
# port, so a stock Sonora build can pair with it as if it were another device.
@router.get("/api/sync/info")
def sonora_peer_info(request: Request):
    service = request.app.state.sonora
    return {"name": service.name, "platform": "self-hosted", "api_version": 1, "deviceId": service.device_id}


@router.post("/api/sync/pair-request")
def sonora_peer_pair_request(request: Request, body: dict = Body(...)):
    client_id = str(body.get("clientId") or "")
    if not client_id:
        raise HTTPException(status_code=400, detail="clientId missing")
    pin = str(random.SystemRandom().randint(1000, 9999))
    request.app.state.sonora.pending[client_id] = {
        "client_id": client_id, "client_name": body.get("clientName") or "Sonora",
        "client_port": int(body.get("clientPort") or 8080), "pin": pin, "created_at": int(time.time()),
    }
    return {"status": "pairing_started"}


@router.post("/api/sync/pair-verify")
def sonora_peer_pair_verify(request: Request, body: dict = Body(...)):
    service = request.app.state.sonora
    client_id = str(body.get("clientId") or "")
    pending = service.pending.get(client_id)
    if not pending or not hmac.compare_digest(str(body.get("pin") or ""), pending["pin"]):
        raise HTTPException(status_code=403, detail="incorrect_pin")
    ip = request.client.host if request.client else ""
    service.save_device(client_id, pending["client_name"], ip, pending["client_port"])
    service.pending.pop(client_id, None)
    return {"status": "paired", "deviceId": service.device_id, "deviceName": service.name}


@router.post("/api/sync/merge")
def sonora_peer_merge(request: Request, body: dict = Body(...)):
    service = request.app.state.sonora
    client_id = str(body.get("clientId") or "")
    if not service.paired(client_id):
        raise HTTPException(status_code=403, detail="Device not paired. Request pairing first.")
    stats = service.adapter.import_backup(body.get("library") or {})
    return {"library": service.adapter.export_backup(), "stats": stats}
