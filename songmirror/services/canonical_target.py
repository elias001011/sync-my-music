"""Read-only transfer source for one restored canonical provider account."""

from pathlib import Path

from ..engine.targets.base import MirrorTarget


class CanonicalAccountTarget(MirrorTarget):
    tag = "database"

    def __init__(self, database, account_id: str, name: str | None = None):
        self.db = database
        self.account_id = self.source = account_id
        self.name = name or account_id
        self.cache_file = str(Path(database.path).with_name("canonical_transfer_cache.json"))

    def browse_playlists(self):
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT c.id, c.title name, c.description, c.artwork_url image,
                          cm.provider_collection_id, COUNT(ci.position) track_count
                   FROM collections c JOIN collection_mirrors cm ON cm.collection_id=c.id
                   LEFT JOIN collection_items ci ON ci.collection_id=c.id
                   WHERE cm.account_id=? GROUP BY c.id, cm.provider_collection_id
                   ORDER BY c.title COLLATE NOCASE""", (self.account_id,)).fetchall()
        return [{**dict(row), "_owned": True} for row in rows]

    def list_playlists(self):
        return {row["name"].casefold(): row for row in self.browse_playlists()}

    def playlist_id(self, playlist):
        return playlist["id"]

    def playlist_name(self, playlist):
        return playlist["name"]

    def playlist_count(self, playlist):
        return playlist["track_count"]

    def playlist_tracks(self, playlist):
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT COALESCE(st.provider_track_id, t.id) id, t.title name, ar.name artist,
                          al.title album, t.duration_ms, t.isrc, ci.added_at
                   FROM collection_items ci JOIN tracks t ON t.id=ci.track_id
                   JOIN artists ar ON ar.id=t.artist_id LEFT JOIN albums al ON al.id=t.album_id
                   LEFT JOIN service_tracks st ON st.track_id=t.id AND st.account_id=?
                   WHERE ci.collection_id=? ORDER BY ci.position""",
                (self.account_id, playlist["id"])).fetchall()
        return [{**dict(row), "artists": [row["artist"]]} for row in rows]

    def track_id(self, track):
        return track.get("id")

    def is_editable(self, playlist):
        return False
