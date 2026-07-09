# AI-Safety Outreach — invite-only web app

An invite-only, browser-based tool. Each user signs in and gets four tabs:
- **Researcher directory** — a shared, read-only, ranked AI-safety directory.
- **My network** — upload your own LinkedIn Connections.csv (private to you).
- **Warm paths** — researchers you can reach: *directly* (they're your contact) or
  via a *bridge* (a connection who co-authored with them), each with an emailing-law
  posture (GDPR / CASL / CAN-SPAM) for the target's country.
- **My outreach** — a private mini-CRM: queue researchers and track status
  (queued → contacted → replied → meeting → declined → done) with notes.

Every user's personal data is isolated by their login email. Built for a small
trusted group (≤ ~10), free-tier hosting.

## This repo is code-only
No data lives here. The researcher directory and the co-author bridge index are
**not** committed to this repo — they live in the app's private database
(Supabase) alongside per-user data. So this repository is safe to make public:
it contains application code only, no rankings and no personal data. Secrets live
in Streamlit **Secrets**, never in the repo.

## Run locally (dev)
```bash
pip install -r requirements.txt
streamlit run app.py
```
With no `db_url` configured, the app uses a local dev sign-in and reads the
directory from a **gitignored** local `researchers.db` (if present), with per-user
data in a local `userdata.db`. Both are dev-only and never committed.

## Deploy — private app, free
1. **Database (Supabase):** create a free project → copy its Postgres connection
   string (Project Settings → Database → Connection string → URI).
2. **Load the directory into it** (maintainer step, from the data pipeline):
   ```bash
   DB_URL="postgresql://postgres:PW@db.PROJECT.supabase.co:5432/postgres" \
       ./publish_supabase.py            # loads researchers + target_coauthors
   ```
3. **Deploy the app:** share.streamlit.io → New app → this repo + `app.py`.
4. **Point the app at Supabase:** app **Settings → Secrets**:
   ```toml
   db_url = "postgresql://postgres:PW@db.PROJECT.supabase.co:5432/postgres"
   ```
5. **Lock it down:** app **Settings → Sharing** → private → add invitees' emails.
   Streamlit then provides `st.user.email`, which isolates each user's data.

The app reads the shared directory/bridges from Supabase in production; per-user
contacts and outreach are isolated by login email. The storage layer (`store.py`)
selects Supabase when `db_url` is set, else the local dev file.

## Warm-path bridges
Bridges are a **pure local join** at request time: for each top researcher the
directory stores the names of their co-authors (`target_coauthors`), and the app
intersects that with each user's contacts — no OpenAlex calls per user, free at
any scale. Co-author names are public (they're on the papers); no PII.

The index is built by the data pipeline (`warmpath.py precompute-targets`,
budget-paced) and loaded into Supabase by `publish_supabase.py` on refresh. Until
it's populated, **direct** matches work and bridges simply show none. A standalone
`precompute_coauthors.py` can fill a local `researchers.db` for dev.

## Status
- Directory + per-user network/outreach + warm-path bridges + emailing-law posture: ✅
- Data kept out of the repo (Supabase-backed), app code-only: ✅
- Next: optional 2-hop bridges & tie-strength.
