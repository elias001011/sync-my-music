"""Portable, validated backups for the complete Sync My Music state."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


BACKUP_FORMAT = "sync-my-music-backup"
BACKUP_VERSION = 1
MAX_BACKUP_BYTES = 512 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_FILES = 2_000
MAX_MANIFEST_BYTES = 1024 * 1024
RECOVERY_BACKUP_LIMIT = 3
EXCLUDED_SECRET_KEYS = {"SPOTIFY_SP_DC"}
EXCLUDED_DATA_FILES = {"spotify_sp_dc.private"}


def _excluded_data_file(name: str) -> bool:
    """The default 0600 sp_dc file AND every per-account variant
    (spotify_sp_dc.<slug>.private) are high-risk web session cookies: they are
    only ever carried by encrypted backups, never by plain ones."""
    return name in EXCLUDED_DATA_FILES or (
        name.startswith("spotify_sp_dc.") and name.endswith(".private")
    )
SANITIZED_SETTING_VALUES = {"SPOTIFY_WRITE_BACKEND": "oauth"}

# These gitignored files can live beside the checkout on older/local installs.
# Downloads are deliberately excluded: the backup is app state, not media.
ROOT_STATE_FILES = {
    "apple_resolve_cache.json",
    "spotify_resolve_cache.json",
    "song_cache.db",
    "spotify_tracks_cache.json",
    "download_state.json",
    "ytmusic_browser.json",
    "ytmusic_oauth.json",
    "ytmusic_resolve_cache.json",
}


class BackupError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _private_replace(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        shutil.copyfile(source, temporary_path)
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


class BackupService:
    def __init__(self, settings: Any, music_db: Any, app_root: str | Path | None = None):
        self.settings = settings
        self.music_db = music_db
        self.data_dir = Path(settings.env_path).parent.resolve()
        self.app_root = Path(app_root or Path(__file__).resolve().parents[2]).resolve()
        self.recovery_dir = self.data_dir / "recovery"

    def _state_files(self) -> list[tuple[Path, str, str]]:
        result: list[tuple[Path, str, str]] = []
        database = self.music_db.path.resolve()
        for path in sorted(self.data_dir.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(self.data_dir)
            if relative.parts and relative.parts[0] == "recovery":
                continue
            if _excluded_data_file(relative.as_posix()):
                continue
            if path.resolve() in {database, Path(f"{database}-wal"), Path(f"{database}-shm")}:
                continue
            result.append((path, f"data/{relative.as_posix()}", "data"))
        for name in sorted(ROOT_STATE_FILES):
            path = self.app_root / name
            if path.is_file() and not path.is_symlink() and self.data_dir not in path.resolve().parents:
                result.append((path, f"root/{name}", "root"))
        return result

    @staticmethod
    def _sanitized_files(files: list[tuple[Path, str, str]], temporary: Path) -> list[tuple[Path, str, str]]:
        """Remove explicitly non-exportable session secrets from config copies."""
        sanitized: list[tuple[Path, str, str]] = []
        for path, archive_path, kind in files:
            replacement = path
            if archive_path == "data/settings.json":
                try:
                    values = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    values = None
                original = dict(values) if isinstance(values, dict) else None
                if isinstance(values, dict) and EXCLUDED_SECRET_KEYS.intersection(values):
                    for key in EXCLUDED_SECRET_KEYS:
                        values.pop(key, None)
                if isinstance(values, dict):
                    for key, value in SANITIZED_SETTING_VALUES.items():
                        if key in values:
                            values[key] = value
                if isinstance(values, dict) and values != original:
                    replacement = temporary / "settings.json"
                    replacement.write_text(json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")
            elif archive_path == "data/app.env":
                lines = path.read_text(encoding="utf-8").splitlines()
                clean = [line for line in lines if line.split("=", 1)[0] not in EXCLUDED_SECRET_KEYS]
                clean = [
                    f"{key}={SANITIZED_SETTING_VALUES[key]}" if key in SANITIZED_SETTING_VALUES else line
                    for line in clean
                    for key in [line.split("=", 1)[0]]
                ]
                if clean != lines:
                    replacement = temporary / "app.env"
                    replacement.write_text("\n".join(clean) + "\n", encoding="utf-8")
            sanitized.append((replacement, archive_path, kind))
        return sanitized

    def create(self, destination: str | Path) -> dict[str, Any]:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="sync-backup-") as temporary:
            temporary_dir = Path(temporary)
            snapshot = temporary_dir / "sync_music.db"
            with self.music_db.connect() as source, sqlite3.connect(snapshot) as target:
                source.backup(target)
            state_snapshots: list[tuple[Path, str, str]] = []
            for source, archive_path, kind in self._state_files():
                copied = temporary_dir / "state" / Path(*PurePosixPath(archive_path).parts)
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, copied)
                state_snapshots.append((copied, archive_path, kind))
            files = [(snapshot, "database/sync_music.db", "database"), *state_snapshots]
            files = self._sanitized_files(files, temporary_dir)
            for path, archive_path, _kind in files:
                if archive_path in {"data/settings.json", "data/syncs.json", "data/links.json"}:
                    try:
                        json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError) as exc:
                        raise BackupError(f"{archive_path} changed or is invalid; retry the export") from exc
            manifest_files = [
                {"path": archive_path, "kind": kind, "size": path.stat().st_size, "sha256": _sha256(path)}
                for path, archive_path, kind in files
            ]
            if len(manifest_files) + 1 > MAX_ARCHIVE_FILES:
                raise BackupError("application state contains too many files to back up safely")
            if sum(entry["size"] for entry in manifest_files) > MAX_UNCOMPRESSED_BYTES:
                raise BackupError("application state exceeds the 1 GiB backup safety limit")
            manifest = {
                "format": BACKUP_FORMAT,
                "version": BACKUP_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "excluded_secrets": [
                    {
                        "name": "Spotify sp_dc",
                        "reason": "high-risk web session cookies require encrypted backup support",
                    }
                ],
                "files": manifest_files,
            }
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
                for path, archive_path, _kind in files:
                    archive.write(path, archive_path)
        if destination.stat().st_size > MAX_BACKUP_BYTES:
            destination.unlink(missing_ok=True)
            raise BackupError("compressed backup exceeds the 512 MiB safety limit")
        try:
            os.chmod(destination, 0o600)
        except OSError:
            pass
        return manifest

    @staticmethod
    def _safe_archive_path(raw: str) -> PurePosixPath:
        path = PurePosixPath(raw)
        if path.is_absolute() or not path.parts or ".." in path.parts or "\\" in raw:
            raise BackupError("backup contains an unsafe file path")
        return path

    def _validate(self, archive: zipfile.ZipFile) -> tuple[dict[str, Any], dict[str, zipfile.ZipInfo]]:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise BackupError("backup contains too many files")
        if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
            raise BackupError("backup expands beyond the 1 GiB safety limit")
        names: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            path = self._safe_archive_path(info.filename)
            if info.is_dir():
                continue
            normalized = path.as_posix()
            if normalized in names:
                raise BackupError("backup contains duplicate file names")
            names[normalized] = info
        if "manifest.json" not in names or names["manifest.json"].file_size > MAX_MANIFEST_BYTES:
            raise BackupError("backup manifest is missing or too large")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BackupError("backup manifest is missing or invalid") from exc
        if not isinstance(manifest, dict):
            raise BackupError("backup manifest is invalid")
        if manifest.get("format") != BACKUP_FORMAT or manifest.get("version") != BACKUP_VERSION:
            raise BackupError("unsupported Sync My Music backup format or version")
        entries = manifest.get("files")
        if not isinstance(entries, list) or not entries:
            raise BackupError("backup manifest has no files")
        expected = {"manifest.json"}
        database_found = False
        manifest_paths: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise BackupError("backup manifest contains an invalid entry")
            raw = entry.get("path")
            if not isinstance(raw, str):
                raise BackupError("backup manifest contains an invalid path")
            path = self._safe_archive_path(raw)
            if path.as_posix() in manifest_paths:
                raise BackupError("backup manifest contains duplicate file entries")
            manifest_paths.add(path.as_posix())
            if path.as_posix() not in names:
                raise BackupError(f"backup is missing {path.as_posix()}")
            if path.parts[0] not in {"database", "data", "root"}:
                raise BackupError("backup contains an unsupported destination")
            if path.parts[0] == "database" and path.as_posix() != "database/sync_music.db":
                raise BackupError("backup contains an unsupported database file")
            if path.parts[0] == "data" and (
                len(path.parts) < 2
                or path.parts[1] == "recovery"
                or _excluded_data_file("/".join(path.parts[1:]))
            ):
                raise BackupError("backup contains an unsupported private session file")
            if path.parts[0] == "root" and (len(path.parts) != 2 or path.name not in ROOT_STATE_FILES):
                raise BackupError("backup contains an unsupported root state file")
            if path.as_posix() == "database/sync_music.db":
                database_found = True
            expected.add(path.as_posix())
        if not database_found:
            raise BackupError("backup does not contain the canonical database")
        if set(names) != expected:
            raise BackupError("backup contains files not declared by its manifest")
        return manifest, names

    def _extract_validated(self, archive: zipfile.ZipFile, manifest: dict[str, Any], target: Path) -> None:
        for entry in manifest["files"]:
            archive_path = entry["path"]
            destination = target / PurePosixPath(archive_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with archive.open(archive_path) as source, destination.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UNCOMPRESSED_BYTES:
                        raise BackupError("backup file exceeds the safety limit")
                    digest.update(chunk)
                    output.write(chunk)
            if size != entry.get("size") or digest.hexdigest() != entry.get("sha256"):
                raise BackupError(f"backup integrity check failed for {archive_path}")

    def _make_recovery_backup(self) -> Path:
        self.recovery_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.recovery_dir / f"sync-pre-restore-{stamp}.zip"
        self.create(path)
        backups = sorted(self.recovery_dir.glob("sync-pre-restore-*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
        for old in backups[RECOVERY_BACKUP_LIMIT:]:
            old.unlink(missing_ok=True)
        return path

    def _restore_destination(self, archive_path: PurePosixPath) -> Path:
        if archive_path.parts[0] == "root":
            return self.app_root / archive_path.name
        relative = Path(*archive_path.parts[1:])
        destination = self.data_dir / relative
        resolved_parent = destination.parent.resolve()
        if resolved_parent != self.data_dir and self.data_dir not in resolved_parent.parents:
            raise BackupError("backup destination escapes the private data directory")
        database = self.music_db.path.resolve()
        if destination.resolve() in {database, Path(f"{database}-wal"), Path(f"{database}-shm")}:
            raise BackupError("backup contains the live database as a regular data file")
        return destination

    def restore(self, source: str | Path) -> dict[str, Any]:
        source = Path(source)
        if source.stat().st_size > MAX_BACKUP_BYTES:
            raise BackupError("backup is larger than 512 MiB")
        try:
            with zipfile.ZipFile(source) as archive, tempfile.TemporaryDirectory(prefix="sync-restore-") as temporary:
                manifest, _infos = self._validate(archive)
                extracted = Path(temporary)
                self._extract_validated(archive, manifest, extracted)
                database = extracted / "database" / "sync_music.db"
                with sqlite3.connect(database) as candidate:
                    if candidate.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise BackupError("backup database failed its integrity check")
                    tables = {row[0] for row in candidate.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {"tracks", "listens", "collections", "service_accounts"}.issubset(tables):
                        raise BackupError("backup database does not match Sync My Music")

                recovery = self._make_recovery_backup()
                wanted = {entry["path"] for entry in manifest["files"]}
                # A restore is replacement, not a merge: stale token/config/cache
                # files could otherwise reactivate state that was absent from the
                # backup. The recovery ZIP above makes this cleanup reversible.
                for current, archive_path, _kind in self._state_files():
                    if archive_path not in wanted:
                        current.unlink(missing_ok=True)
                for entry in manifest["files"]:
                    archive_path = PurePosixPath(entry["path"])
                    if archive_path.parts[0] == "database":
                        continue
                    destination = self._restore_destination(archive_path)
                    _private_replace(extracted / Path(*archive_path.parts), destination)
                with sqlite3.connect(database) as candidate, self.music_db.connect() as current:
                    candidate.backup(current)
                self.settings.reload()
                self.music_db.prune_listening_history(self.settings.get("LISTENING_RETENTION_YEARS"))
        except (zipfile.BadZipFile, OSError) as exc:
            raise BackupError("backup ZIP is invalid or could not be read") from exc
        return {
            "ok": True,
            "restored_files": len(manifest["files"]),
            "created_at": manifest.get("created_at"),
            "recovery_backup": recovery.name,
            "restart_recommended": True,
        }
