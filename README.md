# pl-data-sync

Nightly snapshot engine for a Fantasy Premier League mini-league.
Every day it records what the public FPL API shows right now — prices,
ownership, injury flags, every manager's squad, captain, chips, and
transfers in classic league **449637** — and stores it forever, because the
API itself only ever shows you the present.

Runs free on GitHub Actions. No server, no laptop, no maintenance.

## What it produces

- `data/fpl.db` — SQLite database: full season history of the league
  (picks, captains, chips, transfers, hit points, standings) plus daily
  price/ownership/status for every player in the game.
- `data/raw/YYYY-MM-DD/` — slim dated JSON snapshots (audit trail).
- `docs/latest.json` — computed intelligence digest served via GitHub Pages:
  league table, every rival's chip ledger and captain history, league
  effective ownership, price and ownership movers. This is the URL that
  automated research runs fetch.
- `docs/index.html` — small human-readable view of the digest.

## Deploy (10 minutes, once)

1. Create a **public** GitHub repository named `pl-data-sync`
   (public is required for free GitHub Pages; every byte here is already
   public via the FPL API, so nothing private is exposed).
2. Upload the contents of this folder to the repository
   (easiest: with Claude Code, open this folder and say
   "push this to a new public GitHub repo called pl-data-sync";
   or use the GitHub web UI: Add file → Upload files).
3. In the repo: **Settings → Pages → Source: Deploy from a branch →
   Branch: `main`, folder `/docs` → Save.**
4. In the **Actions** tab: enable workflows if prompted, open
   "FPL nightly sync", press **Run workflow** once to backfill.
5. Done. It now runs daily at 11:30am Brisbane automatically.

After the first run, the digest is live at:
`https://<your-username>.github.io/pl-data-sync/latest.json`

## How Claude connects

- **Scheduled research runs** fetch the digest URL for rival intelligence.
- **Strategy sessions** clone the repo and query the SQLite database
  directly (`python analysis.py table | chips | captains | rival <id> |
  eo | prices <days>`), or with raw SQL.

## Configuration

Everything lives in `config.py`: team id, league id, watchlist names.

## Notes

- Idempotent: re-running any day is safe; picks backfill automatically,
  so a missed day loses only that day's price/ownership snapshot, never
  gameweek history.
- One bad manager fetch never kills the run.
- Respectful of the API: ~40 requests per run with delays.
