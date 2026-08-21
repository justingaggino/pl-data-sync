"""Offline end-to-end test: stubs the FPL API and runs sync -> digest.

Run:  python test_offline.py
Exits 0 and prints OK if the whole pipeline works.
"""

import json
import os
import shutil
import sys

import config

# Isolate test artefacts
config.DB_PATH = "test_out/fpl.db"
config.RAW_DIR = "test_out/raw"
config.DOCS_DIR = "test_out/docs"
config.REQUEST_SLEEP_SECONDS = 0

import sync  # noqa: E402
import digest  # noqa: E402

FAKE = {
    "/bootstrap-static/": {
        "teams": [{"id": 1, "short_name": "MCI"}, {"id": 2, "short_name": "ARS"}],
        "events": [
            {"id": 1, "deadline_time": "2026-08-21T17:30:00Z", "finished": True,
             "data_checked": True, "average_entry_score": 54, "highest_score": 112},
            {"id": 2, "deadline_time": "2026-08-28T17:30:00Z", "finished": False,
             "data_checked": False, "average_entry_score": None, "highest_score": None},
        ],
        "elements": [
            {"id": 10, "web_name": "Haaland", "first_name": "Erling", "second_name": "Haaland",
             "team": 1, "element_type": 4, "now_cost": 155, "selected_by_percent": "60.1",
             "status": "a", "news": "", "form": "8.0", "total_points": 13},
            {"id": 20, "web_name": "Calafiori", "first_name": "Riccardo", "second_name": "Calafiori",
             "team": 2, "element_type": 2, "now_cost": 55, "selected_by_percent": "12.0",
             "status": "a", "news": "", "form": "5.0", "total_points": 6},
        ],
    },
    f"/leagues-classic/{config.LEAGUE_ID}/standings/?page_standings=1": {
        "standings": {"has_next": False, "results": [
            {"entry": config.TEAM_ID, "rank": 1, "total": 71, "event_total": 71,
             "player_name": "Justin Gaggino", "entry_name": "Singapore Slingers"},
            {"entry": 111, "rank": 2, "total": 60, "event_total": 60,
             "player_name": "Rival One", "entry_name": "Rivals FC"},
        ]},
        "new_entries": {"results": []},
    },
}

for eid in (config.TEAM_ID, 111):
    FAKE[f"/entry/{eid}/history/"] = {
        "current": [{"event": 1, "points": 71 if eid == config.TEAM_ID else 60,
                     "total_points": 71 if eid == config.TEAM_ID else 60,
                     "overall_rank": 1000, "bank": 5, "value": 1000,
                     "event_transfers": 0, "event_transfers_cost": 0,
                     "points_on_bench": 7}],
        "chips": [{"name": "bboost", "event": 1, "time": "2026-08-21T00:00:00Z"}]
        if eid == config.TEAM_ID else [],
    }
    FAKE[f"/entry/{eid}/transfers/"] = [
        {"entry": eid, "event": 2, "time": "2026-08-25T02:00:00Z",
         "element_in": 20, "element_out": 10}] if eid == 111 else []
    FAKE[f"/entry/{eid}/event/1/picks/"] = {
        "active_chip": "bboost" if eid == config.TEAM_ID else None,
        "picks": [
            {"element": 10, "position": 1, "multiplier": 2, "is_captain": True,
             "is_vice_captain": False},
            {"element": 20, "position": 2, "multiplier": 1, "is_captain": False,
             "is_vice_captain": True},
        ],
    }


def fake_fetch(path):
    if path not in FAKE:
        raise AssertionError(f"unexpected fetch: {path}")
    return json.loads(json.dumps(FAKE[path]))


def main() -> int:
    shutil.rmtree("test_out", ignore_errors=True)
    sync.fetch_json = fake_fetch
    assert sync.main() == 0

    assert digest.main() == 0
    with open(os.path.join(config.DOCS_DIR, "latest.json"), encoding="utf-8") as fh:
        d = json.load(fh)

    assert d["latest_finished_gw"] == 1
    assert d["justin"]["rank"] == 1
    assert d["league"]["size"] == 2
    assert len(d["table"]) == 2
    justin = d["justin"]["profile"]
    assert justin["chips_used"] == [{"gw": 1, "chip": "bboost"}]
    assert justin["captain_history"][0]["captain"].startswith("Haaland")
    rival = next(r for r in d["rivals"] if r["entry_id"] == 111)
    assert rival["total_transfers"] == 0  # gw2 transfer not in entry_gw yet (gw unfinished)
    eo = {r["player"]: r for r in d["league_effective_ownership"]}
    assert eo["Haaland (MCI)"]["owned_by"] == 2
    assert eo["Haaland (MCI)"]["captained_by"] == 2

    shutil.rmtree("test_out", ignore_errors=True)
    print("OK: full pipeline works (sync -> db -> digest).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
