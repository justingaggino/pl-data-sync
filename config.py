"""Central configuration for pl-data-sync."""

# Justin's FPL entry and the work mini-league
TEAM_ID = 7525442
LEAGUE_ID = 449637
LEAGUE_NAME = "Fantasy Addicts Anonymous"

# Players tracked closely in the digest even if nobody in the league owns them.
# Matched against FPL "web_name" (case-insensitive substring is fine).
WATCHLIST_NAMES = [
    "Haaland", "Palmer", "Isak", "Semenyo", "Gabriel", "Thiago",
    "Sangar", "Hinshelwood", "Le Fee", "Foden", "Buendia",
]

# Paths (relative to repo root)
DB_PATH = "data/fpl.db"
RAW_DIR = "data/raw"
DOCS_DIR = "docs"

API_BASE = "https://fantasy.premierleague.com/api"

# Be polite to the API
REQUEST_SLEEP_SECONDS = 0.6
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_RETRIES = 3
USER_AGENT = "pl-data-sync/1.0 (personal mini-league analytics)"
