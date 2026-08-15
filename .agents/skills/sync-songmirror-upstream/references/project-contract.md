# Sync My Music project contract

Read this reference before importing SongMirror changes.

## Repository relationship

- SongMirror is the MIT-licensed upstream foundation.
- Sync My Music is an independent adaptation with a separately rooted `main`.
- Preserve upstream copyright and attribution.
- Never auto-merge `upstream/main` into `main`.

## Protected behavior

An upstream patch must not change these behaviors without explicit review:

- `data/sync_music.db` remains separate from matching/provider caches.
- Individual listens are append-only and deduplicated.
- Musify and Sonora monthly imports replace the same source/month snapshot; they
  never add a second copy of the already imported total.
- Provider pause preserves imported local data.
- Playlist mutations retain preview, caps, empty/read-collapse guards, and
  bounded recovery versions.
- A restore creates a new pre-restore version.
- Sonora discovery remains disabled by default and pairing requires a PIN.
- The web UI remains LAN-only until real application authentication exists.
- Secrets remain ignored by Git and stored owner-only where POSIX permissions
  are available.

## Protected paths

Treat changes under these paths as manual ports even if a cherry-pick applies:

```text
songmirror/services/music_database.py
songmirror/services/sonora.py
songmirror/services/playlists.py
songmirror/services/events.py
songmirror/web/routers/library.py
songmirror/web/routers/accounts.py
songmirror/web/routers/playlists.py
frontend/src/pages/Library.tsx
frontend/src/pages/Recaps.tsx
frontend/src/pages/Logs.tsx
frontend/src/components/accounts/SonoraPanel.tsx
frontend/src/components/playlists/MusifyExportCard.tsx
frontend/src/components/playlists/PlaylistVersionCard.tsx
frontend/src/components/layout/Sidebar.tsx
docker-compose.yml
deploy/
README.md
README.pt-BR.md
docs/architecture.md
```

Dependency and lockfile changes require fresh security audits and the complete
test/build gate.
