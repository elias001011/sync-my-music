"""Importer for Spotify's official account-data ZIP/JSON exports."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any


MAX_SPOTIFY_EXPORT_BYTES = 512 * 1024 * 1024
MAX_JSON_FILES = 2_000


def _timestamp(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _track(raw: dict[str, Any]) -> dict[str, Any] | None:
    node = raw.get("track") if isinstance(raw.get("track"), dict) else raw
    title = node.get("trackName") or node.get("master_metadata_track_name") or node.get("name") or node.get("track")
    artist = (node.get("artistName") or node.get("master_metadata_album_artist_name") or
              node.get("artist") or node.get("albumArtistName"))
    if isinstance(artist, list):
        artist = ", ".join(str(value.get("name") if isinstance(value, dict) else value) for value in artist)
    if not title or not artist:
        return None
    uri = node.get("trackUri") or node.get("spotify_track_uri") or node.get("uri") or ""
    return {
        "track_name": str(title), "artist_name": str(artist),
        "release_name": str(node.get("albumName") or node.get("master_metadata_album_album_name") or node.get("album") or ""),
        "provider_track_id": str(uri).rsplit(":", 1)[-1] if uri else "",
        "uri": str(uri), "added_at": _timestamp(raw.get("addedDate") or raw.get("added_at")),
    }


def _event(raw: dict[str, Any]) -> dict[str, Any] | None:
    listened_at = _timestamp(raw.get("ts") or raw.get("endTime"))
    track = _track(raw)
    if listened_at is None or track is None:
        return None
    listened_ms = max(0, int(raw.get("ms_played") or raw.get("msPlayed") or 0))
    # The one-year export truncates timestamps to a minute and omits URI, while
    # extended history includes both.  A minute bucket deduplicates their
    # overlapping events when both files are imported together.
    identity = "\x1f".join((str(listened_at // 60), track["artist_name"],
                              track["track_name"], str(listened_ms)))
    return {
        "listened_at": listened_at, "listened_ms": listened_ms,
        "source_event_id": hashlib.sha256(identity.encode()).hexdigest(),
        "skipped": bool(raw.get("skipped")) if raw.get("skipped") is not None else None,
        "track_metadata": track,
    }


def _playlist(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = raw.get("name") or raw.get("playlistName")
    items = raw.get("items") or raw.get("tracks")
    if not name or not isinstance(items, list):
        return None
    tracks = [track for item in items if isinstance(item, dict) for track in [_track(item)] if track]
    uri = raw.get("uri") or raw.get("playlistUri") or raw.get("id") or name
    return {"provider_id": str(uri).rsplit(":", 1)[-1], "name": str(name),
            "description": str(raw.get("description") or ""), "tracks": tracks}


def _json_documents(raw: bytes, filename: str) -> list[tuple[str, Any]]:
    if filename.casefold().endswith(".json") and not raw.startswith(b"PK\x03\x04"):
        return [(filename, json.loads(raw.decode("utf-8-sig")))]
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("select a Spotify JSON file or official export ZIP") from exc
    documents = []
    total = 0
    with archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir() and entry.filename.casefold().endswith(".json")]
        if len(entries) > MAX_JSON_FILES:
            raise ValueError("Spotify export contains too many JSON files")
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("unsafe path in Spotify export")
            total += entry.file_size
            if total > MAX_SPOTIFY_EXPORT_BYTES:
                raise ValueError("Spotify export is larger than 512 MiB unpacked")
            with archive.open(entry) as source:
                documents.append((entry.filename, json.load(io.TextIOWrapper(source, encoding="utf-8-sig"))))
    return documents


def account_id_for(label: str) -> str:
    label = (label or "Spotify").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")[:24] or "account"
    suffix = hashlib.sha256(label.casefold().encode()).hexdigest()[:8]
    return f"spotify:{slug}-{suffix}"


def import_spotify_export(database, raw: bytes, filename: str, label: str,
                          account_id: str | None = None) -> dict[str, Any]:
    if len(raw) > MAX_SPOTIFY_EXPORT_BYTES:
        raise ValueError("Spotify export is larger than 512 MiB")
    account_id = account_id or account_id_for(label)
    if not account_id.startswith("spotify:"):
        raise ValueError("Spotify account id must start with spotify:")
    playlists: list[dict[str, Any]] = []
    liked: list[dict[str, Any]] = []
    albums: list[dict[str, Any]] = []
    artists: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    files = []
    for name, document in _json_documents(raw, filename):
        files.append(name)
        lowered = name.casefold()
        nodes = document if isinstance(document, list) else []
        if isinstance(document, dict):
            if isinstance(document.get("playlists"), list):
                nodes = document["playlists"]
            elif any(key in document for key in ("tracks", "albums", "artists")):
                for item in document.get("tracks") or []:
                    track = _track(item) if isinstance(item, dict) else None
                    if track:
                        liked.append(track)
                albums.extend(item if isinstance(item, dict) else {"name": str(item)} for item in document.get("albums") or [])
                artists.extend(item if isinstance(item, dict) else {"name": str(item)} for item in document.get("artists") or [])
                continue
        if "streaminghistory" in lowered or "endsong" in lowered or (nodes and isinstance(nodes[0], dict) and
                                                                       ("msPlayed" in nodes[0] or "ms_played" in nodes[0])):
            events.extend(event for item in nodes if isinstance(item, dict) for event in [_event(item)] if event)
            continue
        for item in nodes:
            if not isinstance(item, dict):
                continue
            playlist = _playlist(item)
            if playlist:
                playlists.append(playlist)
    library = database.import_provider_library("spotify", account_id, label, playlists, liked, albums, artists)
    listens = database.import_listens(events, "spotify", account_id=account_id, account_label=label)
    return {"account_id": account_id, "label": label, "files": len(files), **library,
            "listens_inserted": listens["inserted"], "listens_duplicates": listens["duplicates"]}
