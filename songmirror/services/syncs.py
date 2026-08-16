"""Named sync jobs — multiple independent sync configurations (Soundiiz-style).

Each job is a self-contained sync config: a name, on/off, direction, one-way
source of truth, participating providers, playlist filter, safety caps, and its
OWN auto-sync interval. The download mirror stays global (SettingsStore's
DOWNLOAD_DIR/LOCAL_MIRROR_FORMAT) — a job just opts in via `download`.

Persisted to data/syncs.json (owner-only) alongside the other data-dir state.
The engine is unchanged: SyncService builds an Options per job and runs it, so
each job is an ordinary pass.
"""

import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ..engine.config import (
    DEFAULT_INTERVAL, DEFAULT_MAX_ADDS, DEFAULT_MAX_REMOVALS, DEFAULT_PROVIDERS,
    DEFAULT_SYNC_MODE, DEFAULT_SYNC_SOURCE,
)
from .settings import _open_private

# Before named jobs stored an explicit provider list, an empty value meant
# "whatever providers happen to be configured right now".  That made a job
# silently grow when support for a new service was added or an account was
# connected later.  Freeze legacy jobs to the provider set available when the
# migration was introduced; all newly saved jobs are explicit.
LEGACY_NAMED_JOB_PROVIDERS = "spotify,apple,ytmusic"


@dataclass
class SyncJob:
    name: str = "Sync"
    enabled: bool = True                      # participates in scheduled auto-sync
    mode: str = DEFAULT_SYNC_MODE             # oneway | nway
    source: str = DEFAULT_SYNC_SOURCE         # one-way source of truth (provider or account_id)
    providers: str = DEFAULT_PROVIDERS        # legacy: comma-separated providers (kept for CLI/compat)
    accounts: str = ""                        # comma-separated account_ids; empty = derived from providers
    playlists: str = ""                       # comma-separated names (empty = every same-named pair)
    interval: str = DEFAULT_INTERVAL          # this job's own auto-sync cadence
    max_adds: int = DEFAULT_MAX_ADDS
    max_removals: int = DEFAULT_MAX_REMOVALS
    apply_large_removals: bool = False        # drain removals over max_removals across passes (default: hold back)
    download: bool = False                    # opt into the global download mirror
    id: str = ""

    @property
    def account_list(self) -> list[str]:
        """The job's participating account ids. Legacy jobs store providers;
        each maps to its `{provider}:default` account, so old jobs keep pointing
        at the migrated default accounts with zero data loss."""
        if self.accounts.strip():
            return [item.strip() for item in self.accounts.split(",") if item.strip()]
        return [f"{item.strip()}:default" for item in self.providers.split(",") if item.strip()]


class SyncStore:
    """Named sync jobs persisted to data/syncs.json (owner-only)."""

    def __init__(self, dir="data"):
        self._path = Path(dir) / "syncs.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def list(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                rows = json.load(f)
            jobs = []
            for row in rows:
                data = dict(row)
                if not str(data.get("providers") or "").strip():
                    data["providers"] = LEGACY_NAMED_JOB_PROVIDERS
                # Old jobs predate per-account selectors: `accounts` is derived
                # from `providers` (each -> its :default account) on first read,
                # and the derived value is persisted back so the stored job is
                # explicit going forward.
                if not str(data.get("accounts") or "").strip():
                    data["accounts"] = ",".join(
                        f"{item.strip()}:default" for item in str(data["providers"]).split(",") if item.strip())
                job = SyncJob(**data)
                jobs.append(job)
            return jobs
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def get(self, job_id):
        return next((j for j in self.list() if j.id == job_id), None)

    def upsert(self, job):
        if not job.id:
            job.id = uuid.uuid4().hex[:8]
        jobs = [j for j in self.list() if j.id != job.id]
        jobs.append(job)
        self._save(jobs)
        return job

    def delete(self, job_id):
        self._save([j for j in self.list() if j.id != job_id])

    def _save(self, jobs):
        with _open_private(self._path) as f:
            json.dump([asdict(j) for j in jobs], f, indent=2)
