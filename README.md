# AI-Safety Outreach — walled-garden web app

An invite-only, browser-based tool. Each user signs in, browses the shared
AI-safety researcher directory, uploads their own LinkedIn network, and sees who
they already know. Every user's personal data is isolated by their login email.
Built for a small trusted group (≤ ~10), free-tier hosting.

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

## What's shared vs private
- `researchers.db` — shared, read-only directory. **No emails, no personal data.**
- Per-user contacts / matches — private to each signed-in user (local `userdata.db`
  in the prototype; Supabase in production).

## Roadmap
- Phase 2: Supabase persistence + private deploy + invite allowlist.
- Phase 3: warm-path bridges (precomputed co-authorship, no per-user OpenAlex),
  outreach queue + CRM + compliance gate, all per-user.
