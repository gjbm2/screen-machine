# The generation API (/api/v1)

Other apps and AI agents can make pictures and videos with the deployment's generation workflows. Everything is JSON
over HTTPS, versioned under `/api/v1`, with an API key on every call. The machine-readable description is
`GET /api/v1/openapi.json`, and it is open: an agent can be pointed at it.

- Base URL: the app's own address plus `/api/v1` (Settings › Apps & API keys shows it, e.g.
  `https://<the deployment's https name>/api/v1`).
- Turned on by the `api` capability (`deployments/<id>/deployment.yaml`; it needs `generation`).
- Code: `routes/api_v1/` (routes, runs, inputs, spec) and `routes/auth/keys.py`. Gates: `config/auth/gates.yaml`
  (`role: key`).

## Keys

Admins make keys on **Settings › Apps & API keys** (`/settings#api`; the menu's Developer fold links there too).

| | |
|---|---|
| Format | `smk_<12-char id>_<43-char secret>`. The id isn't secret (it names the key in lists, logs and jobs). |
| Sending it | `Authorization: Bearer smk_…` (or `X-API-Key: smk_…`). Never in a URL. |
| Kept | A SHA-256 of the secret only (`accounts.db` in the state folder). The whole key is shown once, when it is made or rolled. |
| Where it works | `/api/v1` only. A key on any other `/api` path gets 403 `wrong_api`. |
| Scopes | `generate` (make pictures) and `publish` (also put them on the screens ticked on the key). |
| Private catalogue | Off by default; a key with it on may use the deployment's private workflows (as an approved person may). |
| Limits | Pictures in any 24 hours (default 100) and generations at once (default 3); 0 = no limit. 120 requests a minute, fixed. |
| Expiry | 30 days, 90 days, 1 year (default) or never. |
| Rolling | "New secret" gives the key a new secret. The old one stops now or works 24 hours more, so an app can be moved over. |
| Revoking | At once and for good. The key stays in a folded list, for the record. |
| Records | Last used (when, from which address), the use count, and the pictures in the last 24 h are shown per key. Each generation is a row against its key; its jobs show on the Jobs page as `api`. |

What a key makes lands in the **Generated** tab of whoever made the key. It goes on a screen only with `publish_to`,
the `publish` scope and that screen on the key. The keys work whether or not sign-in is on. A process on this machine
(127.0.0.1, not through the proxy) may call `/api/v1` without a key, as it passes every gate.

## Calls

| Call | What it does |
|---|---|
| `GET /api/v1` | Index (open) |
| `GET /api/v1/openapi.json` | The OpenAPI 3.1 description (open) |
| `GET /api/v1/me` | The calling key: scopes, screens, limits, use in the last 24 h |
| `GET /api/v1/workflows` | The workflows this key may use: id, what it makes (`image`/`video`), the images it needs, and its parameters (type, options, range, default) |
| `GET /api/v1/refiners` | The prompt refiners (`none` sends the prompt as it is) |
| `GET /api/v1/destinations` | The screens this key may publish to |
| `POST /api/v1/generations` | Start a generation: 202 with its id and `Location` |
| `GET /api/v1/generations` | This key's generations, newest first (`limit`, `before` = the previous page's `next_before`) |
| `GET /api/v1/generations/{id}` | Its state, progress and outputs; `?wait=` (up to 60 s) long-polls |
| `DELETE /api/v1/generations/{id}` | Cancel it |
| `GET /api/v1/generations/{id}/outputs/{n}` | The file (JPEG or MP4), with the key |

### Making something

```bash
curl -s https://…/api/v1/generations \
  -H "Authorization: Bearer $SM_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"prompt": "a lighthouse at dusk", "workflow": "krea2-turbo", "params": {"seed": 7}, "wait": 60}'
```

Body fields (anything else is a 400):

- `prompt`: up to 4000 characters.
- `workflow`: an id from `/workflows`, or `auto` (the default: the router picks from the prompt and images).
- `refiner`: an id from `/refiners`, `none`, or `auto` (the default).
- `n`: 1 to 4.
- `params`: the workflow's own parameters (`shape`, `width`, `height`, `seed`, `video_seconds` …), checked against it.
- `images`: up to 4. Each is one of:
  - `{"data": "<base64 or a data: URI>"}`;
  - `{"url": "https://…"}`: public internet only. LAN and loopback addresses are refused, redirects aren't followed,
    and the limit is 20 MB;
  - `{"generation": "gen_…", "output": 0}`: one of this key's own pictures, to work from it.
- `publish_to`: a screen to put the result on, too.
- `wait`: hold the answer up to this many seconds (0 to 60) for the result.

A generation's `status` is `queued`, `running`, `succeeded`, `failed` or `cancelled`. While it is going, `progress`
gives each job's stage and percent. Once it has succeeded, `outputs` gives each picture's `url` (the file on this
server, open to anyone with the address), its `download_url` (the same file, with the key), the seed, the prompt the
model got, the workflow, the seconds it took and the cost. Videos take minutes, so poll with `?wait=30`.

### Retries

Send an `Idempotency-Key` header (any string up to 200 characters) with a POST. The same key and body again returns
the first generation (header `Idempotent-Replayed: true`). The same key with a different body is a 409.

### Errors

Every error is `{"error": {"code": "…", "message": "…"}}`.

| Status | Codes |
|---|---|
| 400 | `bad_request`, `bad_params`, `bad_image`, `image_not_public`, `image_unreachable`, `image_too_large`, `images_required`, `unknown_workflow`, `unknown_refiner`, `unknown_destination` |
| 401 | `key_required`, `invalid_key`, `key_revoked`, `key_expired` (with `WWW-Authenticate`) |
| 403 | `scope_missing`, `destination_not_allowed`, `wrong_api` |
| 404 | `not_found` (another key's generation is not found, too) |
| 409 | `idempotency_conflict` |
| 429 | `rate_limited`, `too_many_running`, `quota_exceeded`, each with `Retry-After` |
| 503 | `api_off`, `generation_off` |

Successful answers carry `Cache-Control: no-store` and `RateLimit-Limit`/`RateLimit-Remaining`.

## Tests and checks

- `tests/unit/test_api_v1.py`: keys (shown once, hashed, rolled with and without grace, revoked, expired), the gates
  (v1 only, scopes, screens, with sign-in off too), the rate limit, the limits, idempotency, cancel, a run lost to a
  restart, image intake (base64, a key's own output, LAN addresses refused). The generator is a stand-in.
- `scripts/check_api.py`: end to end on the running app. It makes a key, checks the refusals, makes one real picture
  (a fast workflow, a few pence; `--no-generate` skips it), fetches it back, finds its job, then rolls and revokes the
  key. It never publishes.
- `tools/ui_evals/api_keys.py`: the Settings fold in a real browser, on a phone and a desktop. It makes a key, uses it,
  revokes it and sees it refused.

## Not yet

- No webhooks: poll with `?wait=`. A signed callback is the natural next step if an app needs one.
- `outputs[].url` is under `/output/`, which serves any file to anyone who knows its name (docs/ACCOUNTS.md "Not
  yet"). `download_url` needs the key.
- The URL check resolves the host before fetching. A host that changes its address between the check and the fetch
  (DNS rebinding) isn't caught.
- Only admins make keys; people can't yet make their own.
