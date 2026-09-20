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

from dynovia import db, differ
from dynovia.differ import WARSAW, kickoff
from dynovia.notify import telegram
from dynovia.scrapers import SCRAPERS
from dynovia.snapshot import load_fixtures

log = logging.getLogger("dynovia.run")

MATCH_WINDOW = dt.timedelta(hours=3)  # whistle to final whistle, generously
AFTERMATH = dt.timedelta(hours=72)  # lineups and protocols trickle in this long


def poll_interval(matches, now: dt.datetime) -> dt.timedelta:
    """How often this source deserves to be polled right now.

    The schedule from the plan: every 10 minutes while a match is on, every two
    hours for three days afterwards, otherwise once a day. Without it a */10
    cron would hit these small community-run sites 144 times a day for nothing.
    """
    interval = dt.timedelta(days=1)
    for match in matches:
        start = kickoff(match)
        if start is None:
            continue
        if start <= now <= start + MATCH_WINDOW:
            return dt.timedelta(minutes=10)  # nothing beats a live match
        if start < now <= start + AFTERMATH:
            interval = min(interval, dt.timedelta(hours=2))
    return interval


def is_due(last: dt.datetime | None, matches, now: dt.datetime) -> bool:
    if last is None:
        return True
    interval = poll_interval(matches, now)
    if interval < dt.timedelta(days=1):
        return now - last >= interval
    # The daily refresh is anchored at 06:00 Warsaw time so the "tomorrow"
    # reminders are built from data fetched that morning. Actions runs on UTC,
    # hence the explicit conversion.
    local, last_local = now.astimezone(WARSAW), last.astimezone(WARSAW)
    return local.hour >= 6 and last_local.date() < local.date()


def collect(conn, now: dt.datetime, *, offline: bool) -> list[differ.Event]:
    # A first run pulls a whole season from every source at once. Sources after
    # the first would otherwise see a populated database and report the gaps
    # they fill as news, so the whole run stays silent, not just source one.
    first_run = not db.stored_matches(conn)
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
            continue
        log.info("%s: %d matches", name, len(result.matches))
        conflicts = db.store_matches(
            conn, result.matches, result.source, result.fetched_at
        )
        if not first_run:
            # Diff the merged view before against the merged view after, never
            # the raw source view: regiowyniki reporting 11.10 while 90minut
            # wins with 10.10 must not produce a message the database
            # contradicts.
            events += differ.diff(known, list(db.stored_matches(conn).values()), now)
            events += _conflict_events(conn, conflicts)
        if result.reports:
            db.store_reports(conn, result.reports, result.source, result.fetched_at)
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
            telegram.send(event.text)
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
    events = collect(conn, now, offline=offline)
    events += differ.due_reminders(db.stored_matches(conn), now)
    log.info("%d event(s)", len(events))
    notify(conn, events, dry_run=dry_run)


if __name__ == "__main__":
    main(sys.argv[1:])
