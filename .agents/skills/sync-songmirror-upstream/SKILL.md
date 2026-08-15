---
name: sync-songmirror-upstream
description: Safely inspect, classify, port, and validate new commits from the public SongMirror upstream into Sync My Music. Use when checking whether SongMirror changed, reviewing an upstream commit or release, importing an upstream bug/security fix, resolving divergence, or updating the recorded reviewed/integrated upstream commits. Never use this workflow for blind merges or automatic force-pushes.
---

# Sync SongMirror Upstream

Review upstream changes commit by commit and port only changes that remain valid
for Sync My Music. The repositories intentionally diverge; never treat upstream
as an automatically mergeable branch.

## Start safely

1. Work from the Sync My Music repository root.
2. Read `upstream.json` and `references/project-contract.md` completely.
3. Run `scripts/inspect-upstream.sh` before fetching or editing.
4. Report a dirty worktree and stop unless the existing changes belong to the
   current task and can be preserved explicitly.
5. Confirm the remotes resolve to:
   - `origin`: `elias001011/sync-my-music`
   - `upstream`: `ahnafnafee/songmirror`

Do not print environment values, cookies, tokens, contents of `data/`, or local
deployment coordinates in reports.

## Inspect

Fetch metadata only after the initial diagnostic:

```bash
git fetch upstream main
```

Compare `last_reviewed` from `upstream.json` with `upstream/main`. List every new
commit and its touched files. Review patches with `git show`; do not infer safety
from commit messages.

Classify each commit:

- **portable**: isolated fix in inherited code with no protected-surface impact;
- **manual port**: useful change overlapping Sync My Music modifications;
- **not applicable**: branding, screenshots, architecture, or behavior replaced
  by this project;
- **blocked**: unclear security, destructive migration, credential handling, or
  dependency change requiring a user decision.

## Integrate selectively

Never run `git merge upstream/main`. The main histories are intentionally
unrelated and major files have diverged.

For a portable commit, prefer a dedicated branch and a no-commit cherry-pick so
the patch can be inspected before recording it:

```bash
git switch -c upstream-review/<short-topic>
git cherry-pick --no-commit <commit>
git diff --check
```

For a manual port, inspect the upstream patch and reproduce only the relevant
hunks with the repository's normal editing workflow. Preserve attribution in the
commit message with `Upstream-commit: <sha>`.

Abort and restore the pre-integration state if a patch unexpectedly touches
canonical data, recap semantics, provider pause behavior, playlist versions,
Musify, Sonora, LAN security, secrets, or deployment files.

## Validate

Run validation proportional to the touched surface. Before proposing any update,
the minimum full gate is:

```bash
.venv/bin/pytest -q
pnpm -C frontend install --frozen-lockfile
pnpm -C frontend lint
pnpm -C frontend build
CI=1 pnpm -C frontend test:e2e
pnpm -C frontend audit
pnpm -C promo audit
```

Also scan newly introduced tracked files and history for credentials or personal
deployment values. Never dismiss a failing test as an upstream difference.

## Record the review

Update `upstream.json` only after every commit through the new head has been
classified:

- Set `last_reviewed` to the newest fully reviewed commit.
- Set `last_integrated` only to a commit whose relevant changes are actually in
  Sync My Music.
- Add a concise note for intentionally skipped commits.

Do not push, merge a pull request, change repository visibility, or deploy unless
the user explicitly requested that external action.

## Report

Return:

1. old and new upstream SHAs;
2. commits classified by outcome;
3. files changed locally;
4. protected-surface impact;
5. validations and audits run;
6. unresolved risks;
7. exact next action requiring approval, if any.
