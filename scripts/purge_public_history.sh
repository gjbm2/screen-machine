#!/usr/bin/env bash
# scripts/purge_public_history.sh: remove what must never have been public from EVERY commit on GitHub, then
# force-push the rewritten history. Run it once, AFTER PC22's work is merged and pushed (docs/PICKUP_FROM_PC22.md,
# "After the merge: purge, then public again"), while the repo is private.
#
#   scripts/purge_public_history.sh            # dry run: rewrite in a temp mirror, show what goes, push nothing
#   scripts/purge_public_history.sh --push     # the same, then force-push every branch and tag
#
# What goes:
#   - the files in PATHS below, from every commit;
#   - in every file and commit message of every commit, the values of the .env keys in REDACT_KEYS (the alert
#     e-mail address and the ngrok domain, which the deployments branch moved out of the tree into .env), the e-mail
#     domain of those addresses (it named the sender and a host too) unless the current tree still uses it, and the
#     VS Code connection tokens a process listing recorded. The values are read from this machine's .env at run
#     time into a private temp file, never printed and never committed; a key that isn't set is skipped.
#
# It never touches this checkout: it works in a fresh mirror clone under a temp directory. Each branch and tag is
# pushed only if GitHub still has the commit the mirror cloned (--force-with-lease), so a push made meanwhile from
# another clone makes this push fail instead of vanishing. Afterwards every other clone (PC22, the laptop) must
# re-sync to the rewritten history (the doc says how), and their old commits, tags and stashes must not be pushed
# again, or the files come back.
#
# Needs python3 -m venv (git-filter-repo, pinned below, goes into a throwaway venv) and SSH access to GitHub.
# Settings for a test run: PURGE_REMOTE (a remote or local path), PURGE_ENV_FILE (default: this checkout's .env).
# PURGE_EXTRA_VALUES: a private file of more literal values to take out of every file and message, one per line
# (a retired password, say, that .env no longer holds); redacted and checked like the .env values, never printed.
set -euo pipefail
umask 077

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE="${PURGE_REMOTE:-git@github.com:gjbm2/screen-machine.git}"
ENV_FILE="${PURGE_ENV_FILE:-$REPO/.env}"
PUSH=0; [[ "${1:-}" == "--push" ]] && PUSH=1

# git-filter-repo, pinned: the 4 Dec 2024 release (PyPI), its wheel's sha256.
FILTER_REPO_VERSION=2.47.0
FILTER_REPO_SHA256=2cd04929b9024e83e65db571cbe36aec65ead0cb5f9ec5abe42158654af5ad83

# What goes, in every commit (paths are repo-relative; a directory takes everything under it).
PATHS=(
  debug/                                   # a network controller backup and a support bundle
  media-server/maintenance.log             # a shell trace that printed the kiosk's login
  wav/                                     # room recordings
  north-screen-token.txt south-screen-token.txt north-screen-token-4.txt south-screen-token-4.txt
  samsungtvws/example/web_interface/north-screen-token.txt   # TV pairing tokens
)
# .env keys whose values the history must not hold (the deployments branch, 0d919d5b, took them out of the tree).
REDACT_KEYS=(ALERT_EMAIL_TO NGROK_DOMAIN)

WORK="$(mktemp -d "${TMPDIR:-/tmp}/sm-purge.XXXXXX")"
RULES="$(mktemp "$WORK/rules.XXXXXX")"        # git-filter-repo --replace-text rules: hold the values, 0600
FIND="$(mktemp "$WORK/find.XXXXXX")"          # the same as Python regexes, to count them, 0600
trap 'rm -f "$RULES" "$FIND" "$WORK/messages"' EXIT
echo "work dir: $WORK"

python3 -m venv "$WORK/venv"
printf 'git-filter-repo==%s --hash=sha256:%s\n' "$FILTER_REPO_VERSION" "$FILTER_REPO_SHA256" > "$WORK/requirements.txt"
"$WORK/venv/bin/pip" -q install --require-hashes --only-binary=:all: -r "$WORK/requirements.txt"
FR="$WORK/venv/bin/git-filter-repo"
echo "git-filter-repo $FILTER_REPO_VERSION (pinned, hash checked)"

git clone -q --mirror "$REMOTE" "$WORK/repo.git"
cd "$WORK/repo.git"
# Each branch and tag as cloned: the push below only replaces a ref GitHub still has at this commit.
mapfile -t CLONED < <(git for-each-ref --format='%(refname) %(objectname)' refs/heads refs/tags)
git ls-tree -r HEAD > "$WORK/tip-before.txt"
echo "before: $(git count-objects -vH | grep size-pack)"

# The rules, built from this machine's .env. Only names and counts are printed.
python3 - "$REPO" "$ENV_FILE" "$RULES" "$FIND" "${REDACT_KEYS[@]}" <<'PY'
import os, re, subprocess, sys, tempfile

repo, env_file, rules_path, find_path, *keys = sys.argv[1:]
sys.path.insert(0, repo)
from smconfig import _parse_env_text  # noqa: E402  (the app's own .env reader)

try:
    env = _parse_env_text(open(env_file).read())
except OSError:
    env = {}
    print(f"note: no {env_file}; no values to redact")
PLACEHOLDER = {"ALERT_EMAIL_TO": "alerts@example.com", "NGROK_DOMAIN": "ngrok-domain.example"}


def in_tip(s):        # does the current tree of the default branch hold s? (s goes through a 0600 file, not argv)
    with tempfile.NamedTemporaryFile("w", dir=os.path.dirname(rules_path)) as f:
        f.write(s + "\n")
        f.flush()
        return subprocess.run(["git", "grep", "-q", "-F", "-f", f.name, "HEAD", "--"]).returncode == 0


rules, finds, used, unset, domains = [], [], [], [], []
for k in keys:
    v = (env.get(k) or "").strip()
    if not v:
        unset.append(k)
        continue
    vals = [x.strip("<>\"'") for x in re.split(r"[,;\s]+", v) if "@" in x] if "@" in v else [v]
    vals = [x for x in vals if len(x) >= 6 and "==>" not in x]      # never a short, common string
    for x in vals:
        rules.append(f"literal:{x}==>{PLACEHOLDER.get(k, 'redacted-' + k.lower().replace('_', '-'))}")
        finds.append(re.escape(x))
        if "@" in x and x.rsplit("@", 1)[1] not in domains:
            domains.append(x.rsplit("@", 1)[1])
    used.append(f"{k} ({len(vals)} value{'s' * (len(vals) != 1)})")
kept_domains = 0
for d in domains:
    if in_tip(d):
        kept_domains += 1
        continue
    rules.append(f"literal:{d}==>example.com")
    finds.append(re.escape(d))
extra = [x.strip() for x in open(os.environ["PURGE_EXTRA_VALUES"]).read().splitlines()] \
    if os.environ.get("PURGE_EXTRA_VALUES") else []
extra = [x for x in extra if len(x) >= 6 and "==>" not in x]      # never a short, common string
for x in extra:
    rules.append(f"literal:{x}==>redacted")
    finds.append(re.escape(x))
token = "--connection-token=[0-9a-fA-F][0-9a-fA-F-]{7,}"     # hex and dashes, so "=redacted" isn't one
rules.append(f"regex:{token}==>--connection-token=redacted")
finds.append(token)
open(rules_path, "w").write("\n".join(rules) + "\n")
open(find_path, "w").write("\n".join(finds) + "\n")
print(f"values from .env: {', '.join(used) or 'none'}" + (f"; not set, skipped: {', '.join(unset)}" if unset else ""))
if extra:
    print(f"values from PURGE_EXTRA_VALUES: {len(extra)}")
print(f"e-mail domains: {len(domains) - kept_domains} redacted"
      + (f", {kept_domains} kept (the current tree still uses it)" if kept_domains else ""))
print("pattern: VS Code connection tokens")
PY

# Counts what holds a value: "<text files> <binary files> <commit messages>". git-filter-repo rewrites text files and
# messages; it leaves a file with a NUL byte in its first 8 KiB alone, so those are counted apart.
COUNT_PY='
import re, sys
pats = [re.compile(p.encode()) for p in open(sys.argv[1]).read().splitlines() if p]
hit = lambda b: any(p.search(b) for p in pats)
text, binary = 0, set()
src = sys.stdin.buffer
while header := src.readline():
    _, size, path = (header.rstrip(b"\n") + b" ").split(b" ", 2)
    data = src.read(int(size)); src.read(1)
    if hit(data):
        if b"\0" in data[:8192]: binary.add(path.strip().decode(errors="replace"))
        else: text += 1
msgs = sum(hit(m) for m in open(sys.argv[2], "rb").read().split(b"\0"))
open(sys.argv[3], "w").write("".join(f"  {p}\n" for p in sorted(binary)))
print(text, len(binary), msgs)
'
count() {
  git log --all --format=%B%x00 > "$WORK/messages"
  git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(rest)' |
    awk '$1 == "blob" {$1 = ""; print substr($0, 2)}' | git cat-file --batch='%(objectname) %(objectsize) %(rest)' |
    python3 -c "$COUNT_PY" "$FIND" "$WORK/messages" "$WORK/binary-paths"
}
read -r text binary msgs < <(count)
[[ -n "${msgs:-}" ]] || { echo "counting the values failed (stopping)" >&2; exit 1; }
echo "hold a value: $text text file(s), $msgs commit message(s), $binary binary file(s)"

args=(); for p in "${PATHS[@]}"; do args+=(--path "$p"); done
"$FR" --force --invert-paths "${args[@]}" --replace-text "$RULES" --replace-message "$RULES"

left="$(git log --all --format=%h -- "${PATHS[@]}" | head -5)"
if [[ -n "$left" ]]; then echo "still in history (stopping): $left" >&2; exit 1; fi
read -r text binary msgs < <(count)
rm -f "$WORK/messages"
[[ -n "${msgs:-}" ]] || { echo "counting the values failed (stopping)" >&2; exit 1; }
if [[ "$text" != 0 || "$msgs" != 0 ]]; then
  echo "values still in history (stopping): $text text file(s), $msgs commit message(s)" >&2; exit 1
fi
echo "after:  $(git count-objects -vH | grep size-pack)"
echo "none of these paths is in any commit now: ${PATHS[*]}"
echo "no text file or commit message in any commit holds a redacted value now"
if [[ "$binary" != 0 ]]; then
  echo "warning: binary files at these paths still hold a value (git-filter-repo leaves binary files alone); add" \
       "them to PATHS if they must go:"
  cat "$WORK/binary-paths"
fi
git ls-tree -r HEAD > "$WORK/tip-after.txt"
if ! diff -q "$WORK/tip-before.txt" "$WORK/tip-after.txt" >/dev/null; then
  echo "warning: the rewrite changed these files in the current tree of $(git symbolic-ref -q HEAD || echo HEAD);" \
       "every clone's re-sync will change them too:"
  { diff "$WORK/tip-before.txt" "$WORK/tip-after.txt" || true; } | sed -n 's/^[<>] [0-9]* [a-z]* [0-9a-f]*\t/  /p' |
    sort -u
fi

if [[ $PUSH -eq 0 ]]; then
  echo "dry run: nothing pushed. Rerun with --push to force-push every branch and tag."
  exit 0
fi
leases=(); specs=()
for line in "${CLONED[@]}"; do
  ref="${line% *}"; sha="${line#* }"
  leases+=("--force-with-lease=$ref:$sha")
  if git show-ref -q --verify "$ref"; then specs+=("$ref:$ref"); else specs+=(":$ref"); fi
done
if ! git push --atomic "$REMOTE" "${leases[@]}" "${specs[@]}"; then
  echo "push refused, nothing changed on the remote. A ref moved since the clone (someone pushed), or the remote" \
       "said no. Push that work from its clone if needed, then run this again from the start." >&2
  exit 1
fi
new="$(git ls-remote --heads --tags "$REMOTE" | awk '{print $2}' | grep -vxF -f <(printf '%s\n' "${CLONED[@]%% *}") |
       grep -v '\^{}$' || true)"
[[ -n "$new" ]] && echo "warning: the remote has refs this run didn't rewrite (pushed after the clone): $new" >&2
echo "pushed. Now re-sync every clone (docs/PICKUP_FROM_PC22.md), then make the repo public again."
