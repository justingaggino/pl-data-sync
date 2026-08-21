"""Build docs/latest.json: the computed league intelligence digest.

This file is what Claude's scheduled research runs fetch. Keep it small,
self-describing, and stable in shape.
Run after sync.py:  python digest.py
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

import config

CHIP_SETS = {
    "wildcard": 2, "bboost": 2, "3xc": 2, "freehit": 2,
}


def q(conn, sql, args=()):
    cur = conn.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def player_name_map(conn):
    return {
        r["id"]: f'{r["web_name"]} ({r["team_short"]})'
        for r in q(conn, "SELECT id, web_name, team_short FROM players")
    }


def latest_date(conn, table):
    row = conn.execute(f"SELECT MAX(date) FROM {table}").fetchone()
    return row[0] if row else None


def build(conn):
    names = player_name_map(conn)
    sd = latest_date(conn, "standings_daily")
    pd = latest_date(conn, "player_daily")
    latest_gw_row = conn.execute(
        "SELECT MAX(gw) FROM events WHERE finished=1"
    ).fetchone()
    latest_gw = latest_gw_row[0] or 0

    # League table (latest snapshot)
    table = q(
        conn,
        """SELECT s.rank, s.entry_id, e.player_name, e.team_name, s.total, s.event_total
           FROM standings_daily s JOIN league_entries e ON e.entry_id = s.entry_id
           WHERE s.date = ? ORDER BY s.rank""",
        (sd,),
    )

    rivals = []
    for row in q(conn, "SELECT entry_id, player_name, team_name FROM league_entries"):
        eid = row["entry_id"]
        gws = q(
            conn,
            """SELECT gw, points, total_points, overall_rank, transfers_made,
                      transfers_cost, points_on_bench, chip, value_tenths, bank_tenths
               FROM entry_gw WHERE entry_id=? ORDER BY gw""",
            (eid,),
        )
        chips_used = [{"gw": g["gw"], "chip": g["chip"]} for g in gws if g["chip"]]
        used_names = [c["chip"] for c in chips_used]
        hits = sum((g["transfers_cost"] or 0) for g in gws)
        moves = sum((g["transfers_made"] or 0) for g in gws)
        caps = q(
            conn,
            """SELECT p.gw, p.player_id FROM picks p
               WHERE p.entry_id=? AND p.is_captain=1 ORDER BY p.gw""",
            (eid,),
        )
        cap_counts = {}
        for c in caps:
            nm = names.get(c["player_id"], str(c["player_id"]))
            cap_counts[nm] = cap_counts.get(nm, 0) + 1
        last_gw_with_moves = max((g["gw"] for g in gws if (g["transfers_made"] or 0) > 0), default=0)
        rivals.append(
            {
                "entry_id": eid,
                "player_name": row["player_name"],
                "team_name": row["team_name"],
                "chips_used": chips_used,
                "chips_left_set1_estimate": {
                    "wildcard": 1 - used_names.count("wildcard") if latest_gw < 20 else None,
                    "bboost": 1 - used_names.count("bboost") if latest_gw < 20 else None,
                    "3xc": 1 - used_names.count("3xc") if latest_gw < 20 else None,
                    "freehit": 1 - used_names.count("freehit") if latest_gw < 20 else None,
                },
                "total_transfers": moves,
                "total_hit_points": hits,
                "captain_history": [
                    {"gw": c["gw"], "captain": names.get(c["player_id"], "?")} for c in caps[-6:]
                ],
                "captain_counts": cap_counts,
                "gws_since_last_transfer": (latest_gw - last_gw_with_moves) if latest_gw else 0,
                "team_value_tenths": gws[-1]["value_tenths"] if gws else None,
            }
        )

    # Effective ownership of every owned player within the league (latest finished GW)
    eo = q(
        conn,
        """SELECT p.player_id, COUNT(*) AS owners,
                  SUM(CASE WHEN p.is_captain=1 THEN 1 ELSE 0 END) AS captains,
                  SUM(CASE WHEN p.slot <= 11 OR p.multiplier > 0 THEN 1 ELSE 0 END) AS starters
           FROM picks p WHERE p.gw = ? GROUP BY p.player_id
           ORDER BY owners DESC""",
        (latest_gw,),
    ) if latest_gw else []
    league_size = max(len(rivals), 1)
    league_eo = [
        {
            "player": names.get(r["player_id"], "?"),
            "owned_by": r["owners"],
            "captained_by": r["captains"],
            "eo_pct": round(100.0 * (r["starters"] + r["captains"]) / league_size, 1),
        }
        for r in eo[:60]
    ]

    # Price and ownership trajectory (last 8 snapshots) for watchlist + high-league-EO players
    watch_ids = set()
    for r in q(conn, "SELECT id, web_name FROM players"):
        for w in config.WATCHLIST_NAMES:
            if w.lower() in r["web_name"].lower():
                watch_ids.add(r["id"])
    for r in eo[:25]:
        watch_ids.add(r["player_id"])

    price_moves = []
    for pid in watch_ids:
        rows = q(
            conn,
            """SELECT date, price_tenths, selected_by_pct, status, news
               FROM player_daily WHERE player_id=? ORDER BY date DESC LIMIT 8""",
            (pid,),
        )
        if not rows:
            continue
        newest, oldest = rows[0], rows[-1]
        price_moves.append(
            {
                "player": names.get(pid, "?"),
                "price_now": (newest["price_tenths"] or 0) / 10.0,
                "price_change_window": ((newest["price_tenths"] or 0) - (oldest["price_tenths"] or 0)) / 10.0,
                "sel_by_pct_now": newest["selected_by_pct"],
                "sel_by_pct_change_window": round((newest["selected_by_pct"] or 0) - (oldest["selected_by_pct"] or 0), 2),
                "status": newest["status"],
                "news": newest["news"],
                "window_days": len(rows),
            }
        )
    price_moves.sort(key=lambda r: abs(r["sel_by_pct_change_window"] or 0), reverse=True)

    justin = next((r for r in rivals if r["entry_id"] == config.TEAM_ID), None)
    justin_rank = next((t["rank"] for t in table if t["entry_id"] == config.TEAM_ID), None)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "league": {"id": config.LEAGUE_ID, "name": config.LEAGUE_NAME, "size": league_size},
        "latest_finished_gw": latest_gw,
        "snapshot_dates": {"standings": sd, "players": pd},
        "justin": {"entry_id": config.TEAM_ID, "rank": justin_rank, "profile": justin},
        "table": table,
        "rivals": rivals,
        "league_effective_ownership": league_eo,
        "price_and_ownership_moves": price_moves,
        "notes": "All data from the public FPL API. eo_pct = (starters+captain doubles)/league size.",
    }


INDEX_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>pl-data-sync</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#111}
table{border-collapse:collapse;width:100%;margin:1rem 0}td,th{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;font-size:14px}
h1{font-size:22px}h2{font-size:17px;margin-top:2rem}small{color:#666}</style></head>
<body><h1>pl-data-sync</h1><p><small id="meta">loading…</small></p>
<h2>League table</h2><table id="tbl"><tr><th>#</th><th>Manager</th><th>Team</th><th>Total</th><th>GW</th></tr></table>
<h2>Top league effective ownership</h2><table id="eo"><tr><th>Player</th><th>Owned</th><th>Capt</th><th>EO%</th></tr></table>
<script>
fetch('latest.json').then(r=>r.json()).then(d=>{
document.getElementById('meta').textContent='Generated '+d.generated_at_utc+' · GW'+d.latest_finished_gw+' · '+d.league.name;
const t=document.getElementById('tbl');
(d.table||[]).forEach(r=>{const tr=document.createElement('tr');
tr.innerHTML='<td>'+r.rank+'</td><td>'+r.player_name+'</td><td>'+r.team_name+'</td><td>'+r.total+'</td><td>'+r.event_total+'</td>';
if(r.entry_id===d.justin.entry_id){tr.style.fontWeight='700'}t.appendChild(tr);});
const e=document.getElementById('eo');
(d.league_effective_ownership||[]).slice(0,20).forEach(r=>{const tr=document.createElement('tr');
tr.innerHTML='<td>'+r.player+'</td><td>'+r.owned_by+'</td><td>'+r.captained_by+'</td><td>'+r.eo_pct+'</td>';e.appendChild(tr);});
}).catch(e=>{document.getElementById('meta').textContent='No data yet. Run the sync workflow.'});
</script></body></html>
"""


def main() -> int:
    conn = sqlite3.connect(config.DB_PATH)
    digest = build(conn)
    os.makedirs(config.DOCS_DIR, exist_ok=True)
    with open(os.path.join(config.DOCS_DIR, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(digest, fh, ensure_ascii=False, indent=1)
    index_path = os.path.join(config.DOCS_DIR, "index.html")
    with open(index_path, "w", encoding="utf-8") as fh:
        fh.write(INDEX_HTML)
    print(f"[digest] wrote docs/latest.json (GW{digest['latest_finished_gw']}, "
          f"{len(digest['rivals'])} rivals)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
