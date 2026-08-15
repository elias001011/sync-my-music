"""Sync settings and portable whole-application backup endpoints."""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ...services.accounts import CONNECTORS
from ...services.backups import BackupError, MAX_BACKUP_BYTES
from ...services.music_database import (
    DEFAULT_LISTENING_RETENTION_YEARS,
    MAX_LISTENING_RETENTION_YEARS,
    MIN_LISTENING_RETENTION_YEARS,
)

router = APIRouter()

# Never echo secret credentials back to the browser.
SECRET_KEYS = {f.key for cls in CONNECTORS.values() for f in cls.config_fields if f.secret}
SECRET_KEYS |= {
    "SPOTIFY_SP_DC",
    "TIDAL_OAUTH_VERIFIER",
    "TIDAL_OAUTH_STATE",
    "DEEZER_OAUTH_STATE",
    "AMAZON_MUSIC_OAUTH_STATE",
}

# Non-secret config the UI manages. When settings.json doesn't have a key, fall
# back to the process environment — a docker-compose env_file / .env (the user's
# gitignored config) — so the form reflects the actual running values, not blanks.
CONFIG_KEYS = ("DISPLAY_NAME", "SYNC_MODE", "SYNC_SOURCE", "SYNC_INTERVAL", "PROVIDERS", "MAX_ADDS",
               "MAX_REMOVALS", "PLAYLISTS", "DOWNLOAD_DIR", "LOCAL_MIRROR_FORMAT",
               "LISTENING_RETENTION_YEARS")


@router.get("/api/settings")
def get_settings(request: Request):
    out = {k: v for k, v in request.app.state.settings.load().items() if k not in SECRET_KEYS}
    for key in CONFIG_KEYS:
        if key not in out and os.getenv(key):
            out[key] = os.getenv(key)
    out.setdefault("LISTENING_RETENTION_YEARS", str(DEFAULT_LISTENING_RETENTION_YEARS))
    return out


@router.put("/api/settings")
def put_settings(request: Request, values: dict = Body(...)):
    if "LISTENING_RETENTION_YEARS" in values:
        try:
            years = int(values["LISTENING_RETENTION_YEARS"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="listening retention must be a whole number") from exc
        if not MIN_LISTENING_RETENTION_YEARS <= years <= MAX_LISTENING_RETENTION_YEARS:
            raise HTTPException(status_code=422, detail="listening retention must be between 1 and 10 years")
        values["LISTENING_RETENTION_YEARS"] = str(years)
    request.app.state.settings.save(values)
    if "LISTENING_RETENTION_YEARS" in values:
        request.app.state.music_db.prune_listening_history(years)
    return {"ok": True}


@router.get("/api/system-backup")
async def export_system_backup(request: Request):
    handle = tempfile.NamedTemporaryFile(prefix="sync-my-music-", suffix=".zip", delete=False)
    handle.close()
    path = Path(handle.name)
    try:
        await request.app.state.sync.run_exclusive(lambda: request.app.state.backups.create(path))
    except BackupError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        path.unlink(missing_ok=True)
        raise
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"sync-my-music-backup-{stamp}.zip",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.post("/api/system-backup/restore")
async def restore_system_backup(request: Request, backup: UploadFile):
    handle = tempfile.NamedTemporaryFile(prefix="sync-my-music-upload-", suffix=".zip", delete=False)
    path = Path(handle.name)
    size = 0
    try:
        while chunk := await backup.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BACKUP_BYTES:
                raise HTTPException(status_code=413, detail="backup is larger than 512 MiB")
            handle.write(chunk)
        handle.close()
        try:
            result = await request.app.state.sync.run_exclusive(lambda: request.app.state.backups.restore(path))
            await request.app.state.sync.reconcile()
            return result
        except BackupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        handle.close()
        path.unlink(missing_ok=True)
