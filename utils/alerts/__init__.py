"""
Siren: in-process production alerting for screen-machine.

Any code path may call alert(category, summary, ...) — it never raises and
never blocks. Dedup, throttling, storm control and delivery happen on a
single self-healing daemon worker (dispatcher.py). Email is sent via the
Gmail REST channel (channels/email_gmail.py), reusing the OAuth grant from
the sibling auto-klevio app.

Design doc: docs/ALERTING_PROPOSAL.md
Self-test:  python -m utils.alerts.selftest
"""

import logging
import time
import traceback as _traceback

_initialized = False
_log = logging.getLogger("screen_machine.alerts")


def alert(category, summary, *, severity=None, detail="", exc=None,
          context=None, dedup_key=None):
    """Raise an operational alert. NEVER raises, NEVER blocks.

    Safe to call from waitress request threads, per-destination asyncio
    scheduler loops, generation worker threads and the WS server thread —
    the only work on the caller's thread is building a dict and a
    non-blocking queue put.
    """
    try:
        from utils.alerts import dispatcher, taxonomy

        exc_text = None
        if exc is not None:
            try:
                exc_text = "".join(_traceback.format_exception(
                    type(exc), exc, exc.__traceback__))[-4000:]
            except Exception:
                exc_text = repr(exc)

        if context is None:
            ctx = {}
        elif isinstance(context, dict):
            ctx = context
        else:
            ctx = {"value": str(context)[:500]}

        record = {
            "category": str(category)[:100],
            "summary": str(summary)[:300],
            "severity": taxonomy.resolve_severity(category, severity),
            "detail": str(detail)[:4000],
            "exc_text": exc_text,
            "context": ctx,
            "dedup_key": str(dedup_key or category)[:200],
            "ts": time.time(),
        }
        dispatcher.submit(record)
    except Exception:
        try:
            _log.exception("alert() itself failed")
        except Exception:
            pass


def init_alerting(flask_app=None):
    """Install global hooks and start the dispatch worker. Idempotent.

    - starts the dispatcher worker (also started lazily by the first alert())
    - installs a threading.excepthook so uncaught exceptions in ANY thread
      (the fire-and-forget generation threads especially) raise an alert
    - registers a Flask errorhandler for unhandled API exceptions
    - emits a log-only startup notice with the previous instance's last-alive age
    """
    global _initialized
    try:
        from utils.alerts import dispatcher

        if not dispatcher.enabled():
            _log.info("alerting disabled (ALERTING_ENABLED)")
            return

        if not _initialized:
            _initialized = True
            _install_thread_excepthook()
            prev_age = dispatcher.previous_instance_age_s()
            if prev_age is not None and prev_age > 600:
                alert("app.startup",
                      f"screen-machine started; previous instance last alive "
                      f"{int(prev_age // 60)} min ago",
                      severity="info")
            else:
                alert("app.startup", "screen-machine started", severity="info")

        if flask_app is not None:
            _register_flask_errorhandler(flask_app)

        dispatcher.start()
    except Exception:
        try:
            _log.exception("init_alerting failed")
        except Exception:
            pass


def _install_thread_excepthook():
    import threading

    orig_hook = threading.excepthook

    def _alerting_excepthook(args):
        try:
            alert("app.thread_crash",
                  f"Uncaught {args.exc_type.__name__} in thread "
                  f"'{getattr(args.thread, 'name', '?')}'",
                  exc=args.exc_value,
                  dedup_key=f"thread-crash:{args.exc_type.__name__}")
        finally:
            orig_hook(args)

    threading.excepthook = _alerting_excepthook


def _register_flask_errorhandler(flask_app):
    from flask import jsonify, request
    from werkzeug.exceptions import HTTPException

    @flask_app.errorhandler(Exception)
    def _alert_unhandled(e):
        if isinstance(e, HTTPException) and (e.code or 500) < 500:
            return e
        try:
            path = request.path
        except Exception:
            path = None
        # Registering this handler suppresses Flask's own traceback logging,
        # so log it here — the alert email may be throttled or undeliverable
        _log.error("unhandled API exception on %s", path, exc_info=e)
        alert("app.unhandled", f"Unhandled API exception: {e}", exc=e,
              dedup_key=f"app.unhandled:{type(e).__name__}",
              context={"path": path})
        if isinstance(e, HTTPException):
            return e
        return jsonify({"error": str(e)}), 500
