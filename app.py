"""AI-Safety Outreach — walled-garden multi-user web app (Phase-1 prototype).

Each invited user signs in, browses the shared researcher directory, uploads
their OWN LinkedIn network, and sees which researchers they already know. Every
user's personal data is isolated by their login email.

Auth model (production): deploy PRIVATE on Streamlit Community Cloud with an
email allowlist — Streamlit then provides st.user.email. Locally there's no
real login, so this prototype offers a dev sign-in.

Storage (prototype): a local SQLite userdata.db keyed by user email. In
production this becomes a hosted DB (Supabase). The shared researcher directory
(researchers.db) is read-only and contains NO personal data.
"""
import os
import re
import csv
import sqlite3
import unicodedata
import pandas as pd
import streamlit as st

import store   # per-user storage (Supabase in prod, local SQLite in dev)

st.set_page_config(page_title="AI-Safety Outreach", page_icon="🛰️", layout="wide")
HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "researchers.db")       # shared, read-only, no PII


def norm(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z\s]", " ", s.lower())).strip()


# ---------------------------------------------------------------- identity
def current_user():
    """Deployed private Streamlit -> st.user.email. Local -> dev sign-in."""
    try:
        u = getattr(st, "user", None)
        if u is not None:
            email = None
            try:
                email = u.email        # attribute form
            except Exception:
                email = (u.get("email") if hasattr(u, "get") else None)
            if email:
                return email
    except Exception:
        pass
    return st.session_state.get("dev_user")


# ------------------------------------------------------------------- data
@st.cache_data(show_spinner=False)
def load_core():
    con = sqlite3.connect(CORE)
    df = pd.read_sql_query("SELECT * FROM researchers", con)
    con.close()
    df["matched_topics"] = df["matched_topics"].fillna("")
    df["name_norm"] = df["name"].map(norm)
    return df


def import_linkedin_bytes(user, raw):
    """Parse a LinkedIn Connections.csv and REPLACE the user's saved network."""
    lines = raw.decode("utf-8-sig", errors="replace").splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if "First Name" in ln and "Last Name" in ln), 0)
    rows = []
    for row in csv.DictReader(lines[start:]):
        name = f"{(row.get('First Name') or '').strip()} {(row.get('Last Name') or '').strip()}".strip()
        if not name:
            continue
        rows.append({"name": name, "name_norm": norm(name),
                     "company": (row.get("Company") or "").strip(),
                     "position": (row.get("Position") or "").strip(),
                     "email": (row.get("Email Address") or "").strip().lower(),
                     "source": "linkedin"})
    return store.replace_contacts(user, rows)


def my_contacts(user):
    return store.get_contacts(user)


# ------------------------------------------------------------- auth gate
user = current_user()
if not user:
    st.title("🛰️ AI-Safety Outreach")
    st.caption("Invite-only. Sign in to continue.")
    st.info("This is the local prototype — enter any email to act as that user. "
            "In production you'd sign in with your invited Google account.")
    with st.form("dev_login"):
        email = st.text_input("Your email")
        if st.form_submit_button("Sign in") and email.strip():
            st.session_state["dev_user"] = email.strip().lower()
            st.rerun()
    st.stop()

core = load_core()

# ------------------------------------------------------------- header
with st.sidebar:
    st.markdown(f"**Signed in as**\n\n{user}")
    if st.button("Sign out"):
        st.session_state.pop("dev_user", None)
        st.rerun()
    st.divider()
    st.caption(f"{len(core):,} researchers in the shared directory.")
    st.caption(f"Storage: {store.backend()}")

st.title("🛰️ AI-Safety Outreach")
tab_dir, tab_net, tab_known = st.tabs(
    ["🔎 Researcher directory", "👥 My network", "🤝 Who I already know"])

# ---- Tab 1: shared directory (read-only) ----
with tab_dir:
    st.subheader("Researcher directory")
    st.caption("Shared, read-only. Ranked by AI-safety fit. No contact info here.")
    q = st.text_input("Search name / institution / topic", key="dir_q")
    c1, c2 = st.columns(2)
    countries = sorted([c for c in core["country"].dropna().unique() if c])
    fc = c1.multiselect("Country", countries, key="dir_c")
    rel_opts = [r for r in ["core", "adjacent", "off-topic"]
                if r in set(core["relevance_label"].dropna())]
    fr = c2.multiselect("Relevance", rel_opts, key="dir_r")
    f = core
    if q:
        ql = q.lower()
        f = f[f["name"].str.lower().str.contains(ql, na=False)
              | f["institution_name"].str.lower().str.contains(ql, na=False)
              | f["matched_topics"].str.lower().str.contains(ql, na=False)]
    if fc:
        f = f[f["country"].isin(fc)]
    if fr:
        f = f[f["relevance_label"].isin(fr)]
    f = f.sort_values("score", ascending=False)
    st.caption(f"{len(f):,} match — showing top 500")
    st.dataframe(
        f.head(500)[["score", "name", "institution_name", "country",
                     "relevance_label", "works", "citations", "matched_topics", "orcid"]],
        hide_index=True, width="stretch", height=460,
        column_config={
            "score": st.column_config.NumberColumn("AI-safety fit", format="%.1f"),
            "institution_name": st.column_config.TextColumn("Institution"),
            "country": st.column_config.TextColumn("Ctry", width="small"),
            "relevance_label": st.column_config.TextColumn("Relevance", width="small"),
            "matched_topics": st.column_config.TextColumn("Topics", width="large"),
            "orcid": st.column_config.LinkColumn("ORCID", display_text="↗", width="small"),
        })

# ---- Tab 2: my network (private, per-user) ----
with tab_net:
    st.subheader("My network")
    st.caption("Private to you. Export from LinkedIn: Settings → Get a copy of "
               "your data → Connections. Then upload the CSV here.")
    up = st.file_uploader("Upload your LinkedIn Connections.csv", type="csv")
    st.caption("Uploading replaces your saved network with the file's contents.")
    if up is not None and st.button("Import"):
        n = import_linkedin_bytes(user, up.getvalue())
        st.success(f"Imported {n} connections.")
    mine = my_contacts(user)
    st.metric("Your connections", f"{len(mine):,}")
    if len(mine):
        st.dataframe(mine[["name", "company", "position"]].sort_values("name"),
                     hide_index=True, width="stretch", height=320)
        if st.button("Clear my network"):
            store.clear_contacts(user)
            st.rerun()

# ---- Tab 3: who I already know (per-user warm intros) ----
with tab_known:
    st.subheader("Researchers you already know")
    st.caption("AI-safety researchers who match someone in your LinkedIn network — "
               "your warmest intros. (Name match; verify before reaching out.)")
    mine = my_contacts(user)
    if not len(mine):
        st.info("Upload your network in the **My network** tab to see this.")
    else:
        known = set(mine["name_norm"])
        hits = core[core["name_norm"].isin(known)].sort_values("score", ascending=False)
        st.metric("Researchers you know", len(hits))
        if len(hits):
            st.dataframe(
                hits[["score", "name", "institution_name", "country",
                      "relevance_label", "matched_topics"]],
                hide_index=True, width="stretch",
                column_config={
                    "score": st.column_config.NumberColumn("AI-safety fit", format="%.1f"),
                    "institution_name": st.column_config.TextColumn("Institution"),
                })
        else:
            st.caption("No direct matches yet. (Bridge paths — a contact who "
                       "co-authored with a target — come in the next phase.)")
