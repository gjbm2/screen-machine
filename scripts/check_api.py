#!/usr/bin/env python3
"""End-to-end check of the generation API (docs/API.md) on the running app: a key made, used, refused where it should
be, one real picture made through /api/v1 and fetched back, the key rolled and revoked. It runs from this machine
(127.0.0.1 passes the sign-in gates, so it can make its own key) and always revokes the key it made.

    .venv/bin/python scripts/check_api.py                 # with one real picture (a fast workflow, a few pence)
    .venv/bin/python scripts/check_api.py --no-generate   # everything but the picture: costs nothing

It never publishes to a screen. The picture lands in the Generated space of the key's maker, which from here is a
fresh anonymous space (admins see it under Everyone's). Exit 1 if any step fails.
"""
import argparse
import sys
import time
import uuid

import requests

FAST = ("krea2-turbo", "zimage-turbo", "qwen21-turbo")   # quick text-to-image workflows, first that exists
failures = []


def step(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + str(detail) if detail and not ok else ''}")
    if not ok:
        failures.append(name)
    return ok


def code(r):
    try:
        return (r.json().get("error") or {}).get("code")
    except ValueError:
        return None


def lan_url(base):
    """The app by this machine's own network address rather than 127.0.0.1, so it is not a local caller."""
    if "127.0.0.1" not in base:
        return None
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 9))   # nothing is sent: this only picks the outgoing interface
        return base.replace("127.0.0.1", sock.getsockname()[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5000", help="the app (this machine passes the gates)")
    ap.add_argument("--no-generate", action="store_true", help="skip the real picture")
    ap.add_argument("--timeout", type=float, default=600, help="seconds to wait for the picture")
    args = ap.parse_args()
    base, v1 = args.url.rstrip("/"), args.url.rstrip("/") + "/api/v1"
    s = requests.Session()

    r = s.get(v1)
    step("index is open", r.status_code == 200 and "spec" in r.json(), r.text[:200])
    r = s.get(f"{v1}/openapi.json")
    step("spec is open", r.status_code == 200 and r.json().get("openapi", "").startswith("3.1"), r.text[:200])

    r = s.post(f"{base}/api/auth/keys", json={"name": f"check_api {time.strftime('%d %b %H:%M')}", "per_day": 5,
                                              "concurrent": 1, "expires_days": 1})
    if not step("make a key", r.status_code == 201, r.text[:300]):
        return 1
    key, secret = r.json()["key"], r.json()["secret"]
    auth = {"Authorization": f"Bearer {secret}"}
    try:
        r = s.get(f"{v1}/me", headers=auth)
        step("the key works", r.status_code == 200 and r.json().get("id") == key["id"], r.text[:200])
        step("rate-limit headers", "RateLimit-Remaining" in r.headers and r.headers.get("Cache-Control") == "no-store")
        lan = lan_url(base)   # waitress drops X-Forwarded-For, so only a caller from another address is not local
        if lan:
            r = requests.get(f"{lan}/api/v1/me", timeout=10)
            step("no key, from the LAN: 401 key_required", r.status_code == 401 and code(r) == "key_required", r.text[:200])
        r = requests.get(f"{v1}/me", headers={"Authorization": f"Bearer {secret[:-2]}xx"})
        step("a wrong key: 401 invalid_key", r.status_code == 401 and code(r) == "invalid_key", r.text[:200])
        r = s.get(f"{base}/api/jobs", headers=auth)
        step("a key off /api/v1: 403", r.status_code == 403 and code(r) == "wrong_api", r.text[:200])
        r = s.get(f"{base}/api/auth/keys", headers=auth)
        step("a key can't manage keys", r.status_code == 403, r.text[:200])

        r = s.get(f"{v1}/workflows", headers=auth)
        wfs = {w["id"]: w for w in r.json().get("data", [])} if r.ok else {}
        step("workflows listed", bool(wfs), r.text[:200])
        r = s.get(f"{v1}/refiners", headers=auth)
        step("refiners listed", r.ok and any(x["id"] == "none" for x in r.json()["data"]), r.text[:200])
        r = s.get(f"{v1}/destinations", headers=auth)
        step("no screens without publish", r.ok and r.json()["data"] == [], r.text[:200])

        r = s.post(f"{v1}/generations", headers=auth, json={"prompt": "x", "colour": "red"})
        step("unknown field: 400", r.status_code == 400 and code(r) == "bad_request", r.text[:200])
        r = s.post(f"{v1}/generations", headers=auth, json={"prompt": "x", "workflow": "no-such"})
        step("unknown workflow: 400", r.status_code == 400 and code(r) == "unknown_workflow", r.text[:200])
        screen = next((d for d in requests.get(f"{base}/api/publish-destinations").json()
                       if d.get("has_bucket") and not d["id"].startswith("_")), {}).get("id", "north-screen")
        r = s.post(f"{v1}/generations", headers=auth, json={"prompt": "x", "publish_to": screen})
        step("publish without the scope: 403", r.status_code == 403 and code(r) == "scope_missing", r.text[:200])
        r = s.post(f"{v1}/generations", headers=auth, json={"prompt": "x", "images": [{"url": "http://192.168.1.1/a.jpg"}]})
        step("a LAN image url: 400", r.status_code == 400 and code(r) == "image_not_public", r.text[:200])

        if not args.no_generate:
            wf = next((w for w in FAST if w in wfs), None)
            idem = {**auth, "Idempotency-Key": str(uuid.uuid4())}
            body = {"prompt": "a small red lighthouse on a rock at dusk, photograph", "workflow": wf or "auto",
                    "refiner": "none", "n": 1}
            r = s.post(f"{v1}/generations", headers=idem, json=body)
            if step(f"start a generation ({wf or 'auto'})", r.status_code in (200, 202) and r.json().get("id"), r.text[:300]):
                gen = r.json()["id"]
                step("Location header", r.headers.get("Location", "").endswith(f"/api/v1/generations/{gen}"))
                r = s.post(f"{v1}/generations", headers=idem, json=body)
                step("retry with the same Idempotency-Key: same generation",
                     r.ok and r.json().get("id") == gen and r.headers.get("Idempotent-Replayed") == "true", r.text[:200])
                end = time.time() + args.timeout
                g = r.json()
                while g.get("status") in ("queued", "running") and time.time() < end:
                    g = s.get(f"{v1}/generations/{gen}?wait=30", headers=auth).json()
                    print(f"     … {g.get('status')} {g.get('progress') or ''}")
                if step("the picture is made", g.get("status") == "succeeded" and g.get("outputs"), g):
                    out = g["outputs"][0]
                    f = s.get(out["download_url"], headers=auth)
                    step("download_url gives the picture", f.ok and f.content[:3] == b"\xff\xd8\xff", f.status_code)
                    f = requests.get(out["url"].replace(out["url"].split("/output/")[0], base))
                    step("url gives the picture", f.ok and len(f.content) > 1000, f.status_code)
                    r = s.get(f"{v1}/generations", headers=auth)
                    step("it is in the key's list", r.ok and any(x["id"] == gen for x in r.json()["data"]))
                    job = (s.get(f"{base}/api/jobs?source=api&limit=20").json().get("jobs") or [])
                    step("its job shows as source api", any(j.get("batch_id") == gen for j in job))

        r = s.post(f"{base}/api/auth/keys/{key['id']}/roll", json={"grace_hours": 0})
        new = r.json().get("secret") if r.ok else None
        step("roll the key", bool(new), r.text[:200])
        if new:
            step("the old secret stops", s.get(f"{v1}/me", headers=auth).status_code == 401)
            auth = {"Authorization": f"Bearer {new}"}
            step("the new secret works", s.get(f"{v1}/me", headers=auth).status_code == 200)
    finally:
        r = s.delete(f"{base}/api/auth/keys/{key['id']}")
        step("revoke the key", r.ok and r.json()["key"]["state"] == "revoked", r.text[:200])
    r = s.get(f"{v1}/me", headers=auth)
    step("a revoked key: 401 key_revoked", r.status_code == 401 and code(r) == "key_revoked", r.text[:200])

    print(f"\n{len(failures)} failed: {', '.join(failures)}" if failures else "\nall passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
