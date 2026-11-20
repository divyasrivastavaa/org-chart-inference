"""
db/connect.py  -  Phase 2: database. One way to open a connection to the Postgres database.

Secrets (never written in code, never printed):
  DATABASE_URL       the Supabase session pooler address, with [YOUR-PASSWORD] in it
  DATABASE_PASSWORD  the database password; put into the address percent-encoded, so
                     characters like @ : / # in the password don't break it
Read from .streamlit/secrets.toml when it exists (local runs and scripts), otherwise from
st.secrets (Streamlit Cloud: Settings -> Secrets).
"""
import tomllib
from pathlib import Path
from urllib.parse import quote

import psycopg

SECRETS_FILE = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
PLACEHOLDER = "[YOUR-PASSWORD]"


def _secrets():
    if SECRETS_FILE.exists():
        with open(SECRETS_FILE, "rb") as f:
            return tomllib.load(f)
    import streamlit as st                       # hosted app: Streamlit's own secrets
    return st.secrets


def get_secret(name, default=None):
    """One secret by name (e.g. LOGFIRE_TOKEN), or default if it isn't set. Never printed."""
    try:
        return _secrets().get(name, default)
    except Exception:                            # no secrets file and no Streamlit secrets
        return default


def get_connection(**kwargs):
    """A new psycopg connection. Use it as `with get_connection() as conn: ...`.
    Extra keyword arguments go to psycopg.connect (e.g. autocommit=True)."""
    secrets = _secrets()
    if "DATABASE_URL" not in secrets:
        raise RuntimeError("DATABASE_URL is not set in .streamlit/secrets.toml / st.secrets")
    url = secrets["DATABASE_URL"]
    password = secrets.get("DATABASE_PASSWORD", "")
    if PLACEHOLDER in url:
        if not password:
            raise RuntimeError("DATABASE_URL has [YOUR-PASSWORD] but DATABASE_PASSWORD "
                               "is not set")
        url = url.replace(PLACEHOLDER, quote(password, safe=""))
    try:
        return psycopg.connect(url, connect_timeout=10, **kwargs)
    except psycopg.OperationalError as e:
        # Keep the reason, but make sure no form of the password can reach the screen
        msg = str(e)
        for secret in {password, quote(password, safe="")} - {""}:
            msg = msg.replace(secret, "***")
        raise RuntimeError(f"Could not connect to the database: {msg}") from None
