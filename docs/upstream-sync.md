# Synchronizing with SongMirror upstream

Sync My Music keeps the public SongMirror repository configured as the
`upstream` Git remote, but its `main` history is intentionally independent.
Updates are reviewed and ported selectively; upstream is never auto-merged.

The machine-readable review state lives in [`upstream.json`](../upstream.json).
AI coding agents should use the repository skill at
[`sync-songmirror-upstream`](../.agents/skills/sync-songmirror-upstream/SKILL.md).

## Why updates are selective

Sync My Music changed the data model, recap semantics, playlist recovery,
provider controls, branding, deployment, and the Musify/Sonora surfaces. A patch
that is correct for SongMirror may overwrite those guarantees even when Git can
apply it without a conflict.

## Human workflow

```bash
.agents/skills/sync-songmirror-upstream/scripts/inspect-upstream.sh
git fetch upstream main
git log --oneline <last-reviewed-sha>..upstream/main
git show <commit>
```

Classify each commit as portable, manual port, not applicable, or blocked. Work
on a dedicated branch, preserve the upstream SHA in the commit message, and run
the complete Python/frontend test gate plus dependency audits before merging.

Do not update `last_reviewed` until every commit through that SHA has been
inspected. Do not update `last_integrated` unless the relevant behavior actually
exists in Sync My Music.
