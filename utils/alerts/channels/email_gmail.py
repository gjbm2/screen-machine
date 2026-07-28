"""Gmail delivery via two plain REST calls, no Google SDK.

Reuses the OAuth grant from the sibling auto-klevio app: the same
GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN values
(hand-copied into .env — refresh tokens are shareable and don't rotate on
use; the granted gmail.modify scope covers sending). The From: header is
omitted so Gmail stamps the authenticated sender.
"""

import base64
import logging
import os
import time
from email.message import EmailMessage

import requests

from utils.alerts.channels.base import AlertChannel

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
SEND_URL = f"{API_BASE}/messages/send"

REQUIRED_ENV = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                "GOOGLE_REFRESH_TOKEN", "ALERT_EMAIL_TO")


class GmailChannel(AlertChannel):
    name = "gmail"

    def __init__(self):
        self._token = None  # (access_token, expiry_epoch)
        self._sender = None  # cached profile emailAddress
        self._log = logging.getLogger("screen_machine.alerts")

    def configured(self):
        return all(os.getenv(k) for k in REQUIRED_ENV)

    def missing_env(self):
        return [k for k in REQUIRED_ENV if not os.getenv(k)]

    def _access_token(self):
        now = time.time()
        if self._token and self._token[1] - 60 > now:
            return self._token[0]
        r = requests.post(TOKEN_URL, data={
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "refresh_token": os.getenv("GOOGLE_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        }, timeout=10)
        r.raise_for_status()
        tok = r.json()
        self._token = (tok["access_token"], now + float(tok.get("expires_in", 3600)))
        return self._token[0]

    def _sender_address(self, token):
        if self._sender is None:
            r = requests.get(f"{API_BASE}/profile",
                             headers={"Authorization": f"Bearer {token}"},
                             timeout=10)
            if r.status_code == 200:
                self._sender = r.json().get("emailAddress", "")
        return self._sender or ""

    def _self_mailbox(self, token, to):
        """Self-addressed sends get only the SENT label — Gmail creates no
        inbox copy when the recipient resolves to the sending mailbox (the
        case here: alerts@gregmarsh.co.uk is an alias of the sender). Detect
        by domain match unless ALERT_SELF_MAILBOX=1/0 overrides."""
        override = os.getenv("ALERT_SELF_MAILBOX")
        if override is not None:
            return override not in ("0", "false", "False", "")
        sender = self._sender_address(token)
        try:
            return sender.split("@")[1].lower() == to.split("@")[1].lower()
        except Exception:
            return False

    def send(self, subject, body):
        try:
            if not self.configured():
                return False
            to = os.getenv("ALERT_EMAIL_TO")
            token = self._access_token()
            msg = EmailMessage()
            msg["To"] = to
            # CR/LF in a header raises ValueError from EmailMessage
            msg["Subject"] = " ".join(str(subject).split())
            msg.set_content(body)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
            r = requests.post(
                SEND_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"raw": raw}, timeout=15)
            if r.status_code != 200:
                if r.status_code == 401:
                    self._token = None  # stale/revoked: force a fresh refresh
                self._log.warning("gmail send failed: HTTP %s %s",
                                  r.status_code, r.text[:300])
                return False
            msg_id = (r.json() or {}).get("id")
            if msg_id and self._self_mailbox(token, to):
                m = requests.post(
                    f"{API_BASE}/messages/{msg_id}/modify",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"addLabelIds": ["INBOX", "UNREAD"]}, timeout=10)
                if m.status_code != 200:
                    self._log.warning(
                        "gmail self-delivery label add failed: HTTP %s "
                        "(message only in Sent)", m.status_code)
            return True
        except Exception as e:
            self._log.warning("gmail send failed: %s", e)
            return False
