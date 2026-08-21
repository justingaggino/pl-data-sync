"""Nightly FPL snapshot job.

Fetches the public FPL API and stores:
  - a slim dated raw snapshot under data/raw/YYYY-MM-DD/
  - normalised history in data/fpl.db (SQLite)

Idempotent: safe to re-run any number of times per day.
Run:  python sync.py
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import requests

import config


# ----------------------------- HTTP -----------------------------

_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT})


def fetch_json(path: str):
    """GET {API_BASE}{path} with retries. Returns parsed JSON or None on 404."""
    url = f"{config.API_BASE}{path}"
    last_err = None
    for attempt in range(1, config.REQUEST_RETRIES + 1):
        try:
            resp = _session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            time.sleep(config.REQUEST_SLEEP_SECONDS)
            return resp.json()
        except Exception as err:  # noqa: BLE001 - retry anything transient
            last_err = err
            time.sleep(2 * attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


# ----------------------------- Schema -----------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS events (
  gw INTEGER PRIMARY KEY,
  deadline_utc TEXT,
  finished INTEGER,
  data_checked INTEGER,
  avg_score INTEGER,
  highest_score INTEGER
);
CREATE TABLE IF NOT EXISTS players (
  id INTEGER PRIMARY KEY,
  web_name TEXT,
  full_name TEXT,
  team_short TEXT,
  position TEXT
);
CREATE TABLE IF NOT EXISTS player_daily (
  date TEXT,
  player_id INTEGER,
  price_tenths INTEGER,
  selected_by_pct REAL,
  status TEXT,
  news TEXT,
  form REAL,
  total_points INTEGER,
  PRIMARY KEY (date, player_id)
);
CREATE TABLE IF NOT EXISTS league_entries (
  entry_id INTEGER PRIMARY KEY,
  player_name TEXT,
  team_name TEXT
);
CREATE TABLE IF NOT EXISTS standings_daily (
  date TEXT,
  entry_id INTEGER,
  rank INTEGER,
  total INTEGER,
  event_total INTEGER,
  PRIMARY KEY (date, entry_id)
);
CREATE TABLE IF NOT EXISTS entry_gw (
  entry_id INTEGER,
  gw INTEGER,
  points INTEGER,
  total_points INTEGER,
  overall_rank INTEGER,
  bank_tenths INTEGER,
  value_tenths INTEGER,
  transfers_made INTEGER,
  transfers_cost INTEGER,
  points_on_bench INTEGER,
  chip TEXT,
  PRIMARY KEY (entry_id, gw)
);
CREATE TABLE IF NOT EXISTS picks (
  entry_id INTEGER,
  gw INTEGER,
  player_id INTEGER,
  slot INTEGER,
  multiplier INTEGER,
  is_captain INTEGER,
  is_vice INTEGER,
  PRIMARY KEY (entry_id, gw, player_id)
);
CREATE TABLE IF NOT EXISTS transfers (
  entry_id INTEGER,
  gw INTEGER,
  time_utc TEXT,
  player_in INTEGER,
  player_out INTEGER,
  PRIMARY KEY (entry_id, time_utc, player_in, player_out)
);
"""

POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def open_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(SCHEMA)
    return conn


# ----------------------------- Sync steps -----------------------------

def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def save_raw(name: str, payload) -> None:
    day_dir = os.path.join(config.RAW_DIR, today_utc())
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, f"{name}.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))


def sync_bootstrap(conn: sqlite3.Connection):
    """Players, teams, events, daily price/ownership. Returns (bootstrap, latest_finished_gw)."""
    bs = fetch_json("/bootstrap-static/")
    teams = {t["id"]: t["short_name"] for t in bs["teams"]}
    date = today_utc()

    latest_finished = 0
    for ev in bs["events"]:
        conn.execute(
            "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)",
            (
                ev["id"],
                ev["deadline_time"],
                1 if ev.get("finished") else 0,
                1 if ev.get("data_checked") else 0,
                ev.get("average_entry_score"),
                ev.get("highest_score"),
            ),
        )
        if ev.get("finished"):
            latest_finished = max(latest_finished, ev["id"])

    slim_players = []
    for el in bs["elements"]:
        full_name = f"{el.get('first_name', '')} {el.get('second_name', '')}".strip()
        conn.execute(
            "INSERT OR REPLACE INTO players VALUES (?,?,?,?,?)",
            (
                el["id"],
                el["web_name"],
                full_name,
                teams.get(el["team"], "?"),
                POSITIONS.get(el["element_type"], "?"),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO player_daily VALUES (?,?,?,?,?,?,?,?)",
            (
                date,
                el["id"],
                el.get("now_cost"),
                float(el.get("selected_by_percent") or 0),
                el.get("status"),
                (el.get("news") or "")[:300],
                float(el.get("form") or 0),
                el.get("total_points"),
            ),
        )
        slim_players.append(
            {
                "id": el["id"],
                "web_name": el["web_name"],
                "team": teams.get(el["team"], "?"),
                "pos": POSITIONS.get(el["element_type"], "?"),
                "cost": el.get("now_cost"),
                "sel": el.get("selected_by_percent"),
                "status": el.get("status"),
                "news": (el.get("news") or "")[:200],
            }
        )

    save_raw("players", slim_players)
    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('last_sync_utc', ?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
    )
    conn.commit()
    return bs, latest_finished


def sync_league(conn: sqlite3.Connection):
    """League standings snapshot. Returns list of entry_ids."""
    date = today_utc()
    entry_ids = []
    page = 1
    rows_seen = 0
    while True:
        data = fetch_json(f"/leagues-classic/{config.LEAGUE_ID}/standings/?page_standings={page}")
        if data is None:
            break
        standings = data.get("standings", {})
        results = standings.get("results", []) or []
        for row in results:
            entry_ids.append(row["entry"])
            conn.execute(
                "INSERT OR REPLACE INTO league_entries VALUES (?,?,?)",
                (row["entry"], row.get("player_name"), row.get("entry_name")),
            )
            conn.execute(
                "INSERT OR REPLACE INTO standings_daily VALUES (?,?,?,?,?)",
                (date, row["entry"], row.get("rank"), row.get("total"), row.get("event_total")),
            )
            rows_seen += 1
        if page == 1:
            for row in (data.get("new_entries", {}).get("results", []) or []):
                entry_ids.append(row["entry"])
                conn.execute(
                    "INSERT OR REPLACE INTO league_entries VALUES (?,?,?)",
                    (row["entry"], row.get("player_name"), row.get("entry_name")),
                )
            save_raw("league", data)
        if not standings.get("has_next"):
            break
        page += 1

    conn.commit()
    # De-dupe, keep order
    return list(dict.fromkeys(entry_ids)), rows_seen


def synced_gws_for(conn: sqlite3.Connection, entry_id: int):
    rows = conn.execute("SELECT DISTINCT gw FROM picks WHERE entry_id=?", (entry_id,)).fetchall()
    return {r[0] for r in rows}


def sync_entry(conn: sqlite3.Connection, entry_id: int, latest_finished: int):
    """History, transfers, and any missing gameweek picks for one manager."""
    hist = fetch_json(f"/entry/{entry_id}/history/")
    chips_by_gw = {}
    if hist:
        for chip in hist.get("chips", []) or []:
            chips_by_gw[chip.get("event")] = chip.get("name")
        for row in hist.get("current", []) or []:
            conn.execute(
                """INSERT OR REPLACE INTO entry_gw
                   VALUES (?,?,?,?,?,?,?,?,?,?,
                           COALESCE((SELECT chip FROM entry_gw WHERE entry_id=? AND gw=?), ?))""",
                (
                    entry_id,
                    row["event"],
                    row.get("points"),
                    row.get("total_points"),
                    row.get("overall_rank"),
                    row.get("bank"),
                    row.get("value"),
                    row.get("event_transfers"),
                    row.get("event_transfers_cost"),
                    row.get("points_on_bench"),
                    entry_id,
                    row["event"],
                    chips_by_gw.get(row["event"]),
                ),
            )

    trans = fetch_json(f"/entry/{entry_id}/transfers/")
    if trans:
        for tr in trans:
            conn.execute(
                "INSERT OR REPLACE INTO transfers VALUES (?,?,?,?,?)",
                (
                    entry_id,
                    tr.get("event"),
                    tr.get("time"),
                    tr.get("element_in"),
                    tr.get("element_out"),
                ),
            )

    have = synced_gws_for(conn, entry_id)
    for gw in range(1, latest_finished + 1):
        if gw in have:
            continue
        picks = fetch_json(f"/entry/{entry_id}/event/{gw}/picks/")
        if not picks:
            continue
        chip = picks.get("active_chip")
        for pk in picks.get("picks", []) or []:
            conn.execute(
                "INSERT OR REPLACE INTO picks VALUES (?,?,?,?,?,?,?)",
                (
                    entry_id,
                    gw,
                    pk["element"],
                    pk.get("position"),
                    pk.get("multiplier"),
                    1 if pk.get("is_captain") else 0,
                    1 if pk.get("is_vice_captain") else 0,
                ),
            )
        if chip:
            conn.execute(
                "UPDATE entry_gw SET chip=? WHERE entry_id=? AND gw=?",
                (chip, entry_id, gw),
            )
    conn.commit()


# ----------------------------- Main -----------------------------

def main() -> int:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = open_db()

    bs, latest_finished = sync_bootstrap(conn)
    print(f"[sync] bootstrap ok, latest finished GW = {latest_finished}")

    entry_ids, ranked_rows = sync_league(conn)
    print(f"[sync] league {config.LEAGUE_ID}: {len(entry_ids)} entries ({ranked_rows} ranked)")

    for i, entry_id in enumerate(entry_ids, 1):
        try:
            sync_entry(conn, entry_id, latest_finished)
        except Exception as err:  # noqa: BLE001 - one bad entry must not kill the run
            print(f"[warn] entry {entry_id} failed: {err}", file=sys.stderr)
        if i % 10 == 0:
            print(f"[sync] entries {i}/{len(entry_ids)}")

    conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_full_sync_utc', ?)", (started,))
    conn.commit()
    conn.close()
    print("[sync] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
