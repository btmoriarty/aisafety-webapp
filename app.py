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
import datetime
import unicodedata
import pandas as pd
import streamlit as st

import store   # per-user storage (Supabase in prod, local SQLite in dev)

st.set_page_config(page_title="AI-Safety Outreach", page_icon="🛰️", layout="wide")
HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "researchers.db")       # shared, read-only, no PII

STATUSES = ["queued", "contacted", "replied", "meeting", "declined", "done"]

# Rough emailing-law posture by ISO-2 country code (guidance, not legal advice).
_EU_EEA = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
           "GR", "HU", "IS", "IE", "IT", "LV", "LI", "LT", "LU", "MT", "NL",
           "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "GB", "CH"}


def jurisdiction(country):
    c = (country or "").upper()
    if not c:
        return "Check local rules"
    if c in _EU_EEA:
        return "GDPR — consent-first"
    if c == "CA":
        return "CASL — consent-first"
    if c == "US":
        return "CAN-SPAM — opt-out ok"
    return "Check local rules"


def norm(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z\s]", " ", s.lower())).strip()


# ---------------------------------------------------------------- identity
def _email_of(obj):
    if obj is None:
        return None
    try:
        e = getattr(obj, "email", None)
        if e:
            return e
    except Exception:
        pass
    try:
        if hasattr(obj, "get"):
            return obj.get("email")
    except Exception:
        pass
    return None


def current_user():
    """Deployed private Streamlit provides the signed-in viewer's email via
    st.user or st.experimental_user. Locally, fall back to the dev sign-in."""
    for attr in ("user", "experimental_user"):
        try:
            e = _email_of(getattr(st, attr, None))
            if e:
                return e
        except Exception:
            pass
    return st.session_state.get("dev_user")


# ------------------------------------------------------------------- data
# The researcher directory + bridge index are PII-free but are NOT kept in this
# (public) repo — they live in the same private DB as user data (Supabase in
# prod). Locally, a gitignored researchers.db file is used for development.
def _read_shared(sql, empty_cols):
    """Read a shared table from Supabase (prod) or the local dev file. Returns an
    empty frame if the directory hasn't been loaded yet (see publish_supabase.py)."""
    try:
        if store.backend().startswith("Supabase"):
            return pd.read_sql(sql, store.engine())
        if os.path.exists(CORE):
            con = sqlite3.connect(CORE)
            try:
                return pd.read_sql_query(sql, con)
            finally:
                con.close()
    except Exception:
        pass
    return pd.DataFrame(columns=empty_cols)


@st.cache_data(show_spinner=False)
def load_core():
    df = _read_shared("SELECT * FROM researchers",
                      ["id", "name", "institution_name", "country", "works",
                       "citations", "matched_topics", "score", "orcid",
                       "relevance_label"])
    if df.empty:
        return df
    df["matched_topics"] = df["matched_topics"].fillna("")
    df["name_norm"] = df["name"].map(norm)
    return df


@st.cache_data(show_spinner=False)
def load_coauthors():
    """Precomputed target -> co-author names (shared, no PII). Empty until the
    bridge index is loaded. Bridges are then a pure local join."""
    return _read_shared(
        "SELECT target_id, coauthor_norm FROM target_coauthors "
        "WHERE coauthor_norm <> ''", ["target_id", "coauthor_norm"])


def warm_paths(core, mine):
    """For each researcher, how the user can reach them:
       'direct' (they're your contact) or 'bridge' (a contact co-authored w/ them).
    Pure local joins — no OpenAlex at request time."""
    known = set(mine["name_norm"])
    by_norm = dict(zip(mine["name_norm"], mine["name"]))
    direct = core[core["name_norm"].isin(known)].copy()
    direct["path"] = "You know them directly"
    direct["via"] = direct["name_norm"].map(by_norm)

    co = load_coauthors()
    bridges = pd.DataFrame()
    if len(co):
        hit = co[co["coauthor_norm"].isin(known)].copy()
        if len(hit):
            hit["via"] = hit["coauthor_norm"].map(by_norm)
            # best (first) bridge contact per target
            hit = hit.drop_duplicates("target_id")
            b = core.merge(hit[["target_id", "via"]], left_on="id",
                           right_on="target_id", how="inner")
            b = b[~b["name_norm"].isin(known)]      # a direct match trumps a bridge
            b["path"] = "Via " + b["via"]
            bridges = b
    cols = list(core.columns) + ["path", "via"]
    out = pd.concat([direct.reindex(columns=cols), bridges.reindex(columns=cols)],
                    ignore_index=True)
    return out.sort_values("score", ascending=False)


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
    st.caption("Private, invite-only. Enter your email to open your workspace.")
    with st.form("who"):
        email = st.text_input("Your email", placeholder="you@example.com")
        st.caption("Use the email you were invited with. Each person's network and "
                   "outreach are kept separate.")
        if st.form_submit_button("Open my workspace") and email.strip():
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

if core.empty:
    st.title("🛰️ AI-Safety Outreach")
    st.info("The researcher directory hasn't been loaded into this deployment yet. "
            "If you're the maintainer, run the loader (`publish_supabase.py`) to "
            "populate it. Otherwise, check back shortly.")
    st.stop()

st.title("🛰️ AI-Safety Outreach")
tab_dir, tab_net, tab_warm, tab_out = st.tabs(
    ["🔎 Researcher directory", "👥 My network",
     "🤝 Warm paths", "📋 My outreach"])

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

# ---- Tab 3: warm paths (direct + co-authorship bridges) ----
with tab_warm:
    st.subheader("Your warm paths in")
    st.caption("AI-safety researchers you can reach — either you know them directly, "
               "or one of your connections co-authored with them. (Name match; "
               "verify before reaching out.)")
    mine = my_contacts(user)
    if not len(mine):
        st.info("Upload your network in the **My network** tab to light this up.")
    else:
        warm = warm_paths(core, mine)
        warm["jurisdiction"] = warm["country"].map(jurisdiction)
        n_direct = int((warm["path"] == "You know them directly").sum())
        n_bridge = len(warm) - n_direct
        c1, c2 = st.columns(2)
        c1.metric("Know directly", n_direct)
        c2.metric("Via a bridge", n_bridge)
        if not load_coauthors().shape[0]:
            st.caption("ℹ️ Bridge paths appear once the shared co-authorship index is "
                       "built (a maintainer step). Direct matches show now.")
        if len(warm):
            st.dataframe(
                warm[["score", "name", "path", "institution_name", "country",
                      "jurisdiction", "relevance_label", "matched_topics"]],
                hide_index=True, width="stretch", height=420,
                column_config={
                    "score": st.column_config.NumberColumn("AI-safety fit", format="%.1f"),
                    "path": st.column_config.TextColumn("How you reach them", width="medium"),
                    "institution_name": st.column_config.TextColumn("Institution"),
                    "country": st.column_config.TextColumn("Ctry", width="small"),
                    "jurisdiction": st.column_config.TextColumn("Emailing rules", width="medium"),
                    "relevance_label": st.column_config.TextColumn("Relevance", width="small"),
                })
            st.divider()
            st.caption("Add someone to your outreach queue:")
            pick = st.multiselect("Researchers to track",
                                  options=list(warm["name"]), key="warm_pick")
            if pick and st.button("Add to my outreach"):
                now = datetime.datetime.utcnow().isoformat(timespec="seconds")
                sel = warm[warm["name"].isin(pick)]
                for _, r in sel.iterrows():
                    store.set_outreach(user, r["id"], r["name"], "queued", "", now)
                st.success(f"Added {len(sel)} to your outreach queue.")
                st.rerun()
        else:
            st.caption("No warm paths yet.")

# ---- Tab 4: my outreach (per-user CRM) ----
with tab_out:
    st.subheader("My outreach")
    st.caption("Private to you. Track who you're reaching out to and where things stand.")
    q = store.get_outreach(user)
    if not len(q):
        st.info("Nothing tracked yet. Add researchers from the **Warm paths** tab.")
    else:
        edit = q.rename(columns={"target_name": "name"})[["name", "status", "note"]].copy()
        edited = st.data_editor(
            edit, hide_index=True, width="stretch", key="out_editor",
            column_config={
                "name": st.column_config.TextColumn("Researcher", disabled=True),
                "status": st.column_config.SelectboxColumn("Status", options=STATUSES),
                "note": st.column_config.TextColumn("Note", width="large"),
            })
        c1, c2 = st.columns(2)
        if c1.button("Save changes"):
            now = datetime.datetime.utcnow().isoformat(timespec="seconds")
            ids = list(q["target_id"])
            names = list(q["target_name"])
            for i in range(len(edited)):
                store.set_outreach(user, ids[i], names[i],
                                   edited.iloc[i]["status"],
                                   edited.iloc[i]["note"], now)
            st.success("Saved.")
            st.rerun()
        drop = c2.multiselect("Remove from queue", options=list(q["target_name"]))
        if drop and c2.button("Remove selected"):
            for _, r in q[q["target_name"].isin(drop)].iterrows():
                store.remove_outreach(user, r["target_id"])
            st.rerun()
