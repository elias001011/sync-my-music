# Sync My Music architecture

> **Relationship to SongMirror.** This codebase is an MIT adaptation of
> [SongMirror](https://github.com/ahnafnafee/songmirror): the engine layer
> (`engine/targets`, `engine/matching`, reconciliation, transfers, safety
> rails) is inherited and kept close to upstream, while the web application,
> canonical database, accounts registry, jobs, recaps, backups and bridges are
> Sync My Music additions. The engine is driven by a serialized service layer
> (`services/`) so scheduled syncs and transfers never overlap; the CLI entry
> point still runs the engine standalone. See `README.md` → "Beyond SongMirror"
> for the full inherited-vs-added breakdown.

## One database, several mirrors

`data/sync_music.db` is the product database. It is separate from the original
matching cache (`song_cache.db`) and stores canonical artists, albums and tracks;
provider identities; service accounts; playlists and ordered items; listening
events and recap snapshots; sync policies/runs; playlist versions; Sonora peers;
and the last 5,000 structured application logs.

A provider is never assumed to support everything. Each account advertises
capabilities such as library read, playlist read, playlist create and playlist
write. The UI only offers valid destinations, and a connector can be paused
without deleting its imported data.

## Multi-account live profiles

Every credential profile lives in the settings registry under a stable
`{provider}:{account}` id — `spotify:default` (the migrated legacy single
account) plus any named profiles the user creates (`spotify:work`). Each
profile carries its own label, enabled flag, surface toggles and config
namespace, so two accounts of the SAME provider can run side by side:

- Connectors are instantiated per account and read/write ONLY that account's
  namespace; a named account never inherits the default's credentials from
  `os.environ` and never overwrites the default's token/cookie files.
- Session/token files are materialized per account in the config snapshot
  (`data/{provider}-{hash}_*.json`), including the Spotify `sp_dc` cookie
  (`spotify_sp_dc.<slug>.private`, 0600) and the OAuth token caches.
- Engine targets receive their config directly (`Options.account_configs`);
  nothing is swapped into `os.environ`, so concurrent accounts can't leak.
- Archive state and resolution caches are namespaced by `state_key` — the bare
  provider for `:default` (byte-identical legacy keys), the account id for
  named profiles. N-way reconcile keys playlist dirs, caches and baselines by
  `state_key`, so two Spotify accounts never collide.
- Jobs store `accounts` (account ids) and `source` (an account id when the
  one-way source is a specific profile). Legacy jobs keep working: bare
  provider ids resolve to their `:default` accounts.

Live accounts vs read-only snapshots are classified once, by auth mode
(`canonical_target.is_canonical_account`): `official-export`,
`sync-account-restore`, `hive-backup`, `aggregate-import` and `history-import`
are local snapshots (read-only transfer sources); anything else — including
`:default` and named profiles — is a live account that builds a real provider
target. PlaylistService, TransferService and the Accounts list share this
single classifier.

A live account's playlists can be pulled into the canonical database
(`POST /api/library/accounts/{account_id}/import`, gated by the account's
`playlists` surface toggle): the read goes through the account's own target and
replaces that account's previous canonical snapshot (versioned before
replacement), keeping entity dedup across accounts intact.

## Synchronization surfaces

The intended surfaces are independent:

| Surface | Typical direction | Notes |
| --- | --- | --- |
| Playlist | one-way or N-way | Preview first; additions and removals have separate caps. |
| Liked/saved tracks | merge or source-of-truth | Only enabled when both adapters can represent the operation. |
| Followed artists | merge | Unsupported destinations are skipped explicitly. |
| Saved albums/playlists | merge | Provider metadata remains attached to the canonical entity. |
| Listening recap | inbound only | Never writes listening history back to commercial services. |
| Musify | outbound custom link / inbound stats snapshot | Uses YouTube IDs; it never rewrites Hive files. |
| Sonora | ZIP backup or paired P2P merge | Surface selection is configurable for each sync. |

Playlist changes take a bounded snapshot before mutation. `PLAYLIST_VERSION_LIMIT`
defaults to 10 per playlist. Restoring a version creates a new `before-restore`
snapshot, so recovery itself is recoverable.

## Listening semantics

There are two deliberately different stores:

- `listens` contains individual events from the ListenBrainz-compatible
  `/1/submit-listens` endpoint. Events are append-only and deduplicated by their
  source event id/fingerprint.
- `listening_aggregates` contains manual Musify/Sonora recap imports. Its primary
  key is account + period + track. Importing July again replaces July. A first
  import of 20 minutes followed by 40 minutes therefore reports 40, never 60.

Monthly and annual recap rows are retained by calendar year. The default is the
current year plus the two previous years; the Settings UI accepts 1–10 years.
Pruning only removes `listens` and `listening_aggregates`, never canonical
tracks, playlists, surfaces, or provider identities.

## Whole-application backups

The Settings page exports a versioned ZIP containing a consistent SQLite
snapshot plus configuration (including the account registry with per-account
configs, surfaces and enabled flags), jobs (with `accounts`), links, and local
connector state. Every file has a SHA-256 digest in the manifest. Restore
validates archive paths, size, hashes, and SQLite integrity, runs through the
same exclusive queue as syncs, and keeps the latest three pre-restore recovery
copies.

Exports are not encrypted and can contain credentials. Spotify's `sp_dc` web
session cookie — the default file and every per-account variant — is therefore
always excluded and Web/cookie mode is reset to OAuth in the portable backup.
Restore the ZIP only on a trusted machine and re-enable cookie mode explicitly
afterward.

The Library also exposes smaller `sync-account-backup` archives. They contain
one provider account's canonical tracks, playlists, surfaces and recap rows but
never connector credentials. Restoring a named slot replaces only that account;
manual Spotify/Musify/Sonora slots remain independently exportable and usable as
read-only transfer sources.

Pano Scrobbler or Web Scrobbler can submit individual plays to
`http://<server>:8080/1/submit-listens`. Set `SCROBBLE_TOKEN` to require an
`Authorization: Token ...` header.

## Sonora LAN bridge

When enabled, the server responds to Sonora discovery broadcasts on UDP 53530,
advertises its HTTP port, and implements Sonora's version-1 peer endpoints:

- `GET /api/sync/info`
- `POST /api/sync/pair-request`
- `POST /api/sync/pair-verify`
- `POST /api/sync/merge`

Pairing by PIN is mandatory. Discovery is off by default. The bridge exchanges
Sonora backup-v2 data (liked songs, followed artists, liked albums/playlists,
local playlists and history); search history and settings are deliberately not
treated as canonical music data.

## Safety defaults

- no public bind or port forwarding is recommended;
- dry-run/preview before provider writes;
- removals disabled by default;
- bounded additions/removals and read-collapse guards;
- connector pause switches preserve all local data;
- Sonora LAN discovery disabled by default and merge restricted to paired IDs;
- secrets stay under the owner-only `data` directory.
