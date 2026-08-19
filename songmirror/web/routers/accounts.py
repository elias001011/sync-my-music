"""Account wizard endpoints — connect/inspect each service uniformly.

Every route accepts either a bare provider id (`spotify`, legacy — resolves to
`spotify:default`) or a full account id (`spotify:work`), so the multi-account
UI can connect/configure/reconnect ONE specific profile without ever touching
another account's credentials, tokens or cookie files.
"""

import glob
import html
import os
import re
from dataclasses import asdict
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...engine.targets import is_peer
from ...services.accounts import CONNECTORS
from ...services.accounts.base import ConnStatus, DeviceCode
from ...services.music_database import PROVIDER_CAPABILITIES, SURFACE_CAPABILITIES
from ...services.settings import DEFAULT_SURFACES, SURFACES

router = APIRouter()

# Auth modes that mark a LOCAL read-only snapshot (restored backup / official
# export / history import), as opposed to a live connected account. Centralized
# here so the Accounts list, PlaylistService and TransferService agree.
CANONICAL_AUTH_MODES = {"official-export", "sync-account-restore", "hive-backup",
                        "aggregate-import", "history-import"}


def _resolve_account_id(request: Request, cid: str) -> str:
    """`spotify` -> `spotify:default`; `spotify:work` -> itself. 404 for an
    unknown provider or an account the registry doesn't know (other than the
    default, which always exists implicitly)."""
    if ":" in cid:
        provider = cid.split(":", 1)[0]
        if provider not in CONNECTORS:
            raise HTTPException(status_code=404, detail=f"unknown provider {provider}")
        return cid
    if cid not in CONNECTORS:
        raise HTTPException(status_code=404, detail=f"unknown provider {cid}")
    return f"{cid}:default"


def _conn(request: Request, account_id: str):
    provider = account_id.split(":", 1)[0]
    return CONNECTORS[provider](request.app.state.settings, account_id=account_id)


def _redirect_uri(request: Request, cid: str) -> str:
    configured = (
        request.app.state.settings.get("SONGMIRROR_PUBLIC_URL")
        or os.getenv("SONGMIRROR_PUBLIC_URL")
        or ""
    )
    base = str(configured).strip() or str(request.base_url)

    if configured:
        parts = urlsplit(base)
        try:
            parts.port  # validate malformed/non-numeric ports too
        except ValueError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"invalid SONGMIRROR_PUBLIC_URL ({exc})",
            ) from exc
        if (
            parts.scheme.lower() not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise HTTPException(
                status_code=500,
                detail=(
                    "SONGMIRROR_PUBLIC_URL must be an absolute http(s) URL "
                    "without credentials, a query, or a fragment"
                ),
            )
        # Preserve an optional reverse-proxy base path while canonicalizing the
        # scheme and removing the trailing slash before appending our route.
        base = urlunsplit((parts.scheme.lower(), parts.netloc, parts.path.rstrip("/"), "", ""))

    base = base.rstrip("/")
    # Spotify (and increasingly others) reject `localhost` for http loopback
    # OAuth redirects — the explicit 127.0.0.1 loopback IP is required over http.
    # Force it for the request-derived fallback. A configured public URL is still
    # normalized too, which catches an accidentally configured localhost alias.
    base = re.sub(r"://localhost(?=[:/]|$)", "://127.0.0.1", base, count=1)
    return base + f"/oauth/{cid}/callback"


@router.get("/api/accounts")
def list_accounts(request: Request):
    store = request.app.state.settings
    disabled = {item.strip() for item in str(store.get("DISABLED_PROVIDERS") or "").split(",") if item.strip()}
    out = []
    for cid, cls in CONNECTORS.items():
        # Every live profile for this provider: the default account plus any
        # named profiles the user created — each one a separate card with its
        # own connect state, surfaces and pause switch.
        account_ids = [f"{cid}:default"]
        account_ids += [aid for aid in store.accounts()
                        if aid.startswith(f"{cid}:") and aid != f"{cid}:default"]
        for account_id in account_ids:
            c = cls(store, account_id=account_id)
            st = c.status()
            profile = store.account(account_id) or {}
            label = profile.get("label") or c.name
            fields = []
            for f in c.config_fields:
                d = asdict(f)
                cur = store.account_config(account_id, f.key) or ""
                # Pre-fill on reconnect, but NEVER echo a secret back to the browser —
                # send its value only when it's non-secret; a `configured` flag lets the
                # wizard show "saved — leave blank to keep" for a stored secret instead.
                d["value"] = "" if f.secret else cur
                d["configured"] = bool(cur)
                fields.append(d)
            # Public id: the bare provider for the default account (legacy
            # contract — old UIs and fixtures use "spotify"); the full account
            # id for named profiles. Every route accepts both forms.
            public_id = cid if account_id == f"{cid}:default" else account_id
            entry = {
                "id": public_id, "provider": cid, "account_id": account_id, "name": label,
                "auth_kind": c.auth_kind, "fields": fields,
                "state": st.state, "detail": st.detail,
                # Browse-only services (Jellyfin) can't be a sync/transfer peer — the
                # UI filters its source/destination pickers on this.
                "transferable": is_peer(cid),
                "capabilities": PROVIDER_CAPABILITIES.get(cid, {}),
                "surface_capabilities": SURFACE_CAPABILITIES.get(cid, {}),
                # Live connected profile (as opposed to a restored local snapshot).
                "live": True,
                "enabled": cid not in disabled and profile.get("enabled", True),
                "surfaces": profile.get("surfaces") or DEFAULT_SURFACES,
            }
            out.append(entry)
            if hasattr(request.app.state, "music_db"):
                request.app.state.music_db.sync_account(cid, label, st.state, c.auth_kind,
                                                        account_id=account_id,
                                                        enabled=entry["enabled"])
    # Musify has no remote login. It becomes a read-only transfer source after
    # the user uploads user.hive, so expose it alongside connected services only
    # when that local snapshot actually exists.
    if hasattr(request.app.state, "music_db"):
        database_accounts = request.app.state.music_db.accounts()
        imported_accounts = [row for row in database_accounts
                             if row["provider"] == "musify" and row["status"] == "connected"]
        for imported in imported_accounts:
            public_id = "musify" if imported["id"] == "musify:default" else imported["id"]
            out.append({
                "id": public_id, "provider": "musify", "name": imported["label"], "auth_kind": "token_paste",
                "fields": [], "state": "connected", "detail": f"Imported user.hive snapshot · {imported['id']}",
                "local_snapshot": True,
                # It can feed a one-off copy, but is not a writable/n-way sync
                # peer. The UI keeps these two capabilities separate.
                "transferable": False, "transfer_source": True,
                "capabilities": PROVIDER_CAPABILITIES["musify"],
                "enabled": "musify" not in disabled,
            })
        for imported in database_accounts:
            if (imported["provider"] == "musify" or imported["id"] == f"{imported['provider']}:default"
                    or str(imported.get("auth_mode") or "") not in CANONICAL_AUTH_MODES):
                continue
            out.append({
                "id": imported["id"], "provider": imported["provider"], "name": imported["label"],
                "auth_kind": "token_paste", "fields": [], "state": "connected",
                "detail": f"Restored local snapshot · {imported['id']}", "local_snapshot": True,
                "transferable": False, "transfer_source": True,
                "capabilities": {**PROVIDER_CAPABILITIES.get(imported["provider"], {}),
                                 "playlist_write": False, "playlist_create": False},
                "enabled": imported["provider"] not in disabled,
            })
    return out


@router.post("/api/accounts/{cid}/accounts")
def create_account(cid: str, request: Request, body: dict = Body(...)):
    """Create a new named live profile for a provider: `{provider}:{slug}` with
    its own isolated config namespace. Nothing is inherited from the default
    account — the user connects it separately."""
    provider = cid.split(":", 1)[0] if ":" in cid else cid
    if provider not in CONNECTORS:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider}")
    store = request.app.state.settings
    label = str(body.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="choose a name for the account")
    account_id = store.create_account_id(provider, label)
    store.save_account(account_id, label=label, enabled=True)
    return {"ok": True, "account_id": account_id, "provider": provider, "name": label}


@router.put("/api/accounts/{cid}/enabled")
def set_account_enabled(cid: str, request: Request, body: dict = Body(...)):
    account_id = _resolve_account_id(request, cid)
    enabled = bool(body.get("enabled"))
    request.app.state.settings.save_account(account_id, enabled=enabled)
    if hasattr(request.app.state, "music_db"):
        try:
            request.app.state.music_db.set_account_enabled(account_id, enabled)
        except KeyError:
            pass  # no canonical rows yet — the flag lives in the registry
    return {"ok": True, "account_id": account_id}


@router.put("/api/accounts/{cid}/prefs")
def set_account_prefs(cid: str, request: Request, body: dict = Body(...)):
    """Per-account switches: rename the profile, pause the whole account, or
    disable individual surfaces (playlists / liked tracks / albums / artists /
    history). Disabling a surface never deletes already-imported data — it only
    stops new imports and sync reads for it."""
    account_id = _resolve_account_id(request, cid)
    if account_id.split(":", 1)[0] not in CONNECTORS:
        raise HTTPException(status_code=404, detail="unknown provider")
    store = request.app.state.settings
    surfaces = {
        surface: bool(body.get("surfaces", {}).get(surface))
        for surface in SURFACES if surface in (body.get("surfaces") or {})
    }
    label = body.get("label")
    if label is not None and not str(label).strip():
        raise HTTPException(status_code=400, detail="label cannot be empty")
    enabled = body.get("enabled")
    store.save_account(account_id, label=(str(label).strip() if label else None),
                       enabled=(bool(enabled) if enabled is not None else None),
                       surfaces=surfaces or None)
    profile = store.account(account_id)
    if hasattr(request.app.state, "music_db"):
        try:
            request.app.state.music_db.set_account_enabled(account_id, profile["enabled"])
            if label:
                request.app.state.music_db.rename_account(account_id, str(label).strip())
        except KeyError:
            pass  # no canonical rows yet — the flag lives in the registry
    return {"ok": True, "account_id": account_id, "enabled": profile["enabled"],
            "surfaces": profile["surfaces"], "name": profile["label"]}


@router.post("/api/accounts/{cid}/config")
def save_config(cid: str, request: Request, values: dict = Body(...)):
    """Save app-config fields for ONE account. Values land in the account's own
    registry namespace (`:default` also mirrors the flat keys for legacy)."""
    account_id = _resolve_account_id(request, cid)
    provider = account_id.split(":", 1)[0]
    connector = CONNECTORS.get(provider)
    if connector is None:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider}")
    allowed = {f.key for f in connector.config_fields}
    payload = {k: v for k, v in values.items() if k in allowed}
    if payload:
        c = _conn(request, account_id)
        c._save(payload)
    return {"ok": True, "account_id": account_id}


@router.post("/api/accounts/{cid}/connect")
async def connect(cid: str, request: Request):
    account_id = _resolve_account_id(request, cid)
    provider = account_id.split(":", 1)[0]
    c = _conn(request, account_id)
    if c.auth_kind == "oauth_redirect":
        # Named profiles get the FULL account id in the callback path
        # (`/oauth/spotify:work/callback`) so the browser handshake resolves
        # back to THIS account. The default keeps the legacy bare-provider path
        # (`/oauth/spotify/callback`) — its registered redirect URI is unchanged.
        public_id = account_id if not account_id.endswith(":default") else provider
        uri = _redirect_uri(request, public_id)
        return {"kind": "redirect", "url": c.begin_redirect(uri), "redirect_uri": uri}
    if c.auth_kind == "oauth_device":
        return {"kind": "device", **asdict(c.begin_device())}
    st = c.submit(await request.json())  # token_paste / api_key
    return {"kind": c.auth_kind, "state": st.state, "detail": st.detail}


@router.get("/oauth/{cid}/callback")
def oauth_callback(cid: str, request: Request):
    # The provider can bounce back with ?error=... (the user denied, or a
    # provider-side failure like Spotify's "server_error") instead of a code.
    # Treat that as a failed connection and, likewise, catch any token-exchange
    # error — the callback must never 500 and show a raw "Internal Server Error".
    provider = cid.split(":", 1)[0]
    connector = CONNECTORS.get(provider)
    if connector is None:
        # An unknown provider id in the callback URL must never 500 — the
        # callback page is reachable without auth, so answer 404 like any
        # other unknown route.
        return HTMLResponse(
            f"<body style='font-family:system-ui;padding:2rem'>"
            f"<h2>Unknown provider</h2><p>No such connector: {html.escape(cid)}</p></body>",
            status_code=404,
        )
    account_id = cid if ":" in cid else f"{cid}:default"
    err = request.query_params.get("error")
    if err:
        st = ConnStatus("error", f"{connector.name} returned '{err}' — nothing was authorized.")
    else:
        try:
            st = _conn(request, account_id).complete_redirect({"url": str(request.url)})
        except Exception as e:
            st = ConnStatus("error", f"could not finish authorization ({e})")
    return HTMLResponse(
        f"<body style='font-family:system-ui;padding:2rem'>"
        f"<h2>{html.escape(connector.name)}: {html.escape(st.state)}</h2>"
        f"<p>{html.escape(st.detail or '')}</p>"
        f"<p>You can close this tab and return to the app.</p></body>"
    )


@router.post("/api/accounts/{cid}/poll")
async def poll(cid: str, request: Request):
    body = await request.json()
    dc = DeviceCode("", "", body["device_code"], body.get("interval", 5))
    st = _conn(request, _resolve_account_id(request, cid)).poll_device(dc)
    return {"state": st.state, "detail": st.detail}


@router.delete("/api/accounts/{cid}")
def disconnect(cid: str, request: Request):
    """Clear ONE account's credentials (the profile stays, unconfigured)."""
    account_id = _resolve_account_id(request, cid)
    c = _conn(request, account_id)
    c.disconnect()
    return {"ok": True, "account_id": account_id}


@router.delete("/api/accounts/{cid}/remove")
def remove_account(cid: str, request: Request, confirm: bool = False):
    """Permanently remove a named live profile: registry entry, its per-account
    token/cookie files, and its canonical rows. The `:default` account can't be
    removed (it's the migration anchor) — disconnect it instead. Destructive, so
    it requires an explicit `confirm=1` query param."""
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm this deletion explicitly")
    account_id = _resolve_account_id(request, cid)
    if account_id.endswith(":default"):
        raise HTTPException(status_code=400, detail="the default account can't be removed — disconnect it instead")
    store = request.app.state.settings
    # Per-account session/cookie files (0600 secrets) are deleted so the removed
    # account leaves no credential behind. The default account's files are never
    # touched.
    data_dir = os.path.dirname(store.env_path) or "."
    slug = store.account_slug(account_id)
    for pattern in (f"{data_dir}/{slug}_*.json", f"{data_dir}/*.{slug}.*.private",
                    f"{data_dir}/*.{slug}*.private"):
        for path in glob.glob(pattern):
            try:
                os.remove(path)
            except OSError:
                pass
    store.delete_account_registry(account_id)
    if hasattr(request.app.state, "music_db"):
        try:
            request.app.state.music_db.delete_account(account_id)
        except KeyError:
            pass
    return {"ok": True, "account_id": account_id, "removed": True}


@router.post("/api/accounts/ytmusic/browser")
async def ytmusic_enable_browser(request: Request, body: dict = Body(...)):
    """Turn on YouTube Music's no-quota (browser cookies) backend from pasted
    music.youtube.com request headers — the fix for large backfills hitting the
    Data API quota. `account_id` (optional) targets a specific profile."""
    account_id = _resolve_account_id(request, body.get("account_id") or "ytmusic")
    st = _conn(request, account_id).enable_browser(body.get("headers", ""))
    return {"state": st.state, "detail": st.detail, "account_id": account_id}


@router.delete("/api/accounts/ytmusic/browser")
def ytmusic_disable_browser(request: Request, account_id: str = ""):
    """Revert one YouTube Music account to the durable OAuth Data API."""
    target = _resolve_account_id(request, account_id or "ytmusic")
    st = _conn(request, target).disable_browser()
    return {"state": st.state, "detail": st.detail, "account_id": target}


@router.post("/api/accounts/spotify/cookie")
async def spotify_enable_cookie(request: Request, body: dict = Body(...)):
    """Use a pasted sp_dc as the complete Spotify Web Player connection for ONE
    account — named accounts get their own 0600 cookie file, so enabling a
    second Spotify account never touches the first one's cookie."""
    account_id = _resolve_account_id(request, body.get("account_id") or "spotify")
    st = _conn(request, account_id).enable_cookie(body.get("sp_dc", ""))
    return {"state": st.state, "detail": st.detail, "account_id": account_id}


@router.delete("/api/accounts/spotify/cookie")
def spotify_disable_cookie(request: Request, account_id: str = ""):
    """Revert one Spotify account to the OAuth dev app (its cookie file stays
    on disk so re-enabling needs no re-paste)."""
    target = _resolve_account_id(request, account_id or "spotify")
    st = _conn(request, target).disable_cookie()
    return {"state": st.state, "detail": st.detail, "account_id": target}


@router.post("/api/accounts/spotify/isrc-app")
async def spotify_set_isrc_app(request: Request, body: dict = Body(...)):
    """Store a batch-capable (extended-quota) app for the ISRC /tracks lookup N-way
    matching needs — a rate bucket separate from the OAuth user token + cookie token.
    Provider-level add-on: the engine reads it from one shared pool, so it's stored
    on the default account."""
    st = _conn(request, "spotify:default").set_isrc_app(body.get("client_id", ""), body.get("client_secret", ""))
    return {"state": st.state, "detail": st.detail}


@router.delete("/api/accounts/spotify/isrc-app")
def spotify_clear_isrc_app(request: Request):
    """Drop the ISRC app (falls back to the OAuth app for /tracks)."""
    st = _conn(request, "spotify:default").clear_isrc_app()
    return {"state": st.state, "detail": st.detail}
