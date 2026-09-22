"""Entry point for one cron tick.

    py -3.12 -m dynovia.run [--dry-run] [--offline]

--dry-run prints the notifications instead of sending them and records nothing,
so it can be repeated. --offline parses the stored snapshots instead of going
online, which is how this gets exercised during development.
"""

from __future__ import annotations

import datetime as dt
import logging
import sys

from dynovia import bot, db, differ, export, models, players, protokol
from dynovia.config import PROTOCOLS_DIR, ROSTER_PATH, SCHEDULE_PATH
from dynovia.differ import AFTERMATH, WARSAW, kickoff
from dynovia.notify import telegram, webpush
from dynovia.scrapers import SCRAPERS
from dynovia.snapshot import load_fixtures

log = logging.getLogger("dynovia.run")

IDLE = dt.timedelta(days=1)
"""Polling interval with no recent match to justify anything faster."""

POLLING = (
    (dt.timedelta(hours=6), dt.timedelta(minutes=10)),
    (dt.timedelta(hours=24), dt.timedelta(minutes=30)),
    (AFTERMATH, dt.timedelta(hours=2)),
)
"""How long after kickoff, and how often to poll inside that.

Six hours rather than three for the fast band because that is when these sites
actually publish: a 14:00 kickoff whistles around 15:50 and the result lands on
regiowyniki or podkarpacielive somewhere between 16:00 and 20:00. A three-hour
window closed at 17:00 and left most of that to the slow band.

Deliberately not faster, and deliberately zero outside the bands: these are
small sites run by people after work, and a */10 cron with no schedule at all
would hit each of them 144 times a day to learn nothing.
"""


def poll_interval(matches, now: dt.datetime) -> dt.timedelta:
    """How often this source deserves to be polled right now."""
    interval = IDLE
    for match in matches:
        start = kickoff(match)
        if start is None:
            continue
        for since, every in POLLING:
            # min across every match rather than returning on the first hit:
            # two matches in one weekend must not depend on list order.
            if start <= now <= start + since:
                interval = min(interval, every)
                break
    return interval


def is_due(last: dt.datetime | None, matches, now: dt.datetime) -> bool:
    if last is None:
        return True
    interval = poll_interval(matches, now)
    if interval < IDLE:
        return now - last >= interval
    # The daily refresh is anchored at 06:00 Warsaw time so the "tomorrow"
    # reminders are built from data fetched that morning. Actions runs on UTC,
    # hence the explicit conversion.
    local, last_local = now.astimezone(WARSAW), last.astimezone(WARSAW)
    return local.hour >= 6 and last_local.date() < local.date()


FAILURES_BEFORE_ALERT = 3
"""A source blips. Three runs in a row is a source that is actually broken."""


def _note_failure(conn, name: str, *, quiet: bool) -> None:
    count = int(db.get_setting(conn, f"failures:{name}") or 0) + 1
    db.set_setting(conn, f"failures:{name}", count)
    if count == FAILURES_BEFORE_ALERT and not quiet:
        try:
            telegram.send(f"⚠️ Źródło {name} nie działa od {count} runów z rzędu.")
        except Exception:  # noqa: BLE001
            log.exception("failure alert could not be sent")


def collect(conn, now: dt.datetime, *, offline: bool, quiet: bool = False) -> list[differ.Event]:
    # A first run pulls a whole season from every source at once. Sources after
    # the first would otherwise see a populated database and report the gaps
    # they fill as news, so the whole run stays silent, not just source one.
    first_run = not db.stored_matches(conn)
    registry = players.load_roster(ROSTER_PATH)
    # The PZPN schedule goes in first: it is the source of record, so every
    # scraper that follows is measured against it rather than the other way.
    pzpn = protokol.import_schedule(conn, SCHEDULE_PATH, now)
    if not registry:
        log.warning("kadra.txt is empty - player names cannot be resolved yet")
    events: list[differ.Event] = []
    for name, scraper_cls in SCRAPERS.items():
        known = db.stored_matches(conn)
        scraper = scraper_cls(seen=db.seen_articles(conn, name))
        try:
            if offline:
                result = scraper.parse(load_fixtures(name))
            elif not is_due(db.last_fetch(conn, name), known.values(), now):
                log.info("%s: not due yet", name)
                continue
            else:
                result = scraper.fetch()
        except Exception:  # noqa: BLE001 - one dead source must not kill the run
            log.exception("%s: scrape failed", name)
            _note_failure(conn, name, quiet=quiet)
            continue
        db.set_setting(conn, f"failures:{name}", 0)
        log.info("%s: %d matches", name, len(result.matches))
        conflicts = db.store_matches(
            conn, result.matches, result.source, result.fetched_at
        )
        conflicts += db.store_lineups(conn, result.lineups, result.source, registry)
        conflicts += db.store_goals(conn, result.goals, result.source, registry)
        conflicts += db.store_cards(conn, result.cards, result.source, registry)
        if not first_run:
            # Diff the merged view before against the merged view after, never
            # the raw source view: regiowyniki reporting 11.10 while 90minut
            # wins with 10.10 must not produce a message the database
            # contradicts.
            events += differ.diff(known, list(db.stored_matches(conn).values()), now)
            events += _conflict_events(conn, conflicts)
        if result.reports:
            db.store_reports(conn, result.reports, result.source, result.fetched_at)
        if result.table:
            season = next(iter(result.matches)).season if result.matches else ""
            db.store_table(conn, result.table, season, result.source, result.fetched_at)

    # Hand-saved PZPN protocols, if any are waiting. Local files, no network,
    # so this runs on every tick regardless of the polling schedule.
    imported = pzpn + protokol.import_directory(conn, PROTOCOLS_DIR, registry)
    if imported and not first_run:
        events += _conflict_events(conn, imported)
    if not first_run:
        events += _detail_events(conn, now)
        events += _assist_questions(conn)
    return events


def _detail_events(conn, now: dt.datetime) -> list[differ.Event]:
    """Goals and squads live in their own tables, keyed by match id, so they
    cannot come out of differ.diff() the way the match fields do."""
    matches = db.stored_matches(conn)
    fresh = differ.fresh_matches(matches, now)
    if not fresh:
        return []
    ids = db.match_ids(conn)
    seasons = {matches[key].season for key in fresh}
    scorers = {}
    for season in seasons:
        scorers |= export.scorers_by_match(conn, season)
    events = []
    for key in fresh:
        match_id = ids[key]
        events += differ.match_details(
            key,
            matches[key],
            scorers.get(match_id, []),
            [dict(row) for row in db.match_appearances(conn, match_id)],
            [dict(row) for row in db.match_cards(conn, match_id)],
            now,
        )
    return events


MAX_ASSIST_QUESTIONS = 3
"""Per run. These are questions, not a report - a backlog dumped all at once
is a chore, and notifications_sent keeps each one to a single asking."""


def _assist_questions(conn) -> list[differ.Event]:
    keys = {match_id: key for key, match_id in db.match_ids(conn).items()}
    season = models.season_for(dt.date.today())
    events = []
    for goal in db.goals_needing_assist(conn, season)[:MAX_ASSIST_QUESTIONS]:
        text, buttons = bot.assist_question(conn, goal)
        events.append(
            differ.Event(keys[goal["match_id"]], f"assist:{goal['id']}", text, buttons)
        )
    return events


def _conflict_events(conn, conflicts) -> list[differ.Event]:
    """A disagreement between sources is never settled quietly - merge.py picks
    a value so the app has something to show, and this puts the question in
    front of the user."""
    if not conflicts:
        return []
    keys = {match_id: key for key, match_id in db.match_ids(conn).items()}
    events = []
    for match_id, conflict in conflicts:
        match = db.stored_matches(conn)[keys[match_id]]
        events.append(
            differ.Event(
                match=keys[match_id],
                # The values are part of the kind: a second, different
                # disagreement about the same field has to alert again.
                kind=f"conflict:{conflict.field}:{conflict.value_a}:{conflict.value_b}",
                text=(
                    f"⚠️ Rozbieżność: {match.home} – {match.away}\n"
                    f"{conflict.describe()}\nSprawdź, które źródło ma rację."
                ),
            )
        )
    return events


def _warn_once(conn, match_id: int, text: str) -> None:
    if db.mark_sent(conn, match_id, "push_expired"):
        telegram.send(text)


def notify(conn, events: list[differ.Event], *, dry_run: bool) -> None:
    ids = db.match_ids(conn)
    for event in events:
        match_id = ids.get(event.match)
        if match_id is None:
            continue
        if dry_run:
            telegram.send(event.text, dry_run=True)
            continue
        if not db.mark_sent(conn, match_id, event.kind):
            continue  # already went out on an earlier tick
        try:
            telegram.send(
                event.text,
                buttons=event.buttons,
                as_html=event.text.startswith("<pre>"),
            )
            # Push is the second channel and never the reason a run fails:
            # it only reports back when the subscription itself has died.
            problem = webpush.send(event.text)
            if problem and problem.expired:
                _warn_once(conn, match_id, problem.message)
        except Exception:  # noqa: BLE001
            # Release the reservation so the next run retries instead of
            # swallowing the message for good.
            db.unmark_sent(conn, match_id, event.kind)
            log.exception("delivery failed: %s", event.text)


def main(argv: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    dry_run = "--dry-run" in argv
    offline = "--offline" in argv

    conn = db.connect()
    now = dt.datetime.now(dt.UTC)
    events = collect(conn, now, offline=offline, quiet=dry_run or offline)
    events += differ.due_reminders(db.stored_matches(conn), now)
    log.info("%d event(s)", len(events))
    notify(conn, events, dry_run=dry_run)
    export.write(conn)
    if not dry_run and not offline:
        bot.poll(conn)
    # WAL keeps recent writes in a sidecar file that is deliberately not
    # committed, so the database has to be closed before Actions commits it.
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
