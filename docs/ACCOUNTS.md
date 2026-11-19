# Accounts: Google sign-in, roles and Generated spaces

Owner, 19 Nov 2026: guests on the Wi-Fi shouldn't be able to inspect or change the house's setup (the scheduler and so
on); the owner uses the app from several places and wants one identity across them; each person (e.g. family) gets
their own generator space; an admin can see everyone's.

## Turning it on

1. Google Cloud console → APIs & Services → Credentials → Create OAuth client ID → Web application. Authorised
   JavaScript origin: the https address (`https://screen.finsburybankforsavings.com`). No secret is needed.
2. Put the client ID in `deployments/<id>/deployment.yaml` → `accounts: {google_client_id: "…"}` (the `accounts`
   capability must be on). Flask picks it up without a restart.
3. List people in `deployments/<id>/private/accounts.yaml` (gitignored):
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

Every LAN client reaches Flask through the Windows port proxy (172.28.240.1), so the network can't tell guests from
the owner: identity is the cookie. Google signs in only on the https address (it won't take an IP origin), so the http
doors (the TVs, `http://192.168.1.205:8080`) are always guests, and their pages (`/display/*`) only use open calls.

## What each role may do

- `config/auth/gates.yaml`: which `/api` calls need which role; anything no rule matches is open. Guests operate (run
  a screen's schedule from the Room page, throw events, set today's values); admins set up (the Scheduler page's
  edits, purges, costs, the bake-off, file edits, reframe settings).
- A destination's role is its `access` in `publish-destinations.json` (no `access` = admins only). Finsbury: `_recent`
  (Generated), `north-screen` and `south-screen` are `guest`. Its bucket, schedule, publishing to or from it, and
  copying into it all follow that role.
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
- Off the premises, Caddy could require sign-in for everything (forward_auth to Flask) and drop the `/room` password.
- `/output/<bucket>/…` files are served to anyone who knows a name; listings are what's filtered.
