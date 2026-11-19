# Accounts: Google sign-in, roles and Generated spaces

Guests on the network shouldn't be able to inspect or change a deployment's setup (the scheduler and so on); a person
using the app from several places keeps one identity across them; each person gets their own generator space; an
admin can see everyone's. Everything site-specific is deployment config: no site, account or address is named in code.

## Turning it on

1. Turn the `accounts` capability on in `deployments/<id>/deployment.yaml`.
2. Make a Google OAuth web client for the deployment (Google Cloud console → Google Auth Platform: audience External,
   published; Clients → Web application; Authorised JavaScript origin: the deployment's https address). No secret is
   used.
3. In `deployments/<id>/private/accounts.yaml` (gitignored), set `google_client_id: "…"` and list the people:
   `people: [{email: …, role: admin|member, name: …}]`. Re-read on change.

With no client ID, nothing is enforced and every caller is an admin, exactly as before. New Generated images are
stamped with their space all the same.

## Who is who

| Who | How Flask knows | Role |
|---|---|---|
| A listed Google account | `sm_session` cookie (signed, 1 year, HttpOnly) | its role in `accounts.yaml` |
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

## Spaces

- A space is `u:<email>` (signed in) or `g:<browser key>` (anonymous).
- `/api/generate-image` notes the batch against the caller's space (`~/.local/state/screen-machine/accounts.db`) and
  the image's side-car in `_recent` gets `space`.
- The Generated tab (`/buckets/_recent/complete`) shows the caller's own; images from before spaces belong to the
  admins; an admin can switch to Everyone's (`?everyone=1`).
- The jobs feed (`/jobs`, `/jobs/stream`): an admin sees every job; anyone else their own generator jobs plus the
  house's jobs for destinations they may use.
- Signing in moves the browser's anonymous images into the account.

## Not yet

- room_service's own setup routes (`:8700`, `/rs/*`: lights patch, modes editor, devices, remote learning) are not
  gated yet; the pages that use them are admin-only in the app.
- Off the premises, the https proxy could require sign-in for everything (a forward-auth call to Flask).
- `/output/<bucket>/…` files are served to anyone who knows a name; listings are what's filtered.
