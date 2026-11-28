"""Settings › Apps & API keys, end to end in the real page (docs/API.md): make a key, see it once, use it on /api/v1,
revoke it, and see it refused. On a phone (390x844) and a desktop.

Drives the real page headless against the running app. This machine passes the sign-in gates, so the key calls are
real; only the page's own "who am I" is answered as an admin, so the admin-only Settings page shows. It only makes and
revokes its own keys: nothing on the house is touched.

    ~/.venvs/sm-room/bin/python tools/ui_evals/api_keys.py
    ~/.venvs/sm-room/bin/python tools/ui_evals/api_keys.py --phone

Exit 1 if a step fails.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import requests
from playwright.sync_api import sync_playwright

ADMIN = {"accounts": True, "google_client_id": None, "signed_in": True, "email": "ui-eval@localhost",
         "name": "UI eval", "role": "admin", "can_unlock": False, "pin_set": False, "unlocked_until": None}


def run(p, url, phone):
    failures = []

    def check(name, ok):
        print(f"{'ok  ' if ok else 'FAIL'} [{'phone' if phone else 'desktop'}] {name}")
        if not ok:
            failures.append(name)
        return ok

    browser = p.chromium.launch()
    ctx = browser.new_context(**({"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}
                                 if phone else {"viewport": {"width": 1280, "height": 900}}))
    page = ctx.new_page()
    page.route("**/api/auth/me", lambda route: route.fulfill(status=200, content_type="application/json",
                                                            body=json.dumps(ADMIN)))
    made = None
    try:
        page.goto(f"{url}/settings#api")
        fold = page.get_by_test_id("settings-api")
        if not check("the Apps & API keys fold is on Settings", fold.count() == 1):
            return failures
        page.get_by_test_id("api-new-key").click()
        name = f"ui-eval {'phone' if phone else 'desktop'} {time.strftime('%H:%M:%S')}"
        page.get_by_test_id("api-key-name").fill(name)
        page.get_by_test_id("api-key-save").click()
        fresh = page.get_by_test_id("api-secret")
        fresh.wait_for(timeout=10000)
        secret = fresh.input_value()
        made = secret.split("_")[1] if secret.startswith("smk_") else None
        check("the new key is shown once", bool(made))
        check("the page shows its address", page.get_by_test_id("api-base").inner_text().endswith("/api/v1"))
        r = requests.get(f"{url}/api/v1/me", headers={"Authorization": f"Bearer {secret}"})
        check("the key works on /api/v1", r.status_code == 200 and r.json().get("name") == name)
        page.get_by_role("button", name="I've copied it").click()
        check("the key's secret is gone from the page", page.get_by_test_id("api-secret").count() == 0)
        card = page.get_by_test_id(f"api-key-{made}")
        check("the key is listed, by its hint", card.count() == 1 and secret[-4:] in card.inner_text())
        page.get_by_test_id(f"api-revoke-{made}").click()
        page.get_by_test_id(f"api-revoke-yes-{made}").click()
        card.wait_for(state="detached", timeout=10000)
        check("revoked, it leaves the list", card.count() == 0)
        r = requests.get(f"{url}/api/v1/me", headers={"Authorization": f"Bearer {secret}"})
        check("the revoked key is refused", r.status_code == 401)
        made = None
    except Exception as e:
        check(f"the page ran ({e.__class__.__name__}: {str(e)[:200]})", False)
    finally:
        if made:   # never leave a key behind
            requests.delete(f"{url}/api/auth/keys/{made}")
        browser.close()
    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:5000", help="this machine passes the sign-in gates")
    ap.add_argument("--phone", action="store_true")
    ap.add_argument("--desktop", action="store_true")
    args = ap.parse_args()
    which = [True] if args.phone else [False] if args.desktop else [True, False]
    failures = []
    with sync_playwright() as p:
        for phone in which:
            failures += run(p, args.url.rstrip("/"), phone)
    print("\nall passed" if not failures else f"\n{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
