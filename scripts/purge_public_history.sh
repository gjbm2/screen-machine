#!/usr/bin/env bash
# scripts/purge_public_history.sh: remove files that must never have been public from EVERY commit on GitHub, then
# force-push the rewritten history. Run it once, AFTER PC22's work is merged and pushed (docs/PICKUP_FROM_PC22.md,
# "After the merge: purge, then public again"), while the repo is private.
#
#   scripts/purge_public_history.sh            # dry run: rewrite in a temp mirror, show what goes, push nothing
#   scripts/purge_public_history.sh --push     # the same, then force-push every branch and tag
#
# It never touches this checkout: it works in a fresh mirror clone under a temp directory. Afterwards every other
# clone (PC22, the laptop) must re-sync to the rewritten history (the doc says how), and their old commits must not be
# pushed again, or the files come back.
#
# Needs git-filter-repo (installed into a throwaway venv here if missing) and SSH access to GitHub.
set -euo pipefail

REMOTE="${PURGE_REMOTE:-git@github.com:gjbm2/screen-machine.git}"
PUSH=0; [[ "${1:-}" == "--push" ]] && PUSH=1

# What goes, in every commit (paths are repo-relative; a directory takes everything under it).
PATHS=(
  debug/                                   # a network controller backup and a support bundle
  media-server/maintenance.log             # a shell trace that printed the kiosk's login
  wav/                                     # room recordings
  north-screen-token.txt south-screen-token.txt north-screen-token-4.txt south-screen-token-4.txt
  samsungtvws/example/web_interface/north-screen-token.txt   # TV pairing tokens
)

WORK="$(mktemp -d "${TMPDIR:-/tmp}/sm-purge.XXXXXX")"
echo "work dir: $WORK"

FR="$(command -v git-filter-repo || true)"
if [[ -z "$FR" ]]; then
  python3 -m venv "$WORK/venv" && "$WORK/venv/bin/pip" -q install git-filter-repo
  FR="$WORK/venv/bin/git-filter-repo"
fi

git clone -q --mirror "$REMOTE" "$WORK/repo.git"
cd "$WORK/repo.git"
echo "before: $(git count-objects -vH | grep size-pack)"
args=(); for p in "${PATHS[@]}"; do args+=(--path "$p"); done
"$FR" --force --invert-paths "${args[@]}"

left="$(git log --all --format=%h -- "${PATHS[@]}" | head -5)"
if [[ -n "$left" ]]; then echo "still in history (stopping): $left" >&2; exit 1; fi
echo "after:  $(git count-objects -vH | grep size-pack)"
echo "none of these paths is in any commit now: ${PATHS[*]}"

if [[ $PUSH -eq 0 ]]; then
  echo "dry run: nothing pushed. Rerun with --push to force-push every branch and tag."
  exit 0
fi
git remote add origin "$REMOTE" 2>/dev/null || git remote set-url origin "$REMOTE"
git push --force origin 'refs/heads/*:refs/heads/*'
git push --force origin 'refs/tags/*:refs/tags/*'
echo "pushed. Now re-sync every clone (docs/PICKUP_FROM_PC22.md), then make the repo public again."
