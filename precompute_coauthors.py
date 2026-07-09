#!/usr/bin/env python3
"""
precompute_coauthors.py — STANDALONE FALLBACK for filling the bridge index.

CANONICAL path is the pipeline: `warmpath.py precompute-targets` (nightly) +
`snapshot.py export-core`, which carries `target_coauthors` into this repo's
researchers.db. Use THIS script only when you don't have the pipeline handy and
want to fill the index directly on researchers.db. A later export-core refresh
overwrites it — the pipeline is the durable source. See README ("Bridge index").

For the top-ranked researchers, fetch their co-author NAMES from OpenAlex once
and store them in researchers.db. The web app then computes each user's warm-path
bridges as a pure local join (their contacts ∩ a target's co-authors) — NO
per-user OpenAlex calls, so it stays free at any number of users.

Co-author names are public (they're on the papers); no personal data.
Budget-aware (stops on OpenAlex 429) and resumable.

  ./precompute_coauthors.py [--limit 500]
"""
import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "researchers.db")
MAILTO = os.environ.get("OPENALEX_MAILTO", "anonymous@example.com")  # OpenAlex polite pool
UA = "Mozilla/5.0"


def norm(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z\s]", " ", s.lower())).strip()


class RateLimit(Exception):
    pass


def api_get(path):
    url = f"https://api.openalex.org/{path}"
    url += ("&" if "?" in url else "?") + f"mailto={MAILTO}"
    for attempt in range(4):
        r = subprocess.run(["curl", "-sSL", "-w", "\n%{http_code}", "--max-time",
                            "40", "-A", UA, url], capture_output=True)
        body = r.stdout.decode("utf-8", "replace")
        nl = body.rfind("\n")
        status, body = (body[nl + 1:].strip(), body[:nl]) if nl >= 0 else ("", body)
        if status == "429" or '"Rate limit exceeded"' in body:
            raise RateLimit("OpenAlex daily budget exhausted (resets midnight UTC)")
        if r.returncode == 0 and body:
            import json
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass
        time.sleep(1.5 * (attempt + 1))
    return {}


def coauthors_of(author_id, max_works=50):
    d = api_get(f"works?filter=authorships.author.id:{author_id}"
                f"&per-page={max_works}&select=authorships")
    out = {}
    for w in d.get("results", []):
        for a in (w.get("authorships") or []):
            au = a.get("author") or {}
            cid = (au.get("id") or "").split("/")[-1]
            nm = au.get("display_name") or ""
            if cid and cid != author_id and nm:
                out[norm(nm)] = nm
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS target_coauthors(
        target_id TEXT, coauthor_norm TEXT, coauthor_name TEXT,
        PRIMARY KEY (target_id, coauthor_norm))""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_tc_norm ON target_coauthors(coauthor_norm)")
    done = {r[0] for r in con.execute("SELECT DISTINCT target_id FROM target_coauthors")}
    todo = [r[0] for r in con.execute("SELECT id FROM researchers ORDER BY score DESC")
            if r[0] not in done][:args.limit]
    print(f"Precomputing co-authors for {len(todo)} targets "
          f"({len(done)} already done)...", file=sys.stderr)
    n = 0
    for tid in todo:
        try:
            co = coauthors_of(tid)
        except RateLimit as e:
            print(f"  {e} — stopping; resume next run.", file=sys.stderr)
            break
        con.executemany(
            "INSERT OR IGNORE INTO target_coauthors VALUES(?,?,?)",
            [(tid, k, v) for k, v in co.items()])
        # ensure a row exists even if no co-authors, so we don't re-query
        con.execute("INSERT OR IGNORE INTO target_coauthors VALUES(?,?,?)",
                    (tid, "", ""))
        n += 1
        if n % 25 == 0:
            con.commit()
            print(f"  ...{n}/{len(todo)}", file=sys.stderr)
        time.sleep(0.15)
    con.commit()
    total = con.execute("SELECT COUNT(DISTINCT target_id) FROM target_coauthors").fetchone()[0]
    print(f"Done (+{n} this run). Targets with co-authors precomputed: {total}.",
          file=sys.stderr)
    con.close()


if __name__ == "__main__":
    main()
