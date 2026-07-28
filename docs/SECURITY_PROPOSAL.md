# Screen Machine — Security Hardening Proposal

**Status: PROPOSAL for your review. No code, config, or infrastructure changes have been made. Nothing here is actioned without your explicit go-ahead, item by item.**

---

## 0. Do this first (safeguard)

The last commit is **2026-03-02** — ~4.5 months ago — and the working tree holds a lot of uncommitted work. Before *any* hardening change, commit or branch the current state so there is a restore point. Everything below is easier and safer once there's a clean checkpoint.

---

## 1. The actual problem

- **The reported symptom** (red error boxes on the TVs) is the **Vite *dev* server** being used to serve the public display. Internet scanners probe it; each failed probe makes Vite broadcast an error, which pops the overlay on every connected screen.
- **Underlying issue:** the dev server — and several other services — are exposed directly to the public internet.

## 2. Constraints (as you've set them)

- **No application auth layer**, no login, no auth-gating reverse proxy. The control we use is **network reach** — keep things off the open internet.
- **Preserve the display freshness mechanism** (see §5) — the naive prod switch breaks it.
- Nothing changes without your sign-off.

## 3. What's currently exposed (facts, not proposals)

Windows `netsh` port-forwards to the WSL box, reachable from the internet:

| Public | Service | Notes |
|---|---|---|
| `:8000`→`:8080` | Vite **dev** server | source of the error overlays |
| `:5000` | Flask API | `debug=True`, `CORS(app)` open, no auth |
| `:8765` | overlay websocket | no auth |
| `:2222`→`:22` | **SSH** | password auth, internet-facing |
| `:8081`, `:3001` | other | purpose unconfirmed |
| ngrok tunnel | → Flask `:5000` | second public path, survives firewall changes |

Also present in the **git repo** (committed, and the repo history is on GitHub): a media-server password (`maintenance.log`), Samsung TV tokens (`*-token*.txt`), an AWS access-key id (`* - Copy.json` backups), and a password-named script (`reboot-with-password.sh`).

## 4. Proposed changes — discrete, each needs your approval

Ordered by what fixes the visible problem first. Each is small and reversible.

1. **Serve the production build instead of the dev server.** One-line config change (`config.py`: `STATIC_FOLDER 'build'→'dist'`) so Flask serves the built app + media + API from one origin. Plus a start-script tweak so prod builds rather than launching a public dev server. **Fixes the error overlays.** Requires one network repoint (§6).
2. **Turn off Flask debug** (`config.py`: `DEBUG=False`). Removes the interactive debugger / source disclosure. No behaviour change for the displays.
3. **Stop publishing dangerous endpoints.** Don't register the media-server admin blueprint (it has a remote-command endpoint) or the test UIs on the public app; make them opt-in for local use.
4. **Stop the extra public paths.** Don't auto-start ngrok; don't bind the dev server to a public interface if it's ever run locally.
5. **Reduce open ports** (§6) — the biggest single risk reduction.

## 5. Why "go to prod" isn't a one-liner (the re-architecture)

The display pages detect *"new art was published"* by polling `/output/<screen>.{jpg,mp4}` every second and reading the **`Last-Modified`** HTTP header. The obvious prod command (`npx serve -s dist`) sends **no `Last-Modified`** and turns missing-file requests into `200`s — so the screens would silently freeze on old art. **Flask's existing file route sends the correct `Last-Modified` and 404s properly** (verified). So the proposal is to serve everything through Flask — which preserves the mechanism with **no frontend changes**. This is the whole reason item 4.1 is "serve via Flask" rather than a static server.

## 6. The one decision that shapes the network changes

To reduce exposure, the display's public port must point at Flask (`:5000`), and ideally the other ports close. That splits on one question:

- **Can the TVs/kiosks reach the app over the LAN** (via the Windows host's LAN IP)?
  - **Yes** → close *all* public forwards; point kiosks at the LAN; use a VPN (Tailscale/WireGuard — network reach, not app auth) if you ever need remote access. Strongest, no code.
  - **No, must stay public** → keep only the display→Flask forward open; accept that the API is reachable with it (no-auth trade-off); still close SSH/`:8081`/`:3001`/ngrok.

All port changes are `netsh` commands on the **Windows host** (need an elevated shell — I can't run them from WSL).

## 7. Left to your judgement (not pushing)

- **Secret rotation.** The items in §3 are exposed. You've said you don't want to rotate; noted. The risk is documented here so the decision is explicit and yours.
- **Git history.** The committed secrets are in history on GitHub. Making the repo private and/or rotating are the levers; history-rewriting (`filter-repo`/force-push) is deliberately **not** proposed given the volume of uncommitted work.
- **The admin endpoint's injection flaw** — worth fixing if you keep that panel for local use.

## 8. Explicitly NOT proposed

Auth layer, login, reverse-proxy-with-auth, per-request API tokens, any history rewrite or force-push, and any destructive git command. Nothing here touches your uncommitted work.
