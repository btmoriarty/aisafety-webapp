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


def _brand():
    """Light-touch styling that works in BOTH light and dark mode — only the
    dark brand banner uses fixed colors (it reads fine on either background);
    everything else inherits the viewer's theme so dark mode is preserved."""
    st.markdown("""<style>
      footer{display:none !important;}   /* hide only the 'Made with Streamlit' footer */
      .block-container{padding-top:1.3rem;padding-bottom:2rem;max-width:1240px;}
      .brandbar{background:linear-gradient(100deg,#161A2E,#212747);border-radius:16px;
        padding:17px 24px;margin:0 0 16px;display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;
        box-shadow:0 14px 34px -22px rgba(20,26,60,.4);}
      .brandbar .bt{font-size:22px;font-weight:800;color:#fff;letter-spacing:-.01em;}
      .brandbar .bs{font-size:13.5px;color:#9BA3CC;font-style:italic;}
      .brandbar .bd{margin-left:auto;font-size:11px;font-weight:700;letter-spacing:.12em;
        color:#17B3A6;text-transform:uppercase;}
      .stTabs [data-baseweb="tab"]{font-weight:600;}
      .stButton button, .stDownloadButton button{border-radius:9px;font-weight:600;}
    </style>""", unsafe_allow_html=True)


def _header():
    st.markdown('<div class="brandbar"><span class="bt">🛰️ AI-Safety Outreach</span>'
                '<span class="bs">Warm intros to the researchers who matter</span>'
                '<span class="bd">invite-only</span></div>', unsafe_allow_html=True)


_brand()

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
# Verified login via Streamlit's native Google OIDC (st.login): the app itself
# authenticates each viewer, so identity is proven by Google — nobody can claim
# someone else's email. An allowlist (allowed_emails in Secrets) restricts entry
# to invited accounts. Locally (no [auth] configured), a dev sign-in is used.
def _auth_ready():
    try:
        return "auth" in st.secrets
    except Exception:
        return False


def allowed_emails():
    try:
        a = st.secrets.get("allowed_emails", None)
        if a:
            return {str(x).strip().lower() for x in a}
    except Exception:
        pass
    return set()


def current_user():
    if _auth_ready():
        try:
            if st.user.is_logged_in:
                return (st.user.email or "").strip().lower()
        except Exception:
            pass
        return None
    return st.session_state.get("dev_user")     # local dev only


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

# Logged in via Google but not invited -> block (allowlist enforced when set).
allow = allowed_emails()
if _auth_ready() and user and allow and user not in allow:
    st.title("🛰️ AI-Safety Outreach")
    st.error(f"**{user}** isn't on the invite list. "
             "Ask the organizer to add you, then sign in again.")
    st.button("Sign out", on_click=st.logout)
    st.stop()

if not user:
    st.title("🛰️ AI-Safety Outreach")
    if _auth_ready():
        st.caption("Private, invite-only. Sign in with your invited Google account.")
        st.button("Sign in with Google", on_click=st.login, type="primary")
        st.stop()
    # Local dev only (no [auth] configured): self-declared email.
    st.caption("Dev mode — enter any email to act as that user.")
    with st.form("who"):
        email = st.text_input("Your email", placeholder="you@example.com")
        if st.form_submit_button("Open my workspace") and email.strip():
            st.session_state["dev_user"] = email.strip().lower()
            st.rerun()
    st.stop()

core = load_core()

# ------------------------------------------------------------- header
with st.sidebar:
    st.markdown(f"**Signed in as**\n\n{user}")
    if _auth_ready():
        st.button("Sign out", on_click=st.logout)
    elif st.button("Sign out"):
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

_header()
tab_dir, tab_net, tab_warm, tab_out = st.tabs(
    ["🔎 Researcher directory", "👥 My network",
     "🤝 Warm paths", "📋 My outreach"])

# ---- Tab 1: shared directory (read-only) ----
with tab_dir:
    st.subheader("Researcher directory")
    st.caption("Shared, read-only. Ranked by AI-safety fit. No contact info here.")
    with st.container(border=True):
        q = st.text_input("Search name / institution / topic", key="dir_q",
                          placeholder="e.g. robustness, Stanford, Bengio…")
        c1, c2 = st.columns(2)
        countries = sorted([c for c in core["country"].dropna().unique() if c])
        fc = c1.multiselect("Country", countries, key="dir_c")
        rel_opts = [r for r in ["core", "adjacent", "off-topic"]
                    if r in set(core["relevance_label"].dropna())]
        fr = c2.multiselect("Relevance", rel_opts, key="dir_r")
        c3, c4 = st.columns(2)
        max_fit = float(round(core["score"].max() or 0))
        minfit = (c3.slider(
                    "Min AI-safety fit", 0.0, max_fit, 0.0, key="dir_fit",
                    help="Only show researchers whose AI-safety fit is at least this. "
                         "Fit blends topic relevance, recency, and citations — higher "
                         "means a stronger, more current match. Leave at 0 to show everyone.")
                  if max_fit > 0 else 0.0)   # slider needs min < max
        max_cit = int(core["citations"].fillna(0).max() or 0)
        mincit = (c4.slider(
                    "Min citations", 0, max_cit, 0, step=max(1, max_cit // 100),
                    key="dir_cit",
                    help="Only show researchers with at least this many total citations "
                         "(a rough proxy for seniority / influence). Leave at 0 to show everyone.")
                  if max_cit > 0 else 0)
        if st.button("↺ Reset filters", key="dir_reset"):
            for k in ("dir_q", "dir_c", "dir_r", "dir_fit", "dir_cit"):
                st.session_state.pop(k, None)
            st.rerun()

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
    if minfit > 0:
        f = f[f["score"] >= minfit]
    if mincit > 0:
        f = f[f["citations"].fillna(0) >= mincit]
    f = f.sort_values("score", ascending=False)
    active = []
    if q:
        active.append(f'"{q}"')
    if fc:
        active.append(", ".join(fc))
    if fr:
        active.append("/".join(fr))
    if minfit > 0:
        active.append(f"fit ≥ {minfit:.0f}")
    if mincit > 0:
        active.append(f"cites ≥ {mincit:,}")
    if active:
        st.caption("Active filters:  " + "   ·   ".join(active))
    dir_cols = ["score", "name", "institution_name", "country", "relevance_label",
                "works", "citations", "matched_topics", "orcid"]
    hc, dc = st.columns([3, 1])
    hc.caption(f"{len(f):,} match — showing top 500 in the table; download gives all.")
    dc.download_button("⬇ Download CSV", f[dir_cols].to_csv(index=False).encode("utf-8"),
                       file_name="ai_safety_researchers.csv", mime="text/csv",
                       key="dl_dir", width="stretch")
    st.dataframe(
        f.head(500)[dir_cols],
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
    st.caption("Private to you — your connections are never shared or scraped.")
    with st.expander("📇  How to get your LinkedIn connections file"):
        st.markdown(
            "1. On LinkedIn, click **Me** (your photo, top-right) → **Settings & Privacy**.\n"
            "2. Go to **Data privacy** → **Get a copy of your data**.\n"
            "3. Choose **\"Want something in particular?\"**, tick **Connections**, "
            "and click **Request archive** (you may need your password).\n"
            "4. LinkedIn emails you a download link — a Connections-only file is "
            "usually ready in **~10 minutes**.\n"
            "5. Download it and unzip if needed to get **Connections.csv**.\n"
            "6. Upload that file below and click **Import**.")
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
            st.download_button(
                "⬇ Download my warm paths (CSV)",
                warm[["score", "name", "path", "institution_name", "country",
                      "jurisdiction", "relevance_label", "matched_topics"]]
                .to_csv(index=False).encode("utf-8"),
                file_name="my_warm_paths.csv", mime="text/csv", key="dl_warm")
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
        st.download_button(
            "⬇ Download my outreach (CSV)",
            q[["target_name", "status", "note", "updated"]]
            .to_csv(index=False).encode("utf-8"),
            file_name="my_outreach.csv", mime="text/csv", key="dl_out")
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
