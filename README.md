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
4. Swap the local `userdata.db` for a hosted DB (Supabase free Postgres) so
   per-user data persists across restarts — connection string goes in Streamlit
   **Secrets**. (Code change is localized to the storage helpers.)

## What's shared vs private
- `researchers.db` — shared, read-only directory. **No emails, no personal data.**
- Per-user contacts / matches — private to each signed-in user (local `userdata.db`
  in the prototype; Supabase in production).

## Roadmap
- Phase 2: Supabase persistence + private deploy + invite allowlist.
- Phase 3: warm-path bridges (precomputed co-authorship, no per-user OpenAlex),
  outreach queue + CRM + compliance gate, all per-user.
