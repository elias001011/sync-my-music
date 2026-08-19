"""Canonical library, recap, scrobble and Musify interoperability routes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import random
import re
import time
import zipfile
from collections import defaultdict
from datetime import datetime

import requests
from fastapi import APIRouter, Body, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from ...engine.logs import Event
from ...services.musify import (
    MAX_BACKUP_BYTES,
    HiveDecodeError,
    MusifyAdapter,
    listening_entries_from_stats,
    listening_periods_from_stats,
)
from ...services.csv_transfer import MAX_CSV_BYTES, export_collection_csv, import_csv_playlist
from ...services.spotify_export import MAX_SPOTIFY_EXPORT_BYTES, import_spotify_export
from ...services.sonora import SonoraAdapter

router = APIRouter()


def _enabled_surfaces(settings, account_id: str) -> dict[str, bool]:
    """Canonical surface toggles for one account (all True by default)."""
    from ...services.settings import SURFACES

    return {surface: settings.account_surface(account_id, surface) for surface in SURFACES}


def _sonora_surfaces(toggles: dict[str, bool]) -> list[str]:
    """Map canonical surface toggles to Sonora's backup-v2 surface names."""
    names = []
    mapping = {"liked_tracks": "likedSongs", "followed_artists": "followedArtists",
               "saved_albums": "likedAlbums", "playlists": "playlists", "history": "history"}
    for surface, enabled in toggles.items():
        if enabled and surface in mapping:
            names.append(mapping[surface])
    if toggles.get("playlists", True):
        names.append("likedPlaylists")
    return names


def _account_slot(provider: str, label: str, requested: str | None, default_label: str) -> str:
    if requested:
        if not requested.startswith(f"{provider}:"):
            raise HTTPException(status_code=400, detail=f"account_id must start with {provider}:")
        return requested
    if label == default_label:
        return f"{provider}:default"
    slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")[:32] or "account"
    return f"{provider}:{slug}-{hashlib.sha256(label.casefold().encode()).hexdigest()[:8]}"


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


@router.get("/api/library/collections")
def library_collections(request: Request):
    """Canonical playlists (id, title, track_count) - e.g. to pick which one
    to export as CSV, independent of which account(s) mirror it."""
    return request.app.state.music_db.collections()


@router.get("/api/library/collections/{collection_id}/csv")
def export_collection_csv_route(collection_id: str, request: Request):
    try:
        filename, payload = export_collection_csv(request.app.state.music_db, collection_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return StreamingResponse(io.BytesIO(payload), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/api/library/csv-import")
async def import_csv_route(request: Request, file: UploadFile, name: str = Form(...),
                           label: str = Form("CSV import"), account_id: str | None = Form(None)):
    if not name.strip():
        raise HTTPException(status_code=400, detail="playlist name is required")
    raw = await file.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail=f"CSV file is larger than {MAX_CSV_BYTES // (1024 * 1024)} MiB")
    try:
        return import_csv_playlist(request.app.state.music_db, raw, name.strip(),
                                   label.strip() or "CSV import", account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/library/accounts/{account_id}/import")
async def import_live_account(account_id: str, request: Request):
    """Pull a live account's playlists + tracks into the canonical library.

    Reads through the account's own engine target (its own credentials/cache)
    and persists the result under the SAME account_id — a second account of the
    same provider never mixes into the first one's rows. Re-imports replace the
    account's previous canonical snapshot (versioned before replacement), so
    nothing sums up twice. Only the `playlists` surface is read today; liked
    tracks/albums/artists have no commercial adapters yet and stay out of the
    canonical store unless imported via an official export. A paused account or
    a disabled playlists surface refuses the read — no data is deleted."""
    settings = request.app.state.settings
    provider, _, rest = account_id.partition(":")
    if not rest:
        account_id = f"{provider}:default"  # bare `spotify` -> the migrated default profile
    from ...services.canonical_target import is_canonical_account
    if is_canonical_account(request.app.state.music_db, account_id):
        raise HTTPException(status_code=400, detail="this is a read-only snapshot account — import into a live account instead")
    if not settings.account_enabled(account_id):
        raise HTTPException(status_code=400, detail="this account is paused — enable it before importing")
    if not settings.account_surface(account_id, "playlists"):
        raise HTTPException(status_code=400, detail="the playlists surface is disabled for this account")

    from ...services.playlists import PlaylistService
    service = PlaylistService(settings, request.app.state.music_db)
    target, opts = service._target(account_id)
    if target is None:
        raise HTTPException(status_code=400, detail=f"{provider}: no live connection for this account")

    def work():
        playlists = target.browse_playlists()
        payload = []
        for pl in playlists:
            try:
                tracks = target.playlist_tracks(pl)
            except Exception:
                tracks = []  # one broken playlist must not fail the whole import
            items = []
            for t in tracks:
                items.append({
                    "provider_track_id": target.track_id(t) or "",
                    "track_name": t.get("name") or "",
                    "artist_name": t.get("artist") or ", ".join(t.get("artists") or []),
                    "release_name": t.get("album") or "",
                    "duration_ms": t.get("duration_ms"),
                    "isrc": t.get("isrc"),
                    "added_at": t.get("added_at"),
                })
            payload.append({
                "provider_id": target.playlist_id(pl),
                "name": target.playlist_name(pl),
                "description": target.playlist_description(pl),
                "snapshot": pl.get("snapshot_id") or "",
                "tracks": items,
            })
        return payload

    try:
        payload = await request.app.state.sync.run_exclusive(work)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"could not read {provider}: {exc!r}") from exc
    profile = settings.account(account_id) or {}
    counts = request.app.state.music_db.import_provider_library(
        provider, account_id, profile.get("label") or account_id,
        playlists=payload, auth_mode="live-import")
    request.app.state.bus.publish(Event(time.time(), "summary", provider,
                                        f"live import: {account_id} · {counts['playlists']} playlists, "
                                        f"{counts['playlist_tracks']} tracks", counts))
    return {"account_id": account_id, "provider": provider, **counts}


@router.put("/api/library/accounts/{account_id}")
def rename_library_account(account_id: str, request: Request, body: dict = Body(...)):
    """Rename an account slot; its stable id keeps jobs/links/recaps attached."""
    try:
        return request.app.state.music_db.rename_account(account_id, str(body.get("label") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/library/accounts/{account_id}")
def delete_library_account(account_id: str, request: Request, body: dict = Body(default={})):
    """Remove one account slot. Destructive, so it requires an explicit
    confirmation flag. Canonical entities still used by other accounts survive;
    only the removed account's own rows and orphans are deleted."""
    if not bool(body.get("confirm")):
        raise HTTPException(status_code=400, detail="removal requires explicit confirmation")
    try:
        return request.app.state.music_db.delete_account(account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/library/accounts/{account_id}/backup")
def export_account_backup(request: Request, account_id: str):
    try:
        payload = request.app.state.music_db.export_account_backup(account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("account.json", json.dumps(payload, ensure_ascii=False))
    buffer.seek(0)
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", account_id)
    return StreamingResponse(buffer, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="sync_account_{safe}.zip"'})


@router.post("/api/library/account-backup")
async def restore_account_backup(request: Request, backup: UploadFile,
                                 label: str | None = Form(None), account_id: str | None = Form(None)):
    raw = await backup.read(256 * 1024 * 1024 + 1)
    if len(raw) > 256 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="account backup is larger than 256 MiB")
    try:
        if raw.startswith(b"PK\x03\x04"):
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                payload = json.loads(archive.read("account.json"))
        else:
            payload = json.loads(raw)
        return request.app.state.music_db.restore_account_backup(payload, account_id, label)
    except (KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid account backup: {exc}") from exc


@router.get("/api/logs")
def app_logs(request: Request, kind: str = "", tag: str = "", q: str = "",
             limit: int = Query(500, ge=1, le=2000)):
    return request.app.state.music_db.logs(kind, tag, q, limit)


def _account_ids(accounts: str) -> list[str]:
    """Comma-separated account filter from the query string; empty = all."""
    return [item.strip() for item in (accounts or "").split(",") if item.strip()]


@router.get("/api/recaps")
def recap(request: Request, year: int | None = None, month: int | None = Query(None, ge=1, le=12),
          accounts: str = ""):
    """Recap for a year/month. `accounts=spotify:default,musify:default` restricts
    every total to those accounts; omitted means the unified recap. A breakdown
    of which accounts contributed to each total is always included in `services`."""
    return request.app.state.music_db.recap(year, month, _account_ids(accounts))


@router.get("/api/recaps/history")
def recap_history(request: Request, accounts: str = ""):
    years = request.app.state.settings.get("LISTENING_RETENTION_YEARS")
    return request.app.state.music_db.recap_history(years, account_ids=_account_ids(accounts))


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
    account_id = f"{source}:default"
    if not request.app.state.settings.account_surface(account_id, "history"):
        return {"inserted": 0, "duplicates": 0, "skipped": len(payloads),
                "reason": "listening history is disabled for this account"}
    return request.app.state.music_db.import_listens(payloads, source)


@router.post("/api/spotify/export-import")
async def spotify_export_import(request: Request, backup: UploadFile,
                                label: str = Form("Spotify export"),
                                account_id: str | None = Form(None)):
    """Restore an official Spotify account-data ZIP/JSON into one account slot."""
    raw = await backup.read(MAX_SPOTIFY_EXPORT_BYTES + 1)
    if len(raw) > MAX_SPOTIFY_EXPORT_BYTES:
        raise HTTPException(status_code=413, detail="Spotify export is larger than 512 MiB")
    try:
        slot = _account_slot("spotify", label.strip() or "Spotify export", account_id, "Spotify export")
        surfaces = _enabled_surfaces(request.app.state.settings, slot)
        result = import_spotify_export(request.app.state.music_db, raw, backup.filename or "export.zip",
                                       label.strip() or "Spotify export", slot, surfaces)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.app.state.bus.publish(Event(
        time.time(), "summary", "spotify",
        f"Spotify export imported into {result['label']}: {result['playlists']} playlists, "
        f"{result['listens_inserted']} new listens ({result['listens_duplicates']} duplicates ignored)", result,
    ))
    return result


@router.post("/api/musify/listening-stats")
def import_musify_listening_stats(request: Request, body: dict = Body(...)):
    """Import Musify's ``wrappedListeningStats`` as replaceable month snapshots."""
    entries = listening_entries_from_stats(body)
    periods = listening_periods_from_stats(body)
    if not periods:
        raise HTTPException(status_code=400, detail="no valid Musify month data found")
    return request.app.state.music_db.replace_listening_aggregates("musify", entries, periods=periods)


@router.post("/api/musify/backup")
async def musify_backup_restore(request: Request, backup: UploadFile,
                                label: str = Form("Musify backup"), account_id: str | None = Form(None)):
    """Import Musify's real ``user.hive`` backup into the canonical database.

    A zip containing ``user.hive`` is accepted for convenience.  ``settings.hive``
    is deliberately never imported: it contains app preferences and may contain
    private connector settings, but no portable music-library data.
    """
    raw = await backup.read(MAX_BACKUP_BYTES + 1)
    if len(raw) > MAX_BACKUP_BYTES:
        raise HTTPException(status_code=413, detail="Musify backup is larger than 32 MiB")
    filename = (backup.filename or "").casefold()
    if filename.endswith(".zip") or raw.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                candidates = [item for item in archive.infolist()
                              if item.filename.rsplit("/", 1)[-1].casefold() == "user.hive"]
                if len(candidates) != 1:
                    raise HTTPException(status_code=400, detail="zip must contain exactly one user.hive")
                item = candidates[0]
                if item.file_size > MAX_BACKUP_BYTES:
                    raise HTTPException(status_code=413, detail="Musify user.hive is larger than 32 MiB")
                with archive.open(item) as source:
                    raw = source.read(MAX_BACKUP_BYTES + 1)
                if len(raw) > MAX_BACKUP_BYTES:
                    raise HTTPException(status_code=413, detail="Musify user.hive is larger than 32 MiB")
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail="invalid Musify backup zip") from exc
    elif filename.endswith("settings.hive"):
        raise HTTPException(status_code=400, detail="select user.hive; settings.hive contains no music library")
    try:
        slot = _account_slot("musify", label, account_id, "Musify backup")
        surfaces = _enabled_surfaces(request.app.state.settings, slot)
        result = MusifyAdapter(request.app.state.music_db, slot, label).import_backup(raw, surfaces)
    except HiveDecodeError as exc:
        request.app.state.bus.publish(Event(time.time(), "warn", "musify", f"Musify backup rejected: {exc}"))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.app.state.bus.publish(Event(
        time.time(), "summary", "musify",
        f"Musify backup imported: {result['likedSongs']} liked songs, "
        f"{result['playlists']} playlists, {result['listeningStats']} recap entries",
        result,
    ))
    return result


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
def sonora_backup(request: Request, account_id: str = "sonora:default"):
    adapter = (request.app.state.sonora.adapter if account_id == request.app.state.sonora.adapter.account_id
               else SonoraAdapter(request.app.state.music_db, account_id, account_id.split(":", 1)[-1]))
    surfaces = _sonora_surfaces(_enabled_surfaces(request.app.state.settings, account_id))
    payload = json.dumps(adapter.export_backup(surfaces), ensure_ascii=False).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("backup.json", payload)
    buffer.seek(0)
    name = datetime.now().strftime("sync_sonora_backup_%Y%m%d_%H%M%S.zip")
    return StreamingResponse(buffer, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/api/sonora/backup")
async def sonora_restore(request: Request, backup: UploadFile, label: str = Form("Sonora"),
                         account_id: str | None = Form(None)):
    try:
        raw = await backup.read()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            data = json.loads(archive.read("backup.json"))
    except (KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid Sonora backup") from exc
    slot = _account_slot("sonora", label, account_id, "Sonora")
    adapter = (request.app.state.sonora.adapter if slot == request.app.state.sonora.adapter.account_id
               else SonoraAdapter(request.app.state.music_db, slot, label))
    surfaces = _sonora_surfaces(_enabled_surfaces(request.app.state.settings, slot))
    return adapter.import_backup(data, surfaces=surfaces)


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


@router.delete("/api/sonora/devices/{device_id}")
def sonora_remove_device(device_id: str, request: Request):
    """Forget one Sonora device's LAN pairing (e.g. the app was restored and
    got a new device id/port, so the old row would fail every sync). Only the
    pairing record is removed — canonical library data is untouched."""
    if not request.app.state.sonora.remove_device(device_id):
        raise HTTPException(status_code=404, detail="device not found")
    return {"ok": True}


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
    # The Sonora app calls this on its own schedule, not just when the desktop
    # UI initiates a sync — it must respect the same account surface toggles
    # as every other Sonora path (import_backup already did; export_backup and
    # this endpoint's import both defaulted to every surface regardless).
    service = request.app.state.sonora
    client_id = str(body.get("clientId") or "")
    if not service.paired(client_id):
        raise HTTPException(status_code=403, detail="Device not paired. Request pairing first.")
    surfaces = _sonora_surfaces(_enabled_surfaces(request.app.state.settings, service.adapter.account_id))
    stats = service.adapter.import_backup(body.get("library") or {}, surfaces=surfaces)
    return {"library": service.adapter.export_backup(surfaces), "stats": stats}
