"""CSV playlist import/export for the canonical library.

Spotify has no native CSV export - the practical way a user gets a CSV of a
Spotify playlist is a third-party tool (e.g. Exportify) reading the public Web
API and writing a spreadsheet. This accepts that shape (and reasonable header
variations) as input, and produces a similarly-shaped file as output, so a
canonical playlist round-trips through a spreadsheet a human can edit -
independent of any one provider's connector.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from typing import Any

MAX_CSV_BYTES = 8 * 1024 * 1024
EXPORT_HEADER = ["Track Name", "Artist Name", "Album Name", "Duration (ms)", "ISRC", "Track URI", "Added At"]

# Header aliases, casefolded: several tools (Exportify, Spotify's own UI copy,
# hand-made spreadsheets) name the same column differently.
_ALIASES: dict[str, tuple[str, ...]] = {
    "track_name": ("track name", "name", "title", "song", "song name"),
    "artist_name": ("artist name(s)", "artist name", "artist", "artist(s)"),
    "album_name": ("album name", "album"),
    "duration_ms": ("duration (ms)", "duration_ms", "duration ms"),
    "isrc": ("isrc",),
    "uri": ("track uri", "spotify uri", "spotify id", "uri", "id"),
    "added_at": ("added at", "date added", "added_at"),
}


def _column_map(fieldnames: list[str] | None) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for raw in fieldnames or []:
        key = (raw or "").strip().casefold()
        for field, aliases in _ALIASES.items():
            if field not in lookup and key in aliases:
                lookup[field] = raw
    return lookup


def parse_csv_playlist(raw: bytes, name: str) -> dict[str, Any]:
    """A `playlists` entry shaped for MusicDatabase.import_provider_library():
    one playlist with its track list, read from a CSV file. A row missing
    both a track name and an artist can't be matched to anything and is
    skipped; every other column is optional."""
    if len(raw) > MAX_CSV_BYTES:
        raise ValueError(f"CSV file is larger than {MAX_CSV_BYTES // (1024 * 1024)} MiB")
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    columns = _column_map(reader.fieldnames)

    def get(row: dict, field: str) -> str:
        col = columns.get(field)
        return (row.get(col) or "").strip() if col else ""

    tracks = []
    for row in reader:
        track_name, artist_name = get(row, "track_name"), get(row, "artist_name")
        if not track_name and not artist_name:
            continue
        uri = get(row, "uri")
        duration_text = get(row, "duration_ms")
        tracks.append({
            "track_name": track_name or "Unknown track",
            "artist_name": artist_name or "Unknown artist",
            "release_name": get(row, "album_name"),
            "isrc": get(row, "isrc") or None,
            "provider_track_id": uri.rsplit(":", 1)[-1] if uri else "",
            "uri": uri,
            "duration_ms": int(duration_text) if duration_text.isdigit() else None,
            "added_at": get(row, "added_at") or None,
        })
    return {"provider_id": name, "name": name, "description": "", "tracks": tracks}


def account_id_for(label: str) -> str:
    label = (label or "CSV import").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")[:24] or "import"
    suffix = hashlib.sha256(label.casefold().encode()).hexdigest()[:8]
    return f"csv:{slug}-{suffix}"


def import_csv_playlist(database, raw: bytes, name: str, label: str,
                        account_id: str | None = None) -> dict[str, Any]:
    """Import one CSV file as one new canonical playlist, reusing the same
    restore path every other provider import uses (matching, dedupe, version
    snapshots) - a CSV is just another source of the same playlist shape."""
    account_id = account_id or account_id_for(label)
    if not account_id.startswith("csv:"):
        raise ValueError("CSV account id must start with csv:")
    playlist = parse_csv_playlist(raw, name)
    counts = database.import_provider_library("csv", account_id, label, playlists=[playlist])
    return {"account_id": account_id, "label": label, "tracks": len(playlist["tracks"]), **counts}


def export_collection_csv(database, collection_id: str) -> tuple[str, bytes]:
    """(filename, csv bytes) for one canonical playlist's tracks, ordered by
    position. Raises ValueError if the collection doesn't exist."""
    with database.connect() as conn:
        collection = conn.execute("SELECT title FROM collections WHERE id=?", (collection_id,)).fetchone()
        if not collection:
            raise ValueError("playlist not found")
        rows = conn.execute(
            """SELECT t.title, ar.name artist, COALESCE(al.title, '') album, t.duration_ms, t.isrc,
                      COALESCE((SELECT st.provider_track_id FROM service_tracks st
                                WHERE st.track_id=t.id AND st.provider_track_id != '' LIMIT 1), '') uri,
                      ci.added_at
               FROM collection_items ci JOIN tracks t ON t.id=ci.track_id
               JOIN artists ar ON ar.id=t.artist_id LEFT JOIN albums al ON al.id=t.album_id
               WHERE ci.collection_id=? ORDER BY ci.position""",
            (collection_id,)).fetchall()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_HEADER)
    for row in rows:
        writer.writerow([row["title"], row["artist"], row["album"], row["duration_ms"] or "",
                         row["isrc"] or "", row["uri"], row["added_at"] or ""])
    safe_name = re.sub(r"[^\w.-]+", "_", collection["title"]).strip("_") or "playlist"
    return f"{safe_name}.csv", buffer.getvalue().encode("utf-8-sig")
