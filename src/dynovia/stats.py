"""Season statistics: one number per player, out of many sources' opinions.

Goals are stored per source, so counting rows would count the same goal once
per source that saw it. Instead, per match, the most trusted source that
reported anything wins outright - mixing two sources' scorer lists for one
match is how you end up with seven goals in a 4-1 win.
"""

from __future__ import annotations

import sqlite3

from dynovia.merge import GOAL_TRUST
from dynovia.models import normalize_player


def goals_by_player(conn: sqlite3.Connection, season: str) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT g.match_id, g.source, p.name FROM goals g"
        " JOIN players p ON p.id = g.player_id"
        " JOIN matches m ON m.id = g.match_id WHERE m.season = ?",
        (season,),
    ).fetchall()

    by_match: dict[int, dict[str, list[str]]] = {}
    for row in rows:
        by_match.setdefault(row["match_id"], {}).setdefault(row["source"], []).append(
            row["name"]
        )

    tally: dict[str, int] = {}
    for sources in by_match.values():
        best = min(sources, key=_rank)
        for name in sources[best]:
            tally[name] = tally.get(name, 0) + 1
    return sorted(tally.items(), key=lambda pair: (-pair[1], pair[0]))


def _rank(source: str) -> int:
    return GOAL_TRUST.index(source) if source in GOAL_TRUST else len(GOAL_TRUST)


def appearances_by_player(conn: sqlite3.Connection, season: str) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT p.name, COUNT(DISTINCT a.match_id) AS played FROM appearances a"
        " JOIN players p ON p.id = a.player_id"
        " JOIN matches m ON m.id = a.match_id WHERE m.season = ?"
        " GROUP BY p.id ORDER BY played DESC, p.name",
        (season,),
    ).fetchall()
    return [(row["name"], row["played"]) for row in rows]


def player_summary(conn: sqlite3.Connection, season: str, player: str) -> dict:
    """Everything the bot shows for one player. Minutes only exist for matches
    with an imported PZPN protocol, so they are reported separately from
    appearances rather than implied by them."""
    row = conn.execute(
        "SELECT p.id, p.name FROM players p WHERE p.normalized_name = ?",
        (normalize_player(player),),
    ).fetchone()
    if row is None:
        return {}

    played = conn.execute(
        "SELECT COUNT(DISTINCT a.match_id) AS n FROM appearances a"
        " JOIN matches m ON m.id = a.match_id"
        " WHERE a.player_id = ? AND m.season = ?",
        (row["id"], season),
    ).fetchone()["n"]
    minutes = conn.execute(
        "SELECT SUM(COALESCE(a.minute_out, 90) - COALESCE(a.minute_in, 0)) AS n"
        " FROM appearances a JOIN matches m ON m.id = a.match_id"
        " WHERE a.player_id = ? AND m.season = ? AND a.source = 'laczynaspilka'",
        (row["id"], season),
    ).fetchone()["n"]
    cards = conn.execute(
        "SELECT c.color, COUNT(*) AS n FROM cards c JOIN matches m ON m.id = c.match_id"
        " WHERE c.player_id = ? AND m.season = ? GROUP BY c.color",
        (row["id"], season),
    ).fetchall()

    goals = dict(goals_by_player(conn, season)).get(row["name"], 0)
    return {
        "name": row["name"],
        "played": played,
        "minutes": minutes or 0,
        "goals": goals,
        "cards": {card["color"]: card["n"] for card in cards},
    }
