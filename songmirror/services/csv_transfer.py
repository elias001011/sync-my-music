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
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # The common non-UTF-8 case in the wild is a spreadsheet tool's default
        # export encoding (Excel on Windows: cp1252). cp1252 accepts any byte
        # sequence, so this is a best-effort recovery, not a guess that can
        # itself raise - better than silently turning every non-ASCII
        # character into "�" (utf-8's `errors="replace"`).
        text = raw.decode("cp1252")
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
    if not tracks:
        raise ValueError("no usable tracks found in this CSV — check that it has Title/Artist-style columns")
    return {"provider_id": name, "name": name, "description": "", "tracks": tracks}


def account_id_for(label: str, name: str = "") -> str:
    """One canonical-library account slot per (label, playlist name).

    import_provider_library() treats a call as the COMPLETE state of its
    account: anything previously mirrored there and not included this time
    gets deleted (correct for an official export, which really does dump the
    whole library every time). A CSV upload is the opposite shape - one
    playlist per file - so keying the account on label alone would make a
    second different playlist under the same (default, unchanged) label
    silently delete the first one. Folding the playlist name into the id
    keeps "same label, same name" as an in-place update (matches the sibling
    import cards' "reuse the label to refresh" convention) while giving two
    different playlists their own slot even under an unedited default label.
    """
    label = (label or "CSV import").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")[:24] or "import"
    suffix = hashlib.sha256(f"{label.casefold()}\x1f{(name or '').strip().casefold()}".encode()).hexdigest()[:8]
    return f"csv:{slug}-{suffix}"


def import_csv_playlist(database, raw: bytes, name: str, label: str,
                        account_id: str | None = None) -> dict[str, Any]:
    """Import one CSV file as one new canonical playlist, reusing the same
    restore path every other provider import uses (matching, dedupe, version
    snapshots) - a CSV is just another source of the same playlist shape.

    `service_accounts` enforces one label per provider (UNIQUE(provider,
    label) - correct for "one label = one Spotify/Musify account slot"), so
    the stored label folds in the playlist name too: otherwise two CSV
    uploads left at the same default "Account label" would collide on that
    constraint. Re-importing the same (label, name) pair still composes to
    the same stored label/account id, so it stays an in-place update.
    """
    label = (label or "CSV import").strip() or "CSV import"
    name = (name or "").strip()
    account_id = account_id or account_id_for(label, name)
    if not account_id.startswith("csv:"):
        raise ValueError("CSV account id must start with csv:")
    stored_label = f"{label} — {name}" if name else label
    playlist = parse_csv_playlist(raw, name)
    counts = database.import_provider_library("csv", account_id, stored_label, playlists=[playlist])
    return {"account_id": account_id, "label": stored_label, "tracks": len(playlist["tracks"]), **counts}


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
