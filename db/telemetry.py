"""
db/telemetry.py  -  Phase 2: Logfire logging.

setup() connects Logfire with LOGFIRE_TOKEN from the secrets. Without a token (or if the
package is missing) everything still works: spans are simply not sent anywhere.
Only IDs and counts are ever sent: never names, titles, emails, keys or passwords.
"""
import contextlib

from db.connect import get_secret

_ready = None


def setup():
    """Returns the logfire module (sending only if a token is set), or None."""
    global _ready
    if _ready is not None:
        return _ready or None
    try:
        import logfire
    except ImportError:
        _ready = False
        return None
    token = get_secret("LOGFIRE_TOKEN")
    logfire.configure(token=token or None, send_to_logfire=bool(token),
                      service_name="org-chart", console=False)
    _ready = logfire
    return logfire


def span(name, **attributes):
    """A Logfire span, or a do-nothing block when Logfire isn't available."""
    lf = setup()
    return lf.span(name, **attributes) if lf else contextlib.nullcontext(_NoSpan())


class _NoSpan:
    def set_attribute(self, *args):
        pass

    def set_attributes(self, *args):
        pass


def sending():
    """True when spans actually go to Logfire (a token is set)."""
    return bool(get_secret("LOGFIRE_TOKEN")) and setup() is not None
