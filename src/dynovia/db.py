"""SQLite storage. Plain sqlite3, no ORM - one writer, one reader, one user.

The shape that matters: `match_sources` holds what each source said, verbatim
and per source, and `matches` holds the merged view the app shows. Nothing
writes to `matches` from a single source - it is recomputed by merge.py every
time a source delivers, so the displayed value never depends on which scraper
happened to run last.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from dynovia import merge, players
from dynovia.config import DB_PATH
from dynovia.models import (
    MatchData,
    MatchKey,
    MatchReport,
    normalize_player,
    normalize_team,
)

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS matches (
    id          INTEGER PRIMARY KEY,
    season      TEXT    NOT NULL,
    home_key    TEXT    NOT NULL,
    away_key    TEXT    NOT NULL,
    date        TEXT    NOT NULL DEFAULT '',   -- '' until the round is scheduled
    time        TEXT    NOT NULL DEFAULT '',
    competition TEXT    NOT NULL DEFAULT '',
    round       INTEGER,
    home        TEXT    NOT NULL,
    away        TEXT    NOT NULL,
    home_score  INTEGER,
    away_score  INTEGER,
    status      TEXT    NOT NULL,
    venue       TEXT,
    updated_at  TEXT    NOT NULL,
    UNIQUE (season, home_key, away_key)
);

-- What one source said, untouched. This is the audit trail that makes a
-- conflict resolvable and a wrong value traceable back to whoever reported it.
CREATE TABLE IF NOT EXISTS match_sources (
    match_id    INTEGER NOT NULL REFERENCES matches(id),
    source      TEXT    NOT NULL,
    external_id TEXT,
    season      TEXT    NOT NULL,
    date        TEXT    NOT NULL DEFAULT '',
    time        TEXT    NOT NULL DEFAULT '',
    competition TEXT    NOT NULL DEFAULT '',
    round       INTEGER,
    home        TEXT    NOT NULL,
    away        TEXT    NOT NULL,
    home_score  INTEGER,
    away_score  INTEGER,
    status      TEXT    NOT NULL,
    venue       TEXT,
    fetched_at  TEXT    NOT NULL,
    PRIMARY KEY (match_id, source)
);

CREATE TABLE IF NOT EXISTS conflicts (
    id          INTEGER PRIMARY KEY,
    match_id    INTEGER NOT NULL REFERENCES matches(id),
    field       TEXT    NOT NULL,
    source_a    TEXT    NOT NULL,
    value_a     TEXT,
    source_b    TEXT    NOT NULL,
    value_b     TEXT,
    detected_at TEXT    NOT NULL,
    resolved    INTEGER NOT NULL DEFAULT 0,
    chosen      TEXT,   -- the value the owner picked, which then wins outright
    UNIQUE (match_id, field, source_a, value_a, source_b, value_b)
);

CREATE TABLE IF NOT EXISTS players (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    -- 0 means an opposition player, stored only to fill in the other half of a
    -- protocol. Every statistic in the app is ours, so every statistic says so.
    ours            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS player_aliases (
    player_id INTEGER NOT NULL REFERENCES players(id),
    alias     TEXT    NOT NULL,
    source    TEXT    NOT NULL,
    PRIMARY KEY (alias, source)
);

CREATE TABLE IF NOT EXISTS appearances (
    match_id   INTEGER NOT NULL REFERENCES matches(id),
    player_id  INTEGER NOT NULL REFERENCES players(id),
    minute_in  INTEGER,
    minute_out INTEGER,
    started    INTEGER NOT NULL,
    source     TEXT    NOT NULL,
    PRIMARY KEY (match_id, player_id, source)
);

CREATE TABLE IF NOT EXISTS goals (
    id        INTEGER PRIMARY KEY,   -- assists point here, so goals need an id
    match_id  INTEGER NOT NULL REFERENCES matches(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    minute    INTEGER,
    type      TEXT    NOT NULL DEFAULT 'normal',   -- normal|penalty|own
    source    TEXT    NOT NULL
);

-- COALESCE, not a plain UNIQUE: futbolowo reports scorers without a minute and
-- SQLite treats every NULL as distinct, so a plain constraint would let the
-- same goal back in on every single run.
-- ponytail: two goals by one player in one match, both minuteless, collapse
-- into one. Without minutes there is nothing to tell them apart anyway.
CREATE UNIQUE INDEX IF NOT EXISTS goals_once
    ON goals (match_id, player_id, source, COALESCE(minute, -1));

CREATE TABLE IF NOT EXISTS assists (
    id        INTEGER PRIMARY KEY,
    goal_id   INTEGER NOT NULL REFERENCES goals(id),
    -- NULL means the owner confirmed there was no assist, which is an answer
    -- and has to be recorded, or the same goal gets asked about for ever.
    player_id INTEGER REFERENCES players(id),
    source    TEXT    NOT NULL,               -- llm|manual
    confirmed INTEGER NOT NULL DEFAULT 0,     -- only confirmed ones count
    UNIQUE (goal_id, player_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS assists_once ON assists (goal_id);

CREATE TABLE IF NOT EXISTS cards (
    match_id  INTEGER NOT NULL REFERENCES matches(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    minute    INTEGER,
    color     TEXT    NOT NULL,
    source    TEXT    NOT NULL,
    PRIMARY KEY (match_id, player_id, color, source)
);

CREATE TABLE IF NOT EXISTS articles (
    url          TEXT    PRIMARY KEY,
    source       TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    published_at TEXT,
    match_id     INTEGER REFERENCES matches(id),   -- NULL: not tied to a match
    text         TEXT    NOT NULL,
    fetched_at   TEXT    NOT NULL
);

-- Replaced wholesale per source on every scrape: a league table is a snapshot,
-- not a log, and merging two sources' standings row by row would be nonsense.
CREATE TABLE IF NOT EXISTS league_table (
    season          TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    position        INTEGER NOT NULL,
    team            TEXT    NOT NULL,
    team_key        TEXT    NOT NULL,
    played          INTEGER NOT NULL,
    points          INTEGER NOT NULL,
    goal_difference INTEGER NOT NULL,
    updated_at      TEXT    NOT NULL,
    PRIMARY KEY (season, source, team_key)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications_sent (
    match_id INTEGER NOT NULL REFERENCES matches(id),
    kind     TEXT    NOT NULL,
    sent_at  TEXT    NOT NULL,
    PRIMARY KEY (match_id, kind)   -- this is the whole deduplication guarantee
);
"""

_MATCH_VALUES = (
    ":season, :home_key, :away_key, :date, :time, :competition, :round, "
    ":home, :away, :home_score, :away_score, :status, :venue, :updated_at"
)
_MATCH_COLUMNS = (
    "season, home_key, away_key, date, time, competition, round, home, away, "
    "home_score, away_score, status, venue, updated_at"
)
_SOURCE_COLUMNS = (
    "match_id, source, external_id, season, date, time, competition, round, "
    "home, away, home_score, away_score, status, venue, fetched_at"
)
_SOURCE_VALUES = (
    ":match_id, :source, :external_id, :season, :date, :time, :competition, "
    ":round, :home, :away, :home_score, :away_score, :status, :venue, :fetched_at"
)
_SOURCE_UPDATES = ", ".join(
    f"{column} = excluded.{column}"
    for column in (
        "external_id date time competition round home away home_score "
        "away_score status venue fetched_at"
    ).split()
)


def connect(path=DB_PATH) -> sqlite3.Connection:
    if path != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Columns added after the fact.

    The database file lives in the repo and is years older than any new column
    by the time one is needed, and CREATE TABLE IF NOT EXISTS never touches a
    table that already exists. So the change has to be made explicitly.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(players)")}
    if "ours" not in columns:
        # Everyone already stored was resolved against kadra.txt, so everyone
        # already stored is ours. Only the protocol brings in anybody else.
        conn.execute(
            "ALTER TABLE players ADD COLUMN ours INTEGER NOT NULL DEFAULT 1"
        )
        conn.commit()


def _to_row(match: MatchData) -> dict:
    return {
        "season": match.season,
        "home_key": normalize_team(match.home),
        "away_key": normalize_team(match.away),
        "date": match.date.isoformat() if match.date else "",
        "time": match.time.strftime("%H:%M") if match.time else "",
        "competition": match.competition,
        "round": match.round,
        "home": match.home,
        "away": match.away,
        "home_score": match.home_score,
        "away_score": match.away_score,
        "status": match.status,
        "venue": match.venue,
        "external_id": match.external_id,
    }


def _from_row(row: sqlite3.Row) -> MatchData:
    columns = row.keys()
    return MatchData(
        season=row["season"],
        date=dt.date.fromisoformat(row["date"]) if row["date"] else None,
        time=dt.time.fromisoformat(row["time"]) if row["time"] else None,
        competition=row["competition"],
        home=row["home"],
        away=row["away"],
        round=row["round"],
        home_score=row["home_score"],
        away_score=row["away_score"],
        status=row["status"],
        venue=row["venue"],
        external_id=row["external_id"] if "external_id" in columns else None,
    )


def store_matches(
    conn: sqlite3.Connection,
    matches: list[MatchData],
    source: str,
    fetched_at: dt.datetime,
) -> list[tuple[int, merge.Conflict]]:
    """Record this source's view of each match and recompute the merged row.

    Returns every disagreement found, so the caller can alert on it. `matches`
    is never written from one source alone: it is always the merge of every
    source that has spoken about that match.
    """
    stamp = fetched_at.isoformat()
    found: list[tuple[int, merge.Conflict]] = []
    first_seen: dict[MatchKey, MatchData] = {}
    with conn:
        for match in matches:
            row = _to_row(match)
            match_id = _ensure_match(conn, row, stamp)
            if match.key in first_seen:
                # The same source listed one fixture twice, which means it
                # disagrees with the others about who is at home - regiowyniki
                # puts Dynovia at home in round 16 where 90minut puts Dąbrówki.
                # Home and away are part of the key, so merge.py cannot see
                # this; keeping the first row and asking is the honest move.
                found.append((match_id, _duplicate(source, first_seen[match.key], match)))
                continue
            first_seen[match.key] = match
            conn.execute(
                f"INSERT INTO match_sources ({_SOURCE_COLUMNS})"
                f" VALUES ({_SOURCE_VALUES})"
                f" ON CONFLICT (match_id, source) DO UPDATE SET {_SOURCE_UPDATES}",
                row | {"match_id": match_id, "source": source, "fetched_at": stamp},
            )
            found += [(match_id, c) for c in _remerge(conn, match_id, stamp)]
    return found


def _duplicate(source: str, first: MatchData, again: MatchData) -> merge.Conflict:
    return merge.Conflict(
        field="gospodarz",
        source_a=source,
        value_a=f"{first.home} – {first.away} ({first.date or 'bez daty'})",
        source_b=source,
        value_b=f"ten sam mecz ponownie ({again.date or 'bez daty'})",
    )


def _ensure_match(conn: sqlite3.Connection, row: dict, stamp: str) -> int:
    conn.execute(
        f"INSERT INTO matches ({_MATCH_COLUMNS}) VALUES ({_MATCH_VALUES})"
        " ON CONFLICT (season, home_key, away_key) DO NOTHING",
        row | {"updated_at": stamp},
    )
    return conn.execute(
        "SELECT id FROM matches WHERE season = ? AND home_key = ? AND away_key = ?",
        (row["season"], row["home_key"], row["away_key"]),
    ).fetchone()["id"]


def _remerge(
    conn: sqlite3.Connection, match_id: int, stamp: str
) -> list[merge.Conflict]:
    views = {
        row["source"]: _from_row(row)
        for row in conn.execute(
            "SELECT * FROM match_sources WHERE match_id = ?", (match_id,)
        )
    }
    merged, conflicts = merge.merge(views)
    merged = _apply_choices(conn, match_id, merged)
    conn.execute(
        "UPDATE matches SET date = :date, time = :time, competition = :competition,"
        " round = :round, home = :home, away = :away, home_score = :home_score,"
        " away_score = :away_score, status = :status, venue = :venue,"
        " updated_at = :updated_at WHERE id = :id",
        _to_row(merged) | {"id": match_id, "updated_at": stamp},
    )
    for conflict in conflicts:
        record_conflict(conn, match_id, conflict, stamp)
    return conflicts


def record_conflict(
    conn: sqlite3.Connection, match_id: int, conflict: merge.Conflict, stamp: str
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO conflicts (match_id, field, source_a, value_a,"
        " source_b, value_b, detected_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            match_id,
            conflict.field,
            conflict.source_a,
            conflict.value_a,
            conflict.source_b,
            conflict.value_b,
            stamp,
        ),
    )


def _apply_choices(conn: sqlite3.Connection, match_id: int, merged: MatchData):
    """A conflict the owner has settled beats the trust order from then on."""
    import dataclasses

    for row in conn.execute(
        "SELECT field, chosen FROM conflicts"
        " WHERE match_id = ? AND resolved = 1 AND chosen IS NOT NULL",
        (match_id,),
    ):
        field, chosen = row["field"], row["chosen"]
        if field == "date":
            merged = dataclasses.replace(merged, date=dt.date.fromisoformat(chosen))
        elif field == "time":
            merged = dataclasses.replace(merged, time=dt.time.fromisoformat(chosen))
        elif field == "score" and "-" in chosen:
            home, away = chosen.split("-", 1)
            merged = dataclasses.replace(
                merged, home_score=int(home), away_score=int(away), status="finished"
            )
        elif field == "competition":
            merged = dataclasses.replace(merged, competition=chosen)
    return merged


def choose_conflict(conn: sqlite3.Connection, conflict_id: int, value: str) -> int | None:
    """Settle a conflict on the chosen value and rebuild that match."""
    row = conn.execute(
        "SELECT match_id FROM conflicts WHERE id = ?", (conflict_id,)
    ).fetchone()
    if row is None:
        return None
    with conn:
        conn.execute(
            "UPDATE conflicts SET resolved = 1, chosen = ? WHERE id = ?",
            (value, conflict_id),
        )
        _remerge(conn, row["match_id"], _now())
    return row["match_id"]


def pin_player(conn: sqlite3.Connection, conflict_id: int, name: str) -> bool:
    """Answer a "who is this?" question by recording the alias for good."""
    row = conn.execute(
        "SELECT source_a, value_a FROM conflicts WHERE id = ?", (conflict_id,)
    ).fetchone()
    if row is None:
        return False
    with conn:
        player_id = _player_id(conn, name)
        conn.execute(
            "INSERT OR IGNORE INTO player_aliases (player_id, alias, source)"
            " VALUES (?, ?, ?)",
            (player_id, normalize_player(row["value_a"]), row["source_a"]),
        )
        conn.execute(
            "UPDATE conflicts SET resolved = 1, chosen = ? WHERE id = ?",
            (name, conflict_id),
        )
    return True


def stored_matches(conn: sqlite3.Connection) -> dict[MatchKey, MatchData]:
    return {
        (row["season"], row["home_key"], row["away_key"]): _from_row(row)
        for row in conn.execute("SELECT * FROM matches")
    }


def match_ids(conn: sqlite3.Connection) -> dict[MatchKey, int]:
    return {
        (row["season"], row["home_key"], row["away_key"]): row["id"]
        for row in conn.execute("SELECT id, season, home_key, away_key FROM matches")
    }


def open_conflicts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT c.*, m.home, m.away, m.date FROM conflicts c"
            " JOIN matches m ON m.id = c.match_id WHERE c.resolved = 0"
            " ORDER BY c.detected_at"
        )
    )


def last_fetch(conn: sqlite3.Connection, source: str) -> dt.datetime | None:
    """When this source last delivered something. Derived from match_sources
    rather than a separate table - one less thing to keep in sync."""
    row = conn.execute(
        "SELECT MAX(fetched_at) AS last FROM match_sources WHERE source = ?",
        (source,),
    ).fetchone()
    return dt.datetime.fromisoformat(row["last"]) if row and row["last"] else None


def mark_sent(conn: sqlite3.Connection, match_id: int, kind: str) -> bool:
    """True if this notification is new. False means it already went out and the
    caller must stay quiet - the same cron tick can easily run twice."""
    with conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO notifications_sent (match_id, kind, sent_at)"
            " VALUES (?, ?, ?)",
            (match_id, kind, dt.datetime.now(dt.UTC).isoformat()),
        )
    return cursor.rowcount == 1


def unmark_sent(conn: sqlite3.Connection, match_id: int, kind: str) -> None:
    """Release a reservation made by mark_sent() when delivery failed, so the
    next run tries again instead of silently swallowing the message."""
    with conn:
        conn.execute(
            "DELETE FROM notifications_sent WHERE match_id = ? AND kind = ?",
            (match_id, kind),
        )


def seen_articles(conn: sqlite3.Connection, source: str) -> set[str]:
    """Urls already downloaded. A futbolowo article is a couple of megabytes and
    never changes, so it is fetched exactly once."""
    return {
        row["url"]
        for row in conn.execute("SELECT url FROM articles WHERE source = ?", (source,))
    }


def store_reports(
    conn: sqlite3.Connection,
    reports: list[MatchReport],
    source: str,
    fetched_at: dt.datetime,
) -> None:
    """Keep every article, including the ones that turned out not to be match
    reports - that is what stops them being downloaded again next run. Reports
    that could not be tied to a match are kept with match_id NULL rather than
    dropped, so they can be matched by hand instead of vanishing."""
    ids = match_ids(conn)
    with conn:
        for report in reports:
            conn.execute(
                """
                INSERT INTO articles (url, source, title, published_at, match_id,
                                      text, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (url) DO UPDATE SET
                    match_id   = COALESCE(excluded.match_id, articles.match_id),
                    text       = excluded.text,
                    fetched_at = excluded.fetched_at
                """,
                (
                    report.url,
                    source,
                    report.title,
                    report.published_at.isoformat() if report.published_at else None,
                    ids.get(report.match) if report.match else None,
                    report.text,
                    fetched_at.isoformat(),
                ),
            )


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _player_id(conn: sqlite3.Connection, name: str) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO players (name, normalized_name) VALUES (?, ?)",
        (name, normalize_player(name)),
    )
    return conn.execute(
        "SELECT id FROM players WHERE normalized_name = ?", (normalize_player(name),)
    ).fetchone()["id"]


def _opponent_id(conn: sqlite3.Connection, name: str) -> int:
    """An opposition player: recorded as written, never asked about.

    kadra.txt holds our team, so resolving these would raise a question about
    every single one. They are stored to fill in the other half of a protocol
    and are kept out of every statistic by `ours`.

    ours = 0 is set on insert only. A namesake already stored as ours keeps
    that row: a shared surname must not turn one of our players into an
    opponent.
    """
    conn.execute(
        "INSERT OR IGNORE INTO players (name, normalized_name, ours)"
        " VALUES (?, ?, 0)",
        (name, normalize_player(name)),
    )
    return conn.execute(
        "SELECT id FROM players WHERE normalized_name = ?", (normalize_player(name),)
    ).fetchone()["id"]


def _identify(
    conn: sqlite3.Connection,
    written: str,
    source: str,
    registry: dict[str, str],
) -> tuple[int | None, merge.Conflict | None]:
    """A written name -> a player, or a question for the user.

    An already-answered spelling is remembered in player_aliases, so the same
    question is never asked twice.
    """
    row = conn.execute(
        "SELECT player_id FROM player_aliases WHERE alias = ? AND source = ?",
        (normalize_player(written), source),
    ).fetchone()
    if row:
        return row["player_id"], None

    name, candidates = players.resolve(written, registry)
    if name is None:
        return None, merge.Conflict(
            field="zawodnik",
            source_a=source,
            value_a=written,
            source_b="kadra",
            value_b=", ".join(candidates),
        )

    player_id = _player_id(conn, name)
    conn.execute(
        "INSERT OR IGNORE INTO player_aliases (player_id, alias, source)"
        " VALUES (?, ?, ?)",
        (player_id, normalize_player(written), source),
    )
    return player_id, None


def _store_player_rows(
    conn: sqlite3.Connection,
    rows,
    source: str,
    registry: dict[str, str],
    statement: str,
    columns,
) -> list[tuple[int, merge.Conflict]]:
    """Shared plumbing for lineups, goals and cards: resolve the player, then
    write. A row whose player cannot be identified is not written at all -
    a wrong attribution is worse than a missing one, and the question goes out
    on Telegram instead.

    registry=None means these rows are the opposition's: stored as written,
    never resolved, never questioned. An empty registry is not the same thing -
    that is our team with kadra.txt missing, and still worth asking about.
    """
    ids = match_ids(conn)
    found: list[tuple[int, merge.Conflict]] = []
    with conn:
        for row in rows:
            match_id = ids.get(row.match)
            if match_id is None:
                continue
            if registry is None:
                player_id, conflict = _opponent_id(conn, row.player), None
            else:
                player_id, conflict = _identify(conn, row.player, source, registry)
            if conflict is not None:
                # Stored as well as returned: an unanswered question has to
                # survive until /konflikty can put buttons under it.
                record_conflict(conn, match_id, conflict, _now())
                found.append((match_id, conflict))
                continue
            conn.execute(
                statement,
                (match_id, player_id, *(getattr(row, column) for column in columns), source),
            )
    return found


def store_lineups(conn, lineups, source, registry) -> list[tuple[int, merge.Conflict]]:
    return _store_player_rows(
        conn,
        lineups,
        source,
        registry,
        "INSERT INTO appearances (match_id, player_id, started, minute_in,"
        " minute_out, source) VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (match_id, player_id, source) DO UPDATE SET"
        " started = excluded.started, minute_in = excluded.minute_in,"
        " minute_out = excluded.minute_out",
        ("started", "minute_in", "minute_out"),
    )


def store_goals(conn, goals, source, registry) -> list[tuple[int, merge.Conflict]]:
    return _store_player_rows(
        conn,
        goals,
        source,
        registry,
        "INSERT OR IGNORE INTO goals (match_id, player_id, minute, type, source)"
        " VALUES (?, ?, ?, ?, ?)",
        ("minute", "type"),
    )


def store_cards(conn, cards, source, registry) -> list[tuple[int, merge.Conflict]]:
    return _store_player_rows(
        conn,
        cards,
        source,
        registry,
        "INSERT OR IGNORE INTO cards (match_id, player_id, minute, color, source)"
        " VALUES (?, ?, ?, ?, ?)",
        ("minute", "color"),
    )


def store_table(conn, table, season: str, source: str, fetched_at: dt.datetime) -> None:
    if not table:
        return
    with conn:
        conn.execute(
            "DELETE FROM league_table WHERE season = ? AND source = ?", (season, source)
        )
        conn.executemany(
            "INSERT INTO league_table (season, source, position, team, team_key,"
            " played, points, goal_difference, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    season,
                    source,
                    row.position,
                    row.team,
                    normalize_team(row.team),
                    row.played,
                    row.points,
                    row.goal_difference,
                    fetched_at.isoformat(),
                )
                for row in table
            ],
        )


def read_table(conn, season: str) -> list[sqlite3.Row]:
    """The standings, from whichever source last supplied them."""
    return list(
        conn.execute(
            "SELECT * FROM league_table WHERE season = ?"
            " AND source = (SELECT source FROM league_table WHERE season = ?"
            "               ORDER BY updated_at DESC LIMIT 1)"
            " ORDER BY position",
            (season, season),
        )
    )


def get_setting(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def source_status(conn) -> list[sqlite3.Row]:
    """When each source last delivered, and how much of the season it covers."""
    return list(
        conn.execute(
            "SELECT source, COUNT(*) AS matches, MAX(fetched_at) AS last"
            " FROM match_sources GROUP BY source ORDER BY source"
        )
    )


def resolve_conflict(conn, conflict_id: int) -> None:
    with conn:
        conn.execute("UPDATE conflicts SET resolved = 1 WHERE id = ?", (conflict_id,))


def goals_needing_assist(conn: sqlite3.Connection, season: str) -> list[sqlite3.Row]:
    """Goals nobody has been credited with setting up yet.

    Only goals from the most trusted source that saw the match: the same goal
    reported by two sources is one goal, and asking twice about it would put
    two assists on one shot.
    """
    rows = conn.execute(
        "SELECT g.id, g.match_id, g.minute, g.source, p.name AS scorer,"
        "       m.home, m.away, m.date"
        " FROM goals g"
        " JOIN players p ON p.id = g.player_id AND p.ours = 1"
        " JOIN matches m ON m.id = g.match_id"
        " WHERE m.season = ?"
        "   AND NOT EXISTS (SELECT 1 FROM assists a WHERE a.goal_id = g.id)"
        " ORDER BY m.date, g.minute",
        (season,),
    ).fetchall()

    best: dict[int, str] = {}
    for row in rows:
        rank = merge.GOAL_TRUST.index(row["source"]) if row["source"] in merge.GOAL_TRUST else 99
        current = best.get(row["match_id"])
        if current is None or rank < current[0]:
            best[row["match_id"]] = (rank, row["source"])
    return [row for row in rows if best[row["match_id"]][1] == row["source"]]


def store_assist(
    conn: sqlite3.Connection,
    goal_id: int,
    player: str | None,
    source: str = "manual",
) -> None:
    """Record the owner's answer. player=None means "nobody assisted", which is
    as much an answer as a name. Never called with a guess - an unconfirmed
    assist does not belong in the statistics."""
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO assists (goal_id, player_id, source, confirmed)"
            " VALUES (?, ?, ?, 1)",
            (goal_id, _player_id(conn, player) if player else None, source),
        )


def match_squad(conn: sqlite3.Connection, match_id: int) -> list[str]:
    """Who actually played, for the buttons. Falls back to nobody rather than
    the whole roster - a list of 24 names is not a choice, it is a haystack."""
    return [
        row["name"]
        for row in conn.execute(
            "SELECT DISTINCT p.name FROM appearances a"
            " JOIN players p ON p.id = a.player_id AND p.ours = 1"
            " WHERE a.match_id = ? ORDER BY p.name",
            (match_id,),
        )
    ]


def match_appearances(conn: sqlite3.Connection, match_id: int) -> list[sqlite3.Row]:
    """Who played and for how long, from the PZPN protocol only - it is the one
    source that carries minutes, the same rule player_summary counts by."""
    return conn.execute(
        "SELECT p.name, a.started, a.minute_in, a.minute_out FROM appearances a"
        " JOIN players p ON p.id = a.player_id AND p.ours = 1"
        " WHERE a.match_id = ? AND a.source = 'laczynaspilka'"
        " ORDER BY a.started DESC, COALESCE(a.minute_in, 0), p.name",
        (match_id,),
    ).fetchall()


def match_cards(conn: sqlite3.Connection, match_id: int) -> list[sqlite3.Row]:
    """Cards shown in one match, earliest first.

    DISTINCT rather than a trust order like the scorers get: today the PZPN
    protocol is the only source that records cards at all, so there is nothing
    to rank - but if a scraper ever learns to, the same card seen twice must
    still be listed once.
    """
    return conn.execute(
        "SELECT DISTINCT p.name, c.color, c.minute FROM cards c"
        " JOIN players p ON p.id = c.player_id AND p.ours = 1"
        " WHERE c.match_id = ? ORDER BY COALESCE(c.minute, 999), p.name",
        (match_id,),
    ).fetchall()


def report_for(conn: sqlite3.Connection, match_id: int) -> str:
    row = conn.execute(
        "SELECT text FROM articles WHERE match_id = ? ORDER BY LENGTH(text) DESC LIMIT 1",
        (match_id,),
    ).fetchone()
    return row["text"] if row else ""
