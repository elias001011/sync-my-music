"""Spotify as a writable mirror peer (N-way sync only).

In one-way mode Spotify is just the source and this target isn't built. In
N-way mode it becomes a first-class peer: the same reconcile that edits Apple
and YouTube Music also adds/removes on Spotify. Reads reuse the helpers in
spotify.py; writes go through spotipy's playlist-modify endpoints and therefore
need the modify scopes (see spotify.client(writable=True)).
"""

import spotipy

from .. import archive, spotify, spotify_cookie
from ..config import polite_sleep, spotify_write_backend
from ..matching import normalize_text, romanized, score_candidate, track_key
from .base import MirrorTarget, TargetAuthError
from .provider_utils import source_playlist_details


def _uri(track_id):
    return track_id if str(track_id).startswith("spotify:") else f"spotify:track:{track_id}"


# account_id:playlist_id -> image url. Best-effort, in-process only (not worth a
# persisted table for a cosmetic field): verified live that the pathfinder
# document cookie-mode browsing reads (fetchPlaylistContents, shared with the
# rootlist detail lookup) carries NO playlist-level image field, only each
# track's own album art — so there is nothing to merely "extract" here.
_image_cache: dict[str, str] = {}


class SpotifyTarget(MirrorTarget):
    name = "Spotify"
    tag = "spotify"
    source = "spotify"

    def __init__(self, sp, cache_file, sync_peer=False, songs=None, account=None, config=None):
        self._sp = sp
        self.cache_file = cache_file
        self._me = None
        self.account_id = account or "spotify:default"
        self._config = config
        # True when built as an N-way reconcile peer (not a one-off transfer). In
        # cookie mode this makes the read backfill ISRC and FAIL CLOSED if it can't —
        # so a sync never matches Spotify on name/artist alone and churns playlists.
        self._sync_peer = sync_peer
        # The songs archive (sqlite conn) — a persistent ISRC cache so the peer read
        # fetches each track's ISRC from /tracks once ever, not every pass (see
        # playlist_tracks). None for transfers/browse, which don't need ISRC.
        self._songs = songs

    def _backend(self):
        """The write backend for THIS account (its own config wins; the legacy
        path falls back to the process env)."""
        return spotify_write_backend(self._config)

    def _user(self):
        if self._me is None:
            if self._backend() == "cookie":
                self._me = spotify_cookie.current_user_id(account_id=self.account_id)
            else:
                self._me = spotify._retry(lambda: self._sp.current_user(), "current_user")["id"]
        return self._me

    def _write(self, fn, what):
        """Run a mutation; map an auth/scope rejection to the fail-closed path."""
        try:
            return spotify._retry(fn, what)
        except spotipy.SpotifyException as e:
            if e.http_status in (401, 403):
                raise TargetAuthError(
                    f"Spotify rejected {what} ({e.http_status}). N-way mode needs the playlist-modify "
                    "scopes — delete the token cache (data/spotify_token_cache) and re-run the OAuth flow."
                ) from e
            raise

    # -- MirrorTarget ----------------------------------------------------------
    def list_playlists(self):
        if self._backend() == "cookie":
            return spotify_cookie.playlists_by_name(account_id=self.account_id)
        return spotify.playlists_by_name(self._sp)

    def browse_playlists(self):
        # Un-deduped, with `_owned` — so browse lists (and the inherited find_playlist
        # scans) every playlist, including a followed one that shares a name with an
        # owned one. list_playlists() name-dedupes for the sync engine and would hide it.
        if self._backend() == "cookie":
            return self._backfill_cover_art(spotify_cookie.all_playlists(account_id=self.account_id))
        return spotify.all_playlists(self._sp)

    def _backfill_cover_art(self, playlists):
        """Cookie-mode playlists carry no cover art (confirmed: the web-player
        read has no image field for the playlist itself). When this account also
        has an OAuth app configured, its READ access is unaffected by the
        dev-mode write gate (only playlist-modify-* content endpoints are
        gated) — borrow it just for the missing thumbnails. Silently does
        nothing if no OAuth app is set up; a missing cover is cosmetic, never
        worth failing a browse over."""
        missing = [pl for pl in playlists if f"{self.account_id}:{pl['id']}" not in _image_cache]
        if missing:
            try:
                sp = spotify.client(config=self._config)
            except (RuntimeError, TargetAuthError):
                sp = None
            if sp is not None:
                for pl in missing:
                    try:
                        images = spotify._retry(
                            lambda pid=pl["id"]: sp.playlist(pid, fields="images"), "playlist cover art"
                        ).get("images") or []
                    except Exception:
                        continue
                    _image_cache[f"{self.account_id}:{pl['id']}"] = (images[0] or {}).get("url", "") if images else ""
        for pl in playlists:
            url = _image_cache.get(f"{self.account_id}:{pl['id']}")
            if url:
                pl["images"] = [{"url": url}]
        return playlists

    def is_editable(self, playlist):
        owner = (playlist.get("owner") or {}).get("id")
        return owner is None or owner == self._user()

    def playlist_count(self, playlist):
        return spotify.track_total(playlist)

    def create(self, sp_playlist):
        name, desc = source_playlist_details(sp_playlist)
        if self._backend() == "cookie":
            pl = spotify_cookie.create(name, public=False, description=desc, account_id=self.account_id)
        else:
            pl = self._write(
                lambda: self._sp.user_playlist_create(self._user(), name, public=False, description=desc),
                "create playlist")
        polite_sleep(1.0)
        return pl

    def playlist_tracks(self, playlist):
        # In cookie mode the official track read 403s under Development Mode (and a
        # just-created private playlist has no public scraper fallback), so read via
        # the same web-player path the writes use. As an N-way peer (sync_peer), the
        # read backfills ISRC and fails closed if it can't — so a bidirectional sync
        # never matches Spotify on name/artist alone and churns.
        if self._backend() == "cookie":
            known = None
            if self._sync_peer and self._songs is not None:
                known = lambda ids: archive.get_isrcs(self._songs, self.state_key, ids)  # noqa: E731
            return spotify_cookie.playlist_tracks(
                playlist["id"], require_isrc=self._sync_peer, known_isrc=known,
                account_id=self.account_id)
        return spotify.playlist_tracks(self._sp, playlist["id"])

    def track_id(self, track):
        return track.get("id")

    def resolve(self, track, cache):
        primary = track["artists"][0] if track["artists"] else ""
        if not f"{track['name']} {primary}".strip():
            return None, None
        key = track_key(track["name"], " ".join(track["artists"]))
        if key in cache["search"]:
            return cache["search"][key], "search"
        best, method = self._search(track, primary)
        cache["search"][key] = best
        cache["dirty"] = True
        polite_sleep(0.3)
        return best, method

    def _search(self, track, primary):
        isrc = track.get("isrc")
        if isrc:  # the hard cross-walk when the originating provider carried an ISRC
            best = self._best(track, self._query(f"isrc:{isrc}"))
            if best:
                return best, "isrc"
        base = f"{track['name']} {primary}".strip()
        queries = [f'track:{track["name"]} artist:{primary}'.strip(), base]
        rom = f"{romanized(track['name'])} {romanized(primary)}".strip()
        if rom and rom != normalize_text(base):
            queries.append(rom)
        for q in queries:
            best = self._best(track, self._query(q))
            if best:
                return best, "search"
        return None, None

    def _query(self, q):
        if self._backend() == "cookie":
            try:
                return spotify_cookie.search_tracks(q, limit=8, account_id=self.account_id)
            except TargetAuthError:
                raise
            except Exception:
                return []
        try:
            res = spotify._retry(lambda: self._sp.search(q=q, type="track", limit=8), "search")
        except spotipy.SpotifyException:
            return []
        return (res.get("tracks") or {}).get("items", [])

    def _best(self, track, items):
        best_id, best_score = None, -1.0
        for it in items:
            arts = [a.get("name", "") for a in it.get("artists", []) if a.get("name")]
            score, ok = score_candidate(track["name"], track["artists"], track["duration_ms"],
                                        it.get("name", ""), ", ".join(arts), it.get("duration_ms"))
            if ok and score > best_score:
                best_id, best_score = it.get("id"), score
        return best_id

    def add(self, playlist, target_ids):
        if self._backend() == "cookie":
            spotify_cookie.add(playlist["id"], target_ids, account_id=self.account_id)  # one at a time, in order (see spotify_cookie.add)
            return
        for tid in target_ids:  # one at a time preserves date-added order
            self._write(lambda t=tid: self._sp.playlist_add_items(playlist["id"], [_uri(t)]), "add")
            polite_sleep(0.3)

    def remove(self, playlist, track):
        tid = self.track_id(track)
        if not tid:
            return
        if self._backend() == "cookie":
            spotify_cookie.remove(playlist["id"], [tid], account_id=self.account_id)
            return
        self._write(lambda: self._sp.playlist_remove_all_occurrences_of_items(playlist["id"], [_uri(tid)]), "remove")
        polite_sleep(0.3)

    def remove_occurrences(self, playlist, positioned):
        if self._backend() == "cookie":
            spotify_cookie.remove_positions(playlist["id"], [pos for pos, _ in positioned], account_id=self.account_id)
            return
        # Position-addressed removal against the read-time snapshot: with the
        # same uri present twice, remove() would drop BOTH copies. All positions
        # are evaluated against the one snapshot, so batches never shift indexes.
        items = [{"uri": _uri(self.track_id(raw)), "positions": [pos]}
                 for pos, raw in positioned if self.track_id(raw)]
        snapshot = playlist.get("snapshot_id")
        for i in range(0, len(items), 100):
            self._write(lambda chunk=items[i:i + 100]: self._sp.playlist_remove_specific_occurrences_of_items(
                playlist["id"], chunk, snapshot_id=snapshot), "remove occurrences")
            polite_sleep(0.3)
