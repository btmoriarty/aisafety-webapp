"""Per-user storage. Uses a hosted Postgres (Supabase) when a connection string
is configured, else a local SQLite file for dev. Same code path either way.

Production: put your Supabase connection string in Streamlit **Secrets** as
    db_url = "postgresql://USER:PASSWORD@HOST:5432/postgres"
(or set the DB_URL env var). Without it, data lands in a local userdata.db —
fine for the prototype, but Streamlit's disk is ephemeral, so use Supabase in
production so users' networks persist across restarts.
"""
import os
import sqlalchemy as sa

try:
    import streamlit as st
except Exception:            # store is importable outside Streamlit (tests)
    st = None

HERE = os.path.dirname(os.path.abspath(__file__))
_engine = None


def _db_url():
    url = None
    if st is not None:
        try:
            url = st.secrets.get("db_url")
        except Exception:
            url = None
    url = url or os.environ.get("DB_URL")
    if url:                                   # normalise to the psycopg2 driver
        if url.startswith("postgres://"):
            url = "postgresql+psycopg2://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url
    return "sqlite:///" + os.path.join(HERE, "userdata.db")


def backend():
    return "Supabase/Postgres" if _db_url().startswith("postgresql") else "local SQLite (dev)"


def engine():
    global _engine
    if _engine is None:
        _engine = sa.create_engine(_db_url(), pool_pre_ping=True)
        with _engine.begin() as c:
            c.execute(sa.text("""CREATE TABLE IF NOT EXISTS contacts(
                user_email TEXT, name TEXT, name_norm TEXT, company TEXT,
                position TEXT, email TEXT, source TEXT)"""))
            try:
                c.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_contacts_user "
                                  "ON contacts(user_email)"))
            except Exception:
                pass
    return _engine


def replace_contacts(user, rows):
    """Replace this user's whole network (a LinkedIn export is the full list).
    Clear-then-insert works identically on SQLite and Postgres (no upsert)."""
    eng = engine()
    with eng.begin() as c:
        c.execute(sa.text("DELETE FROM contacts WHERE user_email=:u"), {"u": user})
        if rows:
            c.execute(sa.text(
                "INSERT INTO contacts"
                "(user_email,name,name_norm,company,position,email,source) VALUES"
                "(:user_email,:name,:name_norm,:company,:position,:email,:source)"),
                [dict(r, user_email=user) for r in rows])
    return len(rows)


def get_contacts(user):
    import pandas as pd
    with engine().connect() as c:
        return pd.read_sql(
            sa.text("SELECT name,name_norm,company,position FROM contacts "
                    "WHERE user_email=:u"), c, params={"u": user})


def clear_contacts(user):
    with engine().begin() as c:
        c.execute(sa.text("DELETE FROM contacts WHERE user_email=:u"), {"u": user})
