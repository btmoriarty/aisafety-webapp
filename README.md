# AI-Safety Outreach — walled-garden web app

An invite-only, browser-based tool. Each user signs in and gets four tabs:
- **Researcher directory** — the shared, read-only, ranked AI-safety directory (no PII).
- **My network** — upload your own LinkedIn Connections.csv (private to you).
- **Warm paths** — researchers you can reach: *directly* (they're your contact) or
  via a *bridge* (a connection who co-authored with them), each with an emailing-law
  posture (GDPR / CASL / CAN-SPAM) for the target's country.
- **My outreach** — a private mini-CRM: queue researchers and track status
  (queued → contacted → replied → meeting → declined → done) with notes.

Every user's personal data is isolated by their login email. Built for a small
trusted group (≤ ~10), free-tier hosting.

## Status: Phase-1 prototype (local)
Runnable locally with a dev sign-in and a local per-user store.
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy (Phase 2) — private, invite-only, free
1. Push this folder to a **private** GitHub repo.
2. **share.streamlit.io → New app** → pick the repo + `app.py`.
3. In the app's **Settings → Sharing**, set it **private** and add the emails of
   the people you invite. Only they can open it; `st.user.email` then identifies
   each user for data isolation.
4. Add persistence (so data survives restarts) — create a free **Supabase**
   project, copy its Postgres connection string, and paste it into the Streamlit
   app's **Settings → Secrets** as:
   ```toml
   db_url = "postgresql://postgres:PASSWORD@db.PROJECT.supabase.co:5432/postgres"
   ```
   With `db_url` set, the app uses Supabase automatically; without it, it falls
   back to a local SQLite file (dev only — Streamlit's disk is ephemeral). No
   code change needed; the storage layer (`store.py`) handles both.

## Bridge index (makes "via a bridge" light up)
Bridge paths are a **pure local join** at request time: the shared core stores,
for each top researcher, the names of their co-authors (`target_coauthors`), and
the app intersects that with each user's contacts. No OpenAlex calls per user →
free at any scale. Co-author names are public (they're on the papers) — no PII.

**How it gets filled (canonical):** the pipeline's nightly job builds this index
incrementally — `warmpath.py precompute-targets` runs as a budget-paced tier and
`snapshot.py export-core` carries `target_coauthors` into the snapshot. To publish
a refresh to the live app, re-export the core into this repo and push:
```bash
# in the pipeline repo:
./snapshot.py export-core -o /path/to/aisafety-webapp/researchers.db
# then in this repo:
git add researchers.db && git commit -m "refresh core + bridge index" && git push
```
Streamlit Cloud redeploys on push. Until the index is populated, **direct** matches
work and bridges simply show none.

**Standalone fallback** (webapp-only, no pipeline): `python precompute_coauthors.py
--limit 500` fills `target_coauthors` directly in this repo's `researchers.db`
(budget-aware, resumable). Note a later `export-core` refresh overwrites it — the
pipeline path is the durable source.

## What's shared vs private
- `researchers.db` — shared, read-only directory + co-author index. **No emails, no personal data.**
- Per-user contacts / outreach — private to each signed-in user (local `userdata.db`
  in the prototype; Supabase in production).

## Status
- Phase 1 ✅ prototype (directory + network + who-you-know).
- Phase 2 ✅ Supabase-ready per-user persistence (`store.py`).
- Phase 3 ✅ warm-path bridges (precomputed co-authorship), per-user outreach CRM,
  emailing-law posture per target. Bridge *data* fills in as `precompute_coauthors.py` runs.
- Next: deploy private + invite allowlist; optional 2-hop bridges & tie-strength.
