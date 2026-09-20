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

from dynovia import merge
from dynovia.config import DB_PATH
from dynovia.models import MatchData, MatchKey, MatchReport, normalize_team

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
    UNIQUE (match_id, field, source_a, value_a, source_b, value_b)
);

CREATE TABLE IF NOT EXISTS players (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE
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
    source    TEXT    NOT NULL,
    UNIQUE (match_id, player_id, minute, source)
);

CREATE TABLE IF NOT EXISTS assists (
    id        INTEGER PRIMARY KEY,
    goal_id   INTEGER NOT NULL REFERENCES goals(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    source    TEXT    NOT NULL,               -- llm|manual
    confirmed INTEGER NOT NULL DEFAULT 0,     -- only confirmed ones count
    UNIQUE (goal_id, player_id)
);

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
    return conn


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
    conn.execute(
        "UPDATE matches SET date = :date, time = :time, competition = :competition,"
        " round = :round, home = :home, away = :away, home_score = :home_score,"
        " away_score = :away_score, status = :status, venue = :venue,"
        " updated_at = :updated_at WHERE id = :id",
        _to_row(merged) | {"id": match_id, "updated_at": stamp},
    )
    for conflict in conflicts:
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
    return conflicts


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
