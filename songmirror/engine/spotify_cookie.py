"""Cookie (sp_dc) web-player backend for Spotify.

A self-hosted Spotify developer app in Development Mode is refused (403) by the
official API on the *content* surface — creating playlists and adding/removing
playlist items — even with the modify scopes granted. Reads already have a
web-player fallback (`spotify_web.py`); this is the matching path for writes.

It authenticates as Spotify's own first-party web client via the `sp_dc` cookie
(spotify_scraper mints the bearer, TOTP and all), which is not subject to the
dev-app gate. Item add/remove go through the web-player GraphQL API
("pathfinder"); playlist creation goes through the official REST endpoint with
the same first-party token. In cookie mode reads, catalog search and writes all
route here, so a self-hosted installation does not also require an OAuth app.

Fragility (why the self-heal exists): pathfinder persisted-query hashes rotate
on each web-player release and a stale one is rejected as PersistedQueryNotFound.
`_refresh_hashes` re-scrapes the current hashes from the live web-player bundle
on that error, so a rotation self-heals instead of hard-failing. The `sp_dc`
cookie itself lasts about a year; the connector surfaces when it needs renewing.
"""

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from .config import REQUEST_TIMEOUT, polite_sleep
from .logs import log, log_note, log_warn
from .targets.base import TargetAuthError

_PATHFINDER = "https://api-partner.spotify.com/pathfinder/v2/query"
_SPCLIENT = "https://spclient.wg.spotify.com"   # web-player backend — no api.spotify.com rate limit / dev-mode gate
_API = "https://api.spotify.com/v1"             # official REST — the batch /tracks?ids ISRC lookup (client-credentials app token; see _track_isrcs)
_WEB = "https://open.spotify.com/"
# Sent as spotify-app-version; loosely paired with the persisted-query hashes and
# refreshed alongside them. A slightly stale value still resolves in practice.
_APP_VERSION = "1.2.95.312.gda5d7e47"
# A browser User-Agent is required — Spotify's edge 403s the default python-requests one.
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/152.0"

# Persisted-query sha256 hashes, keyed by the document they belong to. add/remove/
# move share one mutation document (op selected by name); the fetch* reads share
# another. Seeded with known-good values; _refresh_hashes rewrites them in place
# when a call reports the hash is unknown (a web-player release rotated them).
_HASHES = {
    "playlist_mut": "47b2a1234b17748d332dd0431534f22450e9ecbb3d5ddcdacbd83368636a0990",
    "playlist_read": "a65e12194ed5fc443a1cdebed5fabe33ca5b07b987185d63c72483867ad13cb4",
    "profile": "b197b5adb4b761690f76ad9d9fb278c14c14e7331f357c04a56e7001af7106e0",
    "search": "eff59fa0a3d026b88b56fddbcf4bdfa16a186b8175a5c1a358c072e053c2e5b0",
}
# Which operation name maps to which hashed document — also drives the re-scrape.
_OP_DOC = {
    "addToPlaylist": "playlist_mut", "removeFromPlaylist": "playlist_mut",
    "fetchPlaylistContents": "playlist_read", "profileAttributes": "profile",
    "searchDesktop": "search",
}

_provider = None      # cached spotify_scraper CookieTokenProvider (lazy)
_provider_key = None
_uid_by_cookie = {}   # cookie hash -> cached account user id (rootlist filing)
_isrc_cache = {}      # track_id -> isrc|None, backfilled from /tracks (see _track_isrcs)
_playlist_count_cache = {}  # (account_id, playlist_id) -> (revision_id, total); rootlist omits totals


def _slug(account_id):
    """Filesystem-safe per-account suffix: None/`spotify:default` maps to the
    legacy shared file; a named account gets a stable hash suffix so two
    accounts never share a cookie file."""
    if not account_id:
        return None
    provider, _, rest = str(account_id).partition(":")
    if rest in ("", "default"):
        return None
    return f"{provider}-{hashlib.sha256(account_id.encode()).hexdigest()[:8]}"


def configured(account_id=None):
    """True when an sp_dc cookie is available (env or the stored file) for the
    account (default: the shared default account file)."""
    return bool(_sp_dc(soft=True, account_id=account_id))


def sp_dc_path(account_id=None):
    """Where the account's sp_dc cookie is stored. Under SONGMIRROR_DATA_DIR so
    it lands on the same persistent volume as the other secrets (Docker points
    it at /data). Named accounts get their own 0600 file — two accounts can
    never share (or overwrite) a cookie."""
    explicit = os.getenv("SPOTIFY_SP_DC_FILE")
    if explicit and not account_id:
        return explicit
    slug = _slug(account_id)
    name = "spotify_sp_dc.private" if slug is None else f"spotify_sp_dc.{slug}.private"
    return os.path.join(os.getenv("SONGMIRROR_DATA_DIR") or "data", name)


def _sp_dc(soft=False, account_id=None):
    v = os.getenv("SPOTIFY_SP_DC")
    if v and not account_id:
        return v.strip()
    path = sp_dc_path(account_id)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        if soft:
            return None
        label = account_id or "default"
        raise TargetAuthError(
            f"Spotify cookie mode is on but account '{label}' has no sp_dc cookie — "
            "paste it on the Accounts page (or set SPOTIFY_SP_DC).")


def _prov(account_id=None):
    global _provider, _provider_key
    cookie = _sp_dc(account_id=account_id)
    key = hashlib.sha256(cookie.encode()).hexdigest()
    if _provider is None or _provider_key != key:
        # Imported lazily: spotify_scraper is only pulled in when cookie mode runs.
        from spotify_scraper.auth.cookies import CookieTokenProvider
        from spotify_scraper.http.transport import HttpxTransport
        _provider = CookieTokenProvider(HttpxTransport(), cookie)
        _provider_key = key
        _uid_by_cookie.clear()
    return _provider


def _token(account_id=None):
    try:
        return _prov(account_id).token()
    except Exception as e:  # AuthenticationError (bad/rotated cookie) or transport
        raise TargetAuthError(
            f"Spotify cookie rejected ({e}). Re-paste the sp_dc cookie on the Accounts page.") from e


def _headers(account_id=None):
    return {
        "authorization": f"Bearer {_token(account_id)}",
        "app-platform": "WebPlayer",
        "spotify-app-version": _APP_VERSION,
        "Origin": "https://open.spotify.com",
        "Referer": _WEB,
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json",
        "User-Agent": _UA,
    }


def _persisted_missing(body):
    for err in (body.get("errors") or []):
        msg = (err.get("message") or "") if isinstance(err, dict) else str(err)
        if "PersistedQueryNotFound" in msg:
            return True
    return False


def _pf(op, variables, account_id=None):
    """Run a pathfinder operation for one account, self-healing a stale hash and
    a stale token.

    One retry each: a 401 means the bearer expired (drop it and re-mint); a
    PersistedQueryNotFound means the web player rotated its hashes (re-scrape and
    retry). Anything else surfaces as a fatal TargetAuthError so a pass never
    half-writes."""
    doc = _OP_DOC[op]
    refreshed = False
    for _ in range(3):
        body = {"variables": variables, "operationName": op,
                "extensions": {"persistedQuery": {"version": 1, "sha256Hash": _HASHES[doc]}}}
        r = requests.post(_PATHFINDER, headers=_headers(account_id), data=json.dumps(body), timeout=REQUEST_TIMEOUT)
        if r.status_code == 401:
            _prov(account_id).invalidate()
            continue
        try:
            payload = r.json() if r.content else {}
        except ValueError:
            payload = {}
        if _persisted_missing(payload) and not refreshed:
            refreshed = True
            _refresh_hashes(account_id)
            continue
        if r.status_code == 403:
            raise TargetAuthError(
                f"Spotify refused {op} (403) for the cookie account — the sp_dc account must own "
                "the playlist. Check you pasted the right account's cookie.")
        r.raise_for_status()
        if payload.get("errors"):
            raise TargetAuthError(f"Spotify pathfinder {op} error: {payload['errors']}")
        return payload.get("data") or {}
    raise TargetAuthError(f"Spotify pathfinder {op} failed after token/hash refresh.")


def _refresh_hashes(account_id=None):
    """Re-scrape the current persisted-query hashes from the live web-player
    bundle. Best-effort: on any failure the seeded hashes stay and the caller's
    retry surfaces the original error."""
    try:
        cookie = {"Cookie": f"sp_dc={_sp_dc(account_id=account_id)}"}
        ua = {"User-Agent": _UA}
        shell = requests.get(_WEB, headers={**ua, **cookie}, timeout=REQUEST_TIMEOUT).text
        urls = set(re.findall(r"https://open\.spotifycdn\.com/cdn/build/web-player/[^\"']+\.js", shell))
        blob = "".join(requests.get(u, headers=ua, timeout=REQUEST_TIMEOUT).text for u in urls)
        for op, doc in _OP_DOC.items():
            m = re.search(rf'\.l\("{op}","(?:mutation|query)","([a-f0-9]{{64}})"', blob)
            if m:
                _HASHES[doc] = m.group(1)
        log_note("refreshed Spotify web-player query hashes", tag="spotify")
    except Exception as e:
        log_warn(f"could not refresh Spotify web-player hashes ({e!r})", tag="spotify")


# -- public write operations --------------------------------------------------

def _puri(playlist):
    pid = playlist if isinstance(playlist, str) else playlist.get("id", "")
    return pid if str(pid).startswith("spotify:") else f"spotify:playlist:{pid}"


def _turi(track_id):
    return track_id if str(track_id).startswith("spotify:") else f"spotify:track:{track_id}"


def add(playlist, track_ids, account_id=None):
    """Append tracks one at a time (bottom, in order). One track per call so each
    gets a distinct date-added — a single batched add stamps them all identically,
    which scrambles the destination's "Recently added" view. Mirrors the OAuth /
    Apple sequential-add pattern."""
    puri = _puri(playlist)
    for tid in track_ids:
        _pf("addToPlaylist", {"playlistUri": puri, "playlistItemUris": [_turi(tid)],
                              "newPosition": {"moveType": "BOTTOM_OF_PLAYLIST", "fromUid": None}},
            account_id=account_id)
        polite_sleep(0.3)


def _content_items(playlist, account_id=None):
    """Yield every raw playlist item (paginated) from the web-player read."""
    puri, offset = _puri(playlist), 0
    while True:
        data = _pf("fetchPlaylistContents", {"uri": puri, "offset": offset, "limit": 100},
                   account_id=account_id)
        page = (data.get("playlistV2") or {}).get("content") or {}
        items = page.get("items") or []
        raw_total = page.get("totalCount")
        if raw_total is None:
            raise RuntimeError(
                "Spotify playlist read incomplete: page did not include totalCount"
            )
        total = int(raw_total)
        if not items:
            if offset < total:
                raise RuntimeError(
                    f"Spotify playlist read incomplete: stopped at {offset} of {total} items"
                )
            return
        yield from items
        offset += len(items)
        if offset >= total:
            return


def contents(playlist, account_id=None):
    """[{uid, uri}] for every item — `uid` is the per-item handle remove needs
    (the mutation deletes by item uid, not track uri)."""
    return [{"uid": it.get("uid"), "uri": ((it.get("itemV2") or {}).get("data") or {}).get("uri")}
            for it in _content_items(playlist, account_id)]


def _first_text(node, keys):
    """Find a scalar in Spotify's intentionally unstable rootlist JSON shape."""
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in node.values():
            found = _first_text(value, keys)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _first_text(value, keys)
            if found:
                return found
    return ""


def _rootlist_entries(payload):
    """Yield playlist-bearing nodes, including playlists nested in folders."""
    if isinstance(payload, dict):
        uri = payload.get("uri")
        if isinstance(uri, str) and uri.startswith("spotify:playlist:"):
            yield payload
            return
        for value in payload.values():
            yield from _rootlist_entries(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _rootlist_entries(value)


def _playlist_details(uri, account_id=None):
    data = _pf("fetchPlaylistContents", {"uri": uri, "offset": 0, "limit": 1}, account_id=account_id)
    playlist = data.get("playlistV2") or {}
    name = _first_text(playlist.get("name"), ("text", "name")) or _first_text(playlist, ("name",))
    owner_node = playlist.get("ownerV2") or playlist.get("owner") or {}
    owner_uri = _first_text(owner_node, ("uri", "username", "id"))
    owner = owner_uri.rsplit(":", 1)[-1] if owner_uri else ""
    return name, owner, int((playlist.get("content") or {}).get("totalCount") or 0)


def all_playlists(account_id=None):
    """Every owned or saved playlist in the web player's recursive rootlist."""
    url = f"{_SPCLIENT}/playlist/v2/user/{current_user_id(account_id)}/rootlist"
    r = requests.get(url, headers=_spc_headers(account_id), timeout=REQUEST_TIMEOUT)
    if r.status_code in (401, 403):
        raise TargetAuthError("Spotify cookie expired or was revoked. Paste a new sp_dc cookie on Accounts.")
    r.raise_for_status()
    out, seen = [], set()
    for entry in _rootlist_entries(r.json()):
        uri = entry["uri"]
        if uri in seen:
            continue
        seen.add(uri)
        name = _first_text(entry.get("attributes") or entry.get("metadata") or {}, ("name", "title"))
        owner = _first_text(entry.get("owner") or {}, ("username", "id"))
        total = 0
        if not name or not owner:
            detail_name, detail_owner, total = _playlist_details(uri, account_id)
            name, owner = name or detail_name, owner or detail_owner
        name = name or f"Playlist {uri.rsplit(':', 1)[-1][:8]}"
        out.append({
            "id": uri.rsplit(":", 1)[-1], "uri": uri, "name": name,
            "owner": {"id": owner}, "tracks": {"total": total},
            "_owned": not owner or owner == current_user_id(account_id),
        })
    return out


def playlists_by_name(account_id=None):
    result = {}
    for playlist in all_playlists(account_id):
        key = playlist["name"].strip().casefold()
        if key not in result or playlist.get("_owned"):
            result[key] = playlist
    return result


def _playlist_track_total(playlist, account_id=None):
    """One playlist's item total through the signed-in web-player API.

    The rootlist projection does not reliably include a count. Its revisionId
    does change with playlist contents, so it is a safe cache validator for
    this lightweight limit=1 lookup. The cache key is account-scoped so two
    Spotify accounts' counts never collide.
    """
    pid = str(playlist.get("id") or "")
    revision = playlist.get("snapshot_id") or playlist.get("revisionId")
    cache_key = (account_id, pid)
    hit = _playlist_count_cache.get(cache_key)
    if pid and revision is not None and hit and hit[0] == revision:
        return hit[1]
    try:
        data = _pf("fetchPlaylistContents", {
            "uri": playlist.get("uri") or _puri(pid),
            "offset": 0,
            "limit": 1,
        }, account_id=account_id)
        raw_total = ((data.get("playlistV2") or {}).get("content") or {}).get("totalCount")
        if raw_total is None:
            raise RuntimeError("Spotify playlist count response did not include totalCount")
        count = int(raw_total)
    except Exception:
        return hit[1] if hit else None
    if pid and revision is not None:
        _playlist_count_cache[cache_key] = (revision, count)
    return count


def hydrate_playlist_counts(playlists, account_id=None):
    """Attach Web-API-shaped ``items.total`` values to rootlist rows.

    Counts are browse metadata, not required for sync correctness. Fetch cache
    misses concurrently so a large library does not turn into a long serial
    request train; individual failures leave that card's count unknown while
    preserving the playlist list itself.
    """
    rows = list(playlists)
    if not rows:
        return rows
    workers = min(6, len(rows))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        counts = list(pool.map(lambda pl: _playlist_track_total(pl, account_id), rows))
    for playlist, count in zip(rows, counts):
        if count is not None:
            playlist["items"] = {"total": count}
    return rows


def search_tracks(query, limit=8, account_id=None):
    """Search the catalog through the Web Player pathfinder document."""
    data = _pf("searchDesktop", {
        "searchTerm": str(query), "offset": 0, "limit": max(1, min(int(limit), 50)),
        "numberOfTopResults": 5, "includeAudiobooks": False,
    }, account_id=account_id)
    tracks = ((data.get("searchV2") or {}).get("tracks") or {}).get("items") or []
    out = []
    for wrapper in tracks:
        raw = wrapper.get("item") or wrapper
        track = raw.get("data") or raw
        uri = track.get("uri") or ""
        if not uri.startswith("spotify:track:"):
            continue
        artists_node = track.get("artists") or {}
        artist_items = artists_node.get("items") if isinstance(artists_node, dict) else artists_node
        artists = []
        for item in artist_items or []:
            artist = item.get("profile") or item.get("data") or item
            name = artist.get("name") if isinstance(artist, dict) else None
            if name:
                artists.append({"name": name})
        duration = track.get("trackDuration") or {}
        out.append({
            "id": uri.rsplit(":", 1)[-1], "uri": uri,
            "name": track.get("name") or "", "artists": artists,
            "duration_ms": duration.get("totalMilliseconds") or track.get("duration_ms"),
            "album": {"name": (track.get("albumOfTrack") or {}).get("name") or ""},
        })
    return out


def _track_isrcs(ids):
    """{track_id: isrc|None} from the official catalog via a CLIENT-CREDENTIALS APP token on
    the BATCH /tracks?ids endpoint (50 ids/call). Cached in-process; only unknown ids fetch.

    Token+endpoint choice — every alternative tested live:
      • OAuth user token → 403 on /tracks (dev-mode gate).
      • cookie (first-party) token → does batch, but rate-limits PER-ACCOUNT and, retried
        into a 429, escalates into an hours-long penalty box. Kept for WRITES, not this.
      • APP token → a SEPARATE rate bucket from the user account, so ISRC reads never touch
        the per-account limit. A DEV-MODE app 403s on batch and caps ~300/24h on single; an
        EXTENDED-QUOTA app does the 50-ids batch — a whole library in ~len/50 calls. The
        SPOTIFY_ISRC_CLIENTS pool supplies batch-capable app creds.

    Three outcomes per batch, and the distinction between the last two is the point:
      • 429 rotates to the next pool app; when the LAST app 429s it raises, so the sync
        fails closed rather than matching blind. No retry INTO a 429 on the same app
        (that's what earns a penalty box).
      • 403 means that app cannot serve this endpoint at all, which is not something a
        retry or a wait fixes. Every pool app is tried, then _isrc_singles carries the
        batch. A refused app must not take the sync down: an expired Premium on the
        pool app's owner account, or no extended-quota app at all, is a slower path,
        not a broken one.
    With the DB cache (playlist_tracks' known_isrc), steady-state fetches trend to zero."""
    from . import spotify
    want = [i for i in dict.fromkeys(ids) if i and i not in _isrc_cache]
    napps = spotify.isrc_app_count()
    for i in range(0, len(want), 50):
        chunk = want[i:i + 50]
        for app_idx in range(napps):
            r = requests.get(f"{_API}/tracks", params={"ids": ",".join(chunk)},
                             headers={"Authorization": f"Bearer {spotify.app_token(app_idx)}", "User-Agent": _UA},
                             timeout=REQUEST_TIMEOUT)
            if r.status_code == 403:
                continue   # this app can't batch at all: next app, then the single fallback
            if r.status_code == 429 and app_idx < napps - 1:
                continue   # this app is rate-limited — fail over to the next pool app
            r.raise_for_status()   # last-app 429 / other error -> HTTPError -> fail-closed upstream
            for t in (r.json().get("tracks") or []):
                if t:
                    _isrc_cache[t["id"]] = (t.get("external_ids") or {}).get("isrc")
            break
        else:
            _isrc_singles(chunk)   # every app refused the batch endpoint
        if i + 50 < len(want):
            polite_sleep(0.5)   # space multi-batch backfills; a single-batch pass doesn't sleep
    return {i: _isrc_cache.get(i) for i in ids}


_singles_warned = False   # the degraded-path warning is once per process, not per batch
_singles_used = 0         # tracks served by the degraded path, drained per pass


def take_singles_used():
    """How many tracks the per-track ISRC path served since the last read, and
    resets. The runner drains this into the pass summary so the dashboard can say
    the sync is on the slow path and how much of the daily budget it spent. A
    counter rather than a return value because the lookup sits several layers
    inside a provider read, with nothing summary-shaped to thread it back through."""
    global _singles_used
    n, _singles_used = _singles_used, 0
    return n


def _isrc_singles(ids):
    """Fill the ISRC cache one track at a time via /tracks/{id} with the PRIMARY app's
    token. That endpoint is not behind the Development-Mode gate, so it still answers
    when every pool app is refused on the batch endpoint.

    ponytail: one call per track against a dev-mode app's ~300/24h budget, and no cap
    of its own. The budget holds because only tracks the songs DB has never seen reach
    here; a first-run backfill of a large library will exhaust it and 429, which raises
    and fails the sync closed. Connecting an extended-quota ISRC app restores batching
    and lifts the ceiling."""
    from . import spotify

    global _singles_warned, _singles_used
    if not _singles_warned:
        _singles_warned = True
        log_warn("batch ISRC lookup refused; falling back to one call per track. Connect an "
                 "extended-quota ISRC lookup app (Accounts > Spotify) to restore batching.",
                 tag="spotify")
    for tid in ids:
        r = requests.get(f"{_API}/tracks/{tid}",
                         headers={"Authorization": f"Bearer {spotify.main_app_token()}", "User-Agent": _UA},
                         timeout=REQUEST_TIMEOUT)
        r.raise_for_status()   # includes a 429 once the daily budget is spent -> fail closed
        _isrc_cache[tid] = (r.json().get("external_ids") or {}).get("isrc")
        _singles_used += 1     # counted per call actually spent, not per track asked for
        polite_sleep(0.2)


def playlist_tracks(playlist, require_isrc=False, known_isrc=None, account_id=None):
    """Full track dicts (the shape spotify.playlist_tracks yields) via pathfinder —
    works for private owned playlists the dev-mode official API 403s, and returns []
    for a just-created empty playlist. The pathfinder payload carries no ISRC (confirmed
    absent from the entire web-player surface); with require_isrc (set for N-way sync
    reads) it is backfilled so cross-provider matching stays reliable, and a hard lookup
    failure raises so the sync fails closed instead of matching on name/artist alone.

    known_isrc(ids) -> {id: isrc}, when given, supplies already-known ISRCs (the
    persisted songs-DB cache) so only genuinely-new tracks hit the rate-limited /tracks
    endpoint — the difference between "fetch every track every pass" (which earns a
    penalty box) and "fetch each track once, ever". Transfers pass neither flag — a
    same-provider copy uses the track id directly."""
    out = []
    for it in _content_items(playlist, account_id):
        t = (it.get("itemV2") or {}).get("data") or {}
        uri = t.get("uri") or ""
        if not uri.startswith("spotify:track:"):
            continue  # local file / episode / unavailable — excluded like the official read
        artists = [(a.get("profile") or {}).get("name", "") for a in ((t.get("artists") or {}).get("items") or [])]
        out.append({
            "id": uri.rsplit(":", 1)[-1],
            "isrc": None,
            "name": t.get("name", "") or "",
            "artists": [a for a in artists if a] or [""],
            "album": (t.get("albumOfTrack") or {}).get("name"),
            "duration_ms": (t.get("trackDuration") or {}).get("totalMilliseconds"),
            "added_at": (it.get("addedAt") or {}).get("isoString") or "",
        })
    if require_isrc and out:
        ids = [t["id"] for t in out]
        cached = known_isrc(ids) if known_isrc else {}
        fetched = _track_isrcs([i for i in ids if not cached.get(i)])
        for t in out:
            t["isrc"] = cached.get(t["id"]) or fetched.get(t["id"])
    return out


def remove(playlist, track_ids, account_id=None):
    """Remove every occurrence of the given tracks. Resolves track uris to item
    uids via a contents read, since the mutation deletes by uid."""
    want = {_turi(t) for t in track_ids}
    uids = [c["uid"] for c in contents(playlist, account_id) if c["uri"] in want and c["uid"]]
    if uids:
        _pf("removeFromPlaylist", {"playlistUri": _puri(playlist), "uids": uids}, account_id=account_id)


def remove_positions(playlist, positions, account_id=None):
    """Remove the items at these 0-based positions. ponytail: evaluated against a
    fresh contents read, not the caller's read-time snapshot — acceptable because
    reconcile position-removes within one short pass; revisit if drift bites."""
    items = contents(playlist, account_id)
    uids = [items[p]["uid"] for p in positions if 0 <= p < len(items) and items[p]["uid"]]
    if uids:
        _pf("removeFromPlaylist", {"playlistUri": _puri(playlist), "uids": uids}, account_id=account_id)


def _spc_headers(account_id=None):
    return {"authorization": f"Bearer {_token(account_id)}", "User-Agent": _UA,
            "Content-Type": "application/json;charset=UTF-8", "Accept": "application/json"}


def current_user_id(account_id=None):
    """The cookie account's user id, read once via pathfinder (not api.spotify.com)
    and cached per cookie for the process."""
    cookie = _sp_dc(account_id=account_id)
    key = hashlib.sha256(cookie.encode()).hexdigest()
    uid = _uid_by_cookie.get(key)
    if uid is None:
        prof = ((_pf("profileAttributes", {}, account_id).get("me") or {}).get("profile") or {})
        uid = prof.get("username") or ""
        if not uid:
            raise TargetAuthError("Couldn't read the Spotify account id from the cookie session.")
        _uid_by_cookie[key] = uid
    return uid


def validate_session(account_id=None):
    """Validate the stored cookie and return its account id."""
    return current_user_id(account_id)


def _rootlist_add(playlist_uri, account_id=None):
    """File a just-created playlist into the account's rootlist so it shows in the
    library (spclient create leaves it unfiled). Best-effort: the playlist already
    has its tracks, so a rootlist hiccup shouldn't fail the transfer — just log it."""
    try:
        rl = f"{_SPCLIENT}/playlist/v2/user/{current_user_id(account_id)}/rootlist"
        rev = requests.get(rl, headers=_spc_headers(account_id), timeout=REQUEST_TIMEOUT).json()["revision"]
        body = {"baseRevision": rev, "wantResultingRevisions": False, "wantSyncResult": False, "nonces": [],
                "deltas": [{"ops": [{"kind": 2, "add": {"items": [{"uri": playlist_uri}], "addFirst": True}}]}]}
        requests.post(rl + "/changes", headers=_spc_headers(account_id), data=json.dumps(body),
                      timeout=REQUEST_TIMEOUT).raise_for_status()
    except Exception as e:
        log_warn(f"created {playlist_uri} but couldn't add it to the library ({e!r})", tag="spotify")


def create(name, public=False, description="", account_id=None):
    """Create a playlist via the web-player backend and file it into the account's
    library — neither call touches api.spotify.com or the dev-app dev-mode gate.
    Returns a playlist object shaped like the spotipy path ({id, uri, name}). Only
    the name is set at creation (description/public aren't part of the call); the
    transfer uses name + id."""
    body = {"ops": [{"kind": 6, "updateListAttributes": {"newAttributes": {
        "values": {"name": name or "", "formatAttributes": [], "pictureSize": []}, "noValue": []}}}]}
    r = requests.post(f"{_SPCLIENT}/playlist/v2/playlist", headers=_spc_headers(account_id),
                      data=json.dumps(body), timeout=REQUEST_TIMEOUT)
    if not r.ok:
        raise TargetAuthError(
            f"Couldn't create the playlist via the cookie backend ({r.status_code}). Create '{name}' in "
            "Spotify and re-run the transfer choosing it as an existing playlist (adding tracks works).")
    uri = (r.json() or {}).get("uri", "")
    _rootlist_add(uri, account_id)
    return {"id": uri.rsplit(":", 1)[-1], "uri": uri, "name": name}


def demo():
    """Read-only self-check: mint the token and read a playlist's contents.
    Usage: python -m songmirror.engine.spotify_cookie spotify:playlist:<id>
    (needs SPOTIFY_SP_DC / data/spotify_sp_dc.private set)."""
    import sys
    puri = sys.argv[1] if len(sys.argv) > 1 else None
    assert configured(), "no sp_dc cookie configured"
    assert _token(), "token mint failed"
    if puri:
        tracks = playlist_tracks(puri)
        assert isinstance(tracks, list), "playlist_tracks did not return a list"
        assert all("id" in t and "name" in t for t in tracks), "malformed track dict"
        log(f"cookie self-check OK: {len(tracks)} tracks in {puri}", tag="spotify")
    else:
        log("cookie self-check OK: token minted (pass a playlist uri to read-test)", tag="spotify")


if __name__ == "__main__":
    demo()
