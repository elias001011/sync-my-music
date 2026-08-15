#!/usr/bin/env bash
set -euo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_root="$(git -C "$skill_dir" rev-parse --show-toplevel)"
state_file="$repo_root/upstream.json"

if [[ ! -f "$state_file" ]]; then
  echo "error=missing_upstream_json"
  exit 2
fi
if [[ -n "$(git -C "$repo_root" status --porcelain)" ]]; then
  echo "worktree=dirty"
else
  echo "worktree=clean"
fi

read_state() {
  python3 - "$state_file" "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)[sys.argv[2]])
PY
}

upstream_repo="$(read_state repository)"
upstream_branch="$(read_state branch)"
last_reviewed="$(read_state last_reviewed)"
last_integrated="$(read_state last_integrated)"

origin_url="$(git -C "$repo_root" remote get-url origin 2>/dev/null || true)"
upstream_url="$(git -C "$repo_root" remote get-url upstream 2>/dev/null || true)"
remote_head="$(git -C "$repo_root" ls-remote upstream "refs/heads/$upstream_branch" | awk 'NR == 1 {print $1}')"

echo "repository=$upstream_repo"
echo "branch=$upstream_branch"
echo "origin_url=$origin_url"
echo "upstream_url=$upstream_url"
echo "last_reviewed=$last_reviewed"
echo "last_integrated=$last_integrated"
echo "remote_head=$remote_head"

if [[ -z "$remote_head" ]]; then
  echo "status=remote_unavailable"
  exit 3
elif [[ "$remote_head" == "$last_reviewed" ]]; then
  echo "status=up_to_date"
else
  echo "status=review_required"
fi

if git -C "$repo_root" cat-file -e "$remote_head^{commit}" 2>/dev/null; then
  echo "remote_head_available_locally=true"
  echo "commits_begin"
  git -C "$repo_root" log --oneline --no-merges "$last_reviewed..$remote_head"
  echo "commits_end"
else
  echo "remote_head_available_locally=false"
  echo "next=git fetch upstream $upstream_branch"
fi
