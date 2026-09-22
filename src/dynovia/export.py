"""SQLite -> web/data/*.json, the only thing the frontend ever reads.

The page never talks to a source and never queries anything: it fetches these
files and renders them. Everything that needs judgement - which source to
believe, who a name refers to - has already happened by the time a number
lands here.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from dynovia import db, stats
from dynovia.config import ROOT, VAPID_PUBLIC_KEY
from dynovia.merge import GOAL_TRUST
from dynovia.models import CLUB, normalize_team, season_for

log = logging.getLogger(__name__)

WEB_DATA = ROOT / "web" / "data"
US = normalize_team(CLUB)


def scorers_by_match(conn, season: str) -> dict[int, list[dict]]:
    """Goals per match from the most trusted source that saw the match - the
    same rule the statistics use, so the two can never tell different stories."""
    rows = conn.execute(
        "SELECT g.match_id, g.source, g.minute, p.name FROM goals g"
        " JOIN players p ON p.id = g.player_id AND p.ours = 1"
        " JOIN matches m ON m.id = g.match_id WHERE m.season = ?",
        (season,),
    ).fetchall()

    by_match: dict[int, dict[str, list]] = {}
    for row in rows:
        by_match.setdefault(row["match_id"], {}).setdefault(row["source"], []).append(
            {"player": row["name"], "minute": row["minute"]}
        )

    out = {}
    for match_id, sources in by_match.items():
        best = min(
            sources,
            key=lambda s: GOAL_TRUST.index(s) if s in GOAL_TRUST else len(GOAL_TRUST),
        )
        out[match_id] = sorted(
            sources[best], key=lambda goal: goal["minute"] or 999
        )
    return out


def build(conn, season: str) -> dict[str, object]:
    scorers = scorers_by_match(conn, season)
    ids = db.match_ids(conn)

    matches = []
    for key, match in sorted(
        db.stored_matches(conn).items(),
        key=lambda item: (item[1].date or dt.date.max, item[1].time or dt.time()),
    ):
        if match.season != season:
            continue
        match_id = ids[key]
        matches.append(
            {
                "date": match.date.isoformat() if match.date else None,
                "time": match.time.strftime("%H:%M") if match.time else None,
                "competition": match.competition,
                "round": match.round,
                "home": match.home,
                "away": match.away,
                "homeScore": match.home_score,
                "awayScore": match.away_score,
                "status": match.status,
                "atHome": normalize_team(match.home) == US,
                "scorers": scorers.get(match_id, []),
            }
        )

    players = []
    goals = dict(stats.goals_by_player(conn, season))
    assists = dict(stats.assists_by_player(conn, season))
    for name, played in stats.appearances_by_player(conn, season):
        summary = stats.player_summary(conn, season, name)
        players.append(
            {
                "name": name,
                "played": played,
                "minutes": summary.get("minutes", 0),
                "goals": goals.get(name, 0),
                "assists": assists.get(name, 0),
                "yellow": summary.get("cards", {}).get("yellow", 0),
                "red": summary.get("cards", {}).get("red", 0),
            }
        )

    table = [
        {
            "position": row["position"],
            "team": row["team"],
            "played": row["played"],
            "points": row["points"],
            "goalDifference": row["goal_difference"],
            "us": row["team_key"] == US,
        }
        for row in db.read_table(conn, season)
    ]

    sources = [
        {
            "source": row["source"],
            "matches": row["matches"],
            "last": row["last"],
            "failures": int(db.get_setting(conn, f"failures:{row['source']}") or 0),
        }
        for row in db.source_status(conn)
    ]

    # Not the wall clock: that changes on every run and would make these files
    # differ every time, so Actions would commit and Cloudflare would rebuild
    # even when nothing was fetched. The newest fetch is what "generated"
    # actually means here.
    newest = max((row["last"] for row in db.source_status(conn)), default="")

    return {
        "meta.json": {
            "generatedAt": newest[:19] or None,
            "season": season,
            "club": CLUB,
            # Not a secret: the page needs it to subscribe at all.
            "vapidPublicKey": VAPID_PUBLIC_KEY,
        },
        "matches.json": matches,
        "stats.json": players,
        "table.json": table,
        "sources.json": sources,
    }


def write(conn, season: str | None = None, target: Path = WEB_DATA) -> list[Path]:
    season = season or season_for(dt.date.today())
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, payload in build(conn, season).items():
        path = target / name
        # sort_keys and a trailing newline so an unchanged run produces a
        # byte-identical file and Actions has nothing to commit.
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    log.info("wyeksportowano %d plikow do %s", len(written), target)
    return written
