# Accounts: Google sign-in, roles and Generated spaces

Guests on the network shouldn't be able to inspect or change a deployment's setup (the scheduler and so on); a person
using the app from several places keeps one identity across them; each person gets their own generator space; an
admin can see everyone's. Everything site-specific is deployment config: no site, account or address is named in code.

## Turning it on

1. Turn the `accounts` capability on in `deployments/<id>/deployment.yaml`.
2. Make a Google OAuth web client for the deployment (Google Cloud console → Google Auth Platform: audience External,
   published; Clients → Web application; Authorised JavaScript origin: the deployment's https address). No secret is
   used.
3. In `deployments/<id>/private/accounts.yaml` (gitignored), set `google_client_id: "…"` and list the first admin:
   `people: [{email: …, role: admin, name: …}]`. Re-read on change. After that, admins give roles on
   Settings › People, which rewrites the same file.

With no client ID, nothing is enforced and every caller is an admin, exactly as before. New Generated images are
stamped with their space all the same.

## Who is who

| Who | How Flask knows | Role |
|---|---|---|
| A listed Google account | `sm_session` cookie (signed, 1 year, HttpOnly) | its role in `accounts.yaml`: approved or admin |
| Any other Google account | `sm_session` | guest, with its own space on every device |
| An anonymous browser | `sm_space` cookie (random, made on first visit) | guest |
| A process on this machine | 127.0.0.1 with no `X-Forwarded-For` | passes every gate |

Behind a port proxy every LAN client reaches Flask from the same address, so the network can't tell people apart:
identity is the cookie. Google signs in only on an https origin (not an IP address), so plain-http callers (screens'
display pages, a bare IP address) are always guests, and the display pages (`/display/*`) only use open calls.

## What each role may do

- `config/auth/gates.yaml`: which `/api` calls need which role; anything no rule matches is open. Guests operate (run
  a screen's schedule from the Room page, throw events, set today's values); admins set up (the Scheduler page's
  edits, purges, costs, the bake-off, file edits, reframe settings).
- A destination's role is its `access` in the deployment's `publish-destinations.json` (no `access` = admins only;
  give `_recent`, the Generated tab, `guest` so guests can generate). Its bucket, schedule, publishing to or from it,
  and copying into it all follow that role.
- The pages: Schedules, Jobs, Settings, Lights setup, the admin and developer pages are wrapped in `AdminOnly`
  (`src/components/auth/`); the menu hides them, the screens chips and Tools/Developer for non-admins.

## Roles

- **Guest**: their own Generated pictures, the destinations open to guests, operating the Room page.
- **Approved**: a guest who may also use the deployment's private catalogue (`private/catalogue.json`: its own
  workflows and the routing text that goes with them).
- **Admin**: everything, including Settings › People, everyone's Generated pictures and every destination.

Settings › People (`/settings#people`, `GET/PUT /api/auth/people`) lists everyone who has signed in (and anyone listed)
with a role to pick, and adds someone by e-mail before they first sign in. The last admin can't be removed.

## The private catalogue

- Flask (`routes/utils._load_json_once`) merges `private/catalogue.json` into the workflow catalogue only for callers
  `routes.auth.private_ok()` allows: approved people, admins, processes on this machine, and work with no request
  behind it (schedules, generation threads). Anyone else's router, workflow matching and lists see the shared
  catalogue alone, and the private templates (`private/...` includes) are missing for them.
- `/api/generate-image` leaves a workflow outside the caller's catalogue to the router.
- The generator page never has the private entries in its bundle: it asks `GET /api/workflows/private`, which answers
  `[]` to anyone not allowed them.
- A catalogue entry's `summary` is the one line the workflow menu shows; `description` is for its maintainers.

## Spaces

- A space is `u:<email>` (signed in) or `g:<browser key>` (anonymous).
- `/api/generate-image` notes the batch against the caller's space (`~/.local/state/screen-machine/accounts.db`) and
  the image's side-car in `_recent` gets `space`.
- The Generated tab (`/buckets/_recent/complete`) shows the caller's own; images from before spaces belong to the
  admins; an admin can switch to Everyone's (`?everyone=1`).
- The jobs feed (`/jobs`, `/jobs/stream`): an admin sees every job; anyone else their own generator jobs plus the
  house's jobs for destinations they may use.
  - One screen's feed (`/jobs/stream?target=<screen>`, what its `/display` page reads for the pill) carries every
    job on that screen for anyone, since a screen is never signed in; a job the caller may not see comes down to
    what the pill draws (state, stage, times), with nothing of what was asked for or made.
- Signing in moves the browser's anonymous images into the account.

## Clouds and the hidden Private bucket

- The Generator page's Cloud tab is a menu of cloud stores: destinations with `"cloud": true` (the house's Cloud,
  Private) and each person's own cloud.
- **Each person's own cloud** (`routes/auth/clouds.py`): every signed-in person who isn't an admin has one
  (`cloud-<hash of their e-mail>`, "<first name>'s cloud"), made from `accounts.yaml` and who has signed in, and added
  to the destinations as they are read. It is never written to `publish-destinations.json`. They see only their own;
  admins see all of them. A shared cloud for a group is for later.
- **Hidden buckets** (`routes/auth/lock.py`): a destination with `"locked": true` (Private) is left out of every list
  and answers 404 to every call and `/output/<bucket>/` file until an admin unlocks this browser. To unlock, long-press
  the Cloud tab and type the PIN. It then shows for 30 minutes, or until someone locks it: long-press again, or
  "Lock now" in the Cloud menu.
  - The first PIN typed becomes the PIN; its hash is kept in `private/lock.yaml`. Delete that file to choose another.
  - The unlock is a signed `sm_unlock` cookie bound to the account. The lock applies even with accounts off.
  - Processes on this machine and schedules always see the hidden buckets.

## Not yet

- room_service's own setup routes (`:8700`, `/rs/*`: lights patch, modes editor, devices, remote learning) are not
  gated yet; the pages that use them are admin-only in the app.
- Off the premises, the https proxy could require sign-in for everything (a forward-auth call to Flask).
- `/output/<bucket>/…` files are served to anyone who knows a name; listings are what's filtered.
