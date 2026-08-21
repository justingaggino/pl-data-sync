"""Quick CLI queries over data/fpl.db for strategy sessions.

Examples:
  python analysis.py table
  python analysis.py chips
  python analysis.py captains 679676
  python analysis.py rival 679676
  python analysis.py eo
  python analysis.py prices 7
"""

import sqlite3
import sys

import config


def q(conn, sql, args=()):
    cur = conn.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return cols, cur.fetchall()


def show(cols, rows, limit=60):
    widths = [max(len(str(c)), *(len(str(r[i])) for r in rows[:limit])) if rows else len(str(c))
              for i, c in enumerate(cols)]
    print("  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    for r in rows[:limit]:
        print("  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    if len(rows) > limit:
        print(f"... {len(rows) - limit} more rows")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "table"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    conn = sqlite3.connect(config.DB_PATH)

    if cmd == "table":
        cols, rows = q(conn, """
            SELECT s.rank, e.player_name, e.team_name, s.total, s.event_total
            FROM standings_daily s JOIN league_entries e USING (entry_id)
            WHERE s.date = (SELECT MAX(date) FROM standings_daily) ORDER BY s.rank""")
    elif cmd == "chips":
        cols, rows = q(conn, """
            SELECT e.player_name, g.gw, g.chip FROM entry_gw g
            JOIN league_entries e USING (entry_id)
            WHERE g.chip IS NOT NULL ORDER BY g.gw, e.player_name""")
    elif cmd == "captains":
        where, args = ("WHERE p.entry_id = ?", (int(arg),)) if arg else ("", ())
        cols, rows = q(conn, f"""
            SELECT e.player_name, p.gw, pl.web_name AS captain
            FROM picks p JOIN league_entries e USING (entry_id)
            JOIN players pl ON pl.id = p.player_id
            {where} {'AND' if where else 'WHERE'} p.is_captain = 1
            ORDER BY p.gw, e.player_name""", args)
    elif cmd == "rival" and arg:
        eid = int(arg)
        print("== history ==")
        cols, rows = q(conn, """
            SELECT gw, points, total_points, overall_rank, transfers_made,
                   transfers_cost, points_on_bench, chip
            FROM entry_gw WHERE entry_id=? ORDER BY gw""", (eid,))
        show(cols, rows)
        print("
== transfers ==")
        cols, rows = q(conn, """
            SELECT t.gw, pi.web_name AS came_in, po.web_name AS went_out
            FROM transfers t
            LEFT JOIN players pi ON pi.id = t.player_in
            LEFT JOIN players po ON po.id = t.player_out
            WHERE t.entry_id=? ORDER BY t.time_utc""", (eid,))
    elif cmd == "eo":
        cols, rows = q(conn, """
            SELECT pl.web_name, pl.team_short, COUNT(*) AS owners,
                   SUM(p.is_captain) AS captains
            FROM picks p JOIN players pl ON pl.id = p.player_id
            WHERE p.gw = (SELECT MAX(gw) FROM picks)
            GROUP BY p.player_id ORDER BY owners DESC, captains DESC""")
    elif cmd == "prices":
        days = int(arg or 7)
        cols, rows = q(conn, f"""
            SELECT pl.web_name, pl.team_short,
                   MIN(d.price_tenths)/10.0 AS low, MAX(d.price_tenths)/10.0 AS high,
                   ROUND(MAX(d.selected_by_pct) - MIN(d.selected_by_pct), 2) AS sel_swing
            FROM player_daily d JOIN players pl ON pl.id = d.player_id
            WHERE d.date >= date('now', '-{days} day')
            GROUP BY d.player_id
            HAVING high != low OR sel_swing > 1.0
            ORDER BY sel_swing DESC""")
    else:
        print(__doc__)
        return 1

    show(cols, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
