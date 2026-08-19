"""Named sync jobs: list / create / update / delete / run-now.

The scheduler (SyncService) is reconciled after every mutation so per-job timers
stay in step with the store.
"""

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from ...services.syncs import SyncJob

router = APIRouter()

_FIELDS = {"name", "enabled", "mode", "source", "providers", "accounts", "playlists",
           "interval", "max_adds", "max_removals", "apply_large_removals", "download", "id"}


def _normalize_accounts(values: dict) -> dict:
    """Every account id becomes its stable `{provider}:default` form (a bare
    `spotify` -> `spotify:default`; `spotify:work` stays). `accounts` is derived
    from a legacy `providers` value when absent. A bare `source` that names a
    participating account is promoted to that account id — so a new job with
    `source="spotify"` and `spotify:default` participating stores
    `source="spotify:default"` and the engine treats them as one account."""
    providers = str(values.get("providers") or "")
    accounts = str(values.get("accounts") or "").strip()
    if not accounts and providers.strip():
        accounts = ",".join(
            item.strip() if ":" in item else f"{item.strip()}:default"
            for item in providers.split(",") if item.strip())
    if not accounts.strip():
        return values
    values = dict(values)
    values["accounts"] = ",".join(
        item.strip() if ":" in item else f"{item.strip()}:default"
        for item in accounts.split(",") if item.strip())
    source = str(values.get("source") or "").strip()
    if source and ":" not in source:
        participating = {item.strip() for item in values["accounts"].split(",") if item.strip()}
        if f"{source}:default" in participating:
            values["source"] = f"{source}:default"
    return values


def _job_from(values):
    """SyncJob from a request dict — unknown keys dropped, types coerced."""
    data = {k: v for k, v in values.items() if k in _FIELDS}
    for k in ("max_adds", "max_removals"):
        if k in data:
            try:
                data[k] = int(data[k])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"{k} must be an integer") from None
    for k in ("enabled", "download", "apply_large_removals"):
        if k in data:
            data[k] = bool(data[k])
    return SyncJob(**data)


@router.get("/api/syncs")
def list_syncs(request: Request):
    return [asdict(j) for j in request.app.state.syncs.list()]


@router.post("/api/syncs")
async def create_sync(request: Request, values: dict = Body(...)):
    job = request.app.state.syncs.upsert(_job_from(_normalize_accounts(values)))
    await request.app.state.sync.reconcile()
    return asdict(job)


@router.put("/api/syncs/{job_id}")
async def update_sync(job_id: str, request: Request, values: dict = Body(...)):
    existing = request.app.state.syncs.get(job_id)
    if existing is None:
        return JSONResponse({"detail": "not found"}, status_code=404)
    job = request.app.state.syncs.upsert(_job_from(_normalize_accounts({**asdict(existing), **values, "id": job_id})))
    await request.app.state.sync.reconcile()
    return asdict(job)


@router.delete("/api/syncs/{job_id}")
async def delete_sync(job_id: str, request: Request):
    request.app.state.syncs.delete(job_id)
    await request.app.state.sync.reconcile()
    return {"ok": True}


@router.post("/api/syncs/{job_id}/run")
async def run_sync(job_id: str, request: Request, execute: bool = False):
    # Fire-and-forget onto SyncService's single queue; returns immediately.
    asyncio.create_task(request.app.state.sync.run_job(job_id, execute=execute))
    return JSONResponse({"queued": True}, status_code=202)


@router.post("/api/syncs/{job_id}/{action}")
async def control_sync(job_id: str, action: str, request: Request):
    """Pause / stop the running pass, or resume a paused one. Returns {ok} — False
    when the action doesn't apply (e.g. that job isn't the one currently running).
    Declared after /run so the literal 'run' route isn't shadowed."""
    svc = request.app.state.sync
    fn = {"pause": svc.pause, "stop": svc.stop, "resume": svc.resume}.get(action)
    if fn is None:
        return JSONResponse({"detail": "unknown action"}, status_code=404)
    return {"ok": fn(job_id)}
