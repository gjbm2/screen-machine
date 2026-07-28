"""End-to-end alerting self-test:  python -m utils.alerts.selftest

Sends one real email via the Gmail channel (bypassing throttle state), then
optionally exercises the full alert() -> dispatcher -> channel pipeline with
--pipeline. Prints token/send status; never prints secret values.
"""

import argparse
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline", action="store_true",
                        help="also exercise the full alert() pipeline")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    from utils.alerts.channels.email_gmail import GmailChannel
    ch = GmailChannel()
    if not ch.configured():
        print(f"NOT CONFIGURED — missing env vars: {ch.missing_env()}")
        return 1

    print("Sending channel-level test email...")
    ok = ch.send(
        "[screen-machine] Alerting self-test",
        "If you can read this, the screen-machine -> Gmail alert channel "
        "works.\n\nSent by: python -m utils.alerts.selftest")
    print(f"channel send: {'OK' if ok else 'FAILED'}")
    if not ok:
        return 1

    if args.pipeline:
        import os
        os.environ["ALERTING_ENABLED"] = "1"
        from utils.alerts import alert, dispatcher
        alert("selftest", "Full-pipeline alerting self-test",
              severity="critical",
              detail="Raised via alert(); delivered by the dispatch worker.",
              dedup_key=f"selftest-{int(time.time())}")
        deadline = time.time() + 30
        while time.time() < deadline:
            s = dispatcher.stats()
            if s["queued"] == 0 and s["last_email_success"]:
                print("pipeline send: OK")
                return 0
            time.sleep(0.5)
        print(f"pipeline send: TIMED OUT (stats: {dispatcher.stats()})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
