# Picking up the deployments branch on PC22

Written for whoever (the owner, or a session on PC22) brings PC22 onto the deployments layout. Steps 1–5 below are in
order and each depends on the one before. `deployments/README.md` has the general reference this note points into.

## Where things stand

- **`main`** on GitHub (97c41338) already has the deployments layout: `deployments/<id>/`, `smconfig`, and every
  service reading its site through it. It was built and tested on the owner's laptop, plus a rehearsal of the
  migration on a copy of PC22's last pushed commit. PC22 itself hasn't pulled it.
- **`deployments-generalisation`** (this branch) builds on `main`:
  - every single-site assumption the topology audit found, fixed, plus Energy acting now and a picked palette
    beating colour-repainting effects;
  - multi-room: rooms with their own modes (`"own_modes": true`) and rooms outside the show (`"show": false`). It is
    inert until `rooms.json` opts a room in, and finsbury doesn't (see step 6). If PC22 merged this branch before
    multi-room landed, `git fetch origin && git merge origin/deployments-generalisation` brings it in as plain code,
    with no second migration.
  - the public-repo cleanup, the secrets tool, and the rewritten README.

  **Merge this branch on PC22, not just `main`.**
- **PC22 has uncommitted local work** that is in no commit. One example is the Room page's "Looks good" option.
  It must be committed on PC22 first, or the merge will refuse to run or will mix it up.
- **The repo is private for now.** Some historic commits hold files that should never have been public. The order is:
  1. PC22 merges and pushes;
  2. the history is purged;
  3. every clone re-syncs;
  4. the repo goes public again.

  Don't make the repo public before step 5 below is done.

## 1. Before you start

- **Can PC22 fetch a private repo?** Run `git remote -v` in `~/dev/screen-machine`. An `https://` remote without a
  stored credential stops working once the repo is private. Fix that with either:
  - SSH: add PC22's WSL key to the GitHub account, then
    `git remote set-url origin git@github.com:gjbm2/screen-machine.git`;
  - or a credential helper.

  Check it with `git fetch origin`.
- **Tell the other sessions on PC22.** Ask them to commit their work, then stop editing `config/`, `routes/data/`,
  `deploy/`, `room_service/`, `show_service/`, `stream_service/` and `src/` until step 4 is done.
- **Commit PC22's local work, every session its own paths** (CLAUDE.md rule 9). `git status` must end up clean apart
  from untracked files. Before you commit, check that the "Looks good" option is in the diff.
- **Keep anything you want from `debug/` and `wav/`.** The merge stops tracking both, so it removes them from this
  checkout. Copy them somewhere else first if you want them.
- If a Samsung token file at the repo root shows as modified, follow `deployments/README.md` › "Migrating PC22" ›
  "Before you start".

## 2. Merge and migrate

```bash
cd ~/dev/screen-machine
scripts/ctl stop all; ./start.sh stop flask             # or Spectre's cards

git fetch origin
git merge origin/deployments-generalisation             # includes main
#   Conflicts: PC22's edits to files that moved (config/room/*, routes/data/*, deploy/*) normally follow the move,
#   because git detects the rename. Any conflict left is PC22's change versus the branch's: keep both intents, and
#   ask if unsure. Then `git commit`.

.venv/bin/python scripts/migrate_deployment.py --deployment finsbury            # read the plan
.venv/bin/python scripts/migrate_deployment.py --deployment finsbury --apply    # must end "OK"
```

The migration compares every moved file with the commit before the merge (`ORIG_HEAD`). That commit holds PC22's own
work, so PC22's edits count as the "before". It also does the following:

- adds `SM_DEPLOYMENT=finsbury` to `.env`;
- adds `NGROK_DOMAIN` to `.env`, recovered from the old `start.sh`;
- moves the Samsung tokens to `~/.config/screen-machine/samsung/`;
- runs `python -m smconfig check`.

`deployments/README.md` › "Migrating PC22" explains each line of its report.

Before `scripts/ctl start all`, check the report's `PyYAML in ~/.venvs/<name>` lines (one per `~/.venvs/sm-*`). Each
must say `there` or `installed now`. dot_service and the piano and score workers import smconfig, which needs
PyYAML. A `FAIL` line (pip had no network, say) ends with the command to run by hand; run it, then run `--apply`
again.

Then:

```bash
npx vite build --outDir dist_next --emptyOutDir && rm -rf dist_prev && mv dist dist_prev && mv dist_next dist
./start.sh start flask; scripts/ctl start all
.venv/bin/python scripts/migrate_deployment.py --deployment finsbury --verify-live
python -m smconfig.secrets check          # every secret finsbury's capabilities use: should all say "set"
```

## 3. Test

Work through `deployments/README.md` › "Testing after the migration" (static checks, the room, screens, the show,
generation, tags and TVs, performance). Then check these PC22-visible changes. All are intended, and all were
approved:

- **Alexa "change the scene"** with both TVs live moves the room once, not twice. So do the Room page's Next scene and
  a mode change into a new scene pool.
- **Disco's brightness** (any mode's `brightness.lamps`) dims the DMX/rig lights too, not only Hue. finsbury has no
  Art-Net lights today, so nothing changes yet.
- **Settings › Look** lists the Lab (a one-screen room).
- **The header's lights word** counts DMX lights.
- **The admin page's** screens come from the deployment and look the same.
- **Energy** (Calm/Steady/Lively/Wild) changes the playing scene within about 2 s. A big step picks a new scene. Steady
  is exactly as before.
- **A picked palette** keeps colour-repainting effects off. Effects you put on by hand stay. The Look card says
  "Covered by …" when an effect is repainting.
- **Live card titles** start with the room's name while the Drawing room and the Lab are live together.
- **Labels** use screen names ("North screen", not "north-screen") in a few room_service messages.
- `tools/room/latency_cal/calibrate.py` now needs `--screen`. It used to default to south.

**The golden tests** (`room_service/tests/test_golden.py`, `show_service/tests/test_room_scope.py`) record what
room_service and show_service build from finsbury's files: status, modes, plans, groups and players. On the laptop they
prove that multi-room changes nothing for finsbury. On PC22 they can fail for one benign reason: PC22's own
deployment files differ from the ones they were recorded from (for example, modes edited on the Room page). The
failure message names the changed files when that's the cause. Check that the diff matches those edits, then
re-record with `SM_GOLDEN_RECORD=1 .venv/bin/python -m pytest room_service/tests/test_golden.py -q -p no:cacheprovider`
and commit the re-recorded goldens.

**Everything else must look and behave exactly as before.** That includes the art director, the generator and
tagger prompts, and the wording on every page; finsbury's `deployment.yaml` `wording` keeps them identical. Multi-room
changes nothing until a room opts in, and finsbury's doesn't yet (step 6). The golden tests check that.

## 4. Push

```bash
git push origin HEAD:main                # fast-forward: the merge contains origin/main
git push origin --delete deployments-generalisation    # optional, once main has it
```

From here on, nobody pushes commits made before the purge (next step).

## 5. After the merge: purge, then public again

Do this once step 4 is pushed (with everything on this branch merged) and no machine has unpushed work. Unpushed
commits anywhere would be stranded on the old history.

1. **Rotate first.** The purge doesn't un-publish anything someone already copied. Change these and re-pair the
   TVs:
   - the Wi-Fi passwords;
   - the network controller's device SSH and admin passwords;
   - the kiosk's login password, and make its SSH key-only.
2. **Purge, on PC22, straight after its push.** Then nothing new can land on the old history in between. It needs
   SSH push access to GitHub and `python3 -m venv`; the script installs `git-filter-repo` into a throwaway venv.
   ```bash
   scripts/purge_public_history.sh           # dry run: rewrites a temp mirror and shows what goes
   scripts/purge_public_history.sh --push    # force-pushes the rewritten branches and tags
   ```
   It removes `debug/`, `media-server/maintenance.log`, `wav/` and the TV token files from every commit.
3. **Re-sync every other clone**: the laptop, and anything else. PC22 needs it too, because the script rewrote a
   temp mirror, not PC22's checkout. The old commits must never be pushed again. On each machine:
   ```bash
   git status                               # must be clean; commit and push anything first (before step 2!)
   git fetch origin
   git reset --hard origin/main             # one-off, owner-approved exception to rule 9: the history changed under it
   git branch -D deployments-generalisation 2>/dev/null || true
   ```
   Untracked and ignored files (`.env`, `output/`, `logs/`, venvs, `dist/`) are kept. The tracked files come out
   identical, except the purged ones, which were already gone from the tree.
4. Optionally ask GitHub Support to drop cached views of the old commits.
5. **Make the repo public again**: Settings › General › Change visibility, or
   `gh repo edit gjbm2/screen-machine --visibility public --accept-visibility-change-consequences`. Then tell the
   laptop's session to re-sync (step 3) if it hasn't.

## 6. Optional, later: the Lab gets its own modes

Multi-room is in the code, but finsbury doesn't use it. Opting the Lab in fixes "drawing-room modes also move devtest".
It is a config change, prepared and checked in `docs/multiroom/finsbury-lab/`: `apply.py` edits PC22's own files in
place (dry run, `--apply`, `--rollback`), and `APPLY.md` has the steps, the checks on PC22 and the rollback. Do it once steps 1–5 have settled, while the site is not in
Live test, and tell the other sessions first (it restarts Flask).

## Sharing API keys with the laptop (optional)

```bash
# PC22:
python -m smconfig.secrets export /mnt/c/Users/Greg/OneDrive/Private/screen-machine-keys.env
# laptop, once it has synced:
python -m smconfig.secrets import /mnt/c/Users/<you>/OneDrive/Private/screen-machine-keys.env
```

Only the shareable API keys travel. Pairings stay with their machine. See `deployments/README.md` › "Secrets".

## Rollback

`deployments/README.md` › "Migrating PC22" › "Rollback" reverts without reset or checkout. Before the purge, revert the
merge commit (`git revert -m 1 <merge>`) instead of the individual deployments commits.
